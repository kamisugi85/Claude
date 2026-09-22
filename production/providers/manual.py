"""Manual fallback VideoProvider.

Used only when no API-based provider is available (missing key, region
block, no budget approved yet). It never scrapes or automates a web UI -
that is explicitly out of scope (e.g. no Dreamina browser automation).

It produces a clearly labeled placeholder clip locally with ffmpeg so the
rest of the pipeline (TTS/subtitles/compositing/QA) is fully exercisable
today, and records that a human must manually produce the real shot
(e.g. by hand in a web tool's UI, or with an approved API once configured)
and drop it at `output_path` before the job can leave draft status.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from production.providers.base import AvailabilityReport, ShotResult, VideoProvider
from production.schemas.models import ShotJob

_PLACEHOLDER_COLORS = ["0x2B2D42", "0x8D99AE", "0xEF233C", "0x006D77"]


class ManualProvider(VideoProvider):
    name = "manual"

    def check_availability(self) -> AvailabilityReport:
        return AvailabilityReport(
            available=True,
            reason="Manual fallback is always available; it does not call any external API.",
            required_user_actions=[
                "Before publishing, replace each placeholder clip under "
                "output/<job_id>/shots/ with a real clip produced by hand or by an "
                "approved provider, keeping the same filename and duration."
            ],
        )

    def generate_shot(self, shot: ShotJob, out_dir: Path) -> ShotResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{shot.shot_id}.mp4"
        color = _PLACEHOLDER_COLORS[shot.order % len(_PLACEHOLDER_COLORS)]
        label = shot.video_prompt.replace("'", r"\'").replace(":", r"\:")[:70]
        drawtext = (
            f"drawtext=text='PLACEHOLDER shot {shot.order + 1}\\: {label}':"
            "fontcolor=white:fontsize=42:x=(w-text_w)/2:y=(h-text_h)/2:"
            "box=1:boxcolor=black@0.5:boxborderw=20"
        )
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=1080x1920:d={shot.duration_sec}:r=30",
            "-vf", drawtext,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            return ShotResult(success=False, error=e.stderr, provider=self.name)
        return ShotResult(
            success=True,
            output_path=str(out_path),
            provider=self.name,
            is_placeholder=True,
            cost_usd_estimate=0.0,
        )
