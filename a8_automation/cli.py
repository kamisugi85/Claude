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
from .candidate_quality import compute_population_quality, distinct_detail_headings
from .ai_review_export import run_ai_review_export
from .ai_review_selection import save_ai_review_selection
from .candidate_screening import run_candidate_screening
from .conversion_action_diagnostic import build_diagnostic_report
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
from .utils import ensure_dir, read_json, run_timestamp, write_json


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


def cmd_candidate_quality(args: argparse.Namespace) -> int:
    """plan-detail-fetchで選定した300件の候補母集団だけを対象に、詳細取得の
    完了状況と主要項目の取得件数/欠損率を集計する。カタログ全体は対象にしない。
    LLMは使用しない。見出し名の出現頻度も出し、特定フィールドが0件の場合の
    切り分けに使う。
    """
    settings = load_settings()
    plan = read_json(settings.detail_fetch_plan_path, default=None)
    if not plan:
        print("先に `plan-detail-fetch` を実行してください。")
        return 1

    catalog = load_snapshot(settings.latest_snapshot_path)
    population_ids = plan["population"]

    report = compute_population_quality(catalog, population_ids)

    print(f"① 300件中の詳細取得完了件数: {report['detail_fetched_count']}件 / {report['population_total']}件")
    print()
    print("② 主要項目の取得件数/欠損率(候補300件のみが対象、カタログ全体ではない):")
    for name, info in report["fields"].items():
        print(f"   {name}: {info['count']}件 ({info['rate']:.1%})")
    print()

    headings = distinct_detail_headings(catalog, population_ids)
    print("③ 診断: 詳細取得済みレコードに実際に現れた見出し名の出現頻度(一覧レベル項目を除く):")
    if headings:
        for name, count in headings.most_common():
            print(f"   {name}: {count}件")
    else:
        print("   (詳細取得済みレコードがありません)")
    print("   ※ 抽出は見出しタグを機械的に拾う方式(特定の語句と一致させていない)ため、")
    print("     『禁止事項』がここに出てこない場合、抽出漏れではなく該当ページに")
    print("     その見出し自体が無い可能性が高いです(『リスティングＮＧワード』は全角ＮＧで表示)。")
    return 0


def cmd_screen_candidates(args: argparse.Namespace) -> int:
    """候補300件に、単純な文字列・構造ルールだけの3区分判定
    (excluded_clear / eligible_clear / needs_language_review)を適用する。
    AI/LLMは使用しない。Program Masterからは何も削除しない。
    """
    settings = load_settings()
    plan = read_json(settings.detail_fetch_plan_path, default=None)
    if not plan:
        print("先に `plan-detail-fetch` を実行してください。")
        return 1

    catalog = load_snapshot(settings.latest_snapshot_path)
    population_ids = plan["population"]

    report = run_candidate_screening(catalog, population_ids, settings.latest_snapshot_path, settings.candidate_screening_report_path)

    print(f"① excluded_clear: {report['excluded_clear_count']}件")
    print(f"② eligible_clear: {report['eligible_clear_count']}件")
    print(f"③ needs_language_review: {report['needs_language_review_count']}件")
    print()
    print("④ 除外理由別件数:")
    if report["exclusion_reason_counts"]:
        for reason, count in sorted(report["exclusion_reason_counts"].items(), key=lambda kv: -kv[1]):
            print(f"   - {reason}: {count}件")
    else:
        print("   (該当なし)")
    print()
    print("⑤ needs_language_reviewの主な理由:")
    if report["review_reason_counts"]:
        for reason, count in sorted(report["review_reason_counts"].items(), key=lambda kv: -kv[1]):
            print(f"   - {reason}: {count}件")
    else:
        print("   (該当なし)")
    print()
    print(f"保存: {settings.candidate_screening_report_path}")
    print("Program Masterには screening_status/screening_reason/screened_at を記録しました(削除なし)。")
    print("(Google Driveへの出力はまだ行っていません)")
    return 0


