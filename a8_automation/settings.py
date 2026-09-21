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


def load_settings() -> Settings:
    data_dir = os.environ.get("A8_DATA_DIR", _p("data"))
    snapshot_dir = os.path.join(data_dir, "snapshots")
    return Settings(
        login_url=os.environ.get("A8_LOGIN_URL", "https://www.a8.net/"),
        browser_profile_dir=os.environ.get("BROWSER_PROFILE_DIR", _p("browser_profile")),
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
    )
