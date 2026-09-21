#!/usr/bin/env python3
"""Team Claude: プロトタイプ動画レンダラー(無料・オープンソースのみ、商用利用制限なし)。

9台本(a8_claude_scripts_9_provisional_20260922.json)のうち1本を受け取り、
on_screen_captionsをテロップカードとして描画し、ffmpegで縦型(1080x1920)動画に
組み立てる。使用技術はPython(Pillow)+ffmpegのみ -- どちらもOSS/自己ホストで、
外部AIサービス・APIキー・アカウント登録は一切不要。ナレーション・BGMは
このプロトタイプには含めない(台本上も任意のため)。

出力はローカル保存のみ。TikTokへの投稿・外部公開は行わない。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(os.path.dirname(_HERE))
_SCRIPTS_PATH = os.path.join(_BASE_DIR, "data", "state", "a8_claude_scripts_9_provisional_20260922.json")
_OUTPUT_ROOT = os.path.join(_BASE_DIR, "data", "tiktok_production")
_FONT_PATH = "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"

_WIDTH, _HEIGHT = 1080, 1920
_BG_COLOR = (250, 247, 240)
_ACCENT_COLOR = (255, 138, 101)
_TEXT_COLOR = (40, 40, 40)
_PR_BADGE_BG = (40, 40, 40)
_PR_BADGE_TEXT = (255, 255, 255)


def load_script(creative_id: str) -> dict:
    with open(_SCRIPTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for item in data["items"]:
        if item["creative_id"] == creative_id:
            return item
    raise ValueError(f"creative_id not found: {creative_id}")


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list:
    lines = []
    for raw_line in text.split("\n"):
        words = list(raw_line)  # 日本語は文字単位で折り返す
        current = ""
        for ch in words:
            trial = current + ch
            bbox = draw.textbbox((0, 0), trial, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = trial
        lines.append(current)
    return lines


def render_caption_frame(caption: str, index: int, total: int, out_path: str) -> None:
    img = Image.new("RGB", (_WIDTH, _HEIGHT), _BG_COLOR)
    draw = ImageDraw.Draw(img)

    # 上部アクセントバー
    draw.rectangle([0, 0, _WIDTH, 24], fill=_ACCENT_COLOR)

    # PRバッジ(左上、常時表示 -- required_PR_disclosureを画面上で満たす)
    badge_w, badge_h = 140, 64
    draw.rounded_rectangle([40, 60, 40 + badge_w, 60 + badge_h], radius=12, fill=_PR_BADGE_BG)
    pr_font = ImageFont.truetype(_FONT_PATH, 36)
    draw.text((40 + 30, 60 + 12), "PR", font=pr_font, fill=_PR_BADGE_TEXT)

    # 進捗インジケータ(下部ドット)
    dot_r = 8
    gap = 28
    total_w = (total - 1) * gap
    start_x = (_WIDTH - total_w) // 2
    for i in range(total):
        cx = start_x + i * gap
        color = _ACCENT_COLOR if i == index else (210, 205, 195)
        draw.ellipse([cx - dot_r, _HEIGHT - 120 - dot_r, cx + dot_r, _HEIGHT - 120 + dot_r], fill=color)

    # メインテロップ
    font_size = 82 if len(caption) <= 20 else 64
    font = ImageFont.truetype(_FONT_PATH, font_size)
    max_text_width = _WIDTH - 160
    lines = _wrap_text(caption, font, max_text_width, draw)
    line_height = int(font_size * 1.5)
    total_text_height = line_height * len(lines)
    y = (_HEIGHT - total_text_height) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (_WIDTH - w) // 2
        draw.text((x, y), line, font=font, fill=_TEXT_COLOR)
        y += line_height

    img.save(out_path)


def render_video(creative_id: str) -> dict:
    item = load_script(creative_id)
    captions = item["on_screen_captions"]
    duration = item["estimated_duration_seconds"]
    per_caption = duration / len(captions)

    out_dir = os.path.join(_OUTPUT_ROOT, creative_id)
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    concat_lines = []
    for i, caption in enumerate(captions):
        frame_path = os.path.join(frames_dir, f"frame_{i:02d}.png")
        render_caption_frame(caption, i, len(captions), frame_path)
        concat_lines.append(f"file '{frame_path}'")
        concat_lines.append(f"duration {per_caption:.3f}")
    # concat demuxerの仕様上、最後のファイルをもう一度重複指定する必要がある
    concat_lines.append(f"file '{os.path.join(frames_dir, f'frame_{len(captions) - 1:02d}.png')}'")

    concat_list_path = os.path.join(out_dir, "concat_list.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        f.write("\n".join(concat_lines))

    video_path = os.path.join(out_dir, f"{creative_id}.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264", "-c:a", "aac",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-3000:]}")

    return {
        "creative_id": creative_id,
        "video_path": video_path,
        "frames_dir": frames_dir,
        "frame_count": len(captions),
        "duration_seconds": duration,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "s00000001248025-A"
    result = render_video(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
