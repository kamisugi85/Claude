from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

from .access_log import AccessLog
from .allowlist import AllowlistConfig, decide
from .alert import write_alert
from .crawl_state import load_crawl_state, plan_pages, save_crawl_state
from .diff_store import compute_diff, load_snapshot, promote_snapshot, save_diff
from .errors import AnomalyDetected
from .exclusion import apply_exclusion
from .http_guard import HttpErrorStreakGuard
from .logging_setup import setup_logging
from .page_checks import check_page_state
from .review_queue import save_review_queue
from .scoring import save_shortlist
from .scraper import (
    download_csv,
    extract_program_detail,
    extract_programs,
    extract_search_results,
    load_csv_column_map,
    load_targets,
    parse_csv,
)
from .settings import Settings
from .utils import run_timestamp, write_json


def install_allowlist_router(context, cfg: AllowlistConfig, access_log: AccessLog, logger) -> None:
    def handler(route):
        request = route.request
        allowed, reason = decide(request.url, request.method, cfg)
        if allowed:
            access_log.record_allowed(request.url, request.method)
            route.continue_()
        else:
            access_log.record_blocked(request.url, request.method, reason)
            logger.warning("blocked request: %s %s (%s)", request.method, request.url, reason)
            route.abort("blockedbyclient")

    context.route("**/*", handler)


