from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Tuple
from urllib.parse import urlsplit

DEFAULT_STATIC_ASSET_EXTENSIONS = [
    "css",
    "js",
    "mjs",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "svg",
    "ico",
    "woff",
    "woff2",
    "ttf",
    "json",
]


@dataclass
class AllowlistConfig:
    allowed_hosts: set
    static_asset_hosts: set
    allowed_methods: set
    static_asset_path_pattern: re.Pattern
    csv_download_path_patterns: List[re.Pattern]
    blocked_path_patterns: List[re.Pattern]
    blocked_tracking_host_patterns: List[re.Pattern]

    @classmethod
    def load(cls, path: str) -> "AllowlistConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        extensions = raw.get("static_asset_extensions", DEFAULT_STATIC_ASSET_EXTENSIONS)
        static_asset_pattern = re.compile(r"\.(" + "|".join(re.escape(e) for e in extensions) + r")$")
        return cls(
            allowed_hosts=set(raw.get("allowed_hosts", [])),
            static_asset_hosts=set(raw.get("static_asset_hosts", [])),
            allowed_methods={m.upper() for m in raw.get("allowed_methods", ["GET", "HEAD"])},
            static_asset_path_pattern=static_asset_pattern,
            csv_download_path_patterns=[re.compile(p) for p in raw.get("csv_download_path_patterns", [])],
            blocked_path_patterns=[re.compile(p, re.IGNORECASE) for p in raw.get("blocked_path_patterns", [])],
            blocked_tracking_host_patterns=[re.compile(p) for p in raw.get("blocked_tracking_hosts", [])],
        )


def decide(url: str, method: str, cfg: AllowlistConfig) -> Tuple[bool, str]:
    """Default-deny allowlist check, in two tiers:

    - `allowed_hosts` (the A8 management console itself): any GET/HEAD path is
      allowed by default, since it's a JS-driven app that calls many of its
      own endpoints just to render a page. `blocked_path_patterns` (apply/
      cancel/settings-change etc.) is checked first and always wins, so a
      dangerous path is blocked even though the host is trusted.
    - `static_asset_hosts` (A8's own CDN/asset domains, e.g. the S3 bucket
      serving the console's CSS/JS/images): only GET/HEAD requests for
      recognized static file extensions are allowed. No API/data paths.

    Hard blocks (tracking hosts, dangerous paths) are checked before
    anything else, so a misconfigured allow entry can never override them.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    path = parts.path or "/"
    method = method.upper()

    for pat in cfg.blocked_tracking_host_patterns:
        if pat.search(host):
            return False, f"blocked_tracking_host:{host}"

    is_primary_host = host in cfg.allowed_hosts
    is_static_host = host in cfg.static_asset_hosts

    if not is_primary_host and not is_static_host:
        return False, f"host_not_allowlisted:{host}"

    for pat in cfg.blocked_path_patterns:
        if pat.search(path):
            return False, f"blocked_path:{path}"

    if is_static_host:
        if method not in {"GET", "HEAD"}:
            return False, f"method_not_allowed:{method}"
        if cfg.static_asset_path_pattern.search(path):
            return True, "static_asset"
        return False, "not_in_allowlist"

    is_csv = any(pat.search(path) for pat in cfg.csv_download_path_patterns)

    if method not in cfg.allowed_methods and not (is_csv and method == "POST"):
        return False, f"method_not_allowed:{method}"

    if is_csv:
        return True, "csv_download"

    return True, "allowed_browse_page"
