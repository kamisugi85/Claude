"""Covers the cost/time tracking added for the MiniMax paid-PoC phase:
per-attempt cost log, per-shot cost accumulation, accept/reject bookkeeping,
and the job-level cost rollup (generation vs regeneration cost)."""
import shutil

import pytest

from production.costlog import read_cost_log
from production.pipeline import (
    JOBS_DIR,
    OUTPUT_ROOT,
    accept_shot,
    compute_cost_rollup,
    generate_shots,
    load_job,
    reject_shot,
)


@pytest.fixture
def isolated_job(tmp_path, monkeypatch):
    import production.pipeline as pipeline_mod

    src = JOBS_DIR / "CLAUDE-D01.json"
    tmp_jobs_dir = tmp_path / "jobs"
    tmp_jobs_dir.mkdir()
    shutil.copy(src, tmp_jobs_dir / "CLAUDE-D01.json")
    monkeypatch.setattr(pipeline_mod, "JOBS_DIR", tmp_jobs_dir)
    monkeypatch.setattr(pipeline_mod, "OUTPUT_ROOT", tmp_path / "output")
    for var in ("ARK_API_KEY", "REPLICATE_API_TOKEN", "REPLICATE_VIDEO_MODEL", "MINIMAX_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield "CLAUDE-D01", tmp_path / "output"


def test_manual_provider_shots_cost_zero_and_are_logged(isolated_job):
    job_id, output_root = isolated_job
    job = load_job(job_id)
    generate_shots(job, only_shot_ids={"shot-00"})

    shot = job.shot("shot-00")
    assert shot.assigned_provider == "manual"
    assert shot.total_cost_usd == 0.0
    assert shot.first_attempt_cost_usd == 0.0
    assert shot.last_generation_time_sec is not None

    entries = read_cost_log(output_root, job_id)
    assert len(entries) == 1
    assert entries[0]["shot_id"] == "shot-00"
    assert entries[0]["generation_cost_usd"] == 0.0


def test_accept_and_reject_shot_bookkeeping(isolated_job):
    job_id, _ = isolated_job
    accept_shot(job_id, "shot-00")
    job = load_job(job_id)
    assert job.shot("shot-00").accepted is True

    reject_shot(job_id, "shot-00", "hand distortion in frame 40")
    job = load_job(job_id)
    shot = job.shot("shot-00")
    assert shot.accepted is False
    assert shot.rejection_reason == "hand distortion in frame 40"
    assert shot.status == "needs_regen"
    assert job.status == "needs_regen"


def test_reject_only_regenerates_the_rejected_shot(isolated_job):
    job_id, _ = isolated_job
    job = load_job(job_id)
    generate_shots(job)  # all 4 shots via manual provider
    from production.pipeline import save_job
    save_job(job)

    reject_shot(job_id, "shot-01", "background flicker")
    job = load_job(job_id)
    # only shot-01 should be needs_regen; others remain success
    assert job.shot("shot-01").status == "needs_regen"
    assert job.shot("shot-00").status == "success"
    assert job.shot("shot-02").status == "success"
    assert job.shot("shot-03").status == "success"


def test_compute_cost_rollup_separates_generation_from_regeneration(isolated_job):
    job_id, _ = isolated_job
    job = load_job(job_id)
    shot = job.shot("shot-00")
    shot.first_attempt_cost_usd = 0.48
    shot.total_cost_usd = 0.48 * 2  # simulate one regen at the same cost
    other = job.shot("shot-01")
    other.first_attempt_cost_usd = 0.56
    other.total_cost_usd = 0.56

    rollup = compute_cost_rollup(job)
    assert rollup["total_generation_cost_usd"] == round(0.48 + 0.56, 4)
    assert rollup["total_regeneration_cost_usd"] == round(0.48, 4)
    assert rollup["total_render_cost_usd"] == round(0.48 * 2 + 0.56, 4)
