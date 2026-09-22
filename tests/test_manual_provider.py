from pathlib import Path

from production.providers.manual import ManualProvider
from production.schemas.models import ShotJob


def test_manual_provider_always_available():
    report = ManualProvider().check_availability()
    assert report.available is True


def test_manual_provider_generates_placeholder_clip(tmp_path):
    shot = ShotJob(
        shot_id="shot-00", order=0, duration_sec=2,
        video_prompt="a woman drinking coffee by a window",
        narration_text="hello",
    )
    result = ManualProvider().generate_shot(shot, tmp_path)
    assert result.success is True
    assert result.is_placeholder is True
    assert Path(result.output_path).exists()
    assert Path(result.output_path).stat().st_size > 0
