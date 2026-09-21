from __future__ import annotations

from .errors import ConsecutiveHttpErrorError


class HttpErrorStreakGuard:
    """Trips when `threshold` HTTP responses in a row come back >=400.
    The orchestrator calls `check()` after every step and aborts (no retry)
    the moment it trips.
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.consecutive_errors = 0
        self.tripped = False
        self.last_status = None
        self.last_url = None

    def on_response(self, response) -> None:
        try:
            status = response.status
            url = response.url
        except Exception:
            return

        if status >= 400:
            self.consecutive_errors += 1
            self.last_status = status
            self.last_url = url
            if self.consecutive_errors >= self.threshold:
                self.tripped = True
        else:
            self.consecutive_errors = 0

    def check(self) -> None:
        if self.tripped:
            raise ConsecutiveHttpErrorError(
                f"{self.consecutive_errors} consecutive HTTP errors "
                f"(last status={self.last_status}, url={self.last_url})"
            )