def cmd_conversion_action_diagnostic(args: argparse.Namespace) -> int:
    """excluded_clearを除いた297件の成果条件テキストについて、候補キーワードの
    出現頻度・マッチ数分布・サンプルテキストを機械的に集計する診断のみのコマンド。
    分類体系の確定や適用は行わず、Program Master/screening_statusは一切変更
    しない。ネットワーク・ブラウザ操作も行わない。AI/LLMは使用しない。
    """
    settings = load_settings()
    catalog = load_snapshot(settings.latest_snapshot_path)
    if not catalog:
        print("カタログが空です。先に `run` を実行してください。")
        return 1

    report = build_diagnostic_report(catalog)
    write_json(settings.conversion_action_diagnostic_path, report)

    print(f"対象件数(excluded_clearを除く): {report['target_total']}件")
    print(f"成果条件テキストを取得できた件数: {report['texts_collected']}件")
    print(f"成果条件が空/欠損の件数: {report['texts_missing']}件")
    print()
    print("候補キーワードの出現頻度(そのテキストに含まれるかどうかのみ、分類ではない):")
    for kw, count in report["keyword_frequency"].items():
        print(f"   {kw}: {count}件")
    print()
    print("1件あたりの候補キーワード種類ヒット数の分布:")
    print("   (0=候補リストに無い表現, 1=単一候補で分類しやすい, 2件以上=複数候補が混在)")
    for match_count, count in report["match_count_distribution"].items():
        print(f"   {match_count}件ヒット: {count}件")
    print()
    print(f"保存先: {settings.conversion_action_diagnostic_path}")
    print("(ヒット数0/1/2/3/4のサンプルテキストも上記ファイルに保存済み。分類は未確定。)")
    return 0


def cmd_select_ai_review(args: argparse.Namespace) -> int:
    """excluded_clearを除いた297件から、高性能AI評価に回す60件をPythonのみで
    抽出する(単一の合成スコアは作らない)。EPCあり45件・EPCなし15件を独立に
    選定し、成果地点・業種は選抜基準にはせず事後の多様性確認にのみ使う。
    Program Masterからは何も削除せず、選定理由をタグとして記録する。AI/LLM
    は使用せず、Google Driveへの共有も行わない。
    """
    settings = load_settings()
    catalog = load_snapshot(settings.latest_snapshot_path)
    if not catalog:
        print("カタログが空です。先に `run` を実行してください。")
        return 1
    if not any(r.get("screening_status") for r in catalog.values()):
        print("screening_statusが未設定です。先に `screen-candidates` を実行してください。")
        return 1

    report = save_ai_review_selection(catalog, settings.latest_snapshot_path, settings.ai_review_selection_path)

    print(f"① EPCあり/なし内訳: EPCあり {report['epc_tier_count']}件 / EPCなし {report['non_epc_tier_count']}件 (合計{report['population_count']}件)")
    print()
    epc_range = report["epc_range"]
    print(f"② EPCあり{report['epc_tier_count']}件のEPCレンジ: 最小{epc_range['min']} 〜 最大{epc_range['max']}")
    print()
    reward_range = report["non_epc_reward_range"]
    rate_range = report["non_epc_rate_range"]
    print(f"③ EPCなし{report['non_epc_tier_count']}件の選定方法: 報酬額降順 → 確定率降順 → シグナル無しは元順序のまま")
    print(f"   報酬額レンジ: 最小{reward_range['min']} 〜 最大{reward_range['max']}")
    print(f"   確定率レンジ: 最小{rate_range['min']} 〜 最大{rate_range['max']}")
    print(f"   報酬額・確定率どちらも無い件数: {report['non_epc_no_signal_count']}件")
    print()
    diversity = report["conversion_action_diversity"]
    print(f"④ 成果地点カテゴリの分布(安全に分類できたもののみ、分類不能{diversity['unclassified_count']}件は不利に扱っていない):")
    if diversity["classified_counts"]:
        for cat, count in diversity["classified_counts"].items():
            print(f"   {cat}: {count}件")
    else:
        print("   (安全に分類できたものはありませんでした)")
    print()
    print("⑤ 業種/カテゴリの分布:")
    for cat, count in report["industry_distribution"].items():
        print(f"   {cat}: {count}件")
    print()
    print("⑥ 抽出時に発生した問題:")
    if report["problems"]:
        for problem in report["problems"]:
            print(f"   - {problem}")
    else:
        print("   (なし)")
    print()
    print(f"保存: {settings.ai_review_selection_path}")
    print("Program Masterには ai_review_selected/ai_review_tier/ai_review_reason/ai_review_rank を記録しました(削除なし)。")
    print("(Google Driveへの共有はまだ行っていません)")
    return 0


