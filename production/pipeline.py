"""Orchestrates: Creative JSON -> VideoProvider (per shot) -> TTS ->
Subtitles/Edit -> QA -> MP4. Also supports regenerating only the shots
marked needs_regen/failed, without redoing narration/subtitles/QA for the
shots that already passed.
"""
from __future__ import annotations

from pathlib import Path

from production.compositor.ffmpeg_compose import ComposeInputs, SFXCueInput, compose
from production.providers.registry import generate_with_failover
from production.qa.compliance import run_compliance_qa
from production.qa.technical import run_technical_qa
from production.schemas.models import ProductionJob
from production.subtitles.generator import build_cues, write_srt
from production.tts.espeak_provider import EspeakTTSProvider

JOBS_DIR = Path(__file__).parent / "jobs"
OUTPUT_ROOT = Path(__file__).parent.parent / "output"


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _job_output_dir(job_id: str) -> Path:
    return OUTPUT_ROOT / job_id


def load_job(job_id: str) -> ProductionJob:
    return ProductionJob.load(_job_path(job_id))


def save_job(job: ProductionJob) -> None:
    job.save(_job_path(job.job_id))


def generate_shots(job: ProductionJob, only_shot_ids: set[str] | None = None) -> None:
    """Generate every shot whose id is in only_shot_ids (or every shot that
    isn't already 'success' when only_shot_ids is None)."""
    shots_dir = _job_output_dir(job.job_id) / "shots"
    for shot in job.shots:
        already_done = (
            shot.status == "success"
            and shot.output_path is not None
            and Path(shot.output_path).exists()
        )
        if only_shot_ids is not None:
            if shot.shot_id not in only_shot_ids:
                continue
        elif already_done:
            continue

        shot.status = "generating"
        shot.attempts += 1
        result = generate_with_failover(shot, shots_dir, job.provider_preferences)
        if result.success:
            shot.status = "success"
            shot.output_path = result.output_path
            shot.assigned_provider = result.provider
            shot.error = None
            shot.qa_notes = "placeholder clip - replace before publishing" if result.is_placeholder else None
        else:
            shot.status = "failed"
            shot.error = result.error


def synthesize_narration(job: ProductionJob):
    out_dir = _job_output_dir(job.job_id) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    segments_text = [s.narration_text for s in sorted(job.shots, key=lambda s: s.order) if s.narration_text]
    tts = EspeakTTSProvider()
    return tts.synthesize(segments_text, job.tts.voice, job.tts.language, out_dir / "narration.wav")


def render(job_id: str) -> Path:
    """Full pipeline run for a job. Returns the final MP4 path.
    Raises RuntimeError with a clear message if QA fails."""
    job = load_job(job_id)
    job.status = "generating"

    generate_shots(job)
    failed = [s.shot_id for s in job.shots if s.status != "success"]
    if failed:
        job.status = "needs_regen"
        save_job(job)
        raise RuntimeError(f"shots failed to generate, fix or rerun regen-shot for: {failed}")

    tts_result = synthesize_narration(job)
    if not tts_result.success:
        job.status = "qa_failed"
        save_job(job)
        raise RuntimeError(f"TTS failed: {tts_result.error}")

    work_dir = _job_output_dir(job.job_id) / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    srt_path = None
    if job.subtitles.enabled:
        cues = build_cues(tts_result.segments)
        srt_path = write_srt(cues, work_dir / "subtitles.srt")

    ordered_shots = sorted(job.shots, key=lambda s: s.order)
    shot_start_sec = {}
    cursor = 0.0
    for s in ordered_shots:
        shot_start_sec[s.shot_id] = cursor
        cursor += s.duration_sec

    sfx_cues = [
        SFXCueInput(
            audio_path=Path(cue.effect),
            at_sec=shot_start_sec.get(cue.shot_id, 0.0) + cue.at_sec,
            volume_db=cue.volume_db,
        )
        for cue in job.sfx
    ]

    compose_inputs = ComposeInputs(
        shot_paths=[Path(s.output_path) for s in ordered_shots],
        narration_audio_path=Path(tts_result.audio_path),
        srt_path=srt_path,
        cta_text=job.creative.cta,
        cta_start_sec=max(0.0, sum(s.duration_sec for s in ordered_shots) - 3.0),
        pr_disclosure_text=job.creative.pr_disclosure.text if job.creative.pr_disclosure.required else None,
        bgm_path=Path(job.bgm.track_path) if job.bgm.enabled and job.bgm.track_path else None,
        bgm_volume_db=job.bgm.volume_db,
        sfx_cues=sfx_cues,
        out_path=_job_output_dir(job.job_id) / f"{job.job_id}.mp4",
    )
    final_mp4 = compose(compose_inputs, work_dir)

    tech_report = run_technical_qa(final_mp4, job.qa.technical)
    compliance_report = run_compliance_qa(job)

    if not (tech_report.passed and compliance_report.passed):
        job.status = "qa_failed"
        save_job(job)
        failing = [f.check for f in tech_report.findings if not f.passed] + \
                  [f.check for f in compliance_report.findings if not f.passed]
        raise RuntimeError(f"QA failed: {failing}")

    job.status = "ready"
    job.output.mp4_path = str(final_mp4)
    from datetime import datetime, timezone
    job.output.generated_at = datetime.now(timezone.utc).isoformat()
    save_job(job)
    return final_mp4


def mark_shot_for_regen(job_id: str, shot_id: str) -> None:
    job = load_job(job_id)
    job.shot(shot_id).status = "needs_regen"
    job.status = "needs_regen"
    save_job(job)


def regen_shot(job_id: str, shot_id: str) -> None:
    job = load_job(job_id)
    generate_shots(job, only_shot_ids={shot_id})
    save_job(job)
