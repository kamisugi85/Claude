"""Providers that need a paid account must never attempt a network call
when their required env vars are absent - this is what lets the pipeline
run safely with no keys configured."""
from production.providers.replicate_video import ReplicateVideoProvider
from production.providers.seedance import SeedanceProvider


def test_seedance_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    report = SeedanceProvider().check_availability()
    assert report.available is False
    assert report.required_user_actions


def test_seedance_generate_shot_short_circuits_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    from production.schemas.models import ShotJob
    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=5, video_prompt="a cat", narration_text="hi")
    result = SeedanceProvider().generate_shot(shot, tmp_path)
    assert result.success is False


def test_replicate_unavailable_without_token(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.delenv("REPLICATE_VIDEO_MODEL", raising=False)
    report = ReplicateVideoProvider().check_availability()
    assert report.available is False
    assert report.required_user_actions
