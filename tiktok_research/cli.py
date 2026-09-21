#!/usr/bin/env python3
"""TikTok Competitive Intelligence基盤のCLI(a8_automationのCLIとは完全に独立)。

サブコマンド:
  generate-job      A8エクスポート済みデータからResearch Job(Work向け)を生成する
  ingest-results    Workが返したTikTok調査結果を取り込む
  build-intelligence 取り込み済みデータからCreative Intelligenceを生成する
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_HERE)

_DEFAULT_EXPORT_PATH = os.path.join(_BASE_DIR, "data", "state", "a8_ai_review_selection_60_latest.json")
_DEFAULT_JOB_PATH = os.path.join(_BASE_DIR, "data", "tiktok_research", "intel", "research_job_latest.json")
_DEFAULT_OBSERVED_POSTS_PATH = os.path.join(_BASE_DIR, "data", "tiktok_research", "intel", "observed_posts.json")
_DEFAULT_INTEL_PATH = os.path.join(_BASE_DIR, "data", "tiktok_research", "intel", "creative_intelligence_latest.json")
_DEFAULT_PROGRAM_IDS = ["s00000001248025", "s00000001248024", "s00000026823003"]

from .creative_intelligence import build_creative_intelligence, save_creative_intelligence
from .ingest_results import ingest
from .program_input import load_program_inputs_from_export
from .research_job import build_research_job, save_research_job
from .utils import read_json


def cmd_generate_job(args: argparse.Namespace) -> int:
    program_inputs = load_program_inputs_from_export(args.export_path, args.program_ids)
    if not program_inputs:
        print("指定されたprogram_idがエクスポートファイル内に見つかりませんでした。")
        return 1

    job = build_research_job(program_inputs, queries_per_program=args.queries_per_program)
    save_research_job(job, args.output)

    print(f"Research Jobを生成しました: {args.output}")
    print(f"job_id: {job['job_id']}")
    for p in job["programs"]:
        print(f"  - {p['program_id']} ({p['program_name']}) 職種:{p['occupation_keyword']} 検索語{len(p['search_queries'])}件")
        for q in p["search_queries"]:
            print(f"      ・{q}")
    return 0


def cmd_ingest_results(args: argparse.Namespace) -> int:
    result = ingest(args.result_files, args.output)
    print(f"取り込み件数: {result['ingested_count']}件(重複除外: {result['duplicate_count']}件、却下: {result['rejected_count']}件)")
    if result["rejected"]:
        print("却下されたレコード:")
        for r in result["rejected"]:
            print(f"   - {r.get('source_file')}: {r.get('reason')}")
    print(f"保存先: {result['output_path']}")
    return 0


def cmd_build_intelligence(args: argparse.Namespace) -> int:
    observed = read_json(args.observed_posts, default=None)
    if observed is None:
        print("観測データがありません。先に ingest-results を実行してください。")
        return 1

    result = build_creative_intelligence(observed["posts"], args.program_ids)
    save_creative_intelligence(result, args.output)

    print(f"Creative Intelligenceを生成しました: {args.output}")
    for pid, summary in result["programs"].items():
        print(f"  - {pid}: 投稿{summary['post_count']}件 (data_sufficiency={summary['data_sufficiency']})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tiktok_research")
    sub = parser.add_subparsers(dest="command", required=True)

    job_parser = sub.add_parser("generate-job", help="A8データからResearch Job(Work向け)を生成する")
    job_parser.add_argument("--export-path", default=_DEFAULT_EXPORT_PATH)
    job_parser.add_argument("--program-ids", nargs="*", default=_DEFAULT_PROGRAM_IDS)
    job_parser.add_argument("--queries-per-program", type=int, default=9)  # 職種1+悩み軸7+情報収集軸1が全て入る件数
    job_parser.add_argument("--output", default=_DEFAULT_JOB_PATH)
    job_parser.set_defaults(func=cmd_generate_job)

    ingest_parser = sub.add_parser("ingest-results", help="Workが返した調査結果を取り込む")
    ingest_parser.add_argument("result_files", nargs="+", help="Workが保存したJSONファイルのパス")
    ingest_parser.add_argument("--output", default=_DEFAULT_OBSERVED_POSTS_PATH)
    ingest_parser.set_defaults(func=cmd_ingest_results)

    intel_parser = sub.add_parser("build-intelligence", help="取り込み済みデータからCreative Intelligenceを生成する")
    intel_parser.add_argument("--observed-posts", default=_DEFAULT_OBSERVED_POSTS_PATH)
    intel_parser.add_argument("--program-ids", nargs="*", default=_DEFAULT_PROGRAM_IDS)
    intel_parser.add_argument("--output", default=_DEFAULT_INTEL_PATH)
    intel_parser.set_defaults(func=cmd_build_intelligence)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
