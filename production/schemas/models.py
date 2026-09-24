"""Pydantic models mirroring production_job.schema.json / shot_job.schema.json.

These are the load-bearing types passed between pipeline stages:
Creative JSON -> VideoProvider -> TTS -> Subtitles/Edit -> QA -> MP4.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_DIR = Path(__file__).parent

ShotStatus = Literal["pending", "generating", "success", "failed", "needs_regen"]
JobStatus = Literal["draft", "generating", "needs_regen", "qa_failed", "ready", "published"]

# Video-generation prompts must never ask the model to render text; the renderer
# burns in subtitles/CTA/PR copy in a later stage. This guards that boundary.
FORBIDDEN_PROMPT_PATTERNS = [
    r"subtitle", r"caption", r"on-?screen text", r"字幕", r"テロップ",
    r"\bcta\b", r"pr表記", r"広告表記", r"watermark", r"logo overlay",
]


class Resolution(BaseModel):
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920


class PRDisclosure(BaseModel):
    required: bool
    text: str = ""


class Creative(BaseModel):
    hook: str
    narration_script: str
    cta: str
    pr_disclosure: PRDisclosure
    hashtags: list[str] = Field(default_factory=list)
    source: Literal["team_claude_original"] = "team_claude_original"


class ShotJob(BaseModel):
    shot_id: str = Field(pattern=r"^shot-\d{2}$")
    order: int
    duration_sec: float
    video_prompt: str
    negative_prompt: str = ""
    narration_text: str = ""
    camera_style: str = ""
    reference_image_path: Optional[str] = None
    seed: Optional[int] = None
    assigned_provider: Optional[str] = None
    status: ShotStatus = "pending"
    attempts: int = 0
    output_path: Optional[str] = None
    error: Optional[str] = None
    qa_notes: Optional[str] = None
    # Set only for shots ingested from a human-operated free-tier service
    # (see pipeline.ingest_shot). None means "not applicable" (e.g. still
    # pending, or produced by an API provider whose own ToS already covers
    # commercial use). False must never be treated as True by omission.
    license_commercial_clear: Optional[bool] = None
    # Cost/time tracking (see pipeline.generate_shots and costlog.py).
    # total_cost_usd accumulates across every attempt for this shot, so
    # "expected accepted-shot cost" = total_cost_usd once accepted=True.
    total_cost_usd: float = 0.0
    first_attempt_cost_usd: Optional[float] = None
    last_generation_time_sec: Optional[float] = None
    # Distinct from status=="success" (a file exists): accepted is the
    # human/QA verdict on whether that file is usable in the final video.
    # None = not yet reviewed.
    accepted: Optional[bool] = None
    rejection_reason: Optional[str] = None

    @field_validator("video_prompt")
    @classmethod
    def no_rendered_text_requested(cls, v: str) -> str:
        for pat in FORBIDDEN_PROMPT_PATTERNS:
            if re.search(pat, v, re.IGNORECASE):
                raise ValueError(
                    f"video_prompt must not ask the generation model to render "
                    f"text/subtitles/CTA/PR copy (matched pattern: {pat!r}); "
                    f"that is the renderer's job, not the video model's."
                )
        return v


class TTSConfig(BaseModel):
    provider: str
    voice: str
    language: str
    speaking_rate: float = 1.0


class SubtitleStyle(BaseModel):
    font: str = "Noto Sans JP"
    font_size: int = 64
    position: Literal["bottom", "top", "center"] = "bottom"
    highlight_color: str = "#FFE600"


class SubtitleConfig(BaseModel):
    enabled: bool = True
    burn_in: bool = True
    style: SubtitleStyle = Field(default_factory=SubtitleStyle)


class BGMConfig(BaseModel):
    enabled: bool = False
    track_path: Optional[str] = None
    volume_db: float = -18.0
    license: str = ""


class SFXCue(BaseModel):
    shot_id: str
    effect: str
    at_sec: float
    volume_db: float = -10.0


class TechnicalQAConfig(BaseModel):
    min_resolution: str = "1080x1920"
    max_duration_sec: float = 90
    min_duration_sec: float = 5
    require_audio_track: bool = True
    loudness_target_lufs: float = -14.0


class ComplianceQAConfig(BaseModel):
    require_pr_disclosure: bool = True
    require_cta: bool = True
    banned_words: list[str] = Field(default_factory=list)
    forbid_model_rendered_text: bool = True


class QAConfig(BaseModel):
    technical: TechnicalQAConfig = Field(default_factory=TechnicalQAConfig)
    compliance: ComplianceQAConfig = Field(default_factory=ComplianceQAConfig)


# free_tier_noncommercial_preview: shots come from a real generative model
# (not the ffmpeg ManualProvider stand-in) but were produced under a free
# tier whose terms prohibit commercial use and/or leave an unremovable
# watermark - every major provider checked (Invideo, Dreamina, Kling, Pika,
# Luma) draws this same line at their free tier. Such a render is real
# enough to evaluate quality, but still not publishable, so it must never be
# reported as final_candidate.
QualityTier = Literal["placeholder_preview", "free_tier_noncommercial_preview", "final_candidate"]


class JobOutput(BaseModel):
    mp4_path: Optional[str] = None
    generated_at: Optional[str] = None
    quality_tier: Optional[QualityTier] = None
    quality_notes: list[str] = Field(default_factory=list)
    # Cost rollup across all shots (see pipeline._compute_cost_rollup).
    # total_generation_cost_usd: sum of each shot's first_attempt_cost_usd
    #   (what this video would have cost if every shot were accepted on
    #   the first try).
    # total_regeneration_cost_usd: sum of (total_cost_usd - first_attempt_cost_usd)
    #   across shots - the cost attributable purely to retries.
    # total_render_cost_usd: generation + regeneration (+ TTS, when the
    #   active TTS provider reports a per-character cost).
    # cost_per_finished_video: alias for total_render_cost_usd for a single
    #   job; meant to generalize to an average when rolling up many jobs.
    total_generation_cost_usd: Optional[float] = None
    total_regeneration_cost_usd: Optional[float] = None
    total_render_cost_usd: Optional[float] = None
    cost_per_finished_video: Optional[float] = None


class ProductionJob(BaseModel):
    job_id: str = Field(pattern=r"^[A-Z]+-D\d+$")
    owner_team: Literal["claude", "gpt"]
    schema_version: Literal["1.0.0"] = "1.0.0"
    language: str
    target_platform: Literal["tiktok"] = "tiktok"
    aspect_ratio: Literal["9:16"] = "9:16"
    resolution: Resolution = Field(default_factory=Resolution)
    duration_target_sec: float = 30
    creative: Creative
    shots: list[ShotJob]
    tts: TTSConfig
    subtitles: SubtitleConfig
    bgm: BGMConfig = Field(default_factory=BGMConfig)
    sfx: list[SFXCue] = Field(default_factory=list)
    provider_preferences: list[str]
    qa: QAConfig = Field(default_factory=QAConfig)
    status: JobStatus = "draft"
    output: JobOutput = Field(default_factory=JobOutput)

    @classmethod
    def load(cls, path: str | Path) -> "ProductionJob":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def shot(self, shot_id: str) -> ShotJob:
        for s in self.shots:
            if s.shot_id == shot_id:
                return s
        raise KeyError(f"no such shot: {shot_id}")
