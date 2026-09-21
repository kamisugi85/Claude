"""Research Jobの生成(Claude Code -> Work への調査依頼)。

Workが読み取れる単一のJSONファイルとして出力する。TikTokへの実アクセス方法
(検索・投稿閲覧・コメント確認)はWork側の責務であり、ここでは依頼内容と
期待するデータ形式だけを定義する。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .query_generator import generate_search_queries, occupation_for_program
from .schema import POST_RESULT_FIELDS
from .utils import iso_now, write_json

_INSTRUCTIONS_FOR_WORK = (
    "各programについて、記載されたsearch_queriesでTikTok内検索を行い、実際に表示された投稿を"
    "閲覧して観測できた事実だけをpost_result_schemaの形式で記録してください。"
    "取得できない項目はnull(不明)のままにし、推測値や一般論で埋めないでください。"
    "TikTokのアクセス制限回避・CAPTCHA回避・規約に反するスクレイピング的操作は行わないでください"
    "(通常のログイン済みブラウジング・検索・閲覧の範囲で行ってください)。"
    "結果は本ジョブのjob_idを含むJSONファイル(投稿レコードの配列)として保存し、"
    "Claude Code側の取り込み処理(ingest_results)が読めるようにしてください。"
)


def build_research_job(program_inputs: List[dict], queries_per_program: int = 8) -> dict:
    programs = []
    for pi in program_inputs:
        occupation = occupation_for_program(pi["program_id"])
        if occupation is None:
            # 職種が未確認の案件は検索語を作らない(推測でキーワードを作らない)。
            queries: List[str] = []
        else:
            queries = generate_search_queries(occupation, limit=queries_per_program)

        programs.append({
            "program_id": pi["program_id"],
            "program_name": pi["program_name"],
            "occupation_keyword": occupation,
            "search_queries": queries,
        })

    return {
        "job_id": f"tiktok-research-{iso_now().replace(':', '').replace('+', '_')}",
        "created_at": iso_now(),
        "created_by": "claude_code",
        "instructions_for_work": _INSTRUCTIONS_FOR_WORK,
        "programs": programs,
        "post_result_schema_fields": POST_RESULT_FIELDS,
        "output_instructions": (
            "調査結果は {\"job_id\": \"<このジョブのjob_id>\", \"posts\": [post_result, ...]} という"
            "形式の単一JSONファイルとして保存してください。ファイル名は自由ですが、"
            "job_idを含めることを推奨します。"
        ),
    }


def save_research_job(job: dict, path: str) -> None:
    write_json(path, job)
