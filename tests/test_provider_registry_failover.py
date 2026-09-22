from pathlib import Path

from production.providers.base import AvailabilityReport, ShotResult, VideoProvider
from production.providers.registry import generate_with_failover
from production.schemas.models import ShotJob


class _AlwaysUnavailable(VideoProvider):
    name = "unavailable_stub"

    def check_availability(self):
        return AvailabilityReport(available=False, reason="no key configured")

    def generate_shot(self, shot, out_dir):
        raise AssertionError("must not be called when unavailable")


class _AlwaysSucceeds(VideoProvider):
    name = "succeeds_stub"

    def check_availability(self):
        return AvailabilityReport(available=True, reason="ok")

    def generate_shot(self, shot, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{shot.shot_id}.mp4"
        p.write_bytes(b"fake")
        return ShotResult(success=True, output_path=str(p), provider=self.name)


def test_failover_skips_unavailable_and_uses_next_provider(monkeypatch, tmp_path):
    import production.providers.registry as registry

    monkeypatch.setitem(registry._REGISTRY, "unavailable_stub", _AlwaysUnavailable())
    monkeypatch.setitem(registry._REGISTRY, "succeeds_stub", _AlwaysSucceeds())

    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=2, video_prompt="a cat", narration_text="hi")
    result = generate_with_failover(shot, tmp_path, ["unavailable_stub", "succeeds_stub"])

    assert result.success is True
    assert result.provider == "succeeds_stub"
