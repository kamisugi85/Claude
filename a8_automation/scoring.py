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


def score_record(record: dict) -> Optional[float]:
    """EPC only. reward x conversion_rate is NOT used as an EPC substitute --
    conversion_rate here is A8's 確定率 (approval rate among applications),
    not a click-through rate, so multiplying it by reward does not yield a
    real earnings-per-click figure and would be a fabricated number. A
    record with no EPC gets no score here (None), not a synthetic one; it is
    never excluded on that basis, just left out of an EPC-only ranking.
    """
    return parse_number(record.get("epc"))


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
