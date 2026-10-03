"""Search scope and pre-Claude rules (no network, no Vault): the data / research categories, Auto work kept
without profile keywords, burden exclusions, FILLED_CAPACITY, review-time minutes, and the Claude cap.

Run: python3 scout/tests/test_scout_scope.py
"""
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
os.environ.setdefault("SCOUT_VAULT_KEY", "unused-in-this-test")
import collect  # noqa: E402
import pipeline as P  # noqa: E402

PROFILE = {"birth_year": 1993, "keywords_strong": ["簿記", "財務", "市場調査", "競合調査"],
           "keywords_title_only": ["Excel"], "unconfirmed_skills": []}


def job(title, desc, tiers=("D",), pay=None, entry=None, ai="C"):
    return {"id": 1, "title": title, "desc": desc, "tiers": list(tiers), "ai_policy": ai, "risk": [],
            "requirements": [], "needs_experience": False, "expired_on": "2099-12-31",
            "pay": pay or {"type": "fixed", "min": 20000, "max": 30000}, "entry": entry or {},
            "client": {"averageScore": 4.8}, "client_open_jobs": 1}


def status(r, profile=PROFILE):
    return P.rule_filter(r, profile)


def main():
    # search entries: verified category ids, tier D; keyword entries kept, no guessed ids
    q = {label: (qs, tier) for label, qs, tier in collect.QUERIES}
    for cid, name in collect.DATA_CATEGORIES.items():
        assert q[f"cat:{cid}:{name}"] == (f"category_id={cid}", "D")
    assert {54, 52, 282, 146, 100, 86} <= set(collect.DATA_CATEGORIES)
    assert q["writing_all"][0] == "category_id=228" and q["task_all"][0] == "payment_type=task"
    print("search: data / research categories added as tier D; writing / task / keyword entries unchanged")

    # Auto work without any profile keyword is kept, and its minutes are the check after automation
    pdf = job("PDF議事録をスプレッドシートへ転記するお仕事", "PDFの内容をGoogleスプレッドシートへ転記してください。1ファイル約20ページ。")
    st, why, hits = status(pdf)
    assert st == "PASS" and not hits, why
    manual = dict(pdf, tiers=["B"], title="記事作成", desc="ご自身の言葉で記事を書いてください。" * 3)
    assert P.est_human_minutes(pdf) < P.est_human_minutes(manual) * 0.5
    assert P.lane_guess(pdf, hits) == "Auto"
    print("Auto (PDF -> sheet transcription) kept without profile keywords; minutes = review time")

    # profile-backed research is Professional
    mr = job("競合調査レポートの作成", "業界の市場調査と競合調査を行い、資料作成をお願いします。")
    st, why, hits = status(mr)
    assert st == "PASS" and P.lane_guess(mr, hits) == "Professional", (why, hits)

    # a data-category posting with no Auto signal and no profile link is dropped before Claude
    st, why, _ = status(job("YouTube見るだけ！視聴のお仕事", "動画を最後まで視聴して感想を送ってください。"))
    assert st == "RULE_REJECTED" and any("Auto処理の手掛かりなし" in w for w in why), why

    # burdens: phone, tool ban, forbidden scraping, SNS DM / form outreach
    cases = {
        "電話対応": job("営業リスト作成と架電", "作成したリストに架電していただきます。"),
        "手作業指定": job("データ入力", "Excelへのデータ入力です。ツール使用は禁止、手入力のみでお願いします。"),
        "規約上許されない": job("データ収集", "会員サイトにログインして取得した情報をスクレイピングで収集してください。"),
        "送信作業": job("Instagramのリスト作成・DM送信", "リスト作成後、テンプレートでDM送信していただきます。"),
    }
    for key, r in cases.items():
        st, why, _ = status(r)
        assert st == "RULE_REJECTED" and any(key in w for w in why), (key, why)
    print("phone / manual-only / forbidden scraping / SNS outreach excluded before Claude")

    # tied to the day (instant replies, fixed hours): kept but ranked lower
    free = job("企業リストの作成", "Web調査で企業情報を収集しExcelにまとめます。")
    tied = job("企業リストの作成", "Web調査で企業情報を収集しExcelにまとめます。平日10時〜18時は即レスでお願いします。")
    assert status(tied)[0] == "PASS"
    assert P.priority(tied, 0, "2026-10-03") < P.priority(free, 0, "2026-10-03") * 0.5
    print("tied-down work (即レス・平日日中) ranked lower, not excluded")

    # FILLED_CAPACITY: contracted >= wanted (source-backed) -> excluded; unless the posting keeps hiring; unknown -> kept
    full = {"project_entry": {"project_contract_hope_number": 3, "num_contracts": 3, "num_application_conditions": 40}}
    st, why, _ = status(job("データ入力", "PDFからExcelへのデータ入力です。", entry=full))
    assert st == "RULE_REJECTED" and any(w.startswith("FILLED_CAPACITY") for w in why), why
    more = job("データ入力", "PDFからExcelへのデータ入力です。追加募集のため、募集人数を超えて採用します。", entry=full)
    assert status(more)[0] == "PASS"
    unknown = job("データ入力", "PDFからExcelへのデータ入力です。",
                  entry={"project_entry": {"num_contracts": 5, "num_application_conditions": 40}})
    assert status(unknown)[0] == "PASS"
    print("FILLED_CAPACITY: excluded only when contracts >= wanted is stated and no extra hiring is announced")

    # unchanged: a keyword-only hit without profile link or Auto work is still dropped
    st, why, _ = status(job("英語のレッスン講師", "オンラインで英会話を教えていただきます。", tiers=["C"]))
    assert st == "RULE_REJECTED" and any("専門キーワードのみ一致" in w for w in why), why

    # backlog rows (no text) keep the flags they were scouted with
    flags = P._text_flags(pdf)
    row = {k: pdf[k] for k in ("title", "tiers", "pay", "entry", "client", "ai_policy", "expired_on")} | flags
    assert flags["auto_able"] and P.est_human_minutes(row) == P.est_human_minutes(pdf)

    # pre-Claude mix in the run log
    mix = P.candidate_mix([(1.0, pdf, [], ["新規"]), (1.0, mr, ["市場調査"], ["新規"])])
    assert mix["Auto"] == 1 and mix["Professional"] == 1 and mix["from_data_categories"] == 2
    print("backlog flags kept; candidate_mix in the run log")
    print("OK")


if __name__ == "__main__":
    main()
