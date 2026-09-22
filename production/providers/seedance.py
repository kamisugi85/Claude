"""Seedance VideoProvider (ByteDance Doubao-Seedance, via Volcengine Ark /
BytePlus ModelArk's official content-generation API).

STATUS: implemented but UNVERIFIED end-to-end. This sandbox's network egress
proxy blocks volcengine.com and byteplus.com, so the exact endpoint path,
request/response field names and current pricing below could not be
confirmed against live documentation in this session - see
production/README.md "Seedance findings" for what could and could not be
checked, and confirm against the current official docs before first use.

What is reasonably well established from secondary sources:
  - Official commercial access is via Volcengine Ark (mainland account) or
    BytePlus ModelArk (international account, English docs, English/USD
    billing) - not the Dreamina/Jimeng consumer web app.
  - Auth is a bearer API key tied to a Volcengine/BytePlus account with
    billing enabled.
  - Billing is usage-based (per second of generated video), no browser
    automation or scraping is involved.
  - BytePlus's published country allowlist for international access is
    reported to include Japan, but this was not confirmed against the
    primary BytePlus terms/region page from this sandbox.

This client follows Ark's documented async task shape: POST to create a
generation task, poll GET until the task succeeds, then download the
resulting video URL. Field names are marked where they need reconfirmation.
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
DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"  # UNVERIFIED, confirm before use
MODEL_ENV = "SEEDANCE_MODEL_ID"
DEFAULT_MODEL = "doubao-seedance-1-0-lite"  # UNVERIFIED model id, confirm in Ark console

# Best-effort estimate from third-party pricing writeups, not Ark's own rate
# card (that page was unreachable from this sandbox). Confirm in the Ark/
# BytePlus console before relying on this number.
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
                    "No Ark/BytePlus API key found in the environment. "
                    "Seedance requires an account with billing enabled; it "
                    "cannot be used without one."
                ),
                required_user_actions=[
                    "Create a BytePlus (international) or Volcengine (mainland) "
                    "account and enable billing.",
                    "Confirm in the console that Doubao-Seedance video generation "
                    "is enabled for your account/region (Japan is reported allowed "
                    "for BytePlus international, but this needs your own "
                    "confirmation - not verified from this sandbox).",
                    "Review the current commercial-use terms for the model you "
                    "select (Ark model licenses vary by model).",
                    f"Issue an API key and set it as the {API_KEY_ENV} environment "
                    "variable (and ARK_BASE_URL/SEEDANCE_MODEL_ID if your console "
                    "shows different values than this client's defaults).",
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

    def estimate_cost_usd(self, shot: ShotJob) -> float:
        return round(shot.duration_sec * ESTIMATED_USD_PER_SECOND, 3)

    def generate_shot(self, shot: ShotJob, out_dir: Path) -> ShotResult:
        avail = self.check_availability()
        if not avail.available:
            return ShotResult(success=False, error=avail.reason, provider=self.name)

        headers = {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}
        create_body = {
            "model": self._model(),
            "content": [{"type": "text", "text": shot.video_prompt}],
            "duration": shot.duration_sec,
            "resolution": "1080p",
            "aspect_ratio": "9:16",
        }
        try:
            resp = self._session.post(
                f"{self._base_url()}/contents/generations/tasks",
                json=create_body, headers=headers, timeout=30,
            )
            resp.raise_for_status()
            task_id = resp.json()["id"]

            deadline = time.time() + 600
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
                if status == "failed":
                    return ShotResult(
                        success=False,
                        error=f"Ark task failed: {data.get('error')}",
                        provider=self.name,
                    )
                time.sleep(5)

            if not result_url:
                return ShotResult(success=False, error="Ark task timed out", provider=self.name)

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
