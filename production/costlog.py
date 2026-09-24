"""Per-attempt cost/time audit trail for shot generation.

One JSONL line per generation attempt (success or failure), independent of
the pydantic job state - this is an append-only log, never rewritten, so it
survives even if a shot's status later changes (e.g. accepted then later
regenerated). Aggregates in ProductionJob.output.* are a rollup of this data
computed at render time, not the source of truth themselves.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CostLogEntry:
    provider: str
    model: str
    creative_id: str
    shot_id: str
    duration_sec: float
    resolution: str
    attempt_count: int
    generation_cost_usd: float
    generation_time_sec: float
    accepted: bool | None
    rejection_reason: str | None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


def cost_log_path(output_root: Path, job_id: str) -> Path:
    return output_root / job_id / "cost_log.jsonl"


def append_cost_log(output_root: Path, job_id: str, entry: CostLogEntry) -> None:
    path = cost_log_path(output_root, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")


def read_cost_log(output_root: Path, job_id: str) -> list[dict]:
    path = cost_log_path(output_root, job_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
