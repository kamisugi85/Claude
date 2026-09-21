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

# SNS/TikTok掲載可否に関係しそうな自由記述フィールド。A8側の項目名に依存するため
# 存在しないものは単に空文字として扱われる。
_TEXT_FIELDS_FOR_SNS_JUDGMENT = ["備考", "否認条件", "成果条件", "リスティングNGワード"]

_TIKTOK_EXPLICIT_PROHIBIT = re.compile(r"tiktok.{0,10}(ng|禁止|不可|対象外)", re.IGNORECASE)
_TIKTOK_EXPLICIT_ALLOW = re.compile(r"tiktok.{0,10}(ok|可能|可)", re.IGNORECASE)

_SNS_PROHIBIT_PATTERNS = [
    re.compile(r"sns.{0,10}(ng|禁止|不可|対象外)", re.IGNORECASE),
    re.compile(r"(ng|禁止|不可).{0,10}sns", re.IGNORECASE),
]
_SNS_CONDITIONAL_PATTERNS = [
    re.compile(r"sns.{0,40}(事前|要相談|確認|承諾|関連の無い投稿|関連しない投稿)", re.IGNORECASE),
]
_SNS_ALLOW_PATTERNS = [
    re.compile(r"sns.{0,10}(ok|可能|可)(?!能性)", re.IGNORECASE),
]


def _combined_text(record: dict) -> str:
    return "\n".join(str(record.get(key, "") or "") for key in _TEXT_FIELDS_FOR_SNS_JUDGMENT)


def classify_sns_promotion(record: dict) -> dict:
    """SNS/TikTokでの掲載可否をPythonルールのみで一次判定する。

    明示的な文言が見つからない場合は絶対に推測せず 'unclear' を返す
    (=AI/人間の判断に委ねる)。本PJの共通ルール: SNS掲載OKで、TikTokを名指しで
    除外していなければ、原則TikTokも掲載OKとみなす。
    """
    text = _combined_text(record)

    if _TIKTOK_EXPLICIT_PROHIBIT.search(text):
        return {"sns_verdict": "prohibited", "tiktok_verdict": "prohibited", "sns_basis": "tiktok_explicit_ng"}
    if _TIKTOK_EXPLICIT_ALLOW.search(text):
        return {"sns_verdict": "allowed", "tiktok_verdict": "allowed", "sns_basis": "tiktok_explicit_ok"}

    if any(p.search(text) for p in _SNS_PROHIBIT_PATTERNS):
        return {"sns_verdict": "prohibited", "tiktok_verdict": "prohibited", "sns_basis": "sns_explicit_ng"}

    if any(p.search(text) for p in _SNS_CONDITIONAL_PATTERNS):
        return {"sns_verdict": "conditional", "tiktok_verdict": "conditional", "sns_basis": "sns_conditional_wording"}

    if any(p.search(text) for p in _SNS_ALLOW_PATTERNS):
        return {"sns_verdict": "allowed", "tiktok_verdict": "allowed", "sns_basis": "sns_ok_implies_tiktok_ok"}

    return {"sns_verdict": "unclear", "tiktok_verdict": "unclear", "sns_basis": "no_sns_mention_found"}


def classify_category_risk(record: dict) -> dict:
    category = str(record.get("category", "") or "")
    for keyword in TIKTOK_POLICY_RISK_CATEGORY_KEYWORDS:
        if keyword in category:
            return {"category_risk": "high", "category_basis": f"category_keyword:{keyword}"}
    return {"category_risk": "unknown" if not category else "low", "category_basis": "no_risk_keyword_matched"}


def classify_program(record: dict) -> dict:
    """SNS判定とカテゴリ判定を統合した総合判定。

    - likely_excluded: 明示的にNGと分かる、またはカテゴリが高リスク
    - likely_ok: SNS掲載OKと明示され、カテゴリにも懸念がない
    - needs_ai_or_human: 上記のどちらとも言い切れない(推測しない)
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
