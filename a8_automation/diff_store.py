from __future__ import annotations

from typing import Dict

from .utils import iso_now, read_json, write_json

DIFF_FIELDS = [
    "reward",
    "reward_condition",
    "sns_condition",
    "approval_condition",
    "rejection_condition",
    "prohibited_items",
]


def load_snapshot(path: str) -> Dict[str, dict]:
    return read_json(path, default={})


def compute_diff(previous: Dict[str, dict], current: Dict[str, dict]) -> dict:
    prev_ids = set(previous.keys())
    curr_ids = set(current.keys())

    new_items = [current[pid] for pid in sorted(curr_ids - prev_ids)]

    changed_items = []
    for pid in sorted(curr_ids & prev_ids):
        old = previous[pid]
        new = current[pid]
        field_changes = {}
        for field in DIFF_FIELDS:
            old_value = old.get(field)
            new_value = new.get(field)
            if old_value != new_value:
                field_changes[field] = {"old": old_value, "new": new_value}
        if field_changes:
            changed_items.append(
                {
                    "program_id": pid,
                    "name": new.get("name", old.get("name")),
                    "changes": field_changes,
                }
            )

    return {
        "generated_at": iso_now(),
        "new_count": len(new_items),
        "changed_count": len(changed_items),
        "new_items": new_items,
        "changed_items": changed_items,
    }


def save_diff(diff: dict, path: str) -> None:
    write_json(path, diff)


def promote_snapshot(current: Dict[str, dict], latest_path: str) -> None:
    write_json(latest_path, current)
