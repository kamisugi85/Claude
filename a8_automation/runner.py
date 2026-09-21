from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

from .access_log import AccessLog
from .allowlist import AllowlistConfig, decide
from .alert import write_alert
from .diff_store import compute_diff, load_snapshot, promote_snapshot, save_diff
from .errors import AnomalyDetected
from .http_guard import HttpErrorStreakGuard
from .logging_setup import setup_logging
from .page_checks import check_page_state
from .scraper import download_csv, extract_programs, load_csv_column_map, load_targets, parse_csv
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
            for target in targets:
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

            previous_snapshot = load_snapshot(settings.latest_snapshot_path)
            diff = compute_diff(previous_snapshot, current_snapshot)
            save_diff(diff, f"{settings.diff_dir}/diff-{run_id}.json")
            promote_snapshot(current_snapshot, settings.latest_snapshot_path)

            logger.info(
                "run completed: new=%d changed=%d total_records=%d",
                diff["new_count"],
                diff["changed_count"],
                len(current_snapshot),
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
