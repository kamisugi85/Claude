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


class JobOutput(BaseModel):
    mp4_path: Optional[str] = None
    generated_at: Optional[str] = None


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
