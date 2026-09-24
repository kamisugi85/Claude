# production/ — TikTok video pipeline (Team Claude)

Independent, provider-agnostic pipeline:

```
Creative JSON -> VideoProvider (per shot) -> TTS -> Subtitles/Edit -> QA -> MP4
```

Mirrors the architecture Team GPT settled on for their own pipeline, but is a
separate implementation under `production/` with its own schema, providers,
and jobs directory. It does not read or depend on Team GPT's scripts,
storyboards, or code. It does not touch the existing A8 Collector or TikTok
Research modules (none of those exist in this repository yet; nothing here
imports outside of `production/`).

First job implemented: `CLAUDE-D01` (`production/jobs/CLAUDE-D01.json`), an
original Team Claude creative (short "app hook" promo, Japanese, PR-disclosed).
Team GPT's `GPT-D01` was deliberately not touched.

**Free PoC phase concluded**: every free tier checked (Invideo AI, Dreamina,
Kling, Pika, Luma) prohibits commercial use and leaves an unremovable
watermark, and Team GPT's real Invideo run confirmed free credits don't
cover a full 24s AI video (it fell back to stills). See
`production/README_free_poc_comparison.md` and
`production/jobs/CLAUDE-D01_free_tier_shot_package.md` for that phase's
findings; `pipeline.ingest_shot()` (adopts a human-generated clip,
`quality_tier="free_tier_noncommercial_preview"`) remains available but is
no longer the target path.

Also investigated (kept as a future cost-reduction / API-outage backup, not
currently the active path): open-weight video generation on free cloud GPUs.
See `production/README_local_and_free_gpu_investigation.md` and
`production/jobs/CLAUDE-D01_free_gpu_poc_wan21.ipynb` (Wan2.1-T2V-1.3B,
Apache 2.0, runs on a free Colab/Kaggle T4 - code-verified against the
installed `diffusers` library but not yet executed, since this sandbox has
no GPU and blocks huggingface.co).

