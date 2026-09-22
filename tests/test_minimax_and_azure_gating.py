"""New paid-phase candidates must never call out without credentials, same
guarantee as Seedance/Replicate."""
from production.providers.minimax_hailuo import MiniMaxHailuoProvider
from production.tts.azure_provider import AzureNeuralTTSProvider


def test_minimax_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    report = MiniMaxHailuoProvider().check_availability()
    assert report.available is False
    assert report.required_user_actions


def test_minimax_generate_shot_short_circuits_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    from production.schemas.models import ShotJob
    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=6, video_prompt="a cat", narration_text="hi")
    result = MiniMaxHailuoProvider().generate_shot(shot, tmp_path)
    assert result.success is False


def test_azure_tts_unavailable_without_key_or_region(monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    report = AzureNeuralTTSProvider().check_availability()
    assert report.available is False
    assert report.required_user_actions


def test_azure_tts_synthesize_short_circuits_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    result = AzureNeuralTTSProvider().synthesize(["hello"], voice="", language="ja", out_path=tmp_path / "out.wav")
    assert result.success is False
