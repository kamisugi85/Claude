from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

from .errors import CaptchaOrMfaRequiredError, SessionExpiredError, UnexpectedNavigationError

LOGIN_URL_PATTERN = re.compile(r"/login|/signin|/a8v2/login", re.IGNORECASE)

CAPTCHA_INDICATORS = [
    "recaptcha",
    "g-recaptcha",
    "captcha",
    "画像認証",
    "セキュリティコード",
    "文字を入力してください",
]
MFA_INDICATORS = [
    "ワンタイムパスワード",
    "認証コードを入力",
    "二段階認証",
    "2段階認証",
    "sms認証",
    "確認コードを入力",
]


def check_page_state(
    page,
    expected_path_pattern: Optional[re.Pattern],
    step_name: str,
    allow_login_redirect: bool = False,
) -> None:
    """Raises immediately (no retry) on any of the anomaly conditions this
    project must stop for: session expiry, CAPTCHA/2FA, or an unexpected
    page transition.
    """
    current_url = page.url

    if not allow_login_redirect and LOGIN_URL_PATTERN.search(current_url):
        raise SessionExpiredError(f"step '{step_name}': redirected to login page ({current_url})")

    # A real reCAPTCHA widget is a structural, reliable signal regardless of
    # which page we're on -- it won't show up incidentally in ad copy.
    if page.query_selector("iframe[src*='recaptcha'], .g-recaptcha"):
        raise CaptchaOrMfaRequiredError(f"step '{step_name}': reCAPTCHA widget detected at {current_url}")

    current_path = urlsplit(current_url).path or "/"
    path_mismatch = expected_path_pattern is not None and not expected_path_pattern.search(current_path)

    if path_mismatch:
        # Only scan page text for CAPTCHA/MFA wording once we've already
        # landed somewhere unexpected. A8 lists thousands of advertisers
        # across every industry (including identity-verification and SMS
        # services), so bare keyword matching on an *expected* page is prone
        # to false positives -- e.g. an ad mentioning "SMS認証" as its own
        # product tripped this on an ordinary program search results page.
        # Every real re-authentication observed so far also changed the URL
        # path, so gating the text scan on path_mismatch keeps the reliable
        # signal (navigation went somewhere it shouldn't) while still telling
        # session-expiry apart from CAPTCHA/MFA once we're already off-track.
        content_lower = page.content().lower()
        for kw in CAPTCHA_INDICATORS + MFA_INDICATORS:
            if kw.lower() in content_lower:
                raise CaptchaOrMfaRequiredError(
                    f"step '{step_name}': suspicious indicator '{kw}' found at {current_url}"
                )
        raise UnexpectedNavigationError(
            f"step '{step_name}': expected URL path pattern '{expected_path_pattern.pattern}', got '{current_url}'"
        )
