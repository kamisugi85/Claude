from pathlib import Path

from production.tts.espeak_provider import EspeakTTSProvider


def test_espeak_available_offline():
    report = EspeakTTSProvider().check_availability()
    assert report.available is True


def test_espeak_synthesizes_segments_with_timing(tmp_path):
    result = EspeakTTSProvider().synthesize(
        ["こんにちは", "世界"], voice="ja", language="ja", out_path=tmp_path / "narration.wav",
    )
    assert result.success is True
    assert Path(result.audio_path).exists()
    assert len(result.segments) == 2
    assert result.segments[0].start_sec == 0
    assert result.segments[1].start_sec == result.segments[0].end_sec
    assert result.segments[1].end_sec > result.segments[1].start_sec
