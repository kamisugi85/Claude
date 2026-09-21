from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Tuple
from urllib.parse import urlsplit


@dataclass
class AllowlistConfig:
    allowed_hosts: set
    allowed_methods: set
    allowed_path_patterns: List[re.Pattern]
    csv_download_path_patterns: List[re.Pattern]
    blocked_path_patterns: List[re.Pattern]
    blocked_tracking_host_patterns: List[re.Pattern]

    @classmethod
    def load(cls, path: str) -> "AllowlistConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls(
            allowed_hosts=set(raw.get("allowed_hosts", [])),
            allowed_methods={m.upper() for m in raw.get("allowed_methods", ["GET", "HEAD"])},
            allowed_path_patterns=[re.compile(p) for p in raw.get("allowed_path_patterns", [])],
            csv_download_path_patterns=[re.compile(p) for p in raw.get("csv_download_path_patterns", [])],
            blocked_path_patterns=[re.compile(p, re.IGNORECASE) for p in raw.get("blocked_path_patterns", [])],
            blocked_tracking_host_patterns=[re.compile(p) for p in raw.get("blocked_tracking_hosts", [])],
        )


def decide(url: str, method: str, cfg: AllowlistConfig) -> Tuple[bool, str]:
    """Default-deny allowlist check. Hard blocks (tracking hosts, apply/cancel/
    settings-change paths) are checked before anything else, so a misconfigured
    allow pattern can never override them.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    path = parts.path or "/"
    method = method.upper()

    for pat in cfg.blocked_tracking_host_patterns:
        if pat.search(host):
            return False, f"blocked_tracking_host:{host}"

    if host not in cfg.allowed_hosts:
        return False, f"host_not_allowlisted:{host}"

    for pat in cfg.blocked_path_patterns:
        if pat.search(path):
            return False, f"blocked_path:{path}"

    is_csv = any(pat.search(path) for pat in cfg.csv_download_path_patterns)

    if method not in cfg.allowed_methods and not (is_csv and method == "POST"):
        return False, f"method_not_allowed:{method}"

    if is_csv:
        return True, "csv_download"

    for pat in cfg.allowed_path_patterns:
        if pat.search(path):
            return True, "allowed_browse_page"

    return False, "not_in_allowlist"
