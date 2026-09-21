from a8_automation.diff_store import compute_diff
from a8_automation.review_queue import build_review_queue


def test_new_program_is_queued():
    previous = {}
    current = {"1": {"program_id": "1", "name": "Program A", "reward": "1000"}}
    diff = compute_diff(previous, current)
    queue = build_review_queue(previous, diff)
    assert len(queue) == 1
    assert queue[0]["reason"] == "new"
    assert queue[0]["program_id"] == "1"


def test_changed_non_excluded_program_is_queued():
    previous = {"1": {"program_id": "1", "name": "Program A", "reward": "1000"}}
    current = {"1": {"program_id": "1", "name": "Program A", "reward": "1500"}}
    diff = compute_diff(previous, current)
    queue = build_review_queue(previous, diff)
    assert len(queue) == 1
    assert queue[0]["reason"] == "changed"
    assert queue[0]["changed_fields"] == ["reward"]


def test_excluded_program_with_reward_change_is_reactivated():
    previous = {
        "1": {
            "program_id": "1",
            "name": "Program A",
            "reward": "1000",
            "excluded": True,
            "exclusion_reason": "status_keyword:募集停止",
        }
    }
    current = {"1": {"program_id": "1", "name": "Program A", "reward": "3000"}}
    diff = compute_diff(previous, current)
    queue = build_review_queue(previous, diff)
    assert len(queue) == 1
    assert queue[0]["reason"] == "reactivated"
    assert "reward" in queue[0]["reward_related_fields"]


def test_excluded_program_with_unrelated_change_is_not_queued():
    previous = {
        "1": {
            "program_id": "1",
            "name": "Program A",
            "備考": "old note",
            "excluded": True,
            "exclusion_reason": "status_keyword:募集停止",
        }
    }
    current = {"1": {"program_id": "1", "name": "Program A", "備考": "new note"}}
    diff = compute_diff(previous, current)
    queue = build_review_queue(previous, diff)
    assert queue == []


def test_unchanged_program_is_not_queued():
    previous = {"1": {"program_id": "1", "name": "Program A", "reward": "1000"}}
    current = {"1": {"program_id": "1", "name": "Program A", "reward": "1000"}}
    diff = compute_diff(previous, current)
    queue = build_review_queue(previous, diff)
    assert queue == []
