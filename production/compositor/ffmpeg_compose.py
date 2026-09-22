"""Assembles shot clips + TTS narration + burned-in subtitles + CTA/PR
overlay + optional BGM into a single 1080x1920 (9:16) MP4, using ffmpeg.

Pipeline stage order matches the shared Team GPT/Team Claude convention:
Creative JSON -> VideoProvider (shots) -> TTS -> Subtitles/Edit (this file)
-> QA -> MP4.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SUBTITLE_FONT = "IPAGothic"


@dataclass
class SFXCueInput:
    audio_path: Path
    at_sec: float
    volume_db: float = -10.0


@dataclass
class ComposeInputs:
    shot_paths: list[Path]
    narration_audio_path: Path
    srt_path: Path | None
    cta_text: str
    cta_start_sec: float
    pr_disclosure_text: str | None
    bgm_path: Path | None
    bgm_volume_db: float
    out_path: Path
    sfx_cues: list[SFXCueInput] | None = None
    subtitle_font: str = DEFAULT_SUBTITLE_FONT
    subtitle_font_size: int = 64


def _concat_shots(shot_paths: list[Path], work_dir: Path) -> Path:
    concat_list = work_dir / "shots_concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in shot_paths))
    concatenated = work_dir / "shots_concatenated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat_list), "-c", "copy", str(concatenated)],
        check=True, capture_output=True, text=True,
    )
    return concatenated


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


def compose(inputs: ComposeInputs, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    video_track = _concat_shots(inputs.shot_paths, work_dir)

    video_filters = []
    if inputs.srt_path is not None:
        srt_escaped = str(inputs.srt_path.resolve()).replace(":", r"\:")
        video_filters.append(
            f"subtitles='{srt_escaped}':force_style="
            f"'FontName={inputs.subtitle_font},FontSize={inputs.subtitle_font_size},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,"
            "Outline=2,Alignment=2,MarginV=140'"
        )
    cta_escaped = _escape_drawtext(inputs.cta_text)
    video_filters.append(
        f"drawtext=fontfile=/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf:"
        f"text='{cta_escaped}':fontcolor=white:fontsize=56:"
        "x=(w-text_w)/2:y=h-320:box=1:boxcolor=black@0.55:boxborderw=16:"
        f"enable='gte(t\\,{inputs.cta_start_sec})'"
    )
    if inputs.pr_disclosure_text:
        pr_escaped = _escape_drawtext(inputs.pr_disclosure_text)
        video_filters.append(
            f"drawtext=fontfile=/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf:"
            f"text='{pr_escaped}':fontcolor=yellow:fontsize=40:"
            "x=30:y=60:box=1:boxcolor=black@0.55:boxborderw=10"
        )

    audio_inputs: list[str] = ["-i", str(inputs.narration_audio_path)]
    audio_filter_parts = ["[1:a]volume=1.0[narr]"]
    mix_labels = ["[narr]"]
    next_input_idx = 2

    if inputs.bgm_path is not None:
        audio_inputs += ["-i", str(inputs.bgm_path)]
        audio_filter_parts.append(
            f"[{next_input_idx}:a]volume={inputs.bgm_volume_db}dB,aloop=loop=-1:size=2e9[bgm]"
        )
        mix_labels.append("[bgm]")
        next_input_idx += 1

    for i, cue in enumerate(inputs.sfx_cues or []):
        audio_inputs += ["-i", str(cue.audio_path)]
        delay_ms = max(0, round(cue.at_sec * 1000))
        audio_filter_parts.append(
            f"[{next_input_idx}:a]volume={cue.volume_db}dB,adelay={delay_ms}|{delay_ms}[sfx{i}]"
        )
        mix_labels.append(f"[sfx{i}]")
        next_input_idx += 1

    if len(mix_labels) > 1:
        audio_filter_parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2[mixed]"
        )
        audio_map = "[mixed]"
    else:
        audio_map = "[narr]"

    filter_complex_audio = ";".join(audio_filter_parts)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_track),
        *audio_inputs,
        "-filter_complex",
        f"[0:v]{','.join(video_filters)}[vout];{filter_complex_audio}",
        "-map", "[vout]", "-map", audio_map,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(inputs.out_path),
    ]
    inputs.out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return inputs.out_path
