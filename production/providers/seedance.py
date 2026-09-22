"""Seedance VideoProvider - BytePlus ModelArk's official Dreamina Seedance 2.5
video generation API (model id `dreamina-seedance-2-5-260628`).

STATUS: implemented against the corroborated public API contract below, but
still NOT live-tested from this session - no ARK_API_KEY has been issued, and
this sandbox's egress proxy blocks docs.byteplus.com/volcengine.com directly,
so the primary docs could not be read start-to-end here. What follows was
cross-checked across independent secondary sources: BytePlus's own product/
blog pages (via search snippets), and - most usefully - a third-party
open-source project (CopilotKit/aimock, a BytePlus Ark mock/replay harness)
whose recorded fixtures show the *actual* wire response shape for this exact
API family. Confirm against your own BytePlus console before spending money.

Endpoint and auth (corroborated by multiple independent sources, including
the aimock fixtures):
  POST {base_url}/contents/generations/tasks   - create a task
  GET  {base_url}/contents/generations/tasks/{id}  - poll a task
  Authorization: Bearer <ARK/BytePlus API key>
  base_url default: https://ark.ap-southeast.bytepluses.com/api/v3 (international)

Request body fields (BytePlus ModelArk video-generation docs, via search
summaries - names corroborated, exact validation ranges not independently
re-derived by us):
  model         - e.g. "dreamina-seedance-2-5-260628"
  content       - list of parts: {"type": "text", "text": "..."} for the
                  prompt, optionally {"type": "image_url", "image_url": {...}},
                  {"type": "video_url", ...}, {"type": "audio_url", ...} for
                  Seedance 2.5's reference-asset inputs (up to 50 combined;
                  not used here - CLAUDE-D01 is text-only prompts)
  resolution    - "480p" | "720p" | "1080p" | "4k" (2.5 is documented as
                  480p/720p; default here is "720p")
  duration      - seconds, Seedance 2.5 supports 4-30 (clamped below)
  aspect_ratio  - "16:9" | "9:16" | "4:3" | "3:4" | "1:1" | "21:9" | "adaptive"
  generate_audio - bool; Seedance 2.5 can synthesize its own ambient/sync
                  audio. Deliberately left False here: this pipeline supplies
                  its own narration via a separate TTS stage and mixes BGM/SFX
                  itself in the compositor, so the model's own audio track
                  would either be silent dead weight or conflict with our
                  narration - see production/README.md.

Response envelope (field names taken directly from aimock's recorded
fixtures for this task-lifecycle API, which capture real BytePlus wire
responses):
  {"model": "...", "status": "queued|running|succeeded|failed|cancelled|expired",
   "created_at": <unix>, "updated_at": <unix>,
   "content": {"video_url": "https://..."},   # only present when succeeded
   "usage": {"completion_tokens": N, "total_tokens": N},
   "error": {"message": "..."}}               # only present when failed
Non-terminal states are exactly {"queued", "running"}; anything else is
terminal. Result URLs are reported to expire ~24h after `updated_at`, so this
client downloads the file immediately rather than persisting the URL.

Pricing: reported as token-based billing (order of ~$10/million video
tokens without a video input, per one secondary source), NOT a flat
per-second rate - this materially changes cost estimation vs. a naive
per-second multiply, and could not be confirmed against BytePlus's own rate
card from this sandbox. Treat estimate_cost_usd() below as a rough per-second
proxy only, and get the real number from your BytePlus console after billing
is enabled.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from production.providers.base import AvailabilityReport, ShotResult, VideoProvider
from production.schemas.models import ShotJob

API_KEY_ENV = "ARK_API_KEY"
BASE_URL_ENV = "ARK_BASE_URL"
DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
MODEL_ENV = "SEEDANCE_MODEL_ID"
DEFAULT_MODEL = "dreamina-seedance-2-5-260628"
RESOLUTION_ENV = "SEEDANCE_RESOLUTION"
DEFAULT_RESOLUTION = "720p"

_NON_TERMINAL_STATUSES = {"queued", "running"}
_MIN_DURATION_SEC, _MAX_DURATION_SEC = 4, 30

# Rough proxy only - see module docstring. BytePlus's actual billing is
# reported to be per video-token, not flat per-second.
ESTIMATED_USD_PER_SECOND = 0.10


class SeedanceProvider(VideoProvider):
    name = "seedance"

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def _api_key(self) -> str | None:
        return os.environ.get(API_KEY_ENV)

    def check_availability(self) -> AvailabilityReport:
        key = self._api_key()
        if not key:
            return AvailabilityReport(
                available=False,
                reason=(
                    "No BytePlus/Ark API key found in the environment. Seedance "
                    "2.5 requires a BytePlus ModelArk account with billing "
                    "enabled; it cannot be used without one."
                ),
                required_user_actions=[
                    "Step 1: Create a BytePlus account at byteplus.com (international "
                    "access - this is the account type reported to support Japan).",
                    "Step 2: In the BytePlus console, open ModelArk and confirm "
                    "Dreamina Seedance 2.5 is enabled for your account/region, and "
                    "read its current commercial-use terms.",
                    "Step 3: Enable billing on the account (required before any API "
                    "call succeeds - this is a real payment method, nothing further "
                    "happens until you do this).",
                    f"Step 4: Issue an API key in the console and set it as the "
                    f"{API_KEY_ENV} environment variable (override {MODEL_ENV}/"
                    f"{BASE_URL_ENV}/{RESOLUTION_ENV} only if your console shows "
                    "different values than this client's defaults).",
                    "None of these steps have been performed automatically - this "
                    "provider will not attempt a network call until ARK_API_KEY is set.",
                ],
            )
        return AvailabilityReport(
            available=True,
            reason=f"{API_KEY_ENV} is set; will attempt live calls to {self._base_url()}.",
        )

    def _base_url(self) -> str:
        return os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL)

    def _model(self) -> str:
        return os.environ.get(MODEL_ENV, DEFAULT_MODEL)

    def _resolution(self) -> str:
        return os.environ.get(RESOLUTION_ENV, DEFAULT_RESOLUTION)

    def estimate_cost_usd(self, shot: ShotJob) -> float:
        return round(shot.duration_sec * ESTIMATED_USD_PER_SECOND, 3)

    def generate_shot(self, shot: ShotJob, out_dir: Path) -> ShotResult:
        avail = self.check_availability()
        if not avail.available:
            return ShotResult(success=False, error=avail.reason, provider=self.name)

        headers = {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}
        duration = min(_MAX_DURATION_SEC, max(_MIN_DURATION_SEC, round(shot.duration_sec)))
        create_body = {
            "model": self._model(),
            "content": [{"type": "text", "text": shot.video_prompt}],
            "duration": duration,
            "resolution": self._resolution(),
            "aspect_ratio": "9:16",
            "generate_audio": False,
        }
        try:
            resp = self._session.post(
                f"{self._base_url()}/contents/generations/tasks",
                json=create_body, headers=headers, timeout=30,
            )
            resp.raise_for_status()
            task_id = resp.json()["id"]

            deadline = time.time() + 900
            result_url = None
            while time.time() < deadline:
                poll = self._session.get(
                    f"{self._base_url()}/contents/generations/tasks/{task_id}",
                    headers=headers, timeout=30,
                )
                poll.raise_for_status()
                data = poll.json()
                status = data.get("status")
                if status == "succeeded":
                    result_url = data["content"]["video_url"]
                    break
                if status not in _NON_TERMINAL_STATUSES:
                    error_msg = (data.get("error") or {}).get("message", "no error message given")
                    return ShotResult(
                        success=False,
                        error=f"Ark task ended with status={status!r}: {error_msg}",
                        provider=self.name,
                    )
                time.sleep(5)

            if not result_url:
                return ShotResult(success=False, error="Ark task timed out after 900s", provider=self.name)

            # Result URLs are reported to expire ~24h after updated_at - download now.
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{shot.shot_id}.mp4"
            video_resp = self._session.get(result_url, timeout=120)
            video_resp.raise_for_status()
            out_path.write_bytes(video_resp.content)

            return ShotResult(
                success=True,
                output_path=str(out_path),
                provider=self.name,
                cost_usd_estimate=self.estimate_cost_usd(shot),
            )
        except requests.RequestException as e:
            return ShotResult(success=False, error=str(e), provider=self.name)
        except (KeyError, ValueError) as e:
            return ShotResult(
                success=False,
                error=f"Unexpected Ark response shape (endpoint contract may have "
                      f"changed since this client was written): {e}",
                provider=self.name,
            )
