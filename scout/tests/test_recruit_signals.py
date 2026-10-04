"""Recruitment-funnel risk (2026-10-04): combined signals instead of one word (no network, no Vault).
The texts are synthetic but follow the patterns of the 107 postings the old single-word rule rejected:
- theme words only (一人暮らしの節約術 / ライフスタイルメディア / 実家の相続アンケート / ライフスタイルに合わせて働けます)
  -> no signal (the old rule rejected them)
- one personal question, or an aspiration question alone -> FLAG (Claude / Astra judge the meaning)
- the beginner side-job intake (age, employment, main-job hours, living situation + ideal future) -> REJECT
- off-platform lead (LINE, briefing session) or money from the worker, with personal / aspiration questions -> REJECT
- an online / Zoom interview alone is not an off-platform lead: interview flag only
- the other safety rules (AI ban, purchase / cost request) still reject

Run: python3 scout/tests/test_recruit_signals.py
"""
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
os.environ.setdefault("SCOUT_VAULT_KEY", "unused-in-this-test")
import collect  # noqa: E402
import pipeline as P  # noqa: E402
import source_facts as SF  # noqa: E402

PROFILE = {"birth_year": 1993, "keywords_strong": ["NISA", "お金"], "keywords_title_only": [], "unconfirmed_skills": []}
BASE = "【報酬】1記事2,000円（税込）\n・文字数：1,500文字\n・AIツール使用OK\n"
FUNNEL = ("【応募時に教えてください】\n① お名前：\n② ご年齢：\n③ 現在のご職業・雇用形態：\n④ 本業の週合計労働時間（目安）：\n"
          "⑤ 現在のライフスタイル（一人暮らし・実家暮らし・同棲など）：\n"
          "⑥ 3年後に理想としている働き方・ライフスタイルを教えてください。（例：フリーランスとして場所や時間にとらわれず働きたい）")


def decide(text):
    return SF.recruit_decision(SF.recruit_signals(text))


def row(title, desc, pay=None):
    f = {"ai_policy": "A", "ai_evidence": [], "requirements": [], "risk": [], "needs_experience": False,
         "pay": pay or {"type": "article", "price": 2000, "chars": 1500}, "price_mentions": [], "desc_hash": "x"}
    return {"id": 1, "url": "u", "title": title, "desc": desc, "tiers": ["B"], "category_id": 37, "expired_on": "2099-12-31",
            "entry": {"project_entry": {"num_contracts": 0, "project_contract_hope_number": 5, "num_application_conditions": 5}},
            "client": {"averageScore": 4.9, "jobOfferAchievementCount": 50, "isIdentityVerified": True},
            "client_open_jobs": 1, **f}


