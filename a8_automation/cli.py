from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

from .runner import run
from .settings import load_settings
from .utils import ensure_dir


def cmd_login(args: argparse.Namespace) -> int:
    settings = load_settings()
    ensure_dir(settings.browser_profile_dir)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=settings.browser_profile_dir,
            headless=False,
        )
        page = context.new_page()
        page.goto(settings.login_url)
        print("ブラウザウィンドウで手動ログインを完了してください(2段階認証・CAPTCHA含む)。")
        print("ログイン後、A8管理画面のトップ/マイページが表示されたらこのターミナルに戻ってください。")
        input("ログイン完了後、Enter を押してください > ")
        context.close()

    print(f"セッション情報を {settings.browser_profile_dir} に保存しました。")
    print("ID/パスワードはどこにも保存していません(保存されるのはブラウザのセッション状態のみです)。")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    return run(settings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="a8_automation")
    sub = parser.add_subparsers(dest="command", required=True)

    login_parser = sub.add_parser("login", help="初回のみ: 手動ログインしてセッションを永続化する")
    login_parser.set_defaults(func=cmd_login)

    run_parser = sub.add_parser("run", help="無人実行: 巡回・CSV取得・差分検知を行う")
    run_parser.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