def cmd_export_ai_review(args: argparse.Namespace) -> int:
    """select-ai-reviewが確定した60件について、ChatGPT/Claudeが同一データを
    使って独立評価するための共有用ファイルを書き出す(ローカル保存のみ、
    Google Driveへのコピーはこのコマンドでは行わない)。未取得の項目は
    推測せずnullのまま出力する。AI/LLMは使用しない。
    """
    settings = load_settings()
    payload = run_ai_review_export(settings)
    if payload is None:
        print("先に `select-ai-review` を実行してください。")
        return 1

    print(f"{payload['count']}件を書き出しました。")
    print(f"保存先: {settings.ai_review_export_path}")
    print()
    print("次の手順:")
    print(f"  1. 上記ファイルを Google Drive for Desktop の「A8_TikTok_PoC」共通フォルダへコピーしてください。")
    print(f"  2. ファイル名は変更不要です(既に a8_ai_review_selection_60_latest.json という名前で保存されています)。")
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
    # 注意: A8側の実際の見出しは全角の「ＮＧ」(U+FF2E/U+FF27)であり、半角の「NG」ではない。
    condition_fields = ["備考", "否認条件", "成果条件", "リスティングＮＧワード", "禁止事項"]
    field_counts = {f: sum(1 for r in succeeded_records if r.get(f)) for f in condition_fields}
    sns_text_count = sum(
        1 for r in succeeded_records if any(r.get(f) for f in ["備考", "否認条件", "成果条件", "リスティングＮＧワード"])
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
        f"禁止事項: {field_counts['禁止事項']}件, リスティングＮＧワード: {field_counts['リスティングＮＧワード']}件"
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

    quality_parser = sub.add_parser(
        "candidate-quality", help="候補300件だけを対象に主要項目の取得件数/欠損率と見出し出現頻度を集計する"
    )
    quality_parser.set_defaults(func=cmd_candidate_quality)

    screen_candidates_parser = sub.add_parser(
        "screen-candidates",
        help="候補300件をexcluded_clear/eligible_clear/needs_language_reviewの3区分に一次判定する(AI不使用)",
    )
    screen_candidates_parser.set_defaults(func=cmd_screen_candidates)

    conversion_action_diagnostic_parser = sub.add_parser(
        "conversion-action-diagnostic",
        help="成果条件テキストの候補キーワード出現頻度・マッチ数分布を診断する(分類は確定しない、AI不使用)",
    )
    conversion_action_diagnostic_parser.set_defaults(func=cmd_conversion_action_diagnostic)

    select_ai_review_parser = sub.add_parser(
        "select-ai-review",
        help="297件から高性能AI評価に回す60件をPythonのみで抽出する(EPCあり45件+EPCなし15件、AI不使用)",
    )
    select_ai_review_parser.set_defaults(func=cmd_select_ai_review)

    export_ai_review_parser = sub.add_parser(
        "export-ai-review",
        help="select-ai-reviewが確定した60件を、ChatGPT/Claude共有用の1ファイルに書き出す(ローカル保存のみ)",
    )
    export_ai_review_parser.set_defaults(func=cmd_export_ai_review)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
