#!/usr/bin/env python3
"""Team Claude: プロトタイプ動画レンダラー v2(無料・オープンソースのみ、商用利用制限なし)。

v1からの変更(ユーザーからの「AI作成感が強い/完成度が低い」というフィードバックを
受けた再設計):
- フォントをIPAGothic RegularからNoto Sans JP Black/Bold(OFL、商用利用可)へ変更
- 単色ベタ背景 -> グラデーション背景 + カラーカード
- 静止画の羅列 -> ffmpeg zoompanによる各カットの緩やかなズーム(Ken Burns風)
- カット -> クロスフェード遷移
- ①②③をUnicode文字のまま -> 塗りつぶし円+太字白数字のチップとして描画
- キーワードに応じた簡易アイコン(吹き出し/チェック/カレンダー/虫眼鏡)を追加
- PRバッジ・進捗インジケータのデザインを刷新
- VOICEVOX(ローカルエンジン、http://127.0.0.1:50021)によるナレーション合成に対応。
  エンジンが起動していない場合は無音トラックにフォールバックする(設計書通り任意)。

使用技術はPython(Pillow)+ffmpeg+VOICEVOX、いずれも無料・商用利用可。
外部有料AIサービス・APIキー・アカウント登録は不要。VOICEVOXはキャラクターごとの
クレジット表記(例:「VOICEVOX:青山龍星」)を条件に商用利用無料。
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_VOICEVOX_ENDPOINT = "http://127.0.0.1:50021"
_VOICEVOX_SPEAKER_ID = 13  # 青山龍星(ノーマル) -- 落ち着いた中立トーン、クレジット表記条件で商用利用無料
_VOICEVOX_SPEAKER_NAME = "青山龍星"

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(os.path.dirname(_HERE))
_SCRIPTS_PATH = os.path.join(_BASE_DIR, "data", "state", "a8_claude_scripts_9_provisional_20260922.json")
_OUTPUT_ROOT = os.path.join(_BASE_DIR, "data", "tiktok_production")
_FONT_PATH = os.path.join(_HERE, "assets", "fonts", "NotoSansJP-Variable.ttf")

_WIDTH, _HEIGHT = 1080, 1920
_SS = 2  # スーパーサンプリング倍率(アンチエイリアス品質向上のため高解像度で描画してから縮小)
_RENDER_W, _RENDER_H = _WIDTH * _SS, _HEIGHT * _SS

# プログラムごとのアクセントカラー(3案件で視覚的に区別できるように)
_PALETTES = {
    "s00000001248025": {"accent": (255, 122, 89), "accent2": (255, 179, 71), "bg_top": (255, 241, 232), "bg_bottom": (255, 224, 204)},
    "s00000001248024": {"accent": (74, 144, 217), "accent2": (108, 201, 197), "bg_top": (231, 243, 255), "bg_bottom": (214, 234, 250)},
    "s00000026823003": {"accent": (110, 168, 110), "accent2": (196, 168, 90), "bg_top": (236, 246, 232), "bg_bottom": (222, 238, 214)},
}
_TEXT_COLOR = (34, 30, 26)
_CARD_COLOR = (255, 255, 255)


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(_FONT_PATH, size * _SS)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def load_script(creative_id: str) -> dict:
    with open(_SCRIPTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for item in data["items"]:
        if item["creative_id"] == creative_id:
            return item
    raise ValueError(f"creative_id not found: {creative_id}")


def build_narration_text(item: dict) -> str:
    """narrationフィールドは『任意・断定表現は避ける』等の制作方針であり、逐語の
    読み上げ原稿ではないため、実際に画面に表示されるon_screen_captionsをそのまま
    読み上げ原稿として使う(台本で確定済みの文言以外を新たに作文しない)。"""
    return "。".join(c.rstrip("。") for c in item["on_screen_captions"]) + "。"


def synthesize_narration(text: str, out_path: str, speaker: int = _VOICEVOX_SPEAKER_ID) -> bool:
    """VOICEVOXのローカルエンジンAPIでナレーションを合成する。エンジンが起動して
    いない場合はFalseを返し、呼び出し側は無音トラックにフォールバックする。"""
    try:
        query_req = urllib.request.Request(
            f"{_VOICEVOX_ENDPOINT}/audio_query?" + urllib.parse.urlencode({"text": text, "speaker": speaker}),
            method="POST",
        )
        with urllib.request.urlopen(query_req, timeout=30) as resp:
            query = resp.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False

    synth_req = urllib.request.Request(
        f"{_VOICEVOX_ENDPOINT}/synthesis?" + urllib.parse.urlencode({"speaker": speaker}),
        data=query,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(synth_req, timeout=60) as resp:
        audio = resp.read()

    with open(out_path, "wb") as f:
        f.write(audio)
    return True


def _lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _draw_gradient_bg(draw: ImageDraw.ImageDraw, top, bottom):
    for y in range(_RENDER_H):
        t = y / _RENDER_H
        draw.line([(0, y), (_RENDER_W, y)], fill=_lerp_color(top, bottom, t))


def _draw_soft_shadow(base_img: Image.Image, box, radius, blur=18, offset=(0, 10), opacity=70):
    shadow = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    x0, y0, x1, y1 = box
    sd.rounded_rectangle(
        [x0 + offset[0], y0 + offset[1], x1 + offset[0], y1 + offset[1]],
        radius=radius, fill=(0, 0, 0, opacity),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    base_img.alpha_composite(shadow)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list:
    lines = []
    for raw_line in text.split("\n"):
        current = ""
        for ch in raw_line:
            trial = current + ch
            bbox = draw.textbbox((0, 0), trial, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = trial
        lines.append(current)
    return lines


def _draw_text_with_stroke(draw, xy, text, font, fill, stroke_fill, stroke_width):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


_ICON_KEYWORDS = {
    "speech": ["本音", "悩み", "きつい", "相談", "声"],
    "check": ["①", "②", "③", "無料相談", "チェック", "登録"],
    "calendar": ["準備", "持ち帰り", "夜勤"],
    "search": ["知って", "比べ", "情報", "確認", "選択肢"],
}


def _pick_icon(caption: str) -> str:
    for icon, keywords in _ICON_KEYWORDS.items():
        if any(k in caption for k in keywords):
            return icon
    return "speech"


def _draw_icon(img: Image.Image, kind: str, center, size, color):
    draw = ImageDraw.Draw(img)
    cx, cy = center
    r = size // 2
    if kind == "speech":
        draw.rounded_rectangle([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7], radius=int(r * 0.35), fill=color)
        draw.polygon([(cx - r * 0.2, cy + r * 0.6), (cx + r * 0.2, cy + r * 0.6), (cx - r * 0.1, cy + r * 1.05)], fill=color)
    elif kind == "check":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        draw.line(
            [(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.4), (cx + r * 0.5, cy - r * 0.4)],
            fill=(255, 255, 255), width=max(4, int(r * 0.16)), joint="curve",
        )
    elif kind == "calendar":
        draw.rounded_rectangle([cx - r, cy - r * 0.8, cx + r, cy + r * 0.9], radius=int(r * 0.2), fill=color)
        draw.rectangle([cx - r, cy - r * 0.8, cx + r, cy - r * 0.4], fill=_lerp_color(color, (0, 0, 0), 0.15))
    elif kind == "search":
        draw.ellipse([cx - r * 0.7, cy - r * 0.9, cx + r * 0.5, cy + r * 0.3], outline=color, width=max(6, int(r * 0.2)))
        draw.line([(cx + r * 0.25, cy + r * 0.1), (cx + r * 0.75, cy + r * 0.6)], fill=color, width=max(6, int(r * 0.22)))


def render_caption_frame(caption: str, index: int, total: int, palette: dict, out_path: str) -> None:
    img = Image.new("RGB", (_RENDER_W, _RENDER_H), palette["bg_top"])
    draw = ImageDraw.Draw(img)
    _draw_gradient_bg(draw, palette["bg_top"], palette["bg_bottom"])

    # 上部の柔らかい円形アクセント(単調な平面感を避けるための装飾)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([-_RENDER_W * 0.3, -_RENDER_H * 0.12, _RENDER_W * 0.55, _RENDER_H * 0.22], fill=(*palette["accent2"], 60))
    od.ellipse([_RENDER_W * 0.55, _RENDER_H * 0.75, _RENDER_W * 1.3, _RENDER_H * 1.05], fill=(*palette["accent"], 45))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)

    # 進捗バー(上部、ドットではなく細いプログレスバー)
    bar_margin = 64 * _SS
    bar_y = 56 * _SS
    bar_h = 10 * _SS
    bar_w = _RENDER_W - bar_margin * 2
    draw.rounded_rectangle([bar_margin, bar_y, bar_margin + bar_w, bar_y + bar_h], radius=bar_h // 2, fill=(255, 255, 255, 150))
    progress_w = int(bar_w * (index + 1) / total)
    draw.rounded_rectangle([bar_margin, bar_y, bar_margin + max(progress_w, bar_h), bar_y + bar_h], radius=bar_h // 2, fill=palette["accent"])

    # PRバッジ(右上、控えめだが明確)
    pr_font = _font("Bold", 26)
    badge_pad_x, badge_pad_y = 22 * _SS, 12 * _SS
    bbox = draw.textbbox((0, 0), "PR", font=pr_font)
    badge_w = (bbox[2] - bbox[0]) + badge_pad_x * 2
    badge_h = (bbox[3] - bbox[1]) + badge_pad_y * 2
    badge_x1 = _RENDER_W - 56 * _SS
    badge_x0 = badge_x1 - badge_w
    badge_y0 = 104 * _SS
    draw.rounded_rectangle([badge_x0, badge_y0, badge_x1, badge_y0 + badge_h], radius=badge_h // 2, fill=(34, 30, 26, 210))
    draw.text((badge_x0 + badge_pad_x, badge_y0 + badge_pad_y - bbox[1]), "PR", font=pr_font, fill=(255, 255, 255))

    # 数字チップ(①②③が文頭にあれば、塗りつぶし円+太字数字として描画)
    m = re.match(r"^([①②③])(.*)$", caption)
    number_map = {"①": "1", "②": "2", "③": "3"}
    chip_h = 0
    body_caption = caption
    if m:
        num_char, rest = m.group(1), m.group(2)
        body_caption = rest.strip()
        chip_r = 44 * _SS
        chip_cx = _RENDER_W // 2
        chip_cy = int(_RENDER_H * 0.36)
        draw.ellipse([chip_cx - chip_r, chip_cy - chip_r, chip_cx + chip_r, chip_cy + chip_r], fill=palette["accent"])
        num_font = _font("Black", 52)
        nb = draw.textbbox((0, 0), number_map[num_char], font=num_font)
        draw.text((chip_cx - (nb[2] - nb[0]) / 2, chip_cy - (nb[3] - nb[1]) / 2 - nb[1]), number_map[num_char], font=num_font, fill=(255, 255, 255))
        chip_h = chip_r * 2

    # アイコン(数字チップが無いカードのみ、キーワード連動の簡易アイコン)
    icon_h = 0
    if not m:
        icon_kind = _pick_icon(caption)
        icon_cy = int(_RENDER_H * 0.32)
        _draw_icon(img, icon_kind, (_RENDER_W // 2, icon_cy), 110 * _SS, palette["accent"])
        icon_h = 130 * _SS
        draw = ImageDraw.Draw(img)

    # メインテロップ(カード上に配置し、可読性を確保)
    font_size = 66 if len(body_caption) <= 18 else 52
    font = _font("Black", font_size)
    max_text_width = int(_RENDER_W * 0.78)
    lines = _wrap_text(body_caption, font, max_text_width, draw)
    line_height = int(font_size * _SS * 1.45)
    total_text_height = line_height * len(lines)

    card_pad_x, card_pad_y = 56 * _SS, 56 * _SS
    line_widths = [draw.textbbox((0, 0), ln, font=font)[2] for ln in lines]
    card_w = max(line_widths) + card_pad_x * 2 if line_widths else 400 * _SS
    card_w = min(card_w, _RENDER_W - 100 * _SS)
    card_h = total_text_height + card_pad_y * 2
    card_x0 = (_RENDER_W - card_w) // 2
    card_y0 = int(_RENDER_H * 0.46) + max(chip_h, icon_h) // 2
    card_box = (card_x0, card_y0, card_x0 + card_w, card_y0 + card_h)

    rgba_img = img.convert("RGBA")
    _draw_soft_shadow(rgba_img, card_box, radius=40 * _SS, blur=30 * _SS // 2, offset=(0, 14 * _SS), opacity=55)
    img = rgba_img.convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(card_box, radius=40 * _SS, fill=_CARD_COLOR)
    draw.rounded_rectangle([card_box[0], card_box[1], card_box[2], card_box[1] + 10 * _SS], radius=6 * _SS, fill=palette["accent"])

    y = card_y0 + card_pad_y
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (_WIDTH * _SS - w) // 2
        draw.text((x, y - bbox[1]), line, font=font, fill=_TEXT_COLOR)
        y += line_height

    # 縮小(スーパーサンプリングのアンチエイリアス適用)
    final_img = img.resize((_WIDTH, _HEIGHT), Image.LANCZOS)
    final_img.save(out_path)


def render_video(creative_id: str) -> dict:
    item = load_script(creative_id)
    captions = item["on_screen_captions"]
    scripted_duration = item["estimated_duration_seconds"]
    palette = _PALETTES.get(item["program_id"], _PALETTES["s00000001248025"])

    out_dir = os.path.join(_OUTPUT_ROOT, creative_id)
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # ナレーション合成(VOICEVOXエンジンが起動していれば)。台本のcaptionsをそのまま
    # 読み上げ原稿として使う(新たな文言は作らない)。エンジン未起動時は無音にフォールバック。
    narration_path = os.path.join(out_dir, "narration.wav")
    narration_text = build_narration_text(item)
    has_narration = synthesize_narration(narration_text, narration_path)
    narration_duration = None
    if has_narration:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", narration_path],
            capture_output=True, text=True,
        )
        narration_duration = float(probe.stdout.strip())

    # ナレーションが合成できた場合は実際の音声尺に映像を合わせる(台本のestimated_
    # duration_secondsはナレーション未確定の時点での見積もりのため優先しない)。
    duration = (narration_duration + 0.8) if narration_duration else scripted_duration
    per_caption = duration / len(captions)

    frame_paths = []
    for i, caption in enumerate(captions):
        frame_path = os.path.join(frames_dir, f"frame_{i:02d}.png")
        render_caption_frame(caption, i, len(captions), palette, frame_path)
        frame_paths.append(frame_path)

    # 各静止画にゆっくりしたズーム(Ken Burns)を掛けてから、クロスフェードで繋ぐ。
    fps = 30
    xfade_dur = 0.5
    seg_dur = per_caption + xfade_dur  # クロスフェード分だけ各セグメントを長めに用意する
    clip_paths = []
    for i, frame_path in enumerate(frame_paths):
        clip_path = os.path.join(out_dir, f"clip_{i:02d}.mp4")
        zoom_frames = int(seg_dur * fps)
        zoompan = (
            f"scale=8000:-1,"
            f"zoompan=z='min(zoom+0.0007,1.15)':d={zoom_frames}:s={_WIDTH}x{_HEIGHT}:fps={fps}"
        )
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", frame_path,
            "-t", str(seg_dur),
            "-vf", zoompan,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            clip_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg (zoompan) failed on frame {i}:\n{result.stderr[-3000:]}")
        clip_paths.append(clip_path)

    # クロスフェードで結合(filter_complexのxfadeを逐次適用)
    merged_path = os.path.join(out_dir, "merged_silent.mp4")
    if len(clip_paths) == 1:
        merged_path = clip_paths[0]
    else:
        inputs = []
        for p in clip_paths:
            inputs += ["-i", p]
        filter_parts = []
        cur_label = "0:v"
        offset = seg_dur - xfade_dur
        for i in range(1, len(clip_paths)):
            next_label = f"v{i}"
            filter_parts.append(
                f"[{cur_label}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[{next_label}]"
            )
            cur_label = next_label
            offset += seg_dur - xfade_dur
        filter_complex = ";".join(filter_parts)
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[{cur_label}]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            merged_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg (xfade merge) failed:\n{result.stderr[-3000:]}")

    # 音声トラックを付加して最終出力(ナレーションが合成できていればそれを使用、
    # できなければ無音トラックにフォールバック)。
    video_path = os.path.join(out_dir, f"{creative_id}.mp4")
    if has_narration:
        cmd = [
            "ffmpeg", "-y",
            "-i", merged_path,
            "-i", narration_path,
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-shortest",
            video_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", merged_path,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-shortest",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            video_path,
        ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg (final mux) failed:\n{result.stderr[-3000:]}")

    # 中間ファイル整理
    for p in clip_paths:
        if p != merged_path and os.path.exists(p):
            os.remove(p)
    if os.path.exists(merged_path) and merged_path != video_path and len(clip_paths) > 1:
        os.remove(merged_path)

    return {
        "creative_id": creative_id,
        "video_path": video_path,
        "frames_dir": frames_dir,
        "frame_count": len(captions),
        "duration_seconds": duration,
        "has_narration": has_narration,
        "narration_voicevox_speaker": _VOICEVOX_SPEAKER_NAME if has_narration else None,
        "narration_credit_required": f"VOICEVOX:{_VOICEVOX_SPEAKER_NAME}" if has_narration else None,
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "s00000001248025-A"
    result = render_video(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
