"""The three data-quality failures Astra found on 2026-10-01, reproduced (temporary copy of scout/; the real
Vault is never modified):
1. the posting has application questions -> they are never dropped (or taken for "no questions")
2. trial 500 JPY / regular 3,000 JPY -> this application's reward is 500, not 3,000
3. 27 postings seen / 1 delivery -> never "納品27件"
plus: the Drive copy of the Astra Queue keeps the critical columns when it has to be made smaller.

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_source_facts.py
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402
sys.path.insert(0, SRC)
import source_facts as SF  # noqa: E402

TRIAL = ("暮らしの工夫の記事を書いていただきます。\n【報酬】\n・本契約：1記事3,000円（税込）\n"
         "・テストライティング：初回は500円（税込）になるよう入力してください\n"
         "【応募時に教えてください】\n① お名前\n② ライティング経験\n③ 応募理由\n"
         "ご応募お待ちしております。")
LEAD_IN = "最初は500文字程度のテストをお願いします。\n・報酬：330円（税込）\n・1記事：約3,000文字\n・報酬：4,500円（税込）\n"
NO_Q = ("記事作成のお仕事です。テーマに沿って、ご自身の体験をもとに読みやすい記事を書いていただきます。\n"
        "【文字数】1,500文字程度\n【報酬】1記事あたり2,000円（税込）\n【納期】契約から1週間\n"
        "【納品】Googleドキュメント\n継続してお願いできる方を歓迎します。よろしくお願いします。")
PAID_TRIAL = "【報酬】1本1,000円\n※テスト案件を受けていただく場合があります(報酬あり)\n【納期】3日"

SETUP = r"""
import os, pipeline as P
v = P.vault_load(); m = v["master"]; d = P.today()
def job(jid, cid, status, hist, gross=3300):
    m[jid] = {"job_id": int(jid), "url": "https://crowdworks.jp/public/jobs/" + jid, "title": "テスト記事" + jid,
              "client": {"userId": int(cid), "userDisplayName": "c" + cid, "jobOfferAchievementCount": 279,
                         "averageScore": 4.9, "isIdentityVerified": False},
              "first_seen": d + "T05:00+09:00",
              "eval": {"verdict": "候補", "reason": "t", "classification": "B", "human_minutes": "4-6分",
                       "ai_completion": "85-90%", "client_risk": "低（過去27件納品済み）"},
              "gross": gross, "net_est": round(gross * 0.8), "risk_rule": [], "actual": {}, "expired_on": "2099-12-31",
              "status": status, "pre_draft_due": status == "ASTRA_QA_PENDING",
              "status_history": [{"status": s, "at": d + "T05:1%d+09:00" % i, "by": "test", "note": ""}
                                 for i, s in enumerate(hist)]}
    if status == "ASTRA_QA_PENDING":
        m[jid]["reward_check"] = {"checked_at": d + "T05:31+09:00", "changes": [], "deadline": "2099-12-31", "closed": False}
