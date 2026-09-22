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

**I could not fully verify Seedance's live API from this sandbox**: this
environment's network egress proxy blocks `volcengine.com` and
`byteplus.com` outright, so I could not load the primary Ark/BytePlus docs to
confirm the exact endpoint, current model id, or current price. What follows
is the best I could establish from secondary sources plus general knowledge,
and **must be reconfirmed by a human against the current official docs**
before enabling it for real spend:

| Question | Finding | Confidence |
|---|---|---|
| Official API exists? | Yes — Volcengine Ark (mainland account) and BytePlus ModelArk (international account) both offer Doubao/Seedance video generation via a documented REST API. The Dreamina/Jimeng consumer web app is a different, non-API product and is intentionally **not** automated here (no browser scraping). | Medium |
| Auth | Bearer API key issued from the Ark/BytePlus console, tied to a billing-enabled account. | Medium |
| Pricing | Reported around $0.03–$0.14 per second of generated video depending on route (official Ark direct vs. resold access) — **not confirmed against Ark's own rate card**. | Low — confirm in console before use |
| Commercial use | Ark/BytePlus model licenses are generally commercial-use permitting for paid API tiers, but terms vary by specific model version and must be checked at enablement time. | Low — confirm ToS for the exact model you enable |
| Japan access | Third-party sources report Japan is on BytePlus's international allowlist. | Low — confirm on your own account, region support can change |

`providers/seedance.py` implements the documented async task pattern (create
task → poll → download result) against these best-guess field names, gated
behind `ARK_API_KEY`. **It will not attempt any network call, and costs
nothing, unless that environment variable is set.**

### → What only you can do (Seedance)
1. Create a BytePlus (international) or Volcengine account and enable billing.
2. In the console, confirm Doubao-Seedance video generation is enabled for
   your account/region, confirm Japan billing/ToS works for your use case,
   and read the commercial-use terms for the exact model version you pick.
3. Issue an API key, then set `ARK_API_KEY` (and `ARK_BASE_URL` /
   `SEEDANCE_MODEL_ID` if the console shows different values than this
   client's placeholders) in the environment before running `render`.

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
- **Once Seedance is configured:** roughly $0.5–$2 per ~26s video at the
  per-second rates found above (4 shots × 6–7s each), **unconfirmed** — get
  the real number from your Ark/BytePlus console after enabling billing.
- **Once Replicate is configured instead:** highly model-dependent; check the
  chosen model's per-second/per-run price on its Replicate page before
  enabling it as the default provider.

## The one thing that would unlock the most automation next

Getting **one** of Seedance or Replicate configured (an API key + a
confirmed commercial-use model) removes the single remaining human-in-the-
loop step: today a person still has to produce or approve every shot's real
footage. Everything downstream of "a shot's MP4 exists" — TTS, subtitles,
BGM/SFX, technical QA, compliance QA, retry-only-the-NG-shot — is already
fully automated and already runs unattended via
`python -m production.cli render CLAUDE-D01`.
