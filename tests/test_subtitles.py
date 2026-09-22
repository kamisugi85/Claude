from pathlib import Path

from production.subtitles.generator import SubtitleCue, _srt_timestamp, build_cues, write_srt
from production.tts.base import TTSSegment


def test_srt_timestamp_formatting():
    assert _srt_timestamp(0) == "00:00:00,000"
    assert _srt_timestamp(65.5) == "00:01:05,500"


def test_build_cues_sorted_by_start():
    segments = [TTSSegment("b", 5, 8), TTSSegment("a", 0, 4)]
    cues = build_cues(segments)
    assert [c.text for c in cues] == ["a", "b"]


def test_write_srt_roundtrip(tmp_path):
    cues = [SubtitleCue(0, 2, "こんにちは"), SubtitleCue(2, 4, "世界")]
    out = write_srt(cues, tmp_path / "out.srt")
    text = out.read_text(encoding="utf-8")
    assert "こんにちは" in text
    assert "00:00:00,000 --> 00:00:02,000" in text
