"""The 10/4 Astra HOLD causes that Claude can resolve from the posting, reproduced (no network, no Vault):
the posting text patterns below are the ones of the 10/4 HOLD jobs, the job records are synthetic.
1. questions under "■ 応募用フォーマット（コピペでご回答ください）" / "■応募に際して" are found (13502240 / 13498303)
2. "① 初回（テスト）160円 / ② 継続 10件で2,500円 / 契約金額の欄：税抜146円" -> this application 160 (13497982)
3. "時給1500円くらい（CW手数料控除後）見積もりをお願いします" -> QUOTE_REQUIRED, never a fixed 1,650 (13498301)
4. "200円（入力金額）+ 20円（消費税）" -> 220 tax included (13502318)
5. AI terms A / B / C / D kept apart; C is no hold reason; completeness: "not stated" vs "not fetched"
6. total human minutes (not only Claude's check time); HOLD split into user-only / AI-resolvable / Astra
7. Astra Queue: protected columns, Drive row window, run log for Astra

Run: python3 scout/tests/test_source_hold.py
"""
import csv
import json
import os
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
os.environ.setdefault("SCOUT_VAULT_KEY", "unused-in-this-test")
import application as A  # noqa: E402
import pipeline as P  # noqa: E402
import source_facts as SF  # noqa: E402

FORM = ("PDFデータの文字起こしをお願いできる方を募集いたします。\n■ お仕事の概要\n・作業内容：指定のPDF資料をWordへ打ち込み・整形\n"
        "■ 報酬・納期\n・報酬：固定 2,500円（システム手数料・税込）\n・納期：ご契約から【3日以内】\n"
        "■ 応募用フォーマット（コピペでご回答ください）\n---------------------------------\n・1日の作業可能時間：\n"
        "・文字起こしやデータ入力の経験（未経験可）：\n---------------------------------\nご応募お待ちしております！")
SPLIT_ASK = ("■報酬\n1ファイル当たり4000円でお願いします。\n■この様な方を募集します！\n・守秘義務をお守りいただける方\n※学生さん不可\n"
             "※20歳以上59歳以下の方\n■応募に際して\n1枚当たりの納期をどれぐらいで出せるのか\n"
             "および過去に文字起こしのお仕事をされたことがあるかどうかを添えてご応募ください。")
TRIAL = ("学習用品の説明を読み、情報を欄へ書き写すお仕事です。\n作業の補助として、生成AIの利用も歓迎しています。\n《 条件 》\n"
         "まず初回テストを拝見し、問題がなければ継続の依頼へ進みます。\n① 初回（テスト）\n・分量：3件\n・報酬：160円（税込）\n"
         "② 継続の場合\n・分量：1回10件\n・お渡しする継続報酬：10件で2,500円程度（税込）\n・契約金額の欄：税抜146円\n"
         "※契約画面の源泉徴収の設定では、「してもらう」のチェックを入れずお進みください。\n《 ご応募 》\n"
         "お申し込みには、短く自己紹介をお書き添えください。\nご応募を心よりお待ちしております。")
QUOTE = ("【 依頼内容 】 ・作業：毎月更新されるデータを加工してのデータベースのメンテナンス  ・所要時間目安：1件あたり3－4時間程度\n"
         "【 契約金額(税抜) 】\n  時給1500円くらい（CW手数料控除後）見積もりをお願いします。\n"
         "【 応募方法 】  ・簡単な自己紹介や実績をご提示ください。  ・条件提示にてお見積もり金額を入力してください。")
ENTRY_TAX = ("【 報酬 】\n1記事あたり200円（合計金額 200円  =  200円（入力金額） + 20円（消費税））\nAI利用可能。ただしご自身の言葉で推敲などをしてください。\n"
             "【 応募時のお願い 】\n・金額設定の際は、200と入力いただくようお願いします。\n記事単価\n      200円")
PROFILE = {"birth_year": 1993, "professional": {"employer": "金融機関"}}


