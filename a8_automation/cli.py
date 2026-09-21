from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from playwright.sync_api import sync_playwright

from .access_log import AccessLog
from .allowlist import AllowlistConfig
from .alert import write_alert
from .coverage import compute_field_coverage
from .detail_candidates import save_detail_fetch_population
from .diff_store import load_snapshot
from .export_candidates import run_export
from .http_guard import HttpErrorStreakGuard
from .logging_setup import setup_logging
from .runner import install_allowlist_router, run
from .scraper import has_detail_fields, load_targets
from .screen import run_screen
from .settings import load_settings
from .targeted_detail_fetch import load_progress, run_targeted_detail_fetch
from .utils import ensure_dir, read_json, run_timestamp


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

    detailed = [r for r in snapshot.values() if has_detail_fields(r)]

    print(f"catalog_total={len(snapshot)}")
    print(f"catalog_detail_fetched={len(detailed)}")
    print(f"catalog_list_only={len(snapshot) - len(detailed)}")
    print(f"needs_review={review_queue.get('count', 0)}")
    print(f"shortlist={shortlist.get('count', 0)}")
    print(f"alert_active={os.path.exists(settings.alert_json_path)}")
    print()

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


def cmd_export(args: argparse.Namespace) -> int:
    """ChatGPT/Claude共通で読む候補データ(data/state/ai_candidates.jsonの中身)
    だけを、最小限の項目に絞って書き出す。カタログ全体(4,475件)は出力しない。
    ネットワーク・ブラウザ操作は行わない。
    """
    settings = load_settings()
    ai_candidates = read_json(settings.ai_candidates_path, default={"items": []})
    if not ai_candidates.get("items"):
        print("ai_candidates.jsonが空です。先に `screen` を実行してください。")
        return 1

    payload = run_export(settings)
    print(f"候補 {payload['count']}件 を書き出しました。")
    print(f"保存先: {settings.candidates_export_path}")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    """ランキング式を決める前の診断: カタログ全体で各項目が実際に何件
    取得できているか(欠損率)を確認する。ランキングは行わない。
    """
    settings = load_settings()
    catalog = load_snapshot(settings.latest_snapshot_path)
    if not catalog:
        print("カタログが空です。先に `run` を実行してください。")
        return 1

    report = compute_field_coverage(catalog)
    print(f"catalog_total={report['total']}")
    print()
    print("項目別の取得件数/カバレッジ率:")
    for name, info in report["fields"].items():
        note = f" ({info['note']})" if "note" in info else ""
        print(f"   {name}: {info['count']}件 ({info['rate']:.1%}){note}")
    return 0


def cmd_plan_detail_fetch(args: argparse.Namespace) -> int:
    """詳細ページ追加取得の候補母集団(300件: EPCあり250件+EPCなし50件)を
    選定するだけで、実際の取得(ブラウザ操作)は行わない。
    """
    settings = load_settings()
    catalog = load_snapshot(settings.latest_snapshot_path)
    if not catalog:
        print("カタログが空です。先に `run` を実行してください。")
        return 1

    result = save_detail_fetch_population(catalog, settings.detail_fetch_plan_path)

    print(f"① EPCあり枠: {result['epc_tier_count']}件")
    print(f"② EPCなし枠: {result['non_epc_tier_count']}件")
    print(f"③ 既に詳細取得済み(重複): {result['already_detailed_count']}件")
    print(f"④ 新規に詳細取得が必要: {result['needs_fetch_count']}件")
    print()
    print(f"候補母集団合計: {result['population_count']}件")
    print(f"保存先: {settings.detail_fetch_plan_path}")
    print("(実際の詳細取得はまだ開始していません)")
    return 0


