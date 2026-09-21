import re

from a8_automation.allowlist import AllowlistConfig, decide


def make_cfg(**overrides):
    defaults = dict(
        allowed_hosts={"pub.a8.net"},
        allowed_methods={"GET", "HEAD"},
        allowed_path_patterns=[re.compile(r"^/a8v2/asx/search")],
        csv_download_path_patterns=[re.compile(r"csv")],
        blocked_path_patterns=[re.compile(r"apply|cancel|setting", re.IGNORECASE)],
        blocked_tracking_host_patterns=[re.compile(r"^px\.a8\.net$"), re.compile(r"^www\d+\.a8\.net$")],
    )
    defaults.update(overrides)
    return AllowlistConfig(**defaults)


def test_allows_configured_browse_page():
    cfg = make_cfg()
    allowed, reason = decide("https://pub.a8.net/a8v2/asx/search?x=1", "GET", cfg)
    assert allowed
    assert reason == "allowed_browse_page"


def test_blocks_tracking_domain_even_if_path_looks_safe():
    cfg = make_cfg()
    allowed, reason = decide("https://px.a8.net/svt/ejp?a8mat=xxx", "GET", cfg)
    assert not allowed
    assert "blocked_tracking_host" in reason


def test_blocks_tracking_subdomain_used_for_click_redirects():
    cfg = make_cfg()
    allowed, reason = decide("https://www08.a8.net/0/0", "GET", cfg)
    assert not allowed
    assert "blocked_tracking_host" in reason


def test_blocks_non_allowlisted_host():
    cfg = make_cfg()
    allowed, reason = decide("https://evil.example.com/a8v2/asx/search", "GET", cfg)
    assert not allowed
    assert reason.startswith("host_not_allowlisted")


def test_blocks_apply_path_even_with_get():
    cfg = make_cfg()
    allowed, reason = decide("https://pub.a8.net/a8v2/asx/apply/123", "GET", cfg)
    assert not allowed
    assert reason.startswith("blocked_path")


def test_blocks_post_to_settings():
    cfg = make_cfg()
    allowed, reason = decide("https://pub.a8.net/a8v2/asx/setting/update", "POST", cfg)
    assert not allowed


def test_blocks_generic_post_to_otherwise_allowed_page():
    cfg = make_cfg()
    allowed, reason = decide("https://pub.a8.net/a8v2/asx/search", "POST", cfg)
    assert not allowed
    assert reason.startswith("method_not_allowed")


def test_allows_csv_download_via_post():
    cfg = make_cfg()
    allowed, reason = decide("https://pub.a8.net/a8v2/asx/csv_export", "POST", cfg)
    assert allowed
    assert reason == "csv_download"


def test_default_deny_when_no_pattern_matches():
    cfg = make_cfg()
    allowed, reason = decide("https://pub.a8.net/a8v2/mypage/top", "GET", cfg)
    assert not allowed
    assert reason == "not_in_allowlist"