job("99700001", "99000051", "ASTRA_QA_PENDING", ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"])
job("99700002", "99000051", "ASTRA_QA_PENDING", ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"])
job("99700009", "99000051", "PAID", ["APPLIED", "ACCEPTED", "DELIVERED", "PAID"])   # the one real delivery
for i in range(25):                                                                 # postings Scout saw
    job(str(99710000 + i), "99000051", "CLAUDE_REJECTED", ["CLAUDE_REJECTED"])
import application as A
src = {"desc": DESC, "desc_complete": True}
A._record_source(m["99700001"], src)
A._record_source(m["99700002"], None)  # the posting could not be fetched
os.makedirs(os.path.join(P.ROOT, "data", d, "app_source"), exist_ok=True)
P.save_json(os.path.join(P.ROOT, "data", d, "app_source", "99700001.json"), src)
P.save_json(os.path.join(P.ROOT, "data", d, "app_source", "99700002.json"), {"desc": ""})
P.vault_save(v)
"""


def run(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(" ".join(args) + "\n" + r.stdout + r.stderr)
    return r


def js(r):
    return json.loads(r.stdout[r.stdout.index("{"):])


def py(tmp, code):
    return subprocess.run([sys.executable, "-c", code], cwd=tmp, capture_output=True, text=True, check=True).stdout


def draft(**kw):
    d = {"job_id": "99700001", "actual_reward": 500, "reward_evidence": "初回は500円（税込）",
         "application_draft": "はじめまして。暮らしの工夫の記事のご募集を拝見し、応募いたします。どうぞよろしくお願いいたします。",
         "application_questions": ["① お名前", "② ライティング経験", "③ 応募理由"],
         "application_answers": ["【CrowdWorks表示名を入力】", "未経験ですが丁寧に取り組みます。", "暮らしの工夫を伝えたいと考えました。"],
         "facts_used": [], "unverified_facts": [], "conflict_risk": "低：該当なし",
         "user_confirmation_required": "no", "review_minutes_est": 2}
    d.update(kw)
    return d


def merge(tmp, d, ok=True):
    p = os.path.join(tmp, "d.json")
    json.dump([d], open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return run(tmp, "app-merge", "--drafts", p, ok=ok)


def main():
    # --- extraction (source_facts, no LLM)
    q = SF.questions(TRIAL)
    assert q["status"] == "VERIFIED" and "① お名前" in q["lines"] and "③ 応募理由" in q["lines"], q
    assert SF.questions(NO_Q)["status"] == "NONE_VERIFIED"
    assert SF.questions(NO_Q, complete=False)["status"] == "SOURCE_INCOMPLETE"  # cut text: never "no questions"
    assert SF.questions(None, fetched=False)["status"] == "FETCH_FAILED"
    r = SF.rewards(TRIAL)
    assert (r["initial_reward"], r["ongoing_reward"], r["applicable_reward"], r["reward_status"]) == (500, 3000, 500, "CONFIRMED"), r
    r = SF.rewards(LEAD_IN)  # the trial amount sits on the line after "最初は…テスト"
    assert (r["initial_reward"], r["ongoing_reward"], r["applicable_reward"]) == (330, 4500, 330), r
    r = SF.rewards(PAID_TRIAL)
    assert r["reward_status"] == "AMBIGUOUS" and r["applicable_reward"] is None and r["ongoing_reward"] == 1000, r
    print("source facts: questions VERIFIED / NONE_VERIFIED / SOURCE_INCOMPLETE / FETCH_FAILED; trial 500 vs regular 3,000")

    # 2026-10-04 17:00 NO_DRAFT: a budget-bracket header (固定報酬制 10,000〜30,000円) is not this application's
    # reward; the body's 15,000円（税込） is, and a header lower bound below it is no reward decrease
    import application as A
    PDF = "PDF社内マニュアルをWordへ転記していただきます。\n【報酬】\n15,000円（税込）\n【納期】\n10日程度\n"
    job = {"gross": 15000, "eval": {"ai_condition": "A"}, "ai_policy_rule": "A"}
    rc = {"header_reward": {"type": "固定報酬制", "min": 10000, "max": 30000}, "deadline": "2099-12-31",
          "ai_policy": "A", "desc_hash": "x", "key_lines": []}
    ch, notes = A.classify_changes({}, rc, job, PDF)
    assert ch == [] and any("本文の今回報酬15000円" in n and "減額ではない" in n for n in notes), (ch, notes)
    # the body's reward really below what was evaluated is still a material change
    ch, _ = A.classify_changes({}, rc, {**job, "gross": 20000}, PDF)
    assert any("下回る" in c and "原文の今回報酬15000円" in c for c in ch), ch
    # no body amount: a header bracket that contains the evaluated amount is a note; a single header amount
    # below it is still a change
    ch, notes = A.classify_changes({}, rc, job, "PDFをWordへ転記していただきます。")
    assert ch == [] and any("レンジ" in n for n in notes), (ch, notes)
    ch, _ = A.classify_changes({}, {**rc, "header_reward": {"type": "契約金額（目安）", "min": 10000, "max": None}}, job,
                               "PDFをWordへ転記していただきます。")
    assert any("下回る" in c for c in ch), ch
    # "1件1円で見積もりをお願いします": a unit rate to quote with, not a confirmed reward of 1円
    r = SF.rewards("企業名・電話番号をリストに転記していただきます（数千件〜）。\n【報酬】\n1件1円で見積もりをお願いします。\n")
    assert r["reward_status"] == "QUOTE_REQUIRED" and r["applicable_reward"] is None and "1円/件" in r["reward_basis"], r
    job = {"status": "ASTRA_QA_PENDING", "application": {"user_confirmation_required": "no"},
           "reward_check": {"reward_struct": r, "questions": {"status": "NONE_VERIFIED", "lines": []}}}
    fl = A._qa_flags(job, {"application_questions": [], "application_answers": []})
    assert any("原文の単価で見積" in f for f in fl) and not any(A.USER_ONLY_RE.search(f) for f in fl), fl
    print("reward re-check: header bracket vs source-backed 15,000円 -> note; quote unit rate 1円/件 -> QUOTE_REQUIRED, "
          "quoted at the posting's rate (Astra decides, the user is not asked)")

    tmp = sandbox.make()  # code only, fresh test Vault: the Queue below holds this test's rows only
    py(tmp, "DESC=%r\n" % TRIAL + SETUP)

    # 1. questions in the posting: a draft without them is not stored
    r = merge(tmp, draft(application_questions=[], application_answers=[]), ok=False)
    assert any("application_questions が空" in e for e in js(r)["failed"]["99700001"]), r.stdout
    # 2. the regular 3,000 as this application's reward is refused; the trial 500 is accepted
    r = merge(tmp, draft(actual_reward=3000, reward_evidence="本契約：1記事3,000円（税込）"), ok=False)
    assert any("継続報酬3000円を今回の報酬" in e for e in js(r)["failed"]["99700001"]), r.stdout
    # 3. "納品27件" in the draft is refused (the records say 1 delivery; 27 is not a delivery count)
    r = merge(tmp, draft(application_draft="以前はお仕事をご依頼いただき、ありがとうございました。これまで27件納品させていただきました。"
                                            "今回も応募いたします。どうぞよろしくお願いいたします。"), ok=False)
    assert any("誤変換疑い" in e and "27件納品" in e for e in js(r)["failed"]["99700001"]), r.stdout
    print("app-merge refuses: dropped questions / regular reward as this reward / 27件納品")

    # the corrected draft goes through; the first-pass text "過去27件納品済み" is flagged for Astra, not rejected
    merge(tmp, draft(application_draft="以前はお仕事をご依頼いただき、ありがとうございました。暮らしの工夫の記事のご募集を拝見し、"
                                        "応募いたします。どうぞよろしくお願いいたします。"))
    j = json.loads(py(tmp, "import pipeline as P, json; print(json.dumps(P.vault_load()['master']['99700001'], ensure_ascii=False))"))
    app = j["application"]
    assert j["status"] == "ASTRA_QA_PENDING" and app["final_qa_status"] == "CLAUDE_QA_FLAGGED", app["final_qa_status"]
    assert any("一次評価のクライアント実績値の誤変換疑い" in f and "当方の納品=1" in f for f in app["qa_flags"]), app["qa_flags"]
    assert j["provenance"]["reward"]["initial"] == 500 and j["provenance"]["reward"]["ongoing"] == 3000
    assert j["provenance"]["questions"]["extraction_status"] == "VERIFIED" and "draft設問3件" in j["provenance"]["draft"]["questions"]

    # Astra Queue: status and source columns; tier on this application's reward (500 -> net 400 < 1,000)
    q = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "astra_queue.csv"), encoding="utf-8"))}
    r1, r2 = q["99700001"], q["99700002"]
    assert r1["application_questions_status"] == "VERIFIED" and "② ライティング経験" in r1["source_questions"]
    assert (r1["initial_reward"], r1["ongoing_reward"], r1["applicable_reward"]) == ("500", "3000", "500")
    assert r1["tier"] != "主力", r1["tier"]
    assert "CW公開：募集実績（発注者の募集数。当方の取引ではない）=279" in r1["client_facts"] and "当方の納品=1" in r1["client_facts"]
    assert "Scout検知の同発注者の他募集" in r1["client_history"] and "当方の応募" in r1["client_history"]
    assert r2["application_questions_status"].startswith("FETCH_FAILED") and r2["reward_status"] == "FETCH_FAILED"
    assert r2["claude_qa_result"] == "NO_DRAFT"
    print("Astra Queue: questions status, trial/regular reward, labelled client values, FETCH_FAILED kept explicit")

    # Drive budget: low-priority text is dropped as whole columns (named in `truncated`); critical columns stay
    out = py(tmp, "import pipeline as P, csv, json; v=P.vault_load(); m=v['master']; P._CLIENT_CTX.update("
                  "clients=__import__('client_master').refresh(v), master=m); q=[x for x in m.values() "
                  "if x.get('status')=='ASTRA_QA_PENDING']; d=P.write_astra_queue('small.csv', q, budget=7000, "
                  "slot_start='2000-01-01T00:00+09:00'); "
                  "print(json.dumps([d, m['99700001']['provenance']['drive']]))")
    dropped, drive = json.loads(out)
    assert dropped and drive["critical_intact"] is True, (dropped, drive)
    small = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "small.csv"), encoding="utf-8"))}
    s1 = small["99700001"]
    assert s1["truncated"].startswith("truncated=true") and all(s1[c] == "" for c in dropped)
    for c in ("source_questions", "application_questions", "application_answers", "applicable_reward", "initial_reward",
              "ongoing_reward", "reward_status", "unverified_facts", "application_draft"):
        assert s1[c] == r1[c], c
    print(f"Drive budget: dropped {dropped} as whole columns (truncated=true); questions / reward / unverified kept")

    # over budget with rows from an earlier QA slot: those rows leave the Drive copy first (oldest first, kept in
    # the Vault), and no column is blanked while that is enough
    out = py(tmp, "import pipeline as P, json, copy; v=P.vault_load(); m=v['master']; "
                  "q=[x for x in m.values() if x.get('status')=='ASTRA_QA_PENDING']; "
                  "old=[]\nfor i in range(40):\n x=copy.deepcopy(q[0]); x['job_id']=99790000+i; x.pop('application', None); "
                  "x['queued_run']='2000-01-01 morning_full'; x['queued_at']='2000-01-01T05:%02d+09:00' % i; x['change_log']=[]; old.append(x)\n"
                  "big=P.write_astra_queue('big.csv', old + q, slot_start='2000-01-02T06:00+09:00'); "
                  "print(json.dumps([big, sum(1 for x in old if (x.get('provenance') or {}).get('drive', {}).get('excluded'))]))")
    cols_dropped, n_out = json.loads(out)
    big = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "big.csv"), encoding="utf-8"))}
    assert cols_dropped == [] and n_out > 0 and os.path.getsize(os.path.join(tmp, "big.csv")) <= 80000, (cols_dropped, n_out)
    assert "99700001" in big and big["99700001"]["source_questions"] == r1["source_questions"] and big["99700001"]["truncated"] == ""
    assert "99790000" not in big and "99790039" in big, "oldest rows leave first"
    print(f"Drive budget: {n_out} rows from an earlier QA slot left the Drive copy (Vault keeps them); no column blanked")

    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
