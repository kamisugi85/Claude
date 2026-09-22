#!/usr/bin/env python3
"""Team Claude: TikTok PJ 制作方式比較PoC用レンダラー(既存render_video.pyは変更しない)。

既存render_video.py(a8_claude_scripts_9_provisional_20260922.json専用)は無変更のまま
残し、本モジュールはdata/state/a8_claude_scripts_tiktokpj_v2_provisional_20260922.json
の3クリエイティブ(人物なし情報系/AI人物会話系/AIらしさをフックにする実験系)専用の
レンダリングパイプラインを提供する。

描画プリミティブ(フォント・グラデーション・影・アイコン等)はrender_video.pyから
再利用し、二重実装しない。パイプライン本体(ナレーション合成・zoompan・xfade結合・
BGM・ミックス)はrender_video.pyのrender_video()と同等だが、フレームごとに異なる
描画関数・VOICEVOX話者IDを差し替えられるよう一般化している。

フォーマット③(AI人物会話)は、広告主の人物画像可否が未確定なため、実写・フォト
リアルAIアバターは一切生成せず、色分けされたモノグラム(頭文字)アイコン+吹き出し
UIのみで会話を表現する(安全側の設計判断)。
"""
from __future__ import annotations

import json
import os
import subprocess

from PIL import Image, ImageDraw

