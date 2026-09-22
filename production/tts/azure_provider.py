"""Azure AI Speech (Cognitive Services) Neural TTS - recommended replacement
for espeak-ng once the paid phase starts. Natural-sounding Japanese neural
voices (e.g. ja-JP-NanamiNeural), official commercial-use terms under the
standard Azure Cognitive Services agreement, ~$16 per 1M characters (500K
free per month) - for a script the size of CLAUDE-D01's narration
(~100-150 Japanese characters), this is negligible (<$0.01/video).

STATUS: implemented against Azure's long-stable public REST TTS endpoint
(unlike Seedance/MiniMax, this API has been documented and unchanged in
shape for years, so confidence here is high without needing the same
"unverified" caveats) - but NOT live-tested: no AZURE_SPEECH_KEY has been
issued and no account/billing exists yet.

Endpoint and auth:
  POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1
  Headers: Ocp-Apim-Subscription-Key: <key>
           Content-Type: application/ssml+xml
           X-Microsoft-OutputFormat: riff-24khz-16bit-mono-pcm
  Body: SSML XML specifying voice name and text.
  Response: raw audio bytes (wav) on 200 OK.

This client synthesizes one SSML request per narration segment (so
per-segment timing can be measured the same way EspeakTTSProvider does),
then concatenates with ffmpeg exactly like the offline provider - same
downstream contract, different backend.
"""
from __future__ import annotations

import os
import subprocess
import wave
from pathlib import Path
from xml.sax.saxutils import escape

import requests

from production.providers.base import AvailabilityReport
from production.tts.base import TTSProvider, TTSResult, TTSSegment

API_KEY_ENV = "AZURE_SPEECH_KEY"
REGION_ENV = "AZURE_SPEECH_REGION"
VOICE_ENV = "AZURE_SPEECH_VOICE"
DEFAULT_VOICE = "ja-JP-NanamiNeural"

_LANGUAGE_TO_LOCALE = {"ja": "ja-JP", "ja-JP": "ja-JP", "en": "en-US", "en-US": "en-US"}


class AzureNeuralTTSProvider(TTSProvider):
    name = "azure_neural"

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def _api_key(self) -> str | None:
        return os.environ.get(API_KEY_ENV)

    def _region(self) -> str | None:
        return os.environ.get(REGION_ENV)

    def _voice(self, voice_hint: str) -> str:
        return os.environ.get(VOICE_ENV) or (voice_hint if voice_hint else DEFAULT_VOICE)

    def check_availability(self) -> AvailabilityReport:
        missing = []
        if not self._api_key():
            missing.append(f"{API_KEY_ENV} (Azure Cognitive Services Speech resource key)")
        if not self._region():
            missing.append(f"{REGION_ENV} (e.g. 'japaneast', 'eastus' - must match your resource's region)")
        if missing:
            return AvailabilityReport(
                available=False,
                reason="Azure Neural TTS is not configured.",
                required_user_actions=[
                    "Step 1: Create an Azure account and an 'Speech' (Cognitive "
                    "Services) resource in the Azure Portal (a free tier with "
                    "500K characters/month exists).",
                    "Step 2: Note the resource's key and region.",
                    f"Step 3: Set {API_KEY_ENV} and {REGION_ENV} in the environment "
                    f"(optionally {VOICE_ENV}, default {DEFAULT_VOICE}).",
                    f"(missing now: {'; '.join(missing)})",
                ],
            )
        return AvailabilityReport(available=True, reason=f"Configured for region {self._region()}.")

    def _wav_duration_sec(self, path: Path) -> float:
        with wave.open(str(path), "rb") as f:
            return f.getnframes() / float(f.getframerate())

    def synthesize(self, segments_text: list[str], voice: str, language: str, out_path: Path) -> TTSResult:
        avail = self.check_availability()
        if not avail.available:
            return TTSResult(success=False, error=avail.reason)

        locale = _LANGUAGE_TO_LOCALE.get(language, "ja-JP")
        voice_name = self._voice(voice)
        endpoint = f"https://{self._region()}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key(),
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = out_path.parent / "_tts_segments"
        tmp_dir.mkdir(exist_ok=True)

        segments: list[TTSSegment] = []
        seg_paths: list[Path] = []
        cursor = 0.0
        try:
            for i, text in enumerate(segments_text):
                ssml = (
                    f"<speak version='1.0' xml:lang='{locale}'>"
                    f"<voice name='{voice_name}'>{escape(text)}</voice></speak>"
                )
                resp = self._session.post(endpoint, data=ssml.encode("utf-8"), headers=headers, timeout=30)
                resp.raise_for_status()
                seg_wav = tmp_dir / f"seg_{i:02d}.wav"
                seg_wav.write_bytes(resp.content)
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
        except requests.RequestException as e:
            return TTSResult(success=False, error=str(e))
        except subprocess.CalledProcessError as e:
            return TTSResult(success=False, error=e.stderr)

        return TTSResult(success=True, audio_path=str(out_path), segments=segments)
