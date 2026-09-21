from __future__ import annotations

from typing import Dict, List

from .utils import iso_now, write_json

# Heuristic for "this changed field could affect expected revenue", used to
# decide whether a previously-excluded program should come back up for
# re-screening (e.g. a reward-increase campaign). Matched against the diffed
# field's name, since A8's own field/heading names aren't fixed.
REWARD_RELATED_KEYWORDS = ["報酬", "reward", "epc", "conversion_rate", "キャンペーン", "campaign"]


def _is_reward_related(field_name: str) -> bool:
    lowered = field_name.lower()
    return any(kw.lower() in lowered for kw in REWARD_RELATED_KEYWORDS)


def build_review_queue(previous_snapshot: Dict[str, dict], diff: dict) -> List[dict]:
    """The AI-facing queue for this run: only programs that are new, changed,
    or -- if previously excluded -- have a reward-related change that could
    make them worth re-screening. Programs with no change this run, or that
    stay excluded with no reward-related change, are left out entirely, so
    the AI stages never have to re-look at the whole catalog.
    """
    queue: List[dict] = []

    for item in diff["new_items"]:
        queue.append({"program_id": item["program_id"], "name": item.get("name"), "reason": "new"})

    for item in diff["changed_items"]:
        pid = item["program_id"]
        was_excluded = bool(previous_snapshot.get(pid, {}).get("excluded"))
        changed_fields = list(item["changes"].keys())

        if not was_excluded:
            queue.append(
                {
                    "program_id": pid,
                    "name": item.get("name"),
                    "reason": "changed",
                    "changed_fields": changed_fields,
                }
            )
            continue

        reward_related = [f for f in changed_fields if _is_reward_related(f)]
        if reward_related:
            queue.append(
                {
                    "program_id": pid,
                    "name": item.get("name"),
                    "reason": "reactivated",
                    "changed_fields": changed_fields,
                    "reward_related_fields": reward_related,
                }
            )
        # else: still excluded, and the change wasn't reward-related -- skip.

    return queue


def save_review_queue(previous_snapshot: Dict[str, dict], diff: dict, path: str) -> List[dict]:
    queue = build_review_queue(previous_snapshot, diff)
    write_json(path, {"generated_at": iso_now(), "count": len(queue), "items": queue})
    return queue