**Current phase: MiniMax H3 paid API small-budget PoC (no billing yet).**
See `production/README_minimax_paid_poc_20260924.md` for the full writeup:
official model/pricing re-verification (dated, with source URLs - note
`platform.minimax.io`/`api.minimax.io` are both blocked by this sandbox's
network policy, so even a keyless connectivity check couldn't be run here),
provider audit (added retry-with-backoff on task creation, corrected the
per-second cost constant), the new cost-tracking layer (`costlog.py`, plus
`ShotJob`/`JobOutput` cost fields), and the chosen PoC target
(`shot-00`, cost-estimated at $0.48-$0.96 for 1-2 generation attempts at
768P). `production/README_paid_phase_cost_analysis.md` has a pointer note at
its top marking its $0.055/sec MiniMax figure as superseded/incorrect -
its content is otherwise left as a historical record.
`CLAUDE-D01.json`'s `provider_preferences` is `["minimax_hailuo", "seedance",
"replicate_video", "manual"]`, so setting `MINIMAX_API_KEY` **in an
environment that can actually reach api.minimax.io** (not this sandbox)
switches `render`/`regen-shot` over with no code changes. `tts.provider` is
still `espeak_local` pending an Azure key. No account has been created and
no key has been issued for either MiniMax or Azure.

## Run it

```bash
cd /home/user/Claude
python3 -m production.cli render CLAUDE-D01       # full pipeline -> MP4
python3 -m production.cli regen-shot CLAUDE-D01 shot-02   # redo one NG shot only
python3 -m production.cli qa CLAUDE-D01           # re-run QA on the last render
python3 -m pytest tests/ -q                       # test suite
```

`render` writes to `output/CLAUDE-D01/`: `shots/*.mp4` (per-shot clips),
`audio/narration.wav`, `work/subtitles.srt`, and the final `CLAUDE-D01.mp4`.
Re-running `render` skips shots already marked `success` with an existing
output file, so a partial failure only redoes what's missing.

## Architecture

- `schemas/production_job.schema.json`, `schemas/shot_job.schema.json` —
  JSON Schema for the job and per-shot generation request.
- `schemas/models.py` — pydantic models with the same shape, plus one
  guardrail: a `ShotJob.video_prompt` that asks the generation model to draw
  subtitles/CTA/PR text/watermarks is **rejected at validation time**. Text is
  always composited in the renderer stage, never requested from the video
  model, per the instruction that generation models must not render Japanese
  captions/CTA/PR copy themselves.
- `providers/base.py` — the `VideoProvider` interface: `check_availability()`
  (never raises; reports missing keys/setup) and `generate_shot()` (returns
  `ShotResult(success=False, ...)` instead of raising on expected failure, so
  the pipeline can fail over).
- `providers/registry.py` — tries providers in the job's `provider_preferences`
  order and returns the first success. Adding a vendor means adding one file
  here; nothing else in the pipeline changes.
- `providers/seedance.py`, `providers/replicate_video.py`, `providers/manual.py`
  — see **Provider status** below.
- `tts/` — `TTSProvider` interface + `espeak_provider.py` (offline, no key).
- `subtitles/generator.py` — builds SRT cues from TTS segment timing.
- `compositor/ffmpeg_compose.py` — concatenates shots, burns in subtitles
  (libass) + CTA + PR-disclosure overlay, mixes narration + looped BGM +
  timed SFX cues, encodes final 1080x1920 MP4.
- `qa/technical.py` — ffprobe-based: resolution, 9:16 aspect ratio, duration
  bounds, has an audio track.
- `qa/compliance.py` — PR-disclosure present, CTA present, no banned words,
  no shot prompt asking the model to render text.
- `pipeline.py` — orchestrates all of the above; `mark_shot_for_regen` /
  `regen_shot` let a single NG shot be redone without rebuilding the rest.

## Provider status (item 4–7 of the task)

**Seedance 2.5 is now Team Claude's primary/first-choice provider**
(`provider_preferences: ["seedance", "replicate_video", "manual"]` in
`CLAUDE-D01.json`), per Team GPT's confirmation that BytePlus officially
serves it under model id `dreamina-seedance-2-5-260628`.

This sandbox's network egress proxy still blocks `volcengine.com` and
`byteplus.com` directly, so `docs.byteplus.com` could not be read
start-to-end here either. But the endpoint/field-name picture below is
**corroborated across multiple independent sources**, most usefully a
third-party open-source BytePlus Ark mock/replay harness
([CopilotKit/aimock](https://github.com/CopilotKit/aimock), PR #424) whose
recorded fixtures capture the *actual* wire response shape for this exact
task-lifecycle API — not just marketing copy. Still: **reconfirm pricing and
your account's terms in your own BytePlus console before spending money.**

| Question | Finding | Confidence |
|---|---|---|
| Official API exists? | Yes — BytePlus ModelArk (international account). Endpoint confirmed from multiple sources: `POST {base}/contents/generations/tasks` to create, `GET {base}/contents/generations/tasks/{id}` to poll, `base` = `https://ark.ap-southeast.bytepluses.com/api/v3`. The Dreamina/Jimeng consumer web app is a separate, non-API product and is intentionally **not** automated here (no browser scraping). | Medium-High |
| Model id | `dreamina-seedance-2-5-260628` (per Team GPT + corroborated by BytePlus's own Seedance 2.5 tutorial page). | High |
| Auth | `Authorization: Bearer <key>` header, key issued from a billing-enabled BytePlus account. | Medium-High |
| Request shape | `{"model", "content": [{"type":"text","text": prompt}, ...optional image_url/video_url/audio_url parts], "resolution": "480p"\|"720p"\|"1080p"\|"4k", "duration": seconds (Seedance 2.5: 4–30), "aspect_ratio": "16:9"\|"9:16"\|"4:3"\|"3:4"\|"1:1"\|"21:9"\|"adaptive", "generate_audio": bool}`. | Medium |
| Response shape | `{"model","status","created_at","updated_at","content":{"video_url":...},"usage":{...},"error":{"message":...}}`; status is one of `queued\|running\|succeeded\|failed\|cancelled\|expired` (only `queued`/`running` are non-terminal); result URLs expire ~24h after `updated_at`. | Medium-High (from aimock's recorded real fixtures) |
| Pricing | Reported as **token-based**, not flat per-second (~$10.70 per million video tokens without a video input, per one secondary source) — materially different from a naive per-second estimate. | Low — confirm in console |
| Commercial use | Paid ModelArk API tiers are generally commercial-use permitting, but terms vary by model version and must be checked at enablement time. | Low — confirm ToS for `dreamina-seedance-2-5-260628` specifically |
| Japan access | Multiple secondary sources report Japan is on BytePlus's international allowlist (~40 markets). | Medium — still confirm on your own account, region support can change |

`providers/seedance.py` implements this contract: it builds the request with
`generate_audio: false` (this pipeline supplies its own TTS narration and
mixes its own BGM/SFX — the model's own audio track would either be dead
weight or conflict with our narration), clamps `duration` into Seedance
2.5's documented `[4, 30]` range, and downloads the result immediately since
result URLs expire in ~24h. It is gated behind `ARK_API_KEY`: **it will not
attempt any network call, and costs nothing, unless that environment
variable is set.** `tests/test_seedance_request_shape.py` verifies the exact
request/response handling against fixture data shaped like the above,
without any network access.

### → What only you can do (Seedance) — stopping here, no billing yet
1. **Create a BytePlus account** at byteplus.com (international access —
   this is the account type reported to support Japan).
2. **In the BytePlus console, open ModelArk** and confirm Dreamina Seedance
   2.5 (`dreamina-seedance-2-5-260628`) is enabled for your account/region,
   and read its current commercial-use terms for your intended use
   (affiliate/PR content).
3. **Enable billing** on the account. This is the step that costs money —
   nothing before this point does, and this pipeline will not call the API
   before you've done this and issued a key.
4. **Issue an API key** in the console and set it as the `ARK_API_KEY`
   environment variable. Only override `SEEDANCE_MODEL_ID` / `ARK_BASE_URL`
   / `SEEDANCE_RESOLUTION` if your console shows different values than this
   client's defaults (`dreamina-seedance-2-5-260628`, `https://ark.ap-southeast.bytepluses.com/api/v3`, `720p`).
5. Once `ARK_API_KEY` is set, `python -m production.cli render CLAUDE-D01`
   will call Seedance for every shot with no further code changes — it is
   already first in `provider_preferences`.

**I have not performed any of these steps, and no billing or account
creation has happened.** This is exactly the point the task asked me to stop
at and hand off to you.

### Low-cost/free alternative (item 6): Replicate
`providers/replicate_video.py` wraps `api.replicate.com` generically: no
monthly minimum, pay per prediction-second, official REST API. It is
model-agnostic on purpose — Replicate hosts many text-to-video models, each
with its own license, so this client does **not** hardcode a model or a
price; you choose the model and confirm its commercial-use terms.

### → What only you can do (Replicate)
1. Create a Replicate account, add a payment method (pay-as-you-go).
2. Pick a specific video model on replicate.com and read its license.
3. Create an API token, set `REPLICATE_API_TOKEN`.
4. Set `REPLICATE_VIDEO_MODEL` to that model's `owner/name:version` string.

### Fallback used today: `manual` provider (item 7)
No browser automation of Dreamina or any other web UI is implemented or
planned — that was explicitly out of scope. Until a real key is configured,
every shot is produced by `providers/manual.py`, which renders a clearly
labeled placeholder clip locally with ffmpeg (solid color + the shot's prompt
text so you can see what should be there). This is what let the whole
pipeline — TTS, subtitles, BGM/SFX mixing, technical QA, compliance QA — be
built and tested end-to-end today without any account, key, or spend. Before
publishing, a human replaces each placeholder under `output/CLAUDE-D01/shots/`
with a real clip (produced by hand, or by Seedance/Replicate once configured)
of the same filename and duration, then reruns `render`.

## TTS

`tts/espeak_provider.py` uses `espeak-ng` (installed via apt in this
environment) — fully offline, no key, no network, no cost. Voice quality is
robotic; it exists so the pipeline has a always-available default. Swapping
in a paid cloud TTS voice (if one is already contracted elsewhere) only
requires a new class implementing `tts/base.py`'s `TTSProvider`.

## Compliance guardrails baked into the schema

- `ShotJob.video_prompt` validation rejects any prompt asking for
  subtitles/captions/CTA/PR text/logos/watermarks (see
  `schemas/models.py:FORBIDDEN_PROMPT_PATTERNS`). Text always comes from the
  renderer (`compositor/ffmpeg_compose.py`), never the generation model.
- `qa/compliance.py` re-checks the same thing at the job level (in case a
  shot was hand-edited after validation), plus PR-disclosure presence, CTA
  presence, and a banned-word list (tuned for Japanese affiliate-marketing
  rules, e.g. rejecting "絶対儲かる" / "必ず痩せる" style guaranteed-outcome claims).

## Estimated cost per video

- **Today (manual/placeholder + espeak TTS + local ffmpeg):** $0 — nothing
  calls a paid API.
- **Once Seedance is configured:** billing is token-based, not flat
  per-second (reported ~$10.70 per million video tokens without a video
  input), so a per-second estimate is not reliable — **get the real number
  from your BytePlus console after enabling billing**, ideally from a single
  test generation's `usage.total_tokens` before running the full job.
- **Once Replicate is configured instead:** highly model-dependent; check the
  chosen model's per-second/per-run price on its Replicate page before
  enabling it as the default provider.

## Placeholder quality gating

`render()` computes `output.quality_tier` on every run:
- `"placeholder_preview"` if **any** of: a shot was produced by
  `ManualProvider` (placeholder ffmpeg clip), narration used
  `espeak_local` (offline robotic TTS), or the BGM/SFX tracks are the
  synthetic ffmpeg tones checked into `production/assets/`.
- `"final_candidate"` only once none of the above apply.

`output.quality_notes` lists exactly which pieces are still placeholders.
`python -m production.cli render` prints a loud `PLACEHOLDER PREVIEW` banner
whenever `quality_tier != "final_candidate"` — **today, every CLAUDE-D01
render is `placeholder_preview`, and must not be treated as publishable
TikTok quality**, regardless of whether QA passed (QA checks structural
correctness - resolution/duration/PR-disclosure/etc - not creative quality).
This will only flip to `final_candidate` once a real video provider (e.g.
Seedance) is configured *and* a non-placeholder TTS/BGM source replaces
espeak-ng and the synthetic tones.

## The one thing that would unlock the most automation next

Getting **one** of Seedance or Replicate configured (an API key + a
confirmed commercial-use model) removes the single remaining human-in-the-
loop step: today a person still has to produce or approve every shot's real
footage. Everything downstream of "a shot's MP4 exists" — TTS, subtitles,
BGM/SFX, technical QA, compliance QA, retry-only-the-NG-shot — is already
fully automated and already runs unattended via
`python -m production.cli render CLAUDE-D01`.
