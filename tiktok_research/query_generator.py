"""案件ごとのTikTok検索語(自動生成)。

サービス名だけでなく、職種+悩み/不満/転職意向/給与/人間関係/勤務時間/夜勤/
休日/辞めたい/転職しない場合の情報収集、といったTikTokユーザーが実際に検索
しそうな検索意図へ展開する。ルールベースのテンプレート組み合わせのみで、
LLMは使用しない(本プロジェクト全体の一貫方針)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 案件ごとの職種キーワード。A8側の成果条件・広告主名(other_detail_fieldsの
# 見出しキーに実際に含まれる名称)から確認済みの職種で、推測ではない。
#   s00000001248025: 成果条件の対象ユーザーが「保育士資格保有者」と明記
#   s00000001248024: 成果条件の対象ユーザーが「看護師または准看護師の資格保有者」と明記
#   s00000026823003: other_detail_fieldsの広告主名見出しに
#                     「パーソル発！介護職特化の転職を無料フルサポート【みーつけあエージェント】」
#                     と明記
OCCUPATION_KEYWORDS: Dict[str, str] = {
    "s00000001248025": "保育士",
    "s00000001248024": "看護師",
    "s00000026823003": "介護士",
}

# 職種名+これらの軸を組み合わせて検索語を作る。悩み・不満・転職意向・給与・
# 人間関係・勤務時間・夜勤・休日・辞めたい、および「転職しない場合の情報収集」
# 意図を含む。
_WORRY_AND_INTENT_AXES = [
    "辞めたい",
    "給料",
    "人間関係",
    "夜勤",
    "休日",
    "勤務時間",
    "転職",
]
_INFO_GATHERING_WITHOUT_LEAVING_AXES = [
    "転職しない 情報収集",
]


def generate_search_queries(occupation: str, limit: int = 8) -> List[str]:
    """職種名から検索語リストを生成する(職種単体 + 悩み/意向軸の組み合わせ)。"""
    queries = [occupation]
    for axis in _WORRY_AND_INTENT_AXES:
        queries.append(f"{occupation} {axis}")
    for axis in _INFO_GATHERING_WITHOUT_LEAVING_AXES:
        queries.append(f"{occupation} {axis}")
    return queries[:limit]


def occupation_for_program(program_id: str) -> Optional[str]:
    return OCCUPATION_KEYWORDS.get(program_id)
