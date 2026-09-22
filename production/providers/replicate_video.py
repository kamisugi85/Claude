"""Replicate-backed VideoProvider - the low-cost/pay-as-you-go alternative
required if Seedance turns out not to be usable (no key issued yet, region
issue, or the commercial terms don't work for this use case).

Replicate (replicate.com) is a legitimate official API aggregator: no
monthly minimum, pay per prediction-second, official REST API + auth token,
and each hosted model publishes its own license (several open-weight video
models on it are commercial-use permitted - this must be checked per model
before shipping real content, see required_user_actions below).

STATUS: implemented, gated behind REPLICATE_API_TOKEN, not verified live in
this session (no token configured, and Replicate was not confirmed reachable
through this sandbox's egress proxy either). Treat the same as Seedance:
confirm reachability, pricing and the chosen model's license yourself before
first use.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from production.providers.base import AvailabilityReport, ShotResult, VideoProvider
from production.schemas.models import ShotJob

API_TOKEN_ENV = "REPLICATE_API_TOKEN"
MODEL_VERSION_ENV = "REPLICATE_VIDEO_MODEL"
# Left unset on purpose: the right model (and its license/commercial terms)
# is a per-job decision, not a hardcoded default.
DEFAULT_MODEL_VERSION = ""
BASE_URL = "https://api.replicate.com/v1"


class ReplicateVideoProvider(VideoProvider):
    name = "replicate_video"

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def _token(self) -> str | None:
        return os.environ.get(API_TOKEN_ENV)

    def _model_version(self) -> str:
        return os.environ.get(MODEL_VERSION_ENV, DEFAULT_MODEL_VERSION)

    def check_availability(self) -> AvailabilityReport:
        missing = []
        if not self._token():
            missing.append(f"{API_TOKEN_ENV} environment variable (Replicate account API token)")
        if not self._model_version():
            missing.append(
                f"{MODEL_VERSION_ENV} environment variable (owner/model:version string "
                "for the specific text-to-video model to use)"
            )
        if missing:
            return AvailabilityReport(
                available=False,
                reason="Replicate provider is not configured.",
                required_user_actions=[
                    "Create a Replicate account and add a payment method (pay-as-you-go, "
                    "no monthly fee).",
                    "Pick a specific video-generation model on replicate.com, and read "
                    "that model's license/commercial-use terms yourself - they vary per "
                    "model and this client does not hardcode one.",
                    "Create an API token and set it as REPLICATE_API_TOKEN.",
                    "Set REPLICATE_VIDEO_MODEL to that model's `owner/name:version` string.",
                    f"(missing now: {'; '.join(missing)})",
                ],
            )
        return AvailabilityReport(available=True, reason=f"Configured for model {self._model_version()}.")

    def generate_shot(self, shot: ShotJob, out_dir: Path) -> ShotResult:
        avail = self.check_availability()
        if not avail.available:
            return ShotResult(success=False, error=avail.reason, provider=self.name)

        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        body = {
            "version": self._model_version(),
            "input": {
                "prompt": shot.video_prompt,
                "negative_prompt": shot.negative_prompt,
                "num_frames": int(shot.duration_sec * 24),
                "aspect_ratio": "9:16",
            },
        }
        try:
            resp = self._session.post(f"{BASE_URL}/predictions", json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            prediction = resp.json()
            pred_id = prediction["id"]

            deadline = time.time() + 900
            while time.time() < deadline:
                poll = self._session.get(f"{BASE_URL}/predictions/{pred_id}", headers=headers, timeout=30)
                poll.raise_for_status()
                data = poll.json()
                if data["status"] == "succeeded":
                    output = data["output"]
                    video_url = output[0] if isinstance(output, list) else output
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / f"{shot.shot_id}.mp4"
                    video_resp = self._session.get(video_url, timeout=120)
                    video_resp.raise_for_status()
                    out_path.write_bytes(video_resp.content)
                    return ShotResult(success=True, output_path=str(out_path), provider=self.name)
                if data["status"] in ("failed", "canceled"):
                    return ShotResult(success=False, error=f"Replicate prediction {data['status']}: {data.get('error')}", provider=self.name)
                time.sleep(5)
            return ShotResult(success=False, error="Replicate prediction timed out", provider=self.name)
        except requests.RequestException as e:
            return ShotResult(success=False, error=str(e), provider=self.name)
        except (KeyError, ValueError, IndexError) as e:
            return ShotResult(success=False, error=f"Unexpected Replicate response shape: {e}", provider=self.name)
