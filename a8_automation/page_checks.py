from __future__ import annotations

import re
from typing import Optional

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

    if expected_path_pattern is not None and not expected_path_pattern.search(current_url):
        raise UnexpectedNavigationError(
            f"step '{step_name}': expected URL pattern '{expected_path_pattern.pattern}', got '{current_url}'"
        )

    content_lower = page.content().lower()
    for kw in CAPTCHA_INDICATORS + MFA_INDICATORS:
        if kw.lower() in content_lower:
            raise CaptchaOrMfaRequiredError(f"step '{step_name}': suspicious indicator '{kw}' found at {current_url}")