def test_extraction():
    q = SF.questions(FORM)
    assert q["status"] == "VERIFIED" and q["items"] == ["・1日の作業可能時間：", "・文字起こしやデータ入力の経験（未経験可）："], q
    q = SF.questions(SPLIT_ASK)
    assert q["items"][0] == "1枚当たりの納期をどれぐらいで出せるのか" and len(q["items"]) == 2, q
    q = SF.questions(TRIAL)
    assert "お申し込みには、短く自己紹介をお書き添えください。" in q["items"] and any("源泉徴収" in x for x in q["items"]), q
    q = SF.questions(QUOTE)  # U+2028 line breaks inside one paragraph
    assert q["items"] == ["・簡単な自己紹介や実績をご提示ください。", "・条件提示にてお見積もり金額を入力してください。"], q
    print("questions: 応募用フォーマット / 応募に際して / 《 ご応募 》 / U+2028 lines found; lead-in lines are not asks")

    r = SF.rewards(TRIAL)
    assert (r["applicable_reward"], r["initial_reward"], r["ongoing_reward"], r["entry_amount"], r["reward_status"]) == \
        (160, 160, 2500, 146, "CONFIRMED"), r
    r = SF.rewards(QUOTE)
    assert r["reward_status"] == "QUOTE_REQUIRED" and r["applicable_reward"] is None and r["hourly_rate"] == 1500, r
    r = SF.rewards(ENTRY_TAX)
    assert (r["applicable_reward"], r["reward_status"], r["reward_tax"]) == (220, "CONFIRMED", "税抜→税込換算"), r
    assert SF.rewards(FORM)["applicable_reward"] == 2500 and SF.rewards(FORM)["reward_tax"] == "税込"
    print("rewards: trial 160 (not the 2,500 / 10 items, not the 146 to type), quote request, 200円入力金額 = 220円")

    assert SF.ai_condition(TRIAL)["code"] == "A" and SF.ai_condition(ENTRY_TAX)["code"] == "A"
    assert SF.ai_condition("AIの使用は禁止です。データを入力してください。報酬1,000円。")["code"] == "B"
    c = SF.ai_condition(SPLIT_ASK)
    assert c["code"] == "C" and c["label"].startswith("C:AI記載なし（AI禁止記載なし／利用条件不明）")
    assert SF.ai_condition(SPLIT_ASK, complete=False)["code"] == "D" and SF.ai_condition(None, fetched=False)["code"] == "D"
    wf = SF.work_facts(SPLIT_ASK)
    assert wf["external"]["status"] == "VERIFIED" and wf["requirements"]["status"] == "VERIFIED"
    assert SF.work_facts(SPLIT_ASK, complete=False)["continuity"]["status"] == "SOURCE_INCOMPLETE"
    assert SF.work_facts(None, fetched=False)["work"]["status"] == "FETCH_FAILED"
    notes = SF.requirement_checks(wf["requirements"]["lines"], PROFILE, "2026")
    assert "年齢条件20〜59歳：プロフィール生年1993→33歳で該当" in notes and any("学生不可" in n for n in notes), notes
    print("AI terms A / B / C / D apart (C = not stated, D = not fetched); age / student checked from the profile")


def rc_of(desc, complete=True):
    rc = {"questions": SF.questions(desc, complete), "reward_struct": SF.rewards(desc, complete),
          "source_state": SF.source_state(desc, complete), "ai_condition": SF.ai_condition(desc, complete),
          "work_facts": SF.work_facts(desc, complete), "checked_at": P.today() + "T05:30+09:00", "capacity": 3,
          "contracted": 0, "applicants": 20}
    rc["completeness"] = SF.completeness(rc, "7000001", rc)
    return rc


def job(jid, desc, app=None, hm="8-12分/ファイル", status="ASTRA_QA_PENDING", queued=None):
    j = {"job_id": jid, "url": "u", "title": "t", "client": {"userId": 7000001}, "tiers": ["D"], "pay": {"type": "fixed"},
         "eval": {"human_minutes": hm, "ai_completion": "85-90%"}, "status": status, "net_est": 3200, "gross": 4000,
         "reward_check": rc_of(desc),
         "status_history": [{"status": "ASTRA_QA_PENDING", "at": queued or P.now_iso(), "by": "claude"}]}
    if app:
        j["application"] = {"application_draft": "はじめまして。応募いたします。よろしくお願いいたします。", "unverified_facts": [],
                            "user_confirmation_required": "no", "claim_flags": [], "conflict_risk": "低：該当なし",
                            "qa_flags": [], "stage": "PRE_ASTRA", **app}
    return j


