"""05:00 flow: application drafts are made for every Astra Queue job *before* Astra's QA
(temporary copy of scout/; the real Vault is never modified). No Astra verdict is read or needed.

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_predraft.py
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CID_NEW, CID_APPLIED, CID_DELIVERED = "99000031", "99000032", "99000033"  # clients made up for the test
DESC = ("記事作成のお仕事です。\n【報酬】1記事あたり2,000円（税抜）\n【応募時の質問】\n"
        "・NISAの利用経験はありますか\n・好きな旅行先を教えてください\n・お名前を教えてください\n")
SETUP = r"""
import json, os, pipeline as P
v = P.vault_load(); m = v["master"]; d = P.today()
def job(jid, cid, status, hist, due=True, gross=2200):
    m[jid] = {"job_id": int(jid), "url": "https://crowdworks.jp/public/jobs/" + jid, "title": "テスト記事" + jid,
              "client": {"userId": int(cid), "userDisplayName": "c" + cid}, "first_seen": d + "T05:00+09:00",
              "eval": {"verdict": "候補", "reason": "t", "classification": "B", "human_minutes": "5分"},
              "gross": gross, "net_est": round(gross * 0.8), "risk_rule": [], "actual": {}, "expired_on": "2099-12-31",
              "status": status, "status_history": [{"status": s, "at": d + "T05:1%d+09:00" % i, "by": "test", "note": ""}
                                                   for i, s in enumerate(hist)]}
    if status == "ASTRA_QA_PENDING":
        m[jid]["reward_check"] = {"checked_at": d + "T05:31+09:00", "changes": [], "deadline": "2099-12-31", "closed": False}
        if due:
            m[jid]["pre_draft_due"] = True
        os.makedirs(os.path.join(P.ROOT, "data", d, "app_source"), exist_ok=True)
        # 99400001's posting asks only the NISA question (its draft answers everything the posting asks)
        # each draft below answers what its own posting asks: 99400001 only the NISA question, 99400002-04 none
        desc = DESC.replace("・好きな旅行先を教えてください\n", "").replace("・お名前を教えてください\n", "") \
            if jid == "99400001" else DESC.split("【応募時の質問】")[0] if jid in ("99400002", "99400003", "99400004") else DESC
        P.save_json(os.path.join(P.ROOT, "data", d, "app_source", jid + ".json"), {"desc": desc})
        __import__("application")._record_source(m[jid], {"desc": desc, "desc_complete": True})  # as app-check does
PEND = ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"]
job("99400001", CID_NEW, "ASTRA_QA_PENDING", PEND)          # new client
job("99400002", CID_APPLIED, "ASTRA_QA_PENDING", PEND)      # applied before
job("99400003", CID_DELIVERED, "ASTRA_QA_PENDING", PEND)    # delivered before
job("99400004", CID_NEW, "ASTRA_QA_PENDING", PEND)          # needs the user (unregistered experience)
job("99400005", CID_NEW, "ASTRA_QA_PENDING", PEND, gross=220)  # cheap + personal preference only
job("99400006", CID_NEW, "ASTRA_QA_PENDING", PEND, due=False)  # queued before this flow: never drafted
job("99500001", CID_APPLIED, "APPLIED", ["APPLIED"])
job("99500002", CID_DELIVERED, "PAID", ["APPLIED", "ACCEPTED", "DELIVERED", "PAID"])
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


def job(tmp, jid):
    return json.loads(py(tmp, "import pipeline as P, json; print(json.dumps(P.vault_load()['master'][%r], ensure_ascii=False))" % jid))


def draft(jid, opening, answers=None, unverified=None, questions=None, reward=2200, ev="1記事あたり2,000円（税抜）",
          facts=None):
    return {"job_id": jid, "actual_reward": reward, "reward_evidence": ev,
            "application_draft": opening + "記事作成のご募集を拝見し、応募いたします。構成を整えて期限内に納品いたします。"
                                            "どうぞよろしくお願いいたします。",
            "application_questions": questions or [], "application_answers": answers or [],
            "facts_used": facts or [], "unverified_facts": unverified or [], "conflict_risk": "低：該当なし",
            "user_confirmation_required": "yes" if unverified else "no", "review_minutes_est": 2}


