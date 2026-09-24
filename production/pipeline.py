"""Orchestrates: Creative JSON -> VideoProvider (per shot) -> TTS ->
Subtitles/Edit -> QA -> MP4. Also supports regenerating only the shots
marked needs_regen/failed, without redoing narration/subtitles/QA for the
shots that already passed.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from production.compositor.ffmpeg_compose import ComposeInputs, SFXCueInput, compose
from production.costlog import CostLogEntry, append_cost_log
from production.providers.registry import generate_with_failover
from production.qa.compliance import run_compliance_qa
from production.qa.technical import run_technical_qa
from production.schemas.models import ProductionJob
from production.subtitles.generator import build_cues, write_srt
from production.tts.espeak_provider import EspeakTTSProvider

JOBS_DIR = Path(__file__).parent / "jobs"
OUTPUT_ROOT = Path(__file__).parent.parent / "output"

# Providers/assets that stand in for a real, paid pipeline step. A render
# that used any of these is a pipeline-smoke-test artifact, not a
# publishable TikTok video - render() marks it quality_tier="placeholder_preview"
# accordingly and the CLI prints a loud warning, per explicit instruction:
# placeholder audio/video must never be silently treated as final quality.
PLACEHOLDER_VIDEO_PROVIDERS = {"manual"}
PLACEHOLDER_TTS_PROVIDERS = {"espeak_local"}
PLACEHOLDER_ASSET_MARKERS = ("placeholder_bgm", "sfx_whoosh")
HUMAN_FREE_TIER_PREFIX = "human_free_tier:"


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
    isn't already 'success' when only_shot_ids is None). Every attempt -
    success or failure - is timed, costed, and appended to cost_log.jsonl."""
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
        started = time.perf_counter()
        result = generate_with_failover(shot, shots_dir, job.provider_preferences)
        elapsed = round(time.perf_counter() - started, 2)

        cost = result.cost_usd_estimate if result.success else 0.0
        shot.total_cost_usd = round(shot.total_cost_usd + cost, 4)
        if shot.first_attempt_cost_usd is None:
            shot.first_attempt_cost_usd = cost
        shot.last_generation_time_sec = elapsed

        from production.providers.registry import get_provider
        provider_meta = {}
        if result.provider and result.provider != "none":
            try:
                provider_meta = get_provider(result.provider).current_config()
            except ValueError:
                pass

        if result.success:
            shot.status = "success"
            shot.output_path = result.output_path
            shot.assigned_provider = result.provider
            shot.error = None
            shot.qa_notes = "placeholder clip - replace before publishing" if result.is_placeholder else None
            rejection_reason = None
        else:
            shot.status = "failed"
            shot.error = result.error
            rejection_reason = result.error

        append_cost_log(OUTPUT_ROOT, job.job_id, CostLogEntry(
            provider=result.provider or "none",
            model=provider_meta.get("model", ""),
            creative_id=job.job_id,
            shot_id=shot.shot_id,
            duration_sec=shot.duration_sec,
            resolution=provider_meta.get("resolution", ""),
            attempt_count=shot.attempts,
            generation_cost_usd=cost,
            generation_time_sec=elapsed,
            accepted=shot.accepted,
            rejection_reason=rejection_reason,
        ))


def _assess_quality(job: ProductionJob) -> tuple[str, list[str]]:
    """Never let placeholder audio/video, or real-but-non-commercial free-tier
    video, be mistaken for a publishable result: flag exactly which pieces
    are still stand-ins, and at what tier."""
    notes: list[str] = []
    has_placeholder_video = False
    has_free_tier_video = False

    placeholder_shots = [s.shot_id for s in job.shots if s.assigned_provider in PLACEHOLDER_VIDEO_PROVIDERS]
    if placeholder_shots:
        has_placeholder_video = True
        notes.append(
            f"video: {len(placeholder_shots)} shot(s) rendered by the manual/ffmpeg "
            f"placeholder provider, not a real generation model: {placeholder_shots}"
        )

    free_tier_shots = [s for s in job.shots if (s.assigned_provider or "").startswith(HUMAN_FREE_TIER_PREFIX)]
    non_commercial_free_shots = [s.shot_id for s in free_tier_shots if s.license_commercial_clear is not True]
    if non_commercial_free_shots:
        has_free_tier_video = True
        notes.append(
            "video: "
            f"{len(non_commercial_free_shots)} shot(s) generated on a free-tier "
            "service whose terms do not confirm commercial-use clearance "
            f"(watermarked/personal-use-only unless proven otherwise): {non_commercial_free_shots}"
        )

    if job.tts.provider in PLACEHOLDER_TTS_PROVIDERS:
        notes.append(
            "audio: narration synthesized with espeak-ng, an offline robotic "
            "placeholder voice - not final TikTok narration quality"
        )

    if job.bgm.enabled and job.bgm.track_path and any(m in job.bgm.track_path for m in PLACEHOLDER_ASSET_MARKERS):
        notes.append("bgm: synthetic ffmpeg-generated tone, not a licensed/royalty-free track")

    for cue in job.sfx:
        if any(m in cue.effect for m in PLACEHOLDER_ASSET_MARKERS):
            notes.append(f"sfx: shot {cue.shot_id} uses a synthetic ffmpeg-generated placeholder sound")

    if has_placeholder_video:
        tier = "placeholder_preview"
    elif has_free_tier_video:
        tier = "free_tier_noncommercial_preview"
    elif notes:
        # audio/BGM/SFX placeholders only, video itself came from a real,
        # commercially-clear provider
        tier = "placeholder_preview"
    else:
        tier = "final_candidate"
    return tier, notes


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

    quality_tier, quality_notes = _assess_quality(job)
    cost_rollup = compute_cost_rollup(job)

    job.status = "ready"
    job.output.mp4_path = str(final_mp4)
    job.output.quality_tier = quality_tier
    job.output.quality_notes = quality_notes
    job.output.total_generation_cost_usd = cost_rollup["total_generation_cost_usd"]
    job.output.total_regeneration_cost_usd = cost_rollup["total_regeneration_cost_usd"]
    job.output.total_render_cost_usd = cost_rollup["total_render_cost_usd"]
    job.output.cost_per_finished_video = cost_rollup["cost_per_finished_video"]
    from datetime import datetime, timezone
    job.output.generated_at = datetime.now(timezone.utc).isoformat()
    save_job(job)
    return final_mp4


