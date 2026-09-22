import subprocess
from pathlib import Path

from production.qa.technical import run_technical_qa
from production.schemas.models import TechnicalQAConfig


def _make_clip(path: Path, width: int, height: int, duration: float, with_audio: bool):
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d={duration}:r=30",
    ]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={duration}", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def test_technical_qa_passes_correct_clip(tmp_path):
    clip = tmp_path / "ok.mp4"
    _make_clip(clip, 1080, 1920, 6, with_audio=True)
    report = run_technical_qa(clip, TechnicalQAConfig())
    assert report.passed, report.findings


def test_technical_qa_fails_wrong_resolution(tmp_path):
    clip = tmp_path / "wrong_res.mp4"
    _make_clip(clip, 1920, 1080, 6, with_audio=True)
    report = run_technical_qa(clip, TechnicalQAConfig())
    assert not report.passed
    failing = {f.check for f in report.findings if not f.passed}
    assert "resolution" in failing


def test_technical_qa_fails_missing_audio(tmp_path):
    clip = tmp_path / "no_audio.mp4"
    _make_clip(clip, 1080, 1920, 6, with_audio=False)
    report = run_technical_qa(clip, TechnicalQAConfig())
    assert not report.passed
    failing = {f.check for f in report.findings if not f.passed}
    assert "has_audio_stream" in failing


def test_technical_qa_fails_missing_file(tmp_path):
    report = run_technical_qa(tmp_path / "does_not_exist.mp4", TechnicalQAConfig())
    assert not report.passed
