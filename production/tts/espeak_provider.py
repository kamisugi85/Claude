"""Offline TTS via espeak-ng. No API key, no network, no login, no billing -
this is the zero-setup default so the pipeline is runnable end-to-end today.
Voice quality is robotic; treat it as a stand-in until a cloud TTS provider
(e.g. one already paid for elsewhere) is wired in behind the same interface.
"""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

from production.providers.base import AvailabilityReport
from production.tts.base import TTSProvider, TTSResult, TTSSegment

_LANGUAGE_TO_ESPEAK_VOICE = {
    "ja": "ja", "ja-JP": "ja",
    "en": "en-us", "en-US": "en-us",
}


class EspeakTTSProvider(TTSProvider):
    name = "espeak_local"

    def check_availability(self) -> AvailabilityReport:
        if shutil.which("espeak-ng") is None:
            return AvailabilityReport(
                available=False,
                reason="espeak-ng binary not found on PATH.",
                required_user_actions=["Install espeak-ng (e.g. `apt-get install espeak-ng`)."],
            )
        return AvailabilityReport(available=True, reason="espeak-ng found on PATH; fully offline.")

    def _wav_duration_sec(self, path: Path) -> float:
        with wave.open(str(path), "rb") as f:
            return f.getnframes() / float(f.getframerate())

    def synthesize(self, segments_text: list[str], voice: str, language: str, out_path: Path) -> TTSResult:
        avail = self.check_availability()
        if not avail.available:
            return TTSResult(success=False, error=avail.reason)

        espeak_voice = _LANGUAGE_TO_ESPEAK_VOICE.get(language, _LANGUAGE_TO_ESPEAK_VOICE.get(voice, "en-us"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = out_path.parent / "_tts_segments"
        tmp_dir.mkdir(exist_ok=True)

        segments: list[TTSSegment] = []
        seg_paths: list[Path] = []
        cursor = 0.0
        try:
            for i, text in enumerate(segments_text):
                seg_wav = tmp_dir / f"seg_{i:02d}.wav"
                subprocess.run(
                    ["espeak-ng", "-v", espeak_voice, "-s", "150", "-w", str(seg_wav), text],
                    check=True, capture_output=True, text=True,
                )
                dur = self._wav_duration_sec(seg_wav)
                segments.append(TTSSegment(text=text, start_sec=cursor, end_sec=cursor + dur))
                cursor += dur
                seg_paths.append(seg_wav)

            concat_list = tmp_dir / "concat.txt"
            concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in seg_paths))
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(concat_list), "-ar", "44100", "-ac", "1", str(out_path)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            return TTSResult(success=False, error=e.stderr)

        return TTSResult(success=True, audio_path=str(out_path), segments=segments)
