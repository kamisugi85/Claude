"""Builds an SRT file from TTS segment timings, plus optional CTA/PR overlay
cues. Subtitles/CTA/PR text are always composited here, in post, never
requested from the video-generation model.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from production.tts.base import TTSSegment


def _srt_timestamp(t: float) -> str:
    if t < 0:
        t = 0.0
    hours, rem = divmod(t, 3600)
    minutes, sec = divmod(rem, 60)
    whole_sec, frac = divmod(sec, 1)
    ms = round(frac * 1000)
    return f"{int(hours):02d}:{int(minutes):02d}:{int(whole_sec):02d},{ms:03d}"


@dataclass
class SubtitleCue:
    start_sec: float
    end_sec: float
    text: str


def build_cues(segments: list[TTSSegment], extra_cues: list[SubtitleCue] | None = None) -> list[SubtitleCue]:
    cues = [SubtitleCue(s.start_sec, s.end_sec, s.text) for s in segments]
    if extra_cues:
        cues.extend(extra_cues)
    return sorted(cues, key=lambda c: c.start_sec)


def write_srt(cues: list[SubtitleCue], out_path: Path) -> Path:
    lines = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(cue.start_sec)} --> {_srt_timestamp(cue.end_sec)}")
        lines.append(cue.text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
