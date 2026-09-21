#!/usr/bin/env python3
"""Team Claude: レンダリング済み動画の技術的品質チェック(ffprobeベース)。

TikTok投稿に必要な技術要件(縦型9:16、解像度、尺)を満たしているかを機械的に
確認する。内容面のコンプライアンスはcompliance_check.pyが別途担当する。
"""
from __future__ import annotations

import json
import subprocess
import sys

_MIN_DURATION = 12  # 台本仕様(15-30秒)に対し、若干の許容幅を持たせる
_MAX_DURATION = 35
_EXPECTED_WIDTH = 1080
_EXPECTED_HEIGHT = 1920


def probe(video_path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=width,height,codec_type",
        "-of", "json", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def check_video(video_path: str) -> dict:
    data = probe(video_path)
    duration = float(data["format"]["duration"])
    video_streams = [s for s in data["streams"] if s.get("codec_type") == "video"]
    has_audio = any(s.get("codec_type") == "audio" for s in data["streams"])

    width = video_streams[0]["width"] if video_streams else None
    height = video_streams[0]["height"] if video_streams else None

    checks = {
        "has_video_stream": bool(video_streams),
        "resolution_matches_9_16_vertical": (width, height) == (_EXPECTED_WIDTH, _EXPECTED_HEIGHT),
        "duration_within_range": _MIN_DURATION <= duration <= _MAX_DURATION,
        "has_audio_track": has_audio,
    }

    return {
        "video_path": video_path,
        "width": width,
        "height": height,
        "duration_seconds": round(duration, 2),
        "has_audio_track": has_audio,
        "checks": checks,
        "passed": all([checks["has_video_stream"], checks["resolution_matches_9_16_vertical"], checks["duration_within_range"]]),
    }


if __name__ == "__main__":
    path = sys.argv[1]
    report = check_video(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
