"""TTSProvider abstraction: narration text -> a WAV/MP3 file plus a rough
per-segment timing so subtitles can be aligned to speech.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from production.providers.base import AvailabilityReport


@dataclass
class TTSSegment:
    text: str
    start_sec: float
    end_sec: float


@dataclass
class TTSResult:
    success: bool
    audio_path: str | None = None
    segments: list[TTSSegment] = field(default_factory=list)
    error: str | None = None


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def check_availability(self) -> AvailabilityReport: ...

    @abstractmethod
    def synthesize(self, segments_text: list[str], voice: str, language: str, out_path: Path) -> TTSResult:
        """Synthesize each text segment in order into one audio file at
        out_path, returning per-segment start/end times within that file."""
