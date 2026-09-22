"""Technical QA: verifies the rendered MP4 actually meets TikTok delivery
requirements (resolution, aspect ratio, duration bounds, has an audio track,
no zero-byte/corrupt output) via ffprobe.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from production.schemas.models import TechnicalQAConfig


@dataclass
class QAFinding:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class TechnicalQAReport:
    passed: bool
    findings: list[QAFinding] = field(default_factory=list)


def _ffprobe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def run_technical_qa(mp4_path: Path, config: TechnicalQAConfig) -> TechnicalQAReport:
    findings: list[QAFinding] = []

    if not mp4_path.exists() or mp4_path.stat().st_size == 0:
        return TechnicalQAReport(
            passed=False,
            findings=[QAFinding("file_exists", False, f"missing or empty: {mp4_path}")],
        )

    try:
        probe = _ffprobe(mp4_path)
    except subprocess.CalledProcessError as e:
        return TechnicalQAReport(
            passed=False,
            findings=[QAFinding("ffprobe_readable", False, f"ffprobe failed: {e.stderr}")],
        )
    findings.append(QAFinding("ffprobe_readable", True))

    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]

    if not video_streams:
        findings.append(QAFinding("has_video_stream", False, "no video stream found"))
    else:
        v = video_streams[0]
        want_w, want_h = (int(x) for x in config.min_resolution.split("x"))
        ok_res = int(v["width"]) == want_w and int(v["height"]) == want_h
        findings.append(QAFinding(
            "resolution", ok_res,
            f"got {v['width']}x{v['height']}, want {config.min_resolution}",
        ))
        ok_ratio = abs((int(v["width"]) / int(v["height"])) - (9 / 16)) < 0.01
        findings.append(QAFinding("aspect_ratio_9_16", ok_ratio, f"{v['width']}x{v['height']}"))

    duration = float(probe["format"]["duration"])
    ok_duration = config.min_duration_sec <= duration <= config.max_duration_sec
    findings.append(QAFinding(
        "duration_in_range", ok_duration,
        f"got {duration:.2f}s, want [{config.min_duration_sec}, {config.max_duration_sec}]",
    ))

    if config.require_audio_track:
        findings.append(QAFinding("has_audio_stream", bool(audio_streams)))

    passed = all(f.passed for f in findings)
    return TechnicalQAReport(passed=passed, findings=findings)
