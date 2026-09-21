import json
import re

from a8_automation.http_guard import HttpErrorStreakGuard
from a8_automation.targeted_detail_fetch import load_progress, run_targeted_detail_fetch

DETAIL_PATTERN = re.compile(r"^/program/detail")


class FakeSettings:
    def __init__(self, tmp_path):
        self.request_timeout_ms = 5000
        self.latest_snapshot_path = str(tmp_path / "latest.json")
        self.detail_fetch_progress_path = str(tmp_path / "progress.json")


class FakePage:
    def __init__(self):
        self.url = ""
        self.visited = []

    def goto(self, url, timeout=None):
        self.url = url
        self.visited.append(url)

    def wait_for_load_state(self, state, timeout=None):
        pass

    def query_selector(self, selector):
        return None

    def content(self):
        return "<html></html>"

    def evaluate(self, script):
        return {"成果条件": "WEB申込完了"}


class FakeLogger:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


def _write_catalog(settings, catalog):
    with open(settings.latest_snapshot_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False)


def _list_only(pid):
    return {
        "program_id": pid,
        "detail_url": f"https://media-console.a8.net/program/detail-partnered?programId={pid}",
        "name": "n",
        "reward": "r",
        "epc": "e",
        "conversion_rate": "c",
        "category": "cat",
        "start_date": "d",
        "checked_at": "t",
    }


def test_fetches_up_to_batch_size_and_updates_catalog(tmp_path):
    settings = FakeSettings(tmp_path)
    catalog = {"a": _list_only("a"), "b": _list_only("b"), "c": _list_only("c")}
    _write_catalog(settings, catalog)

    result = run_targeted_detail_fetch(
        FakePage(), settings, HttpErrorStreakGuard(), FakeLogger(), ["a", "b", "c"], batch_size=2, detail_pattern=DETAIL_PATTERN
    )

    assert result["succeeded"] == ["a", "b"]
    assert result["anomaly"] is None

    updated = json.load(open(settings.latest_snapshot_path, encoding="utf-8"))
    assert updated["a"]["成果条件"] == "WEB申込完了"
    assert "成果条件" not in updated["c"]


def test_skips_already_detailed_without_refetching(tmp_path):
    settings = FakeSettings(tmp_path)
    already = {**_list_only("a"), "成果条件": "既存データ"}
    catalog = {"a": already, "b": _list_only("b")}
    _write_catalog(settings, catalog)

    result = run_targeted_detail_fetch(
        FakePage(), settings, HttpErrorStreakGuard(), FakeLogger(), ["a", "b"], batch_size=5, detail_pattern=DETAIL_PATTERN
    )

    assert result["succeeded"] == ["b"]
    assert ("a", "already_detailed") in result["skipped"]


def test_skips_record_missing_detail_url(tmp_path):
    settings = FakeSettings(tmp_path)
    no_url = {k: v for k, v in _list_only("a").items() if k != "detail_url"}
    catalog = {"a": no_url}
    _write_catalog(settings, catalog)

    result = run_targeted_detail_fetch(
        FakePage(), settings, HttpErrorStreakGuard(), FakeLogger(), ["a"], batch_size=5, detail_pattern=DETAIL_PATTERN
    )

    assert result["succeeded"] == []
    assert ("a", "no_detail_url") in result["skipped"]


def test_progress_persists_and_resumes_across_calls(tmp_path):
    settings = FakeSettings(tmp_path)
    catalog = {"a": _list_only("a"), "b": _list_only("b")}
    _write_catalog(settings, catalog)

    run_targeted_detail_fetch(
        FakePage(), settings, HttpErrorStreakGuard(), FakeLogger(), ["a", "b"], batch_size=1, detail_pattern=DETAIL_PATTERN
    )
    assert load_progress(settings.detail_fetch_progress_path) == {"a"}

    result2 = run_targeted_detail_fetch(
        FakePage(), settings, HttpErrorStreakGuard(), FakeLogger(), ["a", "b"], batch_size=1, detail_pattern=DETAIL_PATTERN
    )
    assert result2["succeeded"] == ["b"]
    assert load_progress(settings.detail_fetch_progress_path) == {"a", "b"}


def test_anomaly_stops_batch_immediately_no_retry(tmp_path):
    settings = FakeSettings(tmp_path)
    # detail_url path won't match DETAIL_PATTERN -> UnexpectedNavigationError
    bad = {**_list_only("a"), "detail_url": "https://media-console.a8.net/somewhere/else"}
    catalog = {"a": bad, "b": _list_only("b")}
    _write_catalog(settings, catalog)

    result = run_targeted_detail_fetch(
        FakePage(), settings, HttpErrorStreakGuard(), FakeLogger(), ["a", "b"], batch_size=5, detail_pattern=DETAIL_PATTERN
    )

    assert result["succeeded"] == []
    assert result["anomaly"] is not None
    # must not have gone on to attempt "b" after the anomaly
    updated = json.load(open(settings.latest_snapshot_path, encoding="utf-8"))
    assert "成果条件" not in updated["b"]