def _wait_for_render(page, settings) -> None:
    """media-console.a8.net is a JS-driven SPA that fetches its content after
    the initial page load fires, so reading the DOM right after goto() can
    catch it mid-render. Give it a short grace period; a timeout here just
    means the page was already idle (or is unusually chatty), not a failure,
    so it's never treated as an anomaly.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=min(settings.request_timeout_ms, 10000))
    except Exception:
        pass


def run_search_crawl(page, target, settings, http_guard, logger, previous_snapshot) -> dict:
    """Crawls a bounded slice of the search-results pages each run (resuming
    from where the last run left off, wrapping back to page 1 once the whole
    catalog has been swept), then fetches full detail pages only for programs
    that are new this run -- keeps daily load small regardless of catalog size.
    """
    state = load_crawl_state(settings.crawl_state_path)
    pages_per_run = target.get("pages_per_run", 20)
    page_size = target.get("page_size", 20)
    page_numbers = plan_pages(state, pages_per_run)

    records: dict = {}
    total_pages = state.get("total_pages")

    for page_no in page_numbers:
        url = target["url_template"].format(page=page_no)
        step_name = f"{target['name']}#page{page_no}"
        logger.info("step start: %s -> %s", step_name, url)

        page.goto(url, timeout=settings.request_timeout_ms)
        _wait_for_render(page, settings)
        http_guard.check()
        check_page_state(page, target.get("expected_path_pattern"), step_name)

        page_records, total_count = extract_search_results(page)
        records.update(page_records)

        # Flush to disk after every page, not just at the end of the run: if a
        # later page trips an anomaly, pages already scraped this run stay
        # saved instead of being lost along with the in-memory dict.
        on_disk = load_snapshot(settings.latest_snapshot_path)
        promote_snapshot({**on_disk, **page_records}, settings.latest_snapshot_path)

        if total_count:
            total_pages = -(-total_count // page_size)  # ceil division

        save_crawl_state(settings.crawl_state_path, page_no, total_pages)
        http_guard.check()
        logger.info("step done: %s (records this page: %d)", step_name, len(page_records))

    new_ids = [pid for pid in records if pid not in previous_snapshot]
    detail_limit = target.get("detail_fetch_limit", 20)
    detail_pattern = target.get("detail_expected_path_pattern")

    for pid in new_ids[:detail_limit]:
        detail_url = records[pid].get("detail_url")
        if not detail_url:
            continue
        step_name = f"{target['name']}#detail:{pid}"
        logger.info("step start: %s -> %s", step_name, detail_url)

        page.goto(detail_url, timeout=settings.request_timeout_ms)
        _wait_for_render(page, settings)
        http_guard.check()
        check_page_state(page, detail_pattern, step_name)

        detail_fields = extract_program_detail(page, pid, detail_url)
        records[pid] = {**records[pid], **detail_fields}
        http_guard.check()
        logger.info("step done: %s", step_name)

    logger.info(
        "search_crawl summary: pages=%s records=%d new=%d detail_fetched=%d total_pages=%s",
        page_numbers,
        len(records),
        len(new_ids),
        min(len(new_ids), detail_limit),
        total_pages,
    )
    return records


def run(settings: Settings) -> int:
    run_id = run_timestamp()
    log_path = f"{settings.log_dir}/run-{run_id}.log"
    logger = setup_logging(log_path)

    if not os.path.exists(settings.storage_state_path):
        logger.error(
            "no saved session at %s -- run `login` first (scripts/login.sh or scripts/login.ps1).",
            settings.storage_state_path,
        )
        return 1

    allowlist_cfg = AllowlistConfig.load(settings.allowlist_config_path)
    targets = load_targets(settings.targets_config_path)
    column_map = load_csv_column_map(settings.csv_column_map_path)

    access_log = AccessLog()
    http_guard = HttpErrorStreakGuard(threshold=settings.max_consecutive_http_errors)
    current_snapshot: dict = {}
    exit_code = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.headless)
        context = browser.new_context(storage_state=settings.storage_state_path)
        install_allowlist_router(context, allowlist_cfg, access_log, logger)
        context.on("response", http_guard.on_response)
        page = context.new_page()

        try:
            previous_snapshot = load_snapshot(settings.latest_snapshot_path)

            for target in targets:
                if target["type"] == "search_crawl":
                    current_snapshot.update(
                        run_search_crawl(page, target, settings, http_guard, logger, previous_snapshot)
                    )
                    continue

                logger.info("step start: %s -> %s", target["name"], target["url"])
                page.goto(target["url"], timeout=settings.request_timeout_ms)
                http_guard.check()
                check_page_state(page, target.get("expected_path_pattern"), target["name"])

                if target["type"] == "browse":
                    current_snapshot.update(extract_programs(page))
                elif target["type"] == "csv":
                    csv_path = download_csv(page, target, settings.download_dir, run_id)
                    current_snapshot.update(parse_csv(csv_path, column_map))
                else:
                    raise ValueError(f"unknown target type: {target['type']}")

                http_guard.check()
                logger.info("step done: %s (records so far: %d)", target["name"], len(current_snapshot))

            diff = compute_diff(previous_snapshot, current_snapshot)
            save_diff(diff, f"{settings.diff_dir}/diff-{run_id}.json")

            # Python/rule-based exclusion, re-evaluated only for records touched
            # this run -- not the whole catalog. Excluded programs are kept
            # (never deleted), tagged with why and when.
            current_snapshot = {pid: apply_exclusion(record) for pid, record in current_snapshot.items()}

            # Only this run's crawled slice is in current_snapshot -- merge onto
            # the existing snapshot rather than replacing it, so programs from
            # earlier runs' pages aren't dropped from the catalog.
            merged_snapshot = {**previous_snapshot, **current_snapshot}
            promote_snapshot(merged_snapshot, settings.latest_snapshot_path)

            # AI-facing queue: only new/changed programs, plus previously-excluded
            # ones whose change looks reward-related (e.g. a reward-increase
            # campaign) -- never the whole catalog.
            review_queue = save_review_queue(previous_snapshot, diff, settings.review_queue_path)
            shortlist = save_shortlist(merged_snapshot, settings.shortlist_path)

            logger.info(
                "run completed: new=%d changed=%d records_this_run=%d catalog_total=%d "
                "needs_review=%d shortlist=%d",
                diff["new_count"],
                diff["changed_count"],
                len(current_snapshot),
                len(merged_snapshot),
                len(review_queue),
                len(shortlist),
            )

        except AnomalyDetected as e:
            logger.error("ANOMALY DETECTED (%s): %s -- stopping immediately, no retry.", e.kind, e)
            write_alert(
                settings.alert_json_path,
                kind=e.kind,
                message=str(e),
                context={"last_url": page.url, "run_id": run_id},
            )
            exit_code = 1
        except Exception:
            logger.exception("unexpected error -- stopping immediately, no retry.")
            write_alert(
                settings.alert_json_path,
                kind="unexpected_error",
                message="unexpected exception (see run log for traceback)",
                context={"last_url": page.url, "run_id": run_id},
            )
            exit_code = 1
        finally:
            summary = access_log.summary()
            summary["run_id"] = run_id
            write_json(f"{settings.log_dir}/run-{run_id}-access-summary.json", summary)
            logger.info(
                "access summary: allowed=%d blocked=%d (full URL list/counts in access-summary json)",
                summary["allowed_total"],
                summary["blocked_total"],
            )
            context.close()
            browser.close()

    return exit_code