from render_video import (
    _CARD_COLOR,
    _RENDER_H,
    _RENDER_W,
    _SS,
    _TEXT_COLOR,
    _WIDTH,
    _HEIGHT,
    _PALETTES,
    _draw_gradient_bg,
    _draw_soft_shadow,
    _font,
    _lerp_color,
    _wrap_text,
    _ffprobe_duration,
    _VOICEVOX_SPEAKER_ID,
    synthesize_bgm,
    synthesize_narration,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(os.path.dirname(_HERE))
_SCRIPTS_PATH = os.path.join(_BASE_DIR, "data", "state", "a8_claude_scripts_tiktokpj_v2_provisional_20260922.json")
_OUTPUT_ROOT = os.path.join(_BASE_DIR, "data", "tiktok_production")

_MONOGRAM_COLORS = {"A": (74, 144, 217), "B": (219, 120, 87)}


def load_item(creative_id: str) -> dict:
    with open(_SCRIPTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for item in data["items"]:
        if item["creative_id"] == creative_id:
            return item
    raise ValueError(f"creative_id not found: {creative_id}")


def _base_frame(palette: dict) -> Image.Image:
    img = Image.new("RGB", (_RENDER_W, _RENDER_H), palette["bg_top"])
    draw = ImageDraw.Draw(img)
    _draw_gradient_bg(draw, palette["bg_top"], palette["bg_bottom"])
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([-_RENDER_W * 0.3, -_RENDER_H * 0.12, _RENDER_W * 0.55, _RENDER_H * 0.22], fill=(*palette["accent2"], 60))
    od.ellipse([_RENDER_W * 0.55, _RENDER_H * 0.75, _RENDER_W * 1.3, _RENDER_H * 1.05], fill=(*palette["accent"], 45))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def _draw_pr_badge(img: Image.Image, draw: ImageDraw.ImageDraw) -> None:
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


def render_dialogue_frame(line: dict, index: int, total: int, palette: dict, out_path: str) -> None:
    """吹き出しUI + モノグラムアイコン(顔・実写・フォトリアルAIアバターなし)。"""
    img = _base_frame(palette)
    draw = ImageDraw.Draw(img)
    _draw_pr_badge(img, draw)

    speaker = line.get("speaker", "A")
    is_cta = speaker == "CTA"
    text = line["text"]

    mono_color = _MONOGRAM_COLORS.get(speaker, palette["accent"])
    align_left = speaker == "A"

    font_size = 54 if len(text) <= 20 else 44
    font = _font("Black", font_size)
    max_text_width = int(_RENDER_W * 0.6)
    lines = _wrap_text(text, font, max_text_width, draw)
    line_height = int(font_size * _SS * 1.4)
    total_text_height = line_height * len(lines)

    card_pad_x, card_pad_y = 48 * _SS, 40 * _SS
    line_widths = [draw.textbbox((0, 0), ln, font=font)[2] for ln in lines]
    card_w = (max(line_widths) if line_widths else 300 * _SS) + card_pad_x * 2
    card_w = min(card_w, int(_RENDER_W * 0.72))
    card_h = total_text_height + card_pad_y * 2

    mono_r = 56 * _SS
    gap = 24 * _SS
    card_y0 = int(_RENDER_H * 0.42)

    if is_cta:
        card_x0 = (_RENDER_W - card_w) // 2
    elif align_left:
        card_x0 = 72 * _SS + mono_r * 2 + gap
    else:
        card_x0 = _RENDER_W - 72 * _SS - mono_r * 2 - gap - card_w

    card_box = (card_x0, card_y0, card_x0 + card_w, card_y0 + card_h)
    rgba_img = img.convert("RGBA") if img.mode != "RGBA" else img
    _draw_soft_shadow(rgba_img, card_box, radius=36 * _SS, blur=26 * _SS // 2, offset=(0, 12 * _SS), opacity=55)
    img = rgba_img.convert("RGB")
    draw = ImageDraw.Draw(img)

    bubble_fill = (34, 30, 26) if is_cta else _CARD_COLOR
    text_fill = (255, 255, 255) if is_cta else _TEXT_COLOR
    draw.rounded_rectangle(card_box, radius=36 * _SS, fill=bubble_fill)
    if not is_cta:
        draw.rounded_rectangle([card_box[0], card_box[1], card_box[2], card_box[1] + 8 * _SS], radius=6 * _SS, fill=mono_color)

    y = card_y0 + card_pad_y
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        w = bbox[2] - bbox[0]
        x = card_x0 + (card_w - w) // 2
        draw.text((x, y - bbox[1]), ln, font=font, fill=text_fill)
        y += line_height

    if not is_cta:
        mono_cx = card_x0 - gap - mono_r if align_left else card_x0 + card_w + gap + mono_r
        mono_cy = card_y0 + card_h // 2
        draw.ellipse([mono_cx - mono_r, mono_cy - mono_r, mono_cx + mono_r, mono_cy + mono_r], fill=mono_color)
        letter_font = _font("Black", 48)
        lb = draw.textbbox((0, 0), speaker, font=letter_font)
        draw.text((mono_cx - (lb[2] - lb[0]) / 2, mono_cy - (lb[3] - lb[1]) / 2 - lb[1]), speaker, font=letter_font, fill=(255, 255, 255))

    final_img = img.resize((_WIDTH, _HEIGHT), Image.LANCZOS)
    final_img.save(out_path)


def render_ai_reveal_frame(text: str, palette: dict, out_path: str) -> None:
    """AI制作であることを明示するターミナル風フレーム(Format②の冒頭カード用)。"""
    dark_bg = (24, 26, 30)
    img = Image.new("RGB", (_RENDER_W, _RENDER_H), dark_bg)
    draw = ImageDraw.Draw(img)

    win_margin = 64 * _SS
    win_y0 = int(_RENDER_H * 0.30)
    win_h = int(_RENDER_H * 0.30)
    win_box = (win_margin, win_y0, _RENDER_W - win_margin, win_y0 + win_h)
    rgba_img = img.convert("RGBA")
    _draw_soft_shadow(rgba_img, win_box, radius=28 * _SS, blur=30 * _SS // 2, offset=(0, 16 * _SS), opacity=90)
    img = rgba_img.convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(win_box, radius=28 * _SS, fill=(38, 40, 46))

    chrome_h = 44 * _SS
    draw.rounded_rectangle([win_box[0], win_box[1], win_box[2], win_box[1] + chrome_h], radius=28 * _SS, fill=(50, 52, 58))
    dot_colors = [(255, 95, 86), (255, 189, 46), (39, 201, 63)]
    for i, c in enumerate(dot_colors):
        cx = win_box[0] + (28 + i * 26) * _SS
        cy = win_box[1] + chrome_h // 2
        r = 8 * _SS
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)

    font = _font("Bold", 44)
    max_text_width = int((win_box[2] - win_box[0]) * 0.82)
    lines = _wrap_text(text, font, max_text_width, draw)
    line_height = int(44 * _SS * 1.5)
    total_h = line_height * len(lines)
    text_y0 = win_box[1] + chrome_h + (win_h - chrome_h - total_h) // 2
    y = text_y0
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        w = bbox[2] - bbox[0]
        x = win_box[0] + ((win_box[2] - win_box[0]) - w) // 2
        draw.text((x, y - bbox[1]), ln, font=font, fill=(120, 220, 160))
        y += line_height

    caption_font = _font("Bold", 30)
    caption = "TikTok PJ Team Claude — AI-generated script"
    cb = draw.textbbox((0, 0), caption, font=caption_font)
    draw.text(((_RENDER_W - (cb[2] - cb[0])) // 2, win_box[3] + 40 * _SS), caption, font=caption_font, fill=(150, 150, 158))

    final_img = img.resize((_WIDTH, _HEIGHT), Image.LANCZOS)
    final_img.save(out_path)


def _render_generic_video(creative_id: str, out_dir: str, lines: list, texts: list, voice_ids: list, frame_fn) -> dict:
    """lines: フレーム描画に渡す生データ(dict or str)。texts: ナレーション用テキスト(str)。
    voice_ids: 各行のVOICEVOX話者ID。frame_fn(line, i, total, palette, out_path)。"""
    item = load_item(creative_id)
    palette = _PALETTES.get(item["program_id"], _PALETTES["s00000001248025"])
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    fps = 30
    xfade_dur = 0.4
    hold_pad = 0.35

    narration_wavs = []
    clip_durations = []
    has_narration = True
    for i, (text, voice_id) in enumerate(zip(texts, voice_ids)):
        wav_path = os.path.join(out_dir, f"narration_{i:02d}.wav")
        if not synthesize_narration(text, wav_path, speaker=voice_id):
            has_narration = False
            break
        narration_wavs.append(wav_path)
        clip_durations.append(_ffprobe_duration(wav_path) + hold_pad)

    if not has_narration:
        per_caption = item["estimated_duration_seconds"] / len(lines)
        clip_durations = [per_caption] * len(lines)

    frame_paths = []
    for i, line in enumerate(lines):
        frame_path = os.path.join(frames_dir, f"frame_{i:02d}.png")
        frame_fn(line, i, len(lines), palette, frame_path)
        frame_paths.append(frame_path)

    seg_durs = [d + xfade_dur for d in clip_durations]
    clip_paths = []
    for i, (frame_path, seg_dur) in enumerate(zip(frame_paths, seg_durs)):
        clip_path = os.path.join(out_dir, f"clip_{i:02d}.mp4")
        zoom_frames = int(seg_dur * fps)
        zoompan = (
            f"scale=8000:-1,"
            f"zoompan=z='min(zoom+0.0007,1.15)':d={zoom_frames}:s={_WIDTH}x{_HEIGHT}:fps={fps}"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", frame_path, "-t", str(seg_dur), "-vf", zoompan,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", clip_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg (zoompan) failed on frame {i}:\n{result.stderr[-3000:]}")
        clip_paths.append(clip_path)

    merged_path = os.path.join(out_dir, "merged_silent.mp4")
    if len(clip_paths) == 1:
        merged_path = clip_paths[0]
    else:
        inputs = []
        for p in clip_paths:
            inputs += ["-i", p]
        filter_parts = []
        cur_label = "0:v"
        offset = seg_durs[0] - xfade_dur
        for i in range(1, len(clip_paths)):
            next_label = f"v{i}"
            filter_parts.append(f"[{cur_label}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[{next_label}]")
            cur_label = next_label
            offset += seg_durs[i] - xfade_dur
        filter_complex = ";".join(filter_parts)
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", f"[{cur_label}]",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", merged_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg (xfade merge) failed:\n{result.stderr[-3000:]}")

    total_video_duration = _ffprobe_duration(merged_path)

    bgm_path = os.path.join(out_dir, "bgm.wav")
    synthesize_bgm(bgm_path, total_video_duration)

    audio_path = os.path.join(out_dir, "final_audio.wav")
    if has_narration:
        narration_concat_list = os.path.join(out_dir, "narration_concat.txt")
        with open(narration_concat_list, "w", encoding="utf-8") as f:
            f.write("\n".join(f"file '{p}'" for p in narration_wavs))
        narration_full_path = os.path.join(out_dir, "narration_full.wav")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", narration_concat_list, "-c", "copy", narration_full_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg (narration concat) failed:\n{result.stderr[-3000:]}")

        cmd = ["ffmpeg", "-y", "-i", narration_full_path, "-i", bgm_path, "-filter_complex",
               "[1:a][0:a]sidechaincompress=threshold=0.04:ratio=10:attack=5:release=400[bgm_ducked];"
               "[0:a][bgm_ducked]amix=inputs=2:duration=first:weights=1 1,volume=1.6[aout]",
               "-map", "[aout]", audio_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg (narration+bgm mix) failed:\n{result.stderr[-3000:]}")
    else:
        audio_path = bgm_path

    video_path = os.path.join(out_dir, f"{creative_id}.mp4")
    cmd = ["ffmpeg", "-y", "-i", merged_path, "-i", audio_path, "-c:v", "libx264", "-c:a", "aac",
           "-pix_fmt", "yuv420p", "-shortest", video_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg (final mux) failed:\n{result.stderr[-3000:]}")

    for p in clip_paths:
        if p != merged_path and os.path.exists(p):
            os.remove(p)
    if os.path.exists(merged_path) and merged_path != video_path and len(clip_paths) > 1:
        os.remove(merged_path)

    return {
        "creative_id": creative_id,
        "video_path": video_path,
        "frame_count": len(lines),
        "duration_seconds": total_video_duration,
        "has_narration": has_narration,
        "has_bgm": True,
        "bgm_source": "self-synthesized (ffmpeg sine-wave pad, no external audio -- royalty-free CDNs are blocked by this sandbox's network policy)",
    }


def render_dialogue_video(creative_id: str = "s00000001248024-V2-DIALOGUE") -> dict:
    item = load_item(creative_id)
    dialogue_lines = item["dialogue_lines"]
    texts = [d["text"] for d in dialogue_lines]
    voice_ids = [d["voicevox_speaker_id"] for d in dialogue_lines]
    out_dir = os.path.join(_OUTPUT_ROOT, creative_id)
    result = _render_generic_video(creative_id, out_dir, dialogue_lines, texts, voice_ids, render_dialogue_frame)
    result["narration_voicevox_speakers"] = sorted({d["voicevox_speaker_name"] for d in dialogue_lines})
    result["narration_credit_required"] = [f"VOICEVOX:{n}" for n in result["narration_voicevox_speakers"]] if result["has_narration"] else None
    return result


def render_ai_hook_video(creative_id: str = "s00000026823003-V2-AIHOOK") -> dict:
    item = load_item(creative_id)
    captions = item["on_screen_captions"]
    from render_video import render_caption_frame

    def frame_fn(caption, i, total, palette, out_path):
        if i == 0:
            render_ai_reveal_frame(caption, palette, out_path)
        else:
            render_caption_frame(caption, i, total, palette, out_path)

    voice_ids = [_VOICEVOX_SPEAKER_ID] * len(captions)
    out_dir = os.path.join(_OUTPUT_ROOT, creative_id)
    result = _render_generic_video(creative_id, out_dir, captions, captions, voice_ids, frame_fn)
    result["narration_voicevox_speakers"] = ["青山龍星"] if result["has_narration"] else None
    result["narration_credit_required"] = "VOICEVOX:青山龍星" if result["has_narration"] else None
    return result


def render_person_free_video(creative_id: str = "s00000001248025-V2-A") -> dict:
    item = load_item(creative_id)
    captions = item["on_screen_captions"]
    from render_video import render_caption_frame

    voice_ids = [_VOICEVOX_SPEAKER_ID] * len(captions)
    out_dir = os.path.join(_OUTPUT_ROOT, creative_id)
    result = _render_generic_video(creative_id, out_dir, captions, captions, voice_ids, render_caption_frame)
    result["narration_voicevox_speakers"] = ["青山龍星"] if result["has_narration"] else None
    result["narration_credit_required"] = "VOICEVOX:青山龍星" if result["has_narration"] else None
    return result


_RENDERERS = {
    "person_free_info": render_person_free_video,
    "ai_avatar_dialogue": render_dialogue_video,
    "ai_meta_experiment": render_ai_hook_video,
}


def render_by_creative_id(creative_id: str) -> dict:
    item = load_item(creative_id)
    return _RENDERERS[item["format"]](creative_id)


if __name__ == "__main__":
    import sys

    with open(_SCRIPTS_PATH, "r", encoding="utf-8") as f:
        all_items = json.load(f)["items"]
    targets = [sys.argv[1]] if len(sys.argv) > 1 else [it["creative_id"] for it in all_items]
    for cid in targets:
        print(f"--- rendering {cid} ---")
        r = render_by_creative_id(cid)
        print(json.dumps(r, ensure_ascii=False, indent=2))