def cmd_fetch_details(args: argparse.Namespace) -> int:
    """`plan-detail-fetch` が選定した候補(優先順位順)のうち、まだ完了して
    いないものから最大 --limit 件だけ実際に詳細ページを取得する。一覧ページの
    再巡回は行わない。異常検知時は即座に停止し、リトライしない(既存の安全
    機構と同じ)。AI/LLMは使用しない。
    """
    settings = load_settings()
    if not os.path.exists(settings.storage_state_path):
        print("セッションがありません。先に `login` を実行してください。")
        return 1

    plan = read_json(settings.detail_fetch_plan_path, default=None)
    if not plan or not plan.get("needs_fetch"):
        print("詳細取得候補がありません。先に `plan-detail-fetch` を実行してください。")
        return 1

    candidates = plan["needs_fetch"]

    targets = load_targets(settings.targets_config_path)
    search_crawl_target = next((t for t in targets if t.get("type") == "search_crawl"), None)
    detail_pattern = search_crawl_target.get("detail_expected_path_pattern") if search_crawl_target else None

    run_id = run_timestamp()
    logger = setup_logging(f"{settings.log_dir}/run-{run_id}.log")
    allowlist_cfg = AllowlistConfig.load(settings.allowlist_config_path)
    access_log = AccessLog()
    http_guard = HttpErrorStreakGuard(threshold=settings.max_consecutive_http_errors)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.headless)
        context = browser.new_context(storage_state=settings.storage_state_path)
        install_allowlist_router(context, allowlist_cfg, access_log, logger)
        context.on("response", http_guard.on_response)
        page = context.new_page()
        try:
            result = run_targeted_detail_fetch(page, settings, http_guard, logger, candidates, args.limit, detail_pattern)
        finally:
            context.close()
            browser.close()

    anomaly = result["anomaly"]
    if anomaly is not None:
        write_alert(
            settings.alert_json_path,
            kind=anomaly.kind,
            message=str(anomaly),
            context={"run_id": run_id},
        )
    elif os.path.exists(settings.alert_json_path):
        os.remove(settings.alert_json_path)

    catalog = load_snapshot(settings.latest_snapshot_path)
    succeeded_records = [catalog[pid] for pid in result["succeeded"] if pid in catalog]
    condition_fields = ["備考", "否認条件", "成果条件", "リスティングNGワード", "禁止事項"]
    field_counts = {f: sum(1 for r in succeeded_records if r.get(f)) for f in condition_fields}
    sns_text_count = sum(
        1 for r in succeeded_records if any(r.get(f) for f in ["備考", "否認条件", "成果条件", "リスティングNGワード"])
    )
    skip_reason_counts = Counter(reason for _, reason in result["skipped"])
    access_summary = access_log.summary()

    n = len(succeeded_records)
    print(f"① 取得試行件数: {result['attempted']}件")
    print(f"② 正常取得件数: {n}件")
    print("③ 失敗/スキップ件数と理由:")
    if skip_reason_counts:
        for reason, count in skip_reason_counts.items():
            print(f"   - {reason}: {count}件")
    if anomaly is not None:
        print(f"   - 異常検知により中断: {anomaly.kind} ({anomaly})")
    if not skip_reason_counts and anomaly is None:
        print("   (なし)")
    print(f"④ SNS掲載可否を実データとして取得できた件数: {sns_text_count}件 / {n}件")
    print(f"⑤ 成果地点(成果条件)を取得できた件数: {field_counts['成果条件']}件 / {n}件")
    print("⑥ その他の項目取得状況:")
    print(
        f"   否認条件: {field_counts['否認条件']}件, 備考: {field_counts['備考']}件, "
        f"禁止事項: {field_counts['禁止事項']}件, リスティングNGワード: {field_counts['リスティングNGワード']}件"
    )
    print(f"⑦ 異常・ブロック・セッション問題: {'あり(' + anomaly.kind + ')' if anomaly else 'なし'}")
    print(f"   ブロックされたリクエスト数: {access_summary['blocked_total']}件(許可: {access_summary['allowed_total']}件)")
    completed_total = load_progress(settings.detail_fetch_progress_path)
    remaining = len(candidates) - len(completed_total)
    print()
    print(f"残り未取得(この母集団内、累計): {remaining}件")
    print("(次のバッチには自動で進んでいません)")

    return 1 if anomaly is not None else 0


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

    export_parser = sub.add_parser("export", help="AI候補データのみを共通フォーマットで書き出す(カタログ全体は出力しない)")
    export_parser.set_defaults(func=cmd_export)

    coverage_parser = sub.add_parser("coverage", help="ランキングに使える項目の取得件数/欠損率を確認する")
    coverage_parser.set_defaults(func=cmd_coverage)

    plan_parser = sub.add_parser(
        "plan-detail-fetch", help="詳細ページ追加取得の候補母集団を選定する(実際の取得は行わない)"
    )
    plan_parser.set_defaults(func=cmd_plan_detail_fetch)

    fetch_details_parser = sub.add_parser(
        "fetch-details", help="plan-detail-fetchの候補から指定件数だけ実際に詳細ページを取得する"
    )
    fetch_details_parser.add_argument(
        "--limit", type=int, default=20, help="今回取得する最大件数(既定20、安全のため一時的に変更可能)"
    )
    fetch_details_parser.set_defaults(func=cmd_fetch_details)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
