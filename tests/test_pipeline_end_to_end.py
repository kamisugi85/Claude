"""Runs the full render() pipeline against CLAUDE-D01 using only the
zero-setup providers (ManualProvider for video, espeak-ng for TTS) and
asserts the resulting MP4 clears both QA gates. This is the test that
proves `python -m production.cli render CLAUDE-D01` works end to end with
no API keys configured."""
import os
import shutil

import pytest

from production.pipeline import JOBS_DIR, OUTPUT_ROOT, load_job, render


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch):
    for var in ("ARK_API_KEY", "REPLICATE_API_TOKEN", "REPLICATE_VIDEO_MODEL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def clean_output():
    job_dir = OUTPUT_ROOT / "CLAUDE-D01"
    if job_dir.exists():
        shutil.rmtree(job_dir)
    yield
    # leave the last render on disk for inspection; nothing to clean up


def test_render_claude_d01_end_to_end(clean_output):
    mp4_path = render("CLAUDE-D01")
    assert mp4_path.exists()
    assert mp4_path.stat().st_size > 0

    job = load_job("CLAUDE-D01")
    assert job.status == "ready"
    assert all(s.status == "success" for s in job.shots)
    assert all(s.assigned_provider == "manual" for s in job.shots)
