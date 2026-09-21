from __future__ import annotations

from collections import Counter
from typing import List
from urllib.parse import urlsplit

from .utils import iso_now


class AccessLog:
    """Tracks every allowed/blocked request so a run's URL list and counts
    can be written out to the execution log.
    """

    def __init__(self):
        self.allowed_counts: Counter = Counter()
        self.blocked_events: List[dict] = []

    def record_allowed(self, url: str, method: str) -> None:
        self.allowed_counts[self._key(url, method)] += 1

    def record_blocked(self, url: str, method: str, reason: str) -> None:
        self.blocked_events.append({"url": url, "method": method, "reason": reason, "ts": iso_now()})

    @staticmethod
    def _key(url: str, method: str) -> str:
        parts = urlsplit(url)
        return f"{method} {parts.scheme}://{parts.netloc}{parts.path}"

    def summary(self) -> dict:
        return {
            "allowed_url_counts": dict(sorted(self.allowed_counts.items())),
            "allowed_total": sum(self.allowed_counts.values()),
            "blocked_total": len(self.blocked_events),
            "blocked_events": self.blocked_events,
        }
