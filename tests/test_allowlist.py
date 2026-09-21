import re

from a8_automation.allowlist import AllowlistConfig, decide


def make_cfg(**overrides):
    defaults = dict(
        allowed_hosts={"media-console.a8.net"},
        static_asset_hosts={"a8mc-public.s3.example.com", "support.a8.net"},
        allowed_methods={"GET", "HEAD"},
        static_asset_path_pattern=re.compile(r"\.(css|js|png|svg)$"),
        csv_download_path_patterns=[re.compile(r"csv")],
        blocked_path_patterns=[re.compile(r"apply|cancel|setting", re.IGNORECASE)],
        blocked_tracking_host_patterns=[re.compile(r"^px\.a8\.net$"), re.compile(r"^www\d+\.a8\.net$")],
    )
    defaults.update(overrides)
    return AllowlistConfig(**defaults)


def test_allows_any_get_page_on_primary_host():
    cfg = make_cfg()
    allowed, reason = decide("https://media-console.a8.net/common/timeline", "GET", cfg)
    assert allowed
    assert reason == "allowed_browse_page"


def test_allows_home_page_on_primary_host():
    cfg = make_cfg()
    allowed, reason = decide("https://media-console.a8.net/home", "GET", cfg)
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


def test_blocks_apply_path_on_primary_host_even_with_get():
    cfg = make_cfg()
    allowed, reason = decide("https://media-console.a8.net/asx/apply/123", "GET", cfg)
    assert not allowed
    assert reason.startswith("blocked_path")


def test_blocks_post_to_settings_on_primary_host():
    cfg = make_cfg()
    allowed, reason = decide("https://media-console.a8.net/asx/setting/update", "POST", cfg)
    assert not allowed


def test_blocks_generic_post_on_primary_host():
    cfg = make_cfg()
    allowed, reason = decide("https://media-console.a8.net/common/timeline", "POST", cfg)
    assert not allowed
    assert reason.startswith("method_not_allowed")


def test_allows_csv_download_via_post_on_primary_host():
    cfg = make_cfg()
    allowed, reason = decide("https://media-console.a8.net/asx/csv_export", "POST", cfg)
    assert allowed
    assert reason == "csv_download"


def test_allows_static_asset_on_static_host():
    cfg = make_cfg()
    allowed, reason = decide("https://support.a8.net/images/icon.svg?date=1", "GET", cfg)
    assert allowed
    assert reason == "static_asset"


def test_blocks_non_static_extension_on_static_host():
    cfg = make_cfg()
    allowed, reason = decide("https://support.a8.net/api/data", "GET", cfg)
    assert not allowed
    assert reason == "not_in_allowlist"


def test_blocks_post_on_static_host_even_for_static_extension():
    cfg = make_cfg()
    allowed, reason = decide("https://support.a8.net/images/icon.svg", "POST", cfg)
    assert not allowed
    assert reason.startswith("method_not_allowed")


def test_blocks_dangerous_path_on_static_host_too():
    cfg = make_cfg()
    allowed, reason = decide("https://support.a8.net/setting/icon.svg", "GET", cfg)
    assert not allowed
    assert reason.startswith("blocked_path")
