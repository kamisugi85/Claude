from __future__ import annotations


class AnomalyDetected(Exception):
    """Base class for conditions that must stop the run immediately, with no retry."""

    kind = "anomaly"


class SessionExpiredError(AnomalyDetected):
    kind = "session_expired"


class CaptchaOrMfaRequiredError(AnomalyDetected):
    kind = "captcha_or_mfa_required"


class UnexpectedNavigationError(AnomalyDetected):
    kind = "unexpected_navigation"


class ConsecutiveHttpErrorError(AnomalyDetected):
    kind = "consecutive_http_errors"
