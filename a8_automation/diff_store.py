from __future__ import annotations

from typing import Dict

from .utils import iso_now, read_json, write_json

# Fields that identify/describe a record rather than representing a
# condition of the program itself -- excluded from diffing so that, say,
# a re-crawl picking up a slightly reworded title doesn't count as a
# "condition changed" event. Every other key present on either side (list
# fields like reward/epc, or detail-page section headings such as
# "成果条件"/"否認条件"/"禁止事項") is diffed automatically, since A8's own
# field set isn't fixed and detail pages aren't fetched for every program.
IDENTITY_FIELDS = {
    "program_id",
    "name",
    "url",
    "detail_url",
    "checked_at",
    "score",
    "excluded",
    "exclusion_reason",
    "judged_at",
}


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
        fields = (set(old.keys()) | set(new.keys())) - IDENTITY_FIELDS
        field_changes = {}
        for field in sorted(fields):
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