def test_checks():
    # completeness: complete vs. not fetched
    comp = rc_of(SPLIT_ASK)["completeness"]
    assert comp["status"] == "COMPLETE", comp
    bad = rc_of(SPLIT_ASK, complete=False)["completeness"]
    assert bad["status"] == "INCOMPLETE" and "work" in bad["gaps"] and "ai_condition" in bad["gaps"], bad

    # questions: the half-extracted deadline question is caught; the full pair passes
    j = job(1, SPLIT_ASK, {"application_questions": ["および過去に文字起こしのお仕事をされたことがあるかどうかを添えてご応募ください。"],
                           "application_answers": ["本業で文字起こしの経験がございます。"]})
    qc = A.question_check(j, j["application"])
    assert qc["status"] == "MISMATCH" and qc["missing_in_draft"] == ["1枚当たりの納期をどれぐらいで出せるのか"], qc
    j["application"]["application_questions"] = SF.questions(SPLIT_ASK)["items"]
    assert A.question_check(j, j["application"])["status"] == "OK"
    print("question_check: a source ask missing from the draft is found; the complete pair passes")

    # reward: a fixed amount on a quote request / the regular amount instead of the trial is refused
    q = job(2, QUOTE)
    errs = A._source_errors({"actual_reward": 1500, "application_questions": [], "application_answers": [],
                             "application_draft": ""}, q)
    assert any("見積依頼の案件に固定報酬1500円" in e for e in errs), errs
    t = job(3, TRIAL)
    errs = A._source_errors({"actual_reward": 2500, "application_questions": [], "application_answers": [],
                             "application_draft": ""}, t)
    assert any("継続報酬2500円を今回の報酬" in e for e in errs), errs
    assert P.job_tier(q).startswith("UNKNOWN") and "見積依頼" in P.job_tier(q)
    print("reward: quote request with a fixed amount and trial/regular mix-up are refused; quote -> tier UNKNOWN")

    # total human minutes: Claude's check time + the user's own steps (not the check alone)
    j = job(4, SPLIT_ASK, {"application_questions": [], "application_answers": ["【本人記入】（稼働時間）"],
                           "unverified_facts": ["1日の作業可能時間"], "user_confirmation_required": "yes"})
    hm = P.human_minutes_total(j)
    assert hm["total"] == 3 + 5 + 2 + 10 + 3 + 3 and "成果物確認10" in hm["basis"] and "AI処理時間は含めない" in hm["basis"], hm
    assert P.human_minutes_total(job(5, SPLIT_ASK, hm="")) ["status"] == "UNKNOWN"
    assert P._applicable_net_per_min(j) == round(4000 * 0.8 / hm["total"], 1)
    print(f"human minutes: total {hm['total']} (apply + user input + contract + check + revision + delivery), UNKNOWN without a number")

    # HOLD split: what only the user can answer vs. what Claude should have resolved vs. Astra's call
    user, data, astra = A.hold_split(j)
    assert user and any("本人のみ回答可：1日の作業可能時間" in u for u in user) and not data and not astra, (user, data, astra)
    k = job(6, SPLIT_ASK, {"application_questions": [], "application_answers": []})
    k["application"]["qa_flags"] = A._qa_flags(k, k["application"])
    user, data, astra = A.hold_split(k)
    assert not user and any("draftにない" in d for d in data), (user, data)
    print("HOLD split: user-only (availability) / AI-resolvable (missing question) / Astra")


def test_queue():
    # protected columns never dropped; the run log is a Drive file; rows in the Drive window
    must = {"applicable_reward", "application_questions", "requirements_source", "ai_condition_source", "client_id",
            "entry_counts", "source_completeness", "application_draft", "application_answers", "reward_basis",
            "search_tier", "source_entrypoint", "scout_run"}
    assert must <= set(P.QUEUE_CRITICAL) and not must & set(P.QUEUE_LOW_PRIORITY)
    assert {c for c, _ in P.QUEUE_COLS} >= must | {"human_minutes_total", "hold_user_only", "question_check"}
    d = P.today()
    import datetime as dt
    yday = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
    old = (dt.date.fromisoformat(d) - dt.timedelta(days=3)).isoformat()
    assert P.in_astra_window(job(7, FORM, queued=d + "T05:20+09:00"))
    assert not P.in_astra_window(job(8, FORM, queued=old + "T05:20+09:00"))
    ev = job(9, FORM, queued=yday + "T17:20+09:00")
    ev["queued_run"], ev["queued_at"] = f"{yday} evening_delta", yday + "T17:20+09:00"
    assert P.in_astra_window(ev)
    rep = job(10, FORM, {"application_questions": [], "application_answers": []}, queued=old + "T05:20+09:00")
    rep["application"]["repaired_at"] = d + "T12:00+09:00"
    assert P.in_astra_window(rep)
    print("Drive Astra Queue rows: today / yesterday 17:00 / repaired or changed today; older rows stay local")

    assert "run_log" in P.DRIVE_FILES and "run_log_sheet" in P.DRIVE_KINDS
    tmp = tempfile.mkdtemp()
    old_state = P.STATE
    try:
        P.STATE = tmp
        with open(os.path.join(tmp, "runs.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"run_at": "x", "date": d, "listed": 5, "est_ai_usage": {"eval_input_chars": 1200}}) + "\n")
            f.write(json.dumps({"run_at": "y", "date": d, "run_type": "evening_delta", "candidate_mix": {"Auto": 2},
                                "unsent_mix": {"Professional": 1}, "deferred_to_morning": 3}) + "\n")
        n = P.write_run_log(os.path.join(tmp, "run_log.csv"))
        rows = list(csv.DictReader(open(os.path.join(tmp, "run_log.csv"), encoding="utf-8")))
    finally:
        P.STATE = old_state
    assert n == 2 and rows[0]["run_type"] == "evening_delta" and rows[0]["unsent_professional"] == "1" \
        and rows[0]["deferred_to_morning"] == "3" and rows[1]["run_type"] == "morning_full" \
        and rows[1]["claude_input_chars"] == "1200", rows
    print("run log for Astra: run_type, Claude usage, sent / unsent mix, deferred (older runs = morning_full)")


def main():
    test_extraction()
    test_checks()
    test_queue()
    print("OK")


if __name__ == "__main__":
    main()
