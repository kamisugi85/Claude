from __future__ import annotations

import re

# TikTok自体のプラットフォームポリシーとして、A8側の許可条件に関わらず一般的に
# 扱いづらい/禁止されやすいジャンル。カテゴリ名との部分一致で判定する(★継続的に
# 見直す想定。誤って強い言葉で一致させないよう、意図的に狭いキーワードのみ)。
TIKTOK_POLICY_RISK_CATEGORY_KEYWORDS = [
    "アダルト",
    "出会い",
    "ギャンブル",
    "パチンコ",
    "競馬",
    "競輪",
    "競艇",
    "闇金",
    "たばこ",
    "タバコ",
    "風俗",
]

# SNS/TikTok掲載可否に関係しそうな自由記述フィールド。「リスティングＮＧワード」は
# 意図的に含めない -- これは主に検索連動広告の入札・表記に関するNGワード一覧であり、
# SNS/TikTokでの掲載可否そのものとは別の話のため、これだけを根拠に判定しない。
_TEXT_FIELDS_FOR_SNS_JUDGMENT = ["備考", "否認条件", "成果条件"]

# re.DOTALL: A8の実際の記載は「【禁止事項】\n・SNSでの掲載・投稿。」のように、
# 見出しと本文が改行で分かれていることが多い。デフォルトの`.`は改行にマッチしない
# ため、DOTALLが無いと「禁止...SNS」のような近接判定が改行を挟むだけで失敗し、
# 明確な禁止文言を見逃してしまう(実データで確認済みのバグ)。
_TIKTOK_EXPLICIT_PROHIBIT = re.compile(r"tiktok.{0,10}(ng|禁止|不可|対象外)", re.IGNORECASE | re.DOTALL)
_TIKTOK_EXPLICIT_ALLOW = re.compile(r"tiktok.{0,10}(ok|可能|可)", re.IGNORECASE | re.DOTALL)

_SNS_PROHIBIT_PATTERNS = [
    re.compile(r"sns.{0,10}(ng|禁止|不可|対象外)", re.IGNORECASE | re.DOTALL),
    re.compile(r"(ng|禁止|不可).{0,10}sns", re.IGNORECASE | re.DOTALL),
]
_SNS_CONDITIONAL_PATTERNS = [
    re.compile(r"sns.{0,40}(事前|要相談|確認|承諾|関連の無い投稿|関連しない投稿)", re.IGNORECASE | re.DOTALL),
]
_SNS_ALLOW_PATTERNS = [
    re.compile(r"sns.{0,10}(ok|可能|可)(?!能性)", re.IGNORECASE | re.DOTALL),
]

# 掲載可能なSNSが特定媒体に限定されている旨の文言(例: 「Instagram・Xのみ掲載可」)。
# TikTokが挙げられていない場合、TikTokは対象外と判定する(本PJ方針により除外扱い)。
_SNS_WHITELIST_PLATFORM_PATTERN = re.compile(
    r"(instagram|twitter|x（旧twitter）|youtube|threads|pinterest|note|facebook|line)"
    r"[^。\n]{0,15}(のみ|に限る|に限定)",
    re.IGNORECASE,
)


def _combined_text(record: dict) -> str:
    return "\n".join(str(record.get(key, "") or "") for key in _TEXT_FIELDS_FOR_SNS_JUDGMENT)


def classify_sns_promotion(record: dict) -> dict:
    """SNS/TikTokでの掲載可否をPythonルールのみで一次判定する。

    本PJの方針(2026-09時点): A8.net自体がTikTokへのアフィリエイト広告掲載を
    公式にサポートしていることを前提に、個別プログラムでSNS掲載OKなら原則
    TikTokも掲載OKとみなす。「TikTok」という語が文中に無いことだけを理由に
    保留にはしない -- 明確な除外シグナル(TikTok明示禁止/SNSが他媒体限定/
    高リスクカテゴリ)が無ければ既定で 'allowed' とする。
    """
    text = _combined_text(record)

    if _TIKTOK_EXPLICIT_PROHIBIT.search(text):
        return {"sns_verdict": "prohibited", "tiktok_verdict": "prohibited", "sns_basis": "tiktok_explicit_ng"}
    if _TIKTOK_EXPLICIT_ALLOW.search(text):
        return {"sns_verdict": "allowed", "tiktok_verdict": "allowed", "sns_basis": "tiktok_explicit_ok"}

    if any(p.search(text) for p in _SNS_PROHIBIT_PATTERNS):
        return {"sns_verdict": "prohibited", "tiktok_verdict": "prohibited", "sns_basis": "sns_explicit_ng"}

    whitelist_match = _SNS_WHITELIST_PLATFORM_PATTERN.search(text)
    if whitelist_match and "tiktok" not in whitelist_match.group(0).lower():
        return {
            "sns_verdict": "prohibited",
            "tiktok_verdict": "prohibited",
            "sns_basis": "sns_limited_to_other_platforms_excludes_tiktok",
        }

    if any(p.search(text) for p in _SNS_CONDITIONAL_PATTERNS):
        return {"sns_verdict": "conditional", "tiktok_verdict": "conditional", "sns_basis": "sns_conditional_wording"}

    if any(p.search(text) for p in _SNS_ALLOW_PATTERNS):
        return {"sns_verdict": "allowed", "tiktok_verdict": "allowed", "sns_basis": "sns_ok_implies_tiktok_ok"}

    # 既定値: 明確な除外シグナルが無ければ許可とみなす(A8がSNS/TikTok向け
    # アフィリエイトを公式にサポートしているという前提に基づく)。
    return {"sns_verdict": "allowed", "tiktok_verdict": "allowed", "sns_basis": "no_explicit_restriction_found"}


def classify_category_risk(record: dict) -> dict:
    category = str(record.get("category", "") or "")
    for keyword in TIKTOK_POLICY_RISK_CATEGORY_KEYWORDS:
        if keyword in category:
            return {"category_risk": "high", "category_basis": f"category_keyword:{keyword}"}
    return {"category_risk": "unknown" if not category else "low", "category_basis": "no_risk_keyword_matched"}


def classify_program(record: dict) -> dict:
    """SNS判定とカテゴリ判定を統合した総合判定。

    - likely_excluded: 明示的にNGと分かる、またはカテゴリが高リスク
    - likely_ok: 明確な除外シグナルが無い(SNS明言の有無を問わない)、かつ
      カテゴリにも懸念がない
    - needs_ai_or_human: 条件付き文言(事前相談等、SNSが他媒体限定 等)で
      額面通りに判定できないもの
    """
    sns = classify_sns_promotion(record)
    category = classify_category_risk(record)

    if sns["tiktok_verdict"] == "prohibited" or category["category_risk"] == "high":
        overall = "likely_excluded"
    elif sns["tiktok_verdict"] == "allowed" and category["category_risk"] in ("low", "unknown"):
        overall = "likely_ok"
    else:
        overall = "needs_ai_or_human"

    return {"tiktok_overall": overall, **sns, **category}
