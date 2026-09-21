from a8_automation.diff_store import compute_diff


def test_detects_new_item():
    previous = {}
    current = {"1": {"program_id": "1", "name": "Program A", "reward": "1000"}}
    diff = compute_diff(previous, current)
    assert diff["new_count"] == 1
    assert diff["changed_count"] == 0
    assert diff["new_items"][0]["program_id"] == "1"


def test_detects_reward_change():
    previous = {"1": {"program_id": "1", "name": "Program A", "reward": "1000"}}
    current = {"1": {"program_id": "1", "name": "Program A", "reward": "1500"}}
    diff = compute_diff(previous, current)
    assert diff["new_count"] == 0
    assert diff["changed_count"] == 1
    changes = diff["changed_items"][0]["changes"]
    assert changes["reward"] == {"old": "1000", "new": "1500"}


def test_no_diff_when_unrelated_field_changes():
    previous = {"1": {"program_id": "1", "name": "Program A", "reward": "1000", "note": "x"}}
    current = {"1": {"program_id": "1", "name": "Program A", "reward": "1000", "note": "y"}}
    diff = compute_diff(previous, current)
    assert diff["changed_count"] == 0


def test_detects_multiple_condition_changes():
    previous = {
        "1": {
            "program_id": "1",
            "name": "Program A",
            "sns_condition": "OK",
            "approval_condition": "immediate",
            "prohibited_items": "none",
        }
    }
    current = {
        "1": {
            "program_id": "1",
            "name": "Program A",
            "sns_condition": "NG",
            "approval_condition": "manual review",
            "prohibited_items": "adult content",
        }
    }
    diff = compute_diff(previous, current)
    assert diff["changed_count"] == 1
    changes = diff["changed_items"][0]["changes"]
    assert set(changes.keys()) == {"sns_condition", "approval_condition", "prohibited_items"}


def test_removed_item_is_not_reported_as_changed():
    previous = {"1": {"program_id": "1", "name": "Program A", "reward": "1000"}}
    current = {}
    diff = compute_diff(previous, current)
    assert diff["new_count"] == 0
    assert diff["changed_count"] == 0
