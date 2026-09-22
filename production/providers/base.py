"""VideoProvider abstraction.

Every backend that can turn a ShotJob's video_prompt into a video clip
(a real generation API, a stock-footage API, or a human-in-the-loop manual
step) implements this interface. The pipeline never imports a concrete
provider by name outside of `production/providers/registry.py`, so swapping
or adding a vendor never touches schema, TTS, subtitle, compositing or QA
code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from production.schemas.models import ShotJob


@dataclass
class AvailabilityReport:
    available: bool
    reason: str
    required_user_actions: list[str] = field(default_factory=list)


@dataclass
class ShotResult:
    success: bool
    output_path: str | None = None
    error: str | None = None
    provider: str = ""
    is_placeholder: bool = False
    cost_usd_estimate: float = 0.0


class ProviderUnavailable(RuntimeError):
    """Raised when a provider is selected but cannot run (missing key, region, etc.)."""


class VideoProvider(ABC):
    """Abstract per-shot video generation backend."""

    name: str = "base"

    @abstractmethod
    def check_availability(self) -> AvailabilityReport:
        """Return whether this provider can run right now, with any missing
        prerequisites (API key env vars, account setup, billing) spelled out
        so a human knows exactly what to do next. Must never raise."""

    @abstractmethod
    def generate_shot(self, shot: ShotJob, out_dir: Path) -> ShotResult:
        """Generate (or otherwise obtain) the video clip for one shot and
        write it to out_dir. Must not raise for expected failure modes
        (missing key, quota, region block) - return ShotResult(success=False)
        instead so the pipeline can fail over to the next provider."""

    def estimate_cost_usd(self, shot: ShotJob) -> float:
        return 0.0
