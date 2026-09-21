import re

import pytest

from a8_automation.errors import CaptchaOrMfaRequiredError, SessionExpiredError, UnexpectedNavigationError
from a8_automation.page_checks import check_page_state


class FakePage:
    def __init__(self, url, content="<html></html>", has_recaptcha=False):
        self.url = url
        self._content = content
        self._has_recaptcha = has_recaptcha

    def query_selector(self, selector):
        if "recaptcha" in selector and self._has_recaptcha:
            return object()
        return None

    def content(self):
        return self._content


EXPECTED = re.compile(r"^/program/search/keyword")


def test_no_anomaly_on_expected_page_even_with_suspicious_keyword_in_ad_copy():
    # A real program on the page happens to be selling "SMS認証" services --
    # must not be mistaken for the site challenging us.
    page = FakePage(
        "https://media-console.a8.net/program/search/keyword?pageNo=147",
        content="<html>...SMS認証サービスの広告...</html>",
    )
    check_page_state(page, EXPECTED, "step")  # should not raise


def test_raises_captcha_when_recaptcha_widget_present_even_on_expected_page():
    page = FakePage("https://media-console.a8.net/program/search/keyword?pageNo=1", has_recaptcha=True)
    with pytest.raises(CaptchaOrMfaRequiredError):
        check_page_state(page, EXPECTED, "step")


def test_raises_unexpected_navigation_when_path_mismatch_without_keywords():
    page = FakePage("https://media-console.a8.net/some/other/page", content="<html>normal content</html>")
    with pytest.raises(UnexpectedNavigationError):
        check_page_state(page, EXPECTED, "step")


def test_raises_captcha_when_path_mismatch_and_keyword_present():
    page = FakePage(
        "https://media-console.a8.net/common/re-authentication?messageType=login_required",
        content="<html>SMS認証コードを入力してください</html>",
    )
    with pytest.raises(CaptchaOrMfaRequiredError):
        check_page_state(page, EXPECTED, "step")


def test_raises_session_expired_on_login_redirect():
    page = FakePage("https://www.a8.net/a8v2/login")
    with pytest.raises(SessionExpiredError):
        check_page_state(page, EXPECTED, "step")
