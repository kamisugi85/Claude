import pytest
from pydantic import ValidationError

from production.schemas.models import ProductionJob, ShotJob


def test_claude_d01_job_validates():
    job = ProductionJob.load("production/jobs/CLAUDE-D01.json")
    assert job.job_id == "CLAUDE-D01"
    assert job.owner_team == "claude"
    assert job.creative.source == "team_claude_original"
    assert len(job.shots) == 4


def test_shot_job_rejects_model_rendered_text_prompt():
    with pytest.raises(ValidationError):
        ShotJob(
            shot_id="shot-00",
            order=0,
            duration_sec=5,
            video_prompt="Add Japanese subtitles at the bottom of the screen",
            narration_text="test",
        )


def test_shot_job_id_pattern_enforced():
    with pytest.raises(ValidationError):
        ShotJob(shot_id="bad-id", order=0, duration_sec=5, video_prompt="a woman drinking coffee")


def test_job_id_prefix_matches_owner_team_convention():
    job = ProductionJob.load("production/jobs/CLAUDE-D01.json")
    assert job.job_id.startswith("CLAUDE-")