def main():
    # theme words are not signals (all rejected by the old single-word rule)
    for theme in ("「一人暮らしの節約術」をテーマにした記事です。テーマ例：一人暮らしと実家暮らしの支出額の違い",
                  "20代向けライフスタイルメディアの記事作成です。",
                  "ご自身のライフスタイルに合わせて無理なく働けます。",
                  "【実家を相続した方／質問5つ】実家を相続してから手放すまで何年かかりましたか？",
                  "月140時間稼働の場合、月収30万円前後も可能です。"):
        assert decide(BASE + theme) == "NONE", theme
    print("theme / boilerplate words (一人暮らし・ライフスタイル・実家・月収) are not recruitment signals")

    # one personal question or one aspiration question: a flag for Claude / Astra, never a reject
    assert decide(BASE + "■応募時に教えてください\n・年齢\n・お住まい（一人暮らし／実家暮らし等）：") == "FLAG"
    assert decide(BASE + "■応募時に教えてください\n・今後の目標（フリーランスを目指している等）：") == "FLAG"
    # the beginner side-job intake (several personal items incl. living situation + ideal future): reject
    assert decide(BASE + FUNNEL) == "REJECT"
    # off-platform lead / money from the worker together with personal or aspiration questions: reject
    assert decide(BASE + "■応募時に教えてください\n・年齢：\n・雇用形態：\n選考はLINEで行います。") == "REJECT"
    assert decide(BASE + "■応募時に教えてください\n・今後の目標：\n無料説明会にご参加ください。") == "REJECT"
    assert decide(BASE + "■応募時に教えてください\n・現在の収入状況：\nスクールの受講料が別途必要です。") == "REJECT"
    # a lead alone (no personal / aspiration questions) is a flag
    assert decide(BASE + "ご連絡は公式LINEにお願いします。") == "FLAG"
    print("one personal / aspiration question -> FLAG; funnel intake or lead + questions -> REJECT")

    # an online / Zoom interview is not an off-platform lead: interview flag only (decision unchanged)
    s = SF.recruit_signals(BASE + "■応募条件\n・Zoomで面談が可能な方\n・オンラインでの面談が可能な方")
    assert not s["S3"] and s["interview"] and SF.recruit_decision(s) == "NONE", s
    s = SF.recruit_signals(BASE + FUNNEL.replace("⑥", "⑦") + "\nZoomでの面談をお願いします。")
    assert not s["S3"] and s["interview"], s
    print("Zoom / online interview: not S3, a flag for Claude / Astra (拘束時間・選考コスト・報酬との釣り合い)")

    # rule_filter: theme -> PASS (no flag), single question -> PASS with FLAG note, funnel -> RULE_REJECTED
    r = row("一人暮らしの節約術を書くライター募集", BASE + "テーマ：一人暮らしの節約")
    st, why, _ = P.rule_filter(r, PROFILE)
    assert st == "PASS" and r["recruit_risk"]["decision"] == "NONE" and P._recruit_note(r) == "", why
    r = row("休日の過ごし方の記事", BASE + "■応募時に教えてください\n・お住まい（一人暮らし／実家暮らし等）：\n・年齢：")
    st, why, _ = P.rule_filter(r, PROFILE)
    assert st == "PASS" and P._recruit_note(r).startswith("FLAG："), (why, P._recruit_note(r))
    r = row("【未経験OK】AI画像生成のお仕事", BASE + FUNNEL)
    st, why, _ = P.rule_filter(r, PROFILE)
    assert st == "RULE_REJECTED" and any(w.startswith("勧誘リスク（複合）") for w in why), why
    r = row("【20代歓迎】Zoom面談ありのライター募集", BASE + "■応募条件\n・Zoomで面談が可能な方")
    st, why, _ = P.rule_filter(r, PROFILE)
    assert st == "PASS" and "面談・通話の記載" in P._recruit_note(r), why
    # a stored row still carrying the old label is not rejected for it
    r = row("一人暮らしの記事", BASE + "テーマ：一人暮らし")
    r["risk"] = ["勧誘兆候"]
    st, why, _ = P.rule_filter(r, PROFILE)
    assert st == "PASS" and "勧誘兆候" not in r["risk"], (why, r["risk"])
    print("rule_filter: theme -> PASS, single question -> PASS + FLAG note, funnel -> REJECT; legacy label dropped")

    # the other safety rules are kept
    r = row("記事作成", BASE.replace("AIツール使用OK", "AIの使用は禁止です"))
    r["ai_policy"] = collect.ai_policy(r["desc"])[0]
    assert P.rule_filter(r, PROFILE)[0] == "RULE_REJECTED"
    r = row("記事作成", BASE + "教材を購入していただく必要があります。")
    collect_f = collect.screen({"job_offer": {"title": "t"}, "payment": {}}, r["desc"], {"averageScore": 4.9,
                                                                                      "jobOfferAchievementCount": 50,
                                                                                      "isIdentityVerified": True})
    r["risk"] = collect_f["risk"]
    st, why, _ = P.rule_filter(r, PROFILE)
    assert st == "RULE_REJECTED" and any("購入/費用要求" in w for w in why), why
    print("AI ban and purchase / cost requests still rejected")

    # rescreen rows rebuilt from a posting page: per-character article pay, tier from the page category
    page = ('<title>テスト記事募集のお仕事(記事・Webコンテンツ作成) | x</title>'
            '<div id="compare_market_price" data="{&quot;categoryId&quot;:37,&quot;paymentType&quot;:&quot;fixed_price&quot;}"></div>'
            '<a href="/public/jobs/group/writing_beginner?ref=from_public_job_offer_category">w</a>'
            '<div>単価 固定報酬制 4.1 円 契約金額（目安）: ワーカーと相談する 1記事あたりの文字数 1200 文字 '
            '掲載日 2026年09月30日 応募期限 2099年10月14日 応募状況 応募した人 13 人 契約した人 3 人 募集人数 4 人</div>'
            '<div>仕事の詳細</div><div>' + BASE + 'テーマ：お金の知識</div><div>この仕事の特徴</div>' + " " * 5000)
    rr = collect.row_from_page("99900001", page, None, "2026-10-04T12:00+09:00")
    assert rr["pay"] == {"type": "article", "price": 4920, "chars": 1200} and rr["tiers"] == ["B"], rr["pay"]
    assert rr["entry"]["project_entry"]["project_contract_hope_number"] == 4
    print("rescreen: a row rebuilt from the posting page (4.1円 x 1,200字 = 4,920円, writing group -> tier B)")
    print("OK")


if __name__ == "__main__":
    main()
