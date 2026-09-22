"""Covers the free-PoC ingestion path: a human generates a shot by hand on
a free-tier service (Invideo, Dreamina, ...) and hands the file to
pipeline.ingest_shot, which must never assume commercial clearance."""
import json
import shutil
import subprocess

import pytest

from production.pipeline import (
    OUTPUT_ROOT,
    _job_path,
    ingest_shot,
    load_job,
    render,
)


@pytest.fixture
def isolated_job(tmp_path, monkeypatch):
    """Copy CLAUDE-D01's job file into a temp jobs dir so these tests never
    leave state behind in the real production/jobs/CLAUDE-D01.json."""
    import production.pipeline as pipeline_mod

    src = _job_path("CLAUDE-D01")
    tmp_jobs_dir = tmp_path / "jobs"
    tmp_jobs_dir.mkdir()
    shutil.copy(src, tmp_jobs_dir / "CLAUDE-D01.json")
    monkeypatch.setattr(pipeline_mod, "JOBS_DIR", tmp_jobs_dir)
    tmp_output = tmp_path / "output"
    monkeypatch.setattr(pipeline_mod, "OUTPUT_ROOT", tmp_output)
    for var in ("ARK_API_KEY", "REPLICATE_API_TOKEN", "REPLICATE_VIDEO_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield "CLAUDE-D01"


@pytest.fixture
def fake_clip(tmp_path):
    clip = tmp_path / "fake_source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=blue:s=1280x720:d=8:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, capture_output=True, text=True,
    )
    return clip


def test_ingest_shot_defaults_to_not_commercial_clear(isolated_job, fake_clip):
    ingest_shot(isolated_job, "shot-00", str(fake_clip), service="invideo")
    job = load_job(isolated_job)
    shot = job.shot("shot-00")
    assert shot.status == "success"
    assert shot.license_commercial_clear is False
    assert shot.assigned_provider == "human_free_tier:invideo"


def test_ingest_shot_normalizes_resolution_and_duration(isolated_job, fake_clip):
    ingest_shot(isolated_job, "shot-00", str(fake_clip), service="dreamina")
    job = load_job(isolated_job)
    shot = job.shot("shot-00")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", shot.output_path],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(probe.stdout)
    video = [s for s in data["streams"] if s["codec_type"] == "video"][0]
    assert (int(video["width"]), int(video["height"])) == (1080, 1920)
    assert float(data["format"]["duration"]) == pytest.approx(shot.duration_sec, abs=0.2)


def test_render_reports_free_tier_noncommercial_when_all_shots_ingested(isolated_job, fake_clip):
    job = load_job(isolated_job)
    for shot in job.shots:
        ingest_shot(isolated_job, shot.shot_id, str(fake_clip), service="invideo")

    mp4_path = render(isolated_job)
    assert mp4_path.exists()

    job = load_job(isolated_job)
    assert job.output.quality_tier == "free_tier_noncommercial_preview"
    assert any("commercial-use clearance is NOT confirmed" in n or "commercial-use clearance" in n
               for n in job.output.quality_notes)


def test_render_stays_placeholder_preview_if_any_shot_still_manual(isolated_job, fake_clip):
    job = load_job(isolated_job)
    ingest_shot(isolated_job, job.shots[0].shot_id, str(fake_clip), service="invideo")
    # remaining shots untouched -> ManualProvider fallback fires for them

    render(isolated_job)
    job = load_job(isolated_job)
    assert job.output.quality_tier == "placeholder_preview"


def test_ingest_shot_honors_explicit_commercial_clear_flag(isolated_job, fake_clip):
    ingest_shot(
        isolated_job, "shot-00", str(fake_clip), service="some_paid_tier",
        license_commercial_clear=True, license_note="Pro plan, commercial license confirmed in account dashboard",
    )
    job = load_job(isolated_job)
    assert job.shot("shot-00").license_commercial_clear is True
