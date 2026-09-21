from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _p(*parts: str) -> str:
    return os.path.join(BASE_DIR, *parts)


@dataclass(frozen=True)
class Settings:
    login_url: str
    browser_profile_dir: str
    storage_state_path: str
    allowlist_config_path: str
    targets_config_path: str
    csv_column_map_path: str
    data_dir: str
    log_dir: str
    diff_dir: str
    download_dir: str
    snapshot_dir: str
    latest_snapshot_path: str
    alert_json_path: str
    max_consecutive_http_errors: int
    request_timeout_ms: int
    headless: bool
    crawl_state_path: str
    shortlist_path: str
    review_queue_path: str
    ai_candidates_path: str
    excluded_by_rules_path: str
    candidates_export_path: str
    detail_fetch_plan_path: str
    detail_fetch_progress_path: str
    candidate_screening_report_path: str
    conversion_action_diagnostic_path: str
    ai_review_selection_path: str
    ai_review_export_path: str
    google_drive_shared_folder_dir: str


def load_settings() -> Settings:
    data_dir = os.environ.get("A8_DATA_DIR", _p("data"))
    snapshot_dir = os.path.join(data_dir, "snapshots")
    browser_profile_dir = os.environ.get("BROWSER_PROFILE_DIR", _p("browser_profile"))
    return Settings(
        login_url=os.environ.get("A8_LOGIN_URL", "https://www.a8.net/"),
        browser_profile_dir=browser_profile_dir,
        storage_state_path=os.environ.get(
            "A8_STORAGE_STATE_PATH", os.path.join(browser_profile_dir, "storage_state.json")
        ),
        allowlist_config_path=os.environ.get("A8_ALLOWLIST_CONFIG", _p("config", "allowlist.json")),
        targets_config_path=os.environ.get("A8_TARGETS_CONFIG", _p("config", "targets.json")),
        csv_column_map_path=os.environ.get("A8_CSV_COLUMN_MAP", _p("config", "csv_column_map.json")),
        data_dir=data_dir,
        log_dir=os.path.join(data_dir, "logs"),
        diff_dir=os.path.join(data_dir, "diffs"),
        download_dir=os.path.join(data_dir, "downloads"),
        snapshot_dir=snapshot_dir,
        latest_snapshot_path=os.path.join(snapshot_dir, "latest.json"),
        alert_json_path=os.environ.get("A8_ALERT_JSON", os.path.join(data_dir, "state", "alert.json")),
        max_consecutive_http_errors=int(os.environ.get("A8_MAX_CONSECUTIVE_HTTP_ERRORS", "3")),
        request_timeout_ms=int(os.environ.get("A8_REQUEST_TIMEOUT_MS", "30000")),
        headless=os.environ.get("A8_HEADLESS", "true").strip().lower() != "false",
        crawl_state_path=os.environ.get("A8_CRAWL_STATE_PATH", os.path.join(data_dir, "state", "crawl_progress.json")),
        shortlist_path=os.environ.get("A8_SHORTLIST_PATH", os.path.join(data_dir, "state", "shortlist.json")),
        review_queue_path=os.environ.get(
            "A8_REVIEW_QUEUE_PATH", os.path.join(data_dir, "state", "needs_review.json")
        ),
        ai_candidates_path=os.environ.get(
            "A8_AI_CANDIDATES_PATH", os.path.join(data_dir, "state", "ai_candidates.json")
        ),
        excluded_by_rules_path=os.environ.get(
            "A8_EXCLUDED_BY_RULES_PATH", os.path.join(data_dir, "state", "excluded_by_rules.json")
        ),
        # 既定はローカル出力のみ。Google Drive for Desktop等の同期フォルダへの
        # 配置は、このファイルを配布先にコピーする形で行う(Drive API等は使わない)。
        candidates_export_path=os.environ.get(
            "A8_CANDIDATES_EXPORT_PATH", os.path.join(data_dir, "state", "a8_candidates_latest.json")
        ),
        detail_fetch_plan_path=os.environ.get(
            "A8_DETAIL_FETCH_PLAN_PATH", os.path.join(data_dir, "state", "detail_fetch_plan.json")
        ),
        detail_fetch_progress_path=os.environ.get(
            "A8_DETAIL_FETCH_PROGRESS_PATH", os.path.join(data_dir, "state", "detail_fetch_progress.json")
        ),
        candidate_screening_report_path=os.environ.get(
            "A8_CANDIDATE_SCREENING_REPORT_PATH", os.path.join(data_dir, "state", "candidate_screening.json")
        ),
        conversion_action_diagnostic_path=os.environ.get(
            "A8_CONVERSION_ACTION_DIAGNOSTIC_PATH",
            os.path.join(data_dir, "state", "conversion_action_diagnostic.json"),
        ),
        ai_review_selection_path=os.environ.get(
            "A8_AI_REVIEW_SELECTION_PATH", os.path.join(data_dir, "state", "ai_review_selection.json")
        ),
        # ファイル名はGoogle Drive共通フォルダへコピーする際にそのまま使う名前に
        # 合わせてある(コピー時にリネーム不要にするため)。Drive API等は使わず、
        # Google Drive for Desktop等の同期フォルダへの配置は手動コピーで行う。
        ai_review_export_path=os.environ.get(
            "A8_AI_REVIEW_EXPORT_PATH",
            os.path.join(data_dir, "state", "a8_ai_review_selection_60_latest.json"),
        ),
        # Google Drive for Desktopがローカルに同期している「A8_TikTok_PoC」共通
        # フォルダのパス。Drive APIは使わず、このフォルダへのファイルコピーだけで
        # 共有する(Google Drive for Desktop自体が同期を担う)。
        google_drive_shared_folder_dir=os.environ.get(
            "A8_GOOGLE_DRIVE_SHARED_FOLDER_DIR", r"G:\マイドライブ\A8_TikTok_PoC"
        ),
    )
