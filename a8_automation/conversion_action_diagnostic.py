from __future__ import annotations

from collections import Counter
from typing import Dict, List

# 診断用の候補キーワード(まだ最終分類には使わない、実データでの出現頻度を
# 把握するためだけの粗い候補リスト)。否定文脈・補足条件は一切考慮しない。
CANDIDATE_KEYWORDS = [
    "資料請求",
    "無料会員登録",
    "無料登録",
    "会員登録",
    "無料申込",
    "無料体験",
    "無料相談",
    "トライアル",
    "お試し",
    "問い合わせ",
    "問合せ",
    "予約",
    "来店",
    "新規購入",
    "購入",
    "定期購入",
    "定期コース",
    "有料契約",
    "有料会員",
    "本契約",
    "見積",
    "査定",
    "口座開設",
    "申込",
    "申し込み",
    "成約",
    "契約",
    "加盟",
]

_TARGET_STATUSES = {"eligible_clear", "needs_language_review"}


def select_target_ids(catalog: Dict[str, dict]) -> List[str]:
    """excluded_clearを除いた297件(eligible_clear + needs_language_review)。"""
    return [pid for pid, record in catalog.items() if record.get("screening_status") in _TARGET_STATUSES]


def collect_conversion_action_texts(catalog: Dict[str, dict], target_ids: List[str]) -> Dict[str, str]:
    """成果条件テキストをprogram_id付きでそのまま集める(加工しない)。"""
    return {
        pid: catalog[pid]["成果条件"]
        for pid in target_ids
        if pid in catalog and catalog[pid].get("成果条件")
    }


def keyword_frequency(texts: Dict[str, str]) -> Counter:
    """候補キーワードが成果条件テキストに含まれる件数(存在するかどうかのみ)。"""
    counter: Counter = Counter()
    for text in texts.values():
        for kw in CANDIDATE_KEYWORDS:
            if kw in text:
                counter[kw] += 1
    return counter


def match_count_distribution(texts: Dict[str, str]) -> Counter:
    """1件あたり、候補キーワードが何種類ヒットしたか(0件=候補リストに無い
    未知の表現、1件=単一候補で分類しやすい、2件以上=複数候補が混在)。"""
    counter: Counter = Counter()
    for text in texts.values():
        matched = sum(1 for kw in CANDIDATE_KEYWORDS if kw in text)
        counter[matched] += 1
    return counter


def sample_texts_by_match_count(texts: Dict[str, str], match_count: int, limit: int = 15) -> List[dict]:
    """指定したヒット数のレコードから、実際のテキストをサンプル抽出する
    (分類体系を設計する前に、生のテキストを確認するため)。"""
    samples = []
    for pid, text in texts.items():
        matched_keywords = [kw for kw in CANDIDATE_KEYWORDS if kw in text]
        if len(matched_keywords) == match_count:
            samples.append({"program_id": pid, "text": text, "matched_keywords": matched_keywords})
            if len(samples) >= limit:
                break
    return samples


_SAMPLE_MATCH_COUNTS = [0, 1, 2, 3, 4]


def build_diagnostic_report(catalog: Dict[str, dict], sample_limit: int = 15) -> dict:
    """297件(excluded_clearを除く)の成果条件テキストについて、候補キーワードの
    出現頻度・マッチ数分布・サンプルテキストを集計する。分類は行わない
    (診断のみ)。Program Masterやscreening_statusは一切変更しない。"""
    target_ids = select_target_ids(catalog)
    texts = collect_conversion_action_texts(catalog, target_ids)

    freq = keyword_frequency(texts)
    distribution = match_count_distribution(texts)
    samples = {
        str(mc): sample_texts_by_match_count(texts, mc, limit=sample_limit) for mc in _SAMPLE_MATCH_COUNTS
    }

    return {
        "target_total": len(target_ids),
        "texts_collected": len(texts),
        "texts_missing": len(target_ids) - len(texts),
        "keyword_frequency": dict(freq.most_common()),
        "match_count_distribution": {str(k): v for k, v in sorted(distribution.items())},
        "samples_by_match_count": samples,
    }