def ingest_shot(
    job_id: str,
    shot_id: str,
    source_path: str,
    service: str,
    license_commercial_clear: bool = False,
    license_note: str = "",
) -> None:
    """Adopt a clip a human generated by hand on a free-tier web service
    (e.g. Invideo AI, Dreamina) into the job, in place of calling a
    VideoProvider. Never scrapes or logs into anything itself - this is the
    ingestion half of the "human generates, pipeline takes it from there"
    workflow (item 3 of the free-PoC brief).

    license_commercial_clear defaults to False on purpose: every major
    free tier checked (Invideo, Dreamina, Kling, Pika, Luma) explicitly
    denies commercial use and leaves an unremovable watermark, so "unclear/
    no" is the safe default - the caller must affirmatively confirm
    clearance, never guess yes.

    Re-encodes the source to the job's 1080x1920/h264/30fps convention and
    trims it to the shot's declared duration, so it stream-copies cleanly
    against the other shots at compose time regardless of what resolution/
    codec/fps the source service exported.
    """
    job = load_job(job_id)
    shot = job.shot(shot_id)
    shots_dir = _job_output_dir(job_id) / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    out_path = shots_dir / f"{shot_id}.mp4"

    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", source_path,
            "-t", str(shot.duration_sec),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(out_path),
        ],
        check=True, capture_output=True, text=True,
    )

    shot.status = "success"
    shot.output_path = str(out_path)
    shot.assigned_provider = f"{HUMAN_FREE_TIER_PREFIX}{service}"
    shot.attempts += 1
    shot.error = None
    shot.license_commercial_clear = license_commercial_clear
    clearance = "commercial-use CLEAR" if license_commercial_clear else "commercial-use NOT confirmed"
    shot.qa_notes = f"human-generated via {service} free tier ({clearance}). {license_note}".strip()
    save_job(job)


def mark_shot_for_regen(job_id: str, shot_id: str) -> None:
    job = load_job(job_id)
    job.shot(shot_id).status = "needs_regen"
    job.status = "needs_regen"
    save_job(job)


def regen_shot(job_id: str, shot_id: str) -> None:
    job = load_job(job_id)
    generate_shots(job, only_shot_ids={shot_id})
    save_job(job)


def accept_shot(job_id: str, shot_id: str) -> None:
    """Record the human/QA verdict that a generated shot is usable. This is
    independent of status=="success" (which only means a file exists) - per
    the cost-PoC brief, "accepted" is what expected-accepted-shot-cost is
    computed against."""
    job = load_job(job_id)
    shot = job.shot(shot_id)
    shot.accepted = True
    shot.rejection_reason = None
    save_job(job)


def reject_shot(job_id: str, shot_id: str, reason: str) -> None:
    """Record a rejection and mark the shot for a single regen - never
    triggers regenerating the rest of the job."""
    job = load_job(job_id)
    shot = job.shot(shot_id)
    shot.accepted = False
    shot.rejection_reason = reason
    shot.status = "needs_regen"
    job.status = "needs_regen"
    save_job(job)


def compute_cost_rollup(job: ProductionJob) -> dict:
    """expected accepted-shot cost = generation cost x expected attempts.
    total_generation_cost_usd is what the job would have cost if every shot
    were accepted on the first try; total_regeneration_cost_usd is the extra
    spent on retries beyond that."""
    total_first = sum(s.first_attempt_cost_usd or 0.0 for s in job.shots)
    total_all = sum(s.total_cost_usd for s in job.shots)
    total_regen = round(total_all - total_first, 4)
    return {
        "total_generation_cost_usd": round(total_first, 4),
        "total_regeneration_cost_usd": total_regen,
        "total_render_cost_usd": round(total_all, 4),
        "cost_per_finished_video": round(total_all, 4),
    }
