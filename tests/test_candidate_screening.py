import json

from a8_automation.candidate_screening import run_candidate_screening


def test_screens_population_tags_records_and_does_not_delete_anything(tmp_path):
    catalog = {
        "a": {"program_id": "a", "category": "回線", "備考": "SNSでの紹介OKです。"},
        "b": {"program_id": "b", "category": "ギャンブル"},
        "c": {"program_id": "c", "category": "回線", "備考": "SNSアカウントの投稿について、事前にご相談ください。"},
        "d": {"program_id": "d", "category": "回線"},  # not in population -- must stay untouched
    }
    snapshot_path = str(tmp_path / "latest.json")
    report_path = str(tmp_path / "report.json")

    report = run_candidate_screening(catalog, ["a", "b", "c"], snapshot_path, report_path)

    assert report["population_total"] == 3
    assert report["excluded_clear_count"] == 1
    assert report["eligible_clear_count"] == 1
    assert report["needs_language_review_count"] == 1

    saved = json.load(open(snapshot_path, encoding="utf-8"))
    assert len(saved) == 4  # nothing deleted, "d" still present
    assert saved["a"]["screening_status"] == "eligible_clear"
    assert saved["b"]["screening_status"] == "excluded_clear"
    assert saved["c"]["screening_status"] == "needs_language_review"
    assert "screened_at" in saved["a"]
    assert "screening_status" not in saved["d"]  # untouched, not in population


def test_reason_breakdowns_are_recorded(tmp_path):
    catalog = {
        "a": {"program_id": "a", "category": "ギャンブル"},
        "b": {"program_id": "b", "category": "アダルト"},
    }
    snapshot_path = str(tmp_path / "latest.json")
    report_path = str(tmp_path / "report.json")

    report = run_candidate_screening(catalog, ["a", "b"], snapshot_path, report_path)

    assert sum(report["exclusion_reason_counts"].values()) == 2
    saved_report = json.load(open(report_path, encoding="utf-8"))
    assert saved_report["excluded_clear_count"] == 2
