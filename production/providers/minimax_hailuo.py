"""MiniMax H3 VideoProvider - official direct API at platform.minimax.io.
Recommended primary candidate for CLAUDE-D01's paid phase.

STATUS: implemented against the corroborated public contract below, NOT
live-tested - no MINIMAX_API_KEY has been issued and none of this was called.
Endpoint/response shape corroborated across MiniMax's own docs pages (via
search snippets - platform.minimax.io itself is blocked by this sandbox's
egress proxy, confirmed 2026-09-24) and an open-source ComfyUI integration
node (nodes_minimax.py), which contains real working request/response
handling for this same API. Confirm exact pricing/limits/Japan account
eligibility in your own MiniMax console before spending.

PRICING - reconfirmed 2026-09-24, supersedes this file's earlier
$0.055/sec estimate (that number was for a different/older Hailuo model,
not H3's own pay-as-you-go rate):
  Source: platform.minimax.io/docs/guides/pricing-paygo (page itself
  unreachable from this sandbox; the figures below are corroborated by
  independent third-party write-ups that explicitly cite that page and
  agree with each other on 768P/2K rates - one other search result
  reported $0.18/$0.26 instead, which could not be reconciled and looks
  like it may be describing a different tier or product (e.g. "H3 Max");
  treat $0.08/$0.13 as the better-supported figure but verify the live
  page yourself before budgeting).
    768P: $0.08 / generated second
    2K:   $0.13 / generated second
    768P -> 2K regeneration: $0.05 / second
    Reference images: first 5 free, then ~$0.04 each
  Duration: 4-15 seconds, integers only (official).
  Concurrency limit (not RPM): 2 concurrent tasks on free tier, 15 on paid.
  Commercial use: paid/API-billed generations come with a commercial-use
  license (you own the output, responsible for clearing third-party rights
  in your prompt/references) per MiniMax's own Terms of Service; free
  trial credits are personal-use-only - this pipeline only ever uses the
  paid path. (Source: minimax.io terms-of-service page, checked via search
  summary 2026-09-24, not fetched directly - reconfirm before relying on it
  for a real commercial release.)
  Native audio: H3 generates synced stereo audio (including lip-synced
  dialogue) natively, with Japanese among 11 stably-supported languages.
  Not used by this client - see module note below.

Endpoint and auth:
  POST {base}/v1/video_generation          - create a task, returns task_id
  GET  {base}/v1/query/video_generation?task_id=...  - poll, returns
                                              {status, file_id}
  GET  {base}/v1/files/retrieve?file_id=... - returns {download_url},
                                              reported valid ~9h
  Authorization: Bearer <MiniMax API key>
  base default: https://api.minimax.io/v1   (international account; mainland
  China accounts use api.minimaxi.com instead - NOT used here)

Request body (field names corroborated, exact validation ranges not
independently re-derived):
  model      - "MiniMax-H3"
  content    - [{"type": "text", "text": "..."}] for the prompt (a
               subject-reference / first-frame-image variant exists for
               character consistency across shots but is not used by this
               client yet - see module note below)
  duration   - seconds, 4-15 for MiniMax-H3 (clamped below)
  resolution - "768P" | "2K" (no plain "1080p" option; this pipeline
               defaults to 768P for cost and relies on
               compositor._normalize_shot to scale up to the job's fixed
               1080x1920 output canvas, same as every other shot source)
  ratio      - "9:16" for this pipeline

Response/poll shape (from MiniMax's own docs pages + the ComfyUI node):
  create:  {"task_id": "..."}
  poll:    {"status": "Processing"|"Queueing"|"Success"|"Fail", "file_id": ...}
           (only "Success" and "Fail" are treated as terminal here - any
           other string keeps polling, which is the safe default given the
           in-between state names were not independently confirmed)
  retrieve: {"file_id":..., "download_url": "...", ...}

Audio note: H3's native audio (including possible dialogue) is intentionally
NOT requested/used - this pipeline supplies its own narration via a separate
TTS stage (Azure Neural or espeak-ng) and mixes its own BGM/SFX, so a native
audio track would either be redundant or conflict with the narration. Any
audio baked into the downloaded clip is stripped when the compositor
normalizes every shot before concatenation (production/compositor/
ffmpeg_compose.py:_normalize_shot uses `-an`), so this needs no special
handling here.

Character consistency note: MiniMax publishes a separate Subject-Reference
capability (model "S2V-01") that conditions generation on a single
reference image of a person, which is very likely relevant for keeping
CLAUDE-D01's on-screen person consistent across its 4 shots. This client
does not implement it yet (text-only content, matching how ShotJob is used
today) - wiring in reference_image_path is a natural next step once real
generation testing begins and this becomes the active provider.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from production.providers.base import AvailabilityReport, ShotResult, VideoProvider
from production.schemas.models import ShotJob

API_KEY_ENV = "MINIMAX_API_KEY"
BASE_URL_ENV = "MINIMAX_BASE_URL"
DEFAULT_BASE_URL = "https://api.minimax.io/v1"
MODEL_ENV = "MINIMAX_MODEL_ID"
DEFAULT_MODEL = "MiniMax-H3"
RESOLUTION_ENV = "MINIMAX_RESOLUTION"
DEFAULT_RESOLUTION = "768P"

_TERMINAL_SUCCESS = "Success"
_TERMINAL_FAIL = "Fail"
_MIN_DURATION_SEC, _MAX_DURATION_SEC = 4, 15

# Reconfirmed 2026-09-24 against platform.minimax.io's pay-as-you-go pricing
# for MiniMax-H3 (see module docstring for sourcing/caveats). Supersedes the
# earlier $0.055/sec figure, which was for a different model.
ESTIMATED_USD_PER_SECOND_BY_RESOLUTION = {"768P": 0.08, "2K": 0.13}


class MiniMaxHailuoProvider(VideoProvider):
    name = "minimax_hailuo"

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def _api_key(self) -> str | None:
        return os.environ.get(API_KEY_ENV)

    def _base_url(self) -> str:
        return os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL)

    def _model(self) -> str:
        return os.environ.get(MODEL_ENV, DEFAULT_MODEL)

    def _resolution(self) -> str:
        return os.environ.get(RESOLUTION_ENV, DEFAULT_RESOLUTION)

    def check_availability(self) -> AvailabilityReport:
        key = self._api_key()
        if not key:
            return AvailabilityReport(
                available=False,
                reason=(
                    "No MiniMax API key found in the environment. MiniMax "
                    "Hailuo requires a platform.minimax.io account with "
                    "billing enabled; it cannot be used without one."
                ),
                required_user_actions=[
                    "Step 1: Create an account at platform.minimax.io (use the "
                    "international api.minimax.io endpoint, not the mainland "
                    "api.minimaxi.com one).",
                    "Step 2: Confirm in your account/docs that video generation "
                    "(MiniMax-H3) is available for your account/region, and read "
                    "the current commercial-use terms.",
                    "Step 3: Add a payment method and choose pay-as-you-go API "
                    "billing (not the fixed monthly Token Plan, to match the "
                    "per-second cost analysis).",
                    f"Step 4: Issue an API key and set it as {API_KEY_ENV} "
                    f"(override {MODEL_ENV}/{BASE_URL_ENV}/{RESOLUTION_ENV} only "
                    "if your console shows different values than this client's "
                    "defaults).",
                    "None of these steps have been performed automatically - this "
                    "provider will not attempt a network call until MINIMAX_API_KEY is set.",
                ],
            )
        return AvailabilityReport(
            available=True,
            reason=f"{API_KEY_ENV} is set; will attempt live calls to {self._base_url()}.",
        )

    def estimate_cost_usd(self, shot: ShotJob) -> float:
        rate = ESTIMATED_USD_PER_SECOND_BY_RESOLUTION.get(self._resolution(), ESTIMATED_USD_PER_SECOND_BY_RESOLUTION[DEFAULT_RESOLUTION])
        duration = min(_MAX_DURATION_SEC, max(_MIN_DURATION_SEC, round(shot.duration_sec)))
        return round(duration * rate, 3)

    def current_config(self) -> dict:
        return {"model": self._model(), "resolution": self._resolution()}

    def _create_task_with_retry(self, body: dict, headers: dict, max_retries: int = 2):
        """Transient network hiccups (5xx, timeouts) shouldn't burn a whole
        shot's cost estimate on the first blip - retry the task-creation call
        itself a couple of times before giving up. Does not retry 4xx
        (bad request / auth errors), since those won't fix themselves."""
        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                resp = self._session.post(
                    f"{self._base_url()}/video_generation", json=body, headers=headers, timeout=30,
                )
                if resp.status_code >= 500 and attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                last_exc = e
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise last_exc

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
            "ratio": "9:16",
        }
        try:
            resp = self._create_task_with_retry(create_body, headers)
            task_id = resp.json()["task_id"]

            deadline = time.time() + 900
            file_id = None
            while time.time() < deadline:
                poll = self._session.get(
                    f"{self._base_url()}/query/video_generation",
                    headers=headers, params={"task_id": task_id}, timeout=30,
                )
                poll.raise_for_status()
                data = poll.json()
                status = data.get("status")
                if status == _TERMINAL_SUCCESS:
                    file_id = data["file_id"]
                    break
                if status == _TERMINAL_FAIL:
                    return ShotResult(success=False, error=f"MiniMax task failed: {data}", provider=self.name)
                time.sleep(5)

            if file_id is None:
                return ShotResult(success=False, error="MiniMax task timed out after 900s", provider=self.name)

            retrieve = self._session.get(
                f"{self._base_url()}/files/retrieve",
                headers=headers, params={"file_id": file_id}, timeout=30,
            )
            retrieve.raise_for_status()
            download_url = retrieve.json()["download_url"]

            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{shot.shot_id}.mp4"
            video_resp = self._session.get(download_url, timeout=120)
            video_resp.raise_for_status()
            out_path.write_bytes(video_resp.content)

            return ShotResult(
                success=True, output_path=str(out_path), provider=self.name,
                cost_usd_estimate=self.estimate_cost_usd(shot),
            )
        except requests.RequestException as e:
            return ShotResult(success=False, error=str(e), provider=self.name)
        except (KeyError, ValueError) as e:
            return ShotResult(
                success=False,
                error=f"Unexpected MiniMax response shape (endpoint contract may "
                      f"have changed since this client was written): {e}",
                provider=self.name,
            )
