from __future__ import annotations

import re
from typing import Dict, List, Optional

from .utils import iso_now, write_json

_NUMBER_PATTERN = re.compile(r"[\d,]+(?:\.\d+)?")


def parse_number(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = _NUMBER_PATTERN.search(text.replace(",", ""))
    return float(m.group().replace(",", "")) if m else None


def parse_percent(text: Optional[str]) -> Optional[float]:
    return parse_number(text)


def score_record(record: dict) -> Optional[float]:
    """Cheap, rule-based expected-value proxy: EPC (A8's own "earnings per
    click" figure) already bakes in reward x conversion rate, so prefer it.
    Falls back to reward x conversion_rate when EPC isn't available. No LLM
    call here -- this is the free first-pass filter over the whole catalog.
    """
    epc = parse_number(record.get("epc"))
    if epc is not None:
        return epc

    reward = parse_number(record.get("reward"))
    rate = parse_percent(record.get("conversion_rate"))
    if reward is not None and rate is not None:
        return reward * rate / 100.0

    return None


def build_shortlist(records: Dict[str, dict], top_n: int) -> List[dict]:
    scored = []
    for program_id, record in records.items():
        score = score_record(record)
        if score is None:
            continue
        scored.append({**record, "program_id": program_id, "score": score})

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_n]


def save_shortlist(records: Dict[str, dict], path: str, top_n: int = 200) -> List[dict]:
    shortlist = build_shortlist(records, top_n)
    write_json(path, {"generated_at": iso_now(), "count": len(shortlist), "items": shortlist})
    return shortlist
