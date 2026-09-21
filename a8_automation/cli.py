from __future__ import annotations

import argparse
import json
import os
import sys

from playwright.sync_api import sync_playwright

from .diff_store import load_snapshot
from .runner import run
from .screen import run_screen
from .settings import load_settings
from .utils import ensure_dir, read_json


def cmd_login(args: argparse.Namespace) -> int:
    settings = load_settings()
    ensure_dir(settings.browser_profile_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(settings.login_url)
        print("ブラウザウィンドウで手動ログインを完了してください(2段階認証・CAPTCHA含む)。")
        print("ログイン後、実際に閲覧したい管理画面のページまで進んでから、このターミナルに戻ってください。")
        input("そこまで進めたら、Enter を押してください > ")
        context.storage_state(path=settings.storage_state_path)
        browser.close()

    print(f"セッション情報を {settings.storage_state_path} に保存しました。")
    print("ID/パスワードはどこにも保存していません(保存されるのはCookie等のセッション状態のみです)。")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    return run(settings)


def cmd_inspect(args: argparse.Namespace) -> int:
    """収集済みデータをPowerShellの `ConvertFrom-Json` を経由せず直接確認する
    (文字コード・キー大文字小文字の問題を避けるため)。
    """
    settings = load_settings()
    snapshot = load_snapshot(settings.latest_snapshot_path)
    review_queue = read_json(settings.review_queue_path, default={"count": 0})
    shortlist = read_json(settings.shortlist_path, default={"count": 0})

    print(f"catalog_total={len(snapshot)}")
    print(f"needs_review={review_queue.get('count', 0)}")
    print(f"shortlist={shortlist.get('count', 0)}")
    print(f"alert_active={os.path.exists(settings.alert_json_path)}")
    print()

    detailed = [r for r in snapshot.values() if len(r) > 8]
    if detailed:
        print(f"--- 詳細ページ取得済みサンプル ({len(detailed)}件中の1件) ---")
        print(json.dumps(detailed[0], ensure_ascii=False, indent=2))
    elif snapshot:
        print("--- 一覧レベルのみのサンプル(まだ詳細ページ未取得) ---")
        print(json.dumps(next(iter(snapshot.values())), ensure_ascii=False, indent=2))
    else:
        print("カタログが空です。先に `run` を実行してください。")

    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    """AI/LLMを一切使わず、Pythonルールだけでカタログとshortlistを一次選別する。
    ネットワーク・ブラウザ操作は行わない(既存の収集済みデータのみを対象)。
    """
    settings = load_settings()
    if len(load_snapshot(settings.latest_snapshot_path)) == 0:
        print("カタログが空です。先に `run` を実行してください。")
        return 1

    report = run_screen(settings)

    print(f"① Program Master {report['catalog_total']} -> {report['catalog_remaining']}件")
    print(f"   (Pythonルールで{report['catalog_excluded']}件を除外)")
    print()
    print(f"② shortlist {report['shortlist_total']} -> {report['shortlist_remaining']}件")
    print()
    print("③ 除外ルール内訳:")
    reason_counts = report["reason_counts"]
    if reason_counts:
        for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
            print(f"   - {reason}: {count}件")
    else:
        print("   (該当なし)")
    print()
    overall = report["overall_counts"]
    print("④ TikTok適合性の一次判定内訳(カタログ全体):")
    print(f"   likely_ok       (ルールで概ねOKと確信): {overall.get('likely_ok', 0)}件")
    print(f"   likely_excluded (ルールで除外)        : {overall.get('likely_excluded', 0)}件")
    print(f"   needs_ai_or_human(文言があいまい/言及なし、AI・人間の判断が必要): {overall.get('needs_ai_or_human', 0)}件")
    print()
    print(f"保存: {settings.ai_candidates_path} (AIに渡す候補のみ)")
    print(f"保存: {settings.excluded_by_rules_path} (除外の詳細内訳)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="a8_automation")
    sub = parser.add_subparsers(dest="command", required=True)

    login_parser = sub.add_parser("login", help="初回のみ: 手動ログインしてセッションを永続化する")
    login_parser.set_defaults(func=cmd_login)

    run_parser = sub.add_parser("run", help="無人実行: 巡回・CSV取得・差分検知を行う")
    run_parser.set_defaults(func=cmd_run)

    inspect_parser = sub.add_parser("inspect", help="収集済みデータのサマリとサンプルを表示する")
    inspect_parser.set_defaults(func=cmd_inspect)

    screen_parser = sub.add_parser("screen", help="AI不使用、Pythonルールのみでカタログ/shortlistを一次選別する")
    screen_parser.set_defaults(func=cmd_screen)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
