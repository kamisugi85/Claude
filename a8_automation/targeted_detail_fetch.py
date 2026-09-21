from __future__ import annotations

from typing import List, Optional

from .diff_store import load_snapshot, promote_snapshot
from .errors import AnomalyDetected
from .page_checks import check_page_state
from .runner import _wait_for_render
from .scraper import extract_program_detail, has_detail_fields
from .utils import iso_now, read_json, write_json


def load_progress(path: str) -> set:
    data = read_json(path, default={"completed_ids": []})
    return set(data.get("completed_ids", []))


def save_progress(path: str, completed_ids: set) -> None:
    write_json(path, {"updated_at": iso_now(), "completed_ids": sorted(completed_ids)})


def run_targeted_detail_fetch(
    page, settings, http_guard, logger, candidates: List[str], batch_size: int, detail_pattern
) -> dict:
    """あらかじめ選定済みの候補(candidates, 優先順位順)のうち、まだ完了して
    いないものから最大 batch_size 件だけ詳細ページを取得する。一覧ページの
    再巡回は行わない。異常を検知したら即座に停止し(リトライなし)、それまでの
    結果を返す。
    """
    completed = load_progress(settings.detail_fetch_progress_path)
    catalog = load_snapshot(settings.latest_snapshot_path)

    pending = [pid for pid in candidates if pid not in completed]

    attempted = 0
    succeeded: List[str] = []
    skipped: List[tuple] = []
    anomaly: Optional[AnomalyDetected] = None

    for pid in pending:
        if len(succeeded) >= batch_size:
            break

        record = catalog.get(pid)
        if record is None:
            skipped.append((pid, "not_in_catalog"))
            continue
        if has_detail_fields(record):
            skipped.append((pid, "already_detailed"))
            completed.add(pid)
            continue
        detail_url = record.get("detail_url")
        if not detail_url:
            skipped.append((pid, "no_detail_url"))
            continue

        attempted += 1
        step_name = f"targeted_detail_fetch:{pid}"
        logger.info("step start: %s -> %s", step_name, detail_url)

        try:
            page.goto(detail_url, timeout=settings.request_timeout_ms)
            _wait_for_render(page, settings)
            http_guard.check()
            check_page_state(page, detail_pattern, step_name)

            detail_fields = extract_program_detail(page, pid, detail_url)
            merged_record = {**record, **detail_fields}

            on_disk = load_snapshot(settings.latest_snapshot_path)
            on_disk[pid] = merged_record
            promote_snapshot(on_disk, settings.latest_snapshot_path)
            catalog[pid] = merged_record

            http_guard.check()
            succeeded.append(pid)
            completed.add(pid)
            logger.info("step done: %s", step_name)
        except AnomalyDetected as e:
            anomaly = e
            logger.error("ANOMALY DETECTED (%s): %s -- stopping immediately, no retry.", e.kind, e)
            break

    save_progress(settings.detail_fetch_progress_path, completed)

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "skipped": skipped,
        "anomaly": anomaly,
        "candidates_total": len(candidates),
        "pending_before": len(pending),
    }