def merge(tmp, drafts, ok=True):
    p = os.path.join(tmp, "drafts.json")
    json.dump(drafts, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return run(tmp, "app-merge", "--drafts", p, ok=ok)


def main():
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests"))
    py(tmp, "DESC=%r\nCID_NEW=%r\nCID_APPLIED=%r\nCID_DELIVERED=%r\n" % (DESC, CID_NEW, CID_APPLIED, CID_DELIVERED) + SETUP)

    # the 06:30 / 12:30 Astra-verdict import is retired: the guard stops before touching anything
    g = js(run(tmp, "postqa", "guard", "--now", "06:30"))
    assert g["ok"] is False and g["mode"] == "retired", g
    assert js(run(tmp, "postqa", "guard", "--now", "12:30"))["ok"] is False
    print("06:30 / 12:30 guard: retired (no Status Updates import in the daily flow)")

    # app-plan: today's Astra Queue jobs only; the relationship decides the opening; no Astra row anywhere
    plan = js(run(tmp, "app-plan", "--cap", "50"))
    want = {"99400001", "99400002", "99400003", "99400004", "99400005"}
    assert set(plan["draft_now"]) == want and "99400006" not in plan["draft_now"], plan
    assert plan["client"]["99400001"]["relationship"] == "NONE"
    assert plan["client"]["99400002"]["relationship"] == "APPLIED"
    assert plan["client"]["99400003"]["relationship"] == "DELIVERED"
    assert plan["client"]["99400003"]["opening"].startswith("以前はお仕事をご依頼いただき")
    facts = {f["fact"]: f["ref"] for f in plan["confirmed_facts"]}
    assert "NISAの利用経験がある" in facts
    print("app-plan: Astra Queue jobs of today only (older queue rows untouched); relationship + opening per client")

    # new client: complete from known facts alone, no question left for the user
    nisa = [{"fact": "NISAの利用経験がある", "profile_ref": facts["NISAの利用経験がある"]}]
    qs = ["・NISAの利用経験はありますか"]
    ok1 = draft("99400001", "はじめまして。", questions=qs, answers=["はい、NISAを利用した経験があります。"], facts=nisa)
    merge(tmp, [ok1])
    j = job(tmp, "99400001")
    assert j["status"] == "ASTRA_QA_PENDING", j["status"]  # a draft only: never READY, no Astra verdict needed
    app = j["application"]
    assert app["final_qa_status"] == "CLAUDE_QA_PASSED" and app["stage"] == "PRE_ASTRA"
    assert app["user_confirmation_required"] == "no" and app["unverified_facts"] == [] and not j.get("pre_draft_due")
    assert not j.get("astra")  # Astra's verdict is neither read nor invented
    print("new client, known profile only: draft complete, CLAUDE_QA_PASSED, still ASTRA_QA_PENDING (never READY)")

    # applied before: no はじめまして; delivered before: past-work opening
    r = merge(tmp, [draft("99400002", "はじめまして。")], ok=False)
    assert any("はじめまして" in e for e in js(r)["failed"]["99400002"]), r.stdout
    merge(tmp, [draft("99400002", plan["client"]["99400002"]["opening"])])
    assert "はじめまして" not in job(tmp, "99400002")["application"]["application_draft"]
    r = merge(tmp, [draft("99400003", "はじめまして。")], ok=False)
    assert "99400003" in js(r)["failed"]
    merge(tmp, [draft("99400003", plan["client"]["99400003"]["opening"])])
    assert job(tmp, "99400003")["application"]["application_draft"].startswith("以前はお仕事をご依頼いただき")
    assert all(job(tmp, x)["status"] == "ASTRA_QA_PENDING" for x in ("99400002", "99400003"))
    print("applied client: はじめまして fails; delivered client: past-work opening; both stay drafts")

    # no invented experience: a fact that is not in the profile cannot back the draft; known facts are not re-asked
    fake = draft("99400004", "はじめまして。", facts=[{"fact": "暗号資産の売買経験がある", "profile_ref": "professional.crypto"}])
    r = merge(tmp, [fake], ok=False)
    assert any("プロフィールに無い" in e for e in js(r)["failed"]["99400004"]), r.stdout
    reask = draft("99400004", "はじめまして。", questions=qs, answers=["【本人記入】"], unverified=["NISAの利用経験"])
    r = merge(tmp, [reask], ok=False)
    assert any("本人確認済み" in e for e in js(r)["failed"]["99400004"]), r.stdout
    name = draft("99400004", "はじめまして。", questions=["・お名前を教えてください"], answers=["【本人記入】"],
                 unverified=["お名前"])
    r = merge(tmp, [name], ok=False)
    assert any("直接入力" in e for e in js(r)["failed"]["99400004"]), r.stdout
    print("unregistered experience is not invented; NISA is not asked again; the name typed on CrowdWorks is not a question")

    # what really needs the user is flagged (and only that): unregistered own experience
    need = draft("99400004", "はじめまして。", unverified=["暗号資産の売買経験（プロフィール未登録の本人経験）"])
    merge(tmp, [need])
    j = job(tmp, "99400004")
    assert j["status"] == "ASTRA_QA_PENDING" and j["application"]["user_confirmation_required"] == "yes"
    assert j["application"]["final_qa_status"] == "CLAUDE_QA_FLAGGED" and "本人確認" in j["application"]["next_action"]
    # a 220-yen job waiting only on a personal preference: not sent to the user, Astra REJECT candidate
    cheap = draft("99400005", "はじめまして。", questions=["・好きな旅行先を教えてください"], answers=["【本人記入】"],
                  unverified=["好きな旅行先（本人の好み）"], reward=220, ev="1記事あたり200円（税抜）")
    open(os.path.join(tmp, "data", py(tmp, "import pipeline as P; print(P.today())").strip(), "app_source",
                      "99400005.json"), "w", encoding="utf-8").write(json.dumps({"desc": DESC.replace("2,000円", "200円")},
                                                                                ensure_ascii=False))
    merge(tmp, [cheap])
    a5 = job(tmp, "99400005")["application"]
    assert a5["confirm_cost"] == "REJECT_CANDIDATE" and a5["user_confirmation_required"] == "no"
    assert "REJECT候補" in a5["next_action"] or "確認コスト" in a5["next_action"], a5["next_action"]
    print("only real needs are flagged; low price + personal-only question -> Astra REJECT candidate, user not asked")

    # idempotent: the same job_id / same drafts twice change nothing
    before = json.dumps({x: job(tmp, x) for x in want}, sort_keys=True)
    r = merge(tmp, [ok1, draft("99400002", plan["client"]["99400002"]["opening"])])
    assert set(js(r)["already_drafted"]) == {"99400001", "99400002"} and not js(r)["merged"], r.stdout
    assert not (set(js(run(tmp, "app-plan", "--cap", "50"))["draft_now"]) & want)
    assert before == json.dumps({x: job(tmp, x) for x in want}, sort_keys=True)
    print("same job_id again: no second draft, no change")

    # Astra Queue carries the application prep; Application Queue / READY notice do not list pre-drafts
    q = {r["job_id"]: r for r in csv.DictReader(open(os.path.join(tmp, "out", "astra_queue.csv"), encoding="utf-8"))}
    for c in ("application_draft", "application_questions", "application_answers", "facts_used", "unverified_facts",
              "client_history", "final_qa_status", "user_confirmation_required"):
        assert c in q["99400001"], c
    assert q["99400001"]["application_draft"].startswith("はじめまして。") and "NISA" in q["99400001"]["facts_used"]
    assert q["99400001"]["final_qa_status"] == "CLAUDE_QA_PASSED" and q["99400004"]["user_confirmation_required"] == "yes"
    assert q["99400006"]["final_qa_status"] == "NO_DRAFT" and q["99400006"]["application_draft"] == ""
    assert "APPLIED" in q["99400002"]["client_history"] or "応募" in q["99400002"]["client_history"]
    aq = {r["job_id"] for r in csv.DictReader(open(os.path.join(tmp, "out", "application_queue.csv"), encoding="utf-8"))}
    assert not aq & want, aq
    assert run(tmp, "ready-notice").stdout.strip() == "（READY_TO_APPLYの案件なし）"
    assert all(job(tmp, x)["status"] != "READY_TO_APPLY" for x in want)
    print("Astra Queue has draft / questions / answers / facts / client history / QA status; Application Queue and READY notice untouched")

    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
