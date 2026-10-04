"""Application Queue checks (sandbox: code only, a fresh test Vault with a synthetic profile and synthetic
jobs; the live Vault, state/, data/ and the live Status Updates sheet are never read, so the test gives the
same result in a fresh clone, in the routines and whatever today's runs have done).

Run: python3 scout/tests/test_application.py
"""
import copy
import csv
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402

# made-up jobs: 3 that become READY_TO_APPLY, the draft fixture (BASE), one NEED_USER, Claude rejections
READY3 = ("99600001", "99600002", "99600003")
BASE_ID, NEED_ID = "99600020", "99600030"
REJ = tuple(str(99600040 + i) for i in range(6))
DESC = ("家計管理をテーマにしたコラム記事の作成をお願いします。\n【概要】\n・文字数：1記事あたり2,000文字程度\n"
        "・ウェブ上の情報を調べてご自身の言葉でまとめてください。AIツールの利用も可です。\n"
        "【報酬】\n1記事あたり2,000円（税抜）\n【納期】\n契約から1週間程度\n")
DRAFT = ("はじめまして。家計管理のコラム記事を担当させてください。\n"
         "公的機関の情報を確認しながら、読みやすい構成で2,000字程度にまとめ、期限内に納品いたします。\n"
         "どうぞよろしくお願いいたします。")
SETUP = r"""
import json, os, pipeline as P, application as A
v = P.vault_load(); m = v["master"]; d = P.today()
def job(jid, status_chain, cls="B", gross=2200, astra=None, src=True):
    m[jid] = {"job_id": int(jid), "url": "https://crowdworks.jp/public/jobs/" + jid, "title": "家計管理コラム" + jid,
              "client": {"userId": 99000000 + int(jid) % 1000, "userDisplayName": "c" + jid},
              "first_seen": d + "T05:00+09:00", "expired_on": "2099-12-31", "gross": gross,
              "net_est": round(gross * 0.8), "tiers": ["B"], "risk_rule": [], "actual": {},
              "eval": {"verdict": "候補", "reason": "t", "classification": cls, "human_minutes": "5分",
                       "ai_condition": "A", "ai_completion": "90%"},
              "status": status_chain[-1],
              "status_history": [{"status": s, "at": d + "T05:%02d+09:00" % (10 + i), "by": "test", "note": ""}
                                 for i, s in enumerate(status_chain)]}
    if astra:
        m[jid]["astra"] = {"astra_verdict": astra, "astra_reason": "t", "updated_by": "Astra",
                           "updated_at": d + " 06:00 JST"}
    if src:  # the posting as app-check saves it today
        s = {"desc": DESC, "desc_complete": True, "client": {"userId": 99000000 + int(jid) % 1000},
             "header_reward": {"type": "契約金額（目安）", "min": 2200, "max": None}, "deadline": "2099-12-31",
             "applicants": 3, "contracted": 0, "capacity": 5, "closed": False,
             "body_reward_mentions": ["1記事あたり2,000円（税抜）"]}
        P.save_json(os.path.join(P.ROOT, "data", d, "app_source", jid + ".json"), s)
        m[jid]["reward_check"] = {k: s[k] for k in ("header_reward", "deadline", "applicants", "contracted",
                                                    "capacity", "closed", "body_reward_mentions")}
        m[jid]["reward_check"].update(checked_at=P.now_iso(), changes=[], notes=[], ai_policy="A")
        A._record_source(m[jid], s)
PASS = ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING", "ASTRA_PASS"]
for i, jid in enumerate(READY3):
    job(jid, PASS, cls="C" if i == 0 else "B", astra="PASS")  # one Professional, two Auto
job(BASE_ID, PASS, astra="PASS")
job(NEED_ID, ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING", "NEED_USER"], astra="要確認", src=False)
for jid in REJ:
    job(jid, ["CLAUDE_REJECTED"], src=False)
P.vault_save(v)
"""
STATUS_UPDATES = [  # the Astra sheet as it was imported earlier (a re-import must change nothing)
    {"job_id": NEED_ID, "astra_verdict": "要確認", "astra_reason": "本人確認が必要", "new_status": "NEED_USER",
     "need_user": "yes", "next_action": "本人確認", "updated_at": "2026-09-27 03:40 JST", "updated_by": "Astra",
     "actual_human_minutes": "", "actual_ai_processing": "", "revision_count": "", "actual_gross_reward": "",
     "actual_net_reward": "", "result": "", "client_rating": "", "repeat_order": "", "note": "",
     "application_preparation_ai_time": "", "human_review_minutes": "", "applied_at": ""},
    {"job_id": BASE_ID, "astra_verdict": "PASS", "astra_reason": "AI可・調査型", "new_status": "ASTRA_PASS",
     "need_user": "no", "next_action": "応募準備", "updated_at": "2026-09-27 03:40 JST", "updated_by": "Astra",
     "actual_human_minutes": "", "actual_ai_processing": "", "revision_count": "", "actual_gross_reward": "",
     "actual_net_reward": "", "result": "", "client_rating": "", "repeat_order": "", "note": "",
     "application_preparation_ai_time": "", "human_review_minutes": "", "applied_at": ""},
]


def run(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(" ".join(args) + "\n" + r.stdout + r.stderr)
    return r


def setst(tmp, jid, status):
    code = ("import pipeline as P; v=P.vault_load(); j=v['master'][%r]; "
            "P.set_status(j, %r, 'test'); P.vault_save(v)" % (str(jid), status))
    subprocess.run([sys.executable, "-c", code], cwd=tmp, check=True)


def write_csv(path, rows):
    cols = sorted({k for r in rows for k in r})
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)
    return path


def last_json(out):
    return json.loads(out[out.index("{"):])


def draft(jid):
    return {"job_id": int(jid), "actual_reward": 2200, "reward_evidence": "1記事あたり2,000円（税抜）",
            "application_draft": DRAFT, "application_questions": [], "application_answers": [], "facts_used": [],
            "unverified_facts": [], "conflict_risk": "低：該当なし", "user_confirmation_required": "no",
            "review_minutes_est": 2}


def main():
    tmp = sandbox.make()
    os.remove(os.path.join(tmp, "routine.json"))  # the post-QA checks below cover the legacy (retired) import code
    subprocess.run([sys.executable, "-c", "READY3=%r\nBASE_ID=%r\nNEED_ID=%r\nREJ=%r\nDESC=%r\n"
                    % (READY3, BASE_ID, NEED_ID, REJ, DESC) + SETUP], cwd=tmp, check=True)
    date = subprocess.run([sys.executable, "-c", "import pipeline as P; print(P.today())"], cwd=tmp,
                          capture_output=True, text=True, check=True).stdout.strip()
    with open(os.path.join(tmp, "state", "runs.jsonl"), "w", encoding="utf-8") as fo:  # today's 05:00 run only
        fo.write(json.dumps({"date": date, "run_type": "morning_full", "run_at": date + "T05:20+09:00"}) + "\n")
    su = write_csv(os.path.join(tmp, "status_updates.csv"), STATUS_UPDATES)
    run(tmp, "apply-updates", "--csv", su, "--date", date)  # imported once before (fixture history)
    # 3 jobs ready to apply (drafted from today's re-read posting)
    p = os.path.join(tmp, "ready.json")
    json.dump([draft(x) for x in READY3], open(p, "w", encoding="utf-8"), ensure_ascii=False)
    run(tmp, "app-merge", "--drafts", p, "--date", date)
    # a few jobs waiting for Astra QA
    code = ("import pipeline as P; v=P.vault_load(); ids=[j for j,x in v['master'].items() "
            "if x.get('status')=='CLAUDE_REJECTED'][:3]; [P.set_status(v['master'][j],'ASTRA_QA_PENDING','test') "
            "for j in ids]; P.vault_save(v)")
    subprocess.run([sys.executable, "-c", code], cwd=tmp, check=True)
    base = draft(BASE_ID)
    # open the fixture draft the way the Client Master says
    rel = json.loads(run(tmp, "client-show", "--job", str(base["job_id"])).stdout)
    base["application_draft"] = re.sub(r"^(はじめまして|初めまして)。?", rel["opening"], base["application_draft"])
    p = os.path.join(tmp, "ok.json")
    json.dump([base], open(p, "w", encoding="utf-8"), ensure_ascii=False)
    run(tmp, "app-merge", "--drafts", p, "--date", date)
    st = lambda x: json.loads(subprocess.run([sys.executable, "-c", "import pipeline as P,json; "
                                              "print(json.dumps(P.vault_load()['master'][%r]['status']))" % str(x)],
                                             cwd=tmp, capture_output=True, text=True, check=True).stdout)
    assert st(base["job_id"]) == "READY_TO_APPLY"  # posting re-read today, nothing to confirm
    setst(tmp, base["job_id"], "ASTRA_PASS")
    hold = copy.deepcopy(base)
    hold.update(user_confirmation_required="yes", unverified_facts=["要確認"])
    json.dump([hold], open(p, "w", encoding="utf-8"), ensure_ascii=False)
    run(tmp, "app-merge", "--drafts", p, "--date", date)
    assert st(base["job_id"]) == "ASTRA_PASS"  # needs the user → not ready
    run(tmp, "app-merge", "--drafts", p, "--date", "2099-01-01", ok=False)  # no same-day posting re-check
    print("valid draft accepted; READY_TO_APPLY only after same-day re-check and nothing to confirm")

    def expect_fail(name, mutate):
        d = copy.deepcopy(base)
        mutate(d)
        p = os.path.join(tmp, f"bad_{name}.json")
        json.dump([d], open(p, "w", encoding="utf-8"), ensure_ascii=False)
        r = run(tmp, "app-merge", "--drafts", p, "--date", date, ok=False)
        assert r.returncode != 0 and str(d["job_id"]) in r.stdout, (name, r.stdout)
        print("rejected as expected:", name)

    expect_fail("fabricated_profile_ref", lambda d: d["facts_used"].append(
        {"fact": "Webライター経験3年", "profile_ref": "professional.writer_years"}))
    expect_fail("mentions_ai_unasked", lambda d: d.update(application_draft=d["application_draft"] + "\nAIで下書きします。"))
    expect_fail("listing_reward_not_body", lambda d: d.update(actual_reward=880))
    expect_fail("evidence_not_in_body", lambda d: d.update(reward_evidence="1記事800円"))
    expect_fail("question_not_in_body", lambda d: d.update(
        application_questions=["Q1. ライター歴は？"], application_answers=["5年"]))
    expect_fail("unverified_without_confirmation", lambda d: d.update(unverified_facts=["執筆実績"]))
    expect_fail("not_astra_pass", lambda d: d.update(job_id=int(NEED_ID)))  # NEED_USER

    # Status Updates re-import stays idempotent, also when the sheet gains columns
    r = last_json(run(tmp, "apply-updates", "--csv", su, "--date", date).stdout)
    assert r["applied"] == 0 and not any(r["tally"].values()), r
    rows = list(csv.DictReader(open(su, encoding="utf-8-sig")))
    wide = os.path.join(tmp, "wide.csv")
    cols = list(rows[0].keys()) + ["final_qa_status", "user_confirmed", "applied_at",
                                   "application_preparation_ai_time", "production_human_minutes"]
    with open(wide, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)
    r = last_json(run(tmp, "apply-updates", "--csv", wide, "--date", date).stdout)
    assert r["applied"] == 0, r
    print("status updates re-import: 0 applied (also with added columns)")

    def tables():
        q = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "application_queue.csv"), encoding="utf-8"))}
        m = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "job_master.csv"), encoding="utf-8"))}
        return q, m

    def upd(rows):
        rows = [{"updated_by": "Astra", **r} for r in rows]
        return last_json(run(tmp, "apply-updates", "--csv", write_csv(os.path.join(tmp, "u.csv"), rows),
                             "--date", date).stdout)

    # SKIP is its own status, not an Astra rejection; the job stays visible with its reason
    r = upd([{"job_id": READY3[2], "new_status": "SKIPPED", "final_qa_status": "SKIP", "note": "今回見送り",
              "human_review_minutes": "4"}])
    assert r["tally"]["astra_reject"] == 0, r
    q, m = tables()
    assert m[READY3[2]]["status"] == "SKIPPED" and m[READY3[2]]["astra_verdict"] == "PASS"
    assert q[READY3[2]]["final_qa_status"] == "SKIP" and q[READY3[2]]["actual_net_per_human_min"] == "0.0"
    print("SKIP: status SKIPPED, Astra verdict kept, counted as 0 JPY for its review minutes")

    # actual tracking: applied -> accepted (via result) -> paid; aliases map to existing fields
    jid = READY3[1]
    upd([{"job_id": jid, "new_status": "APPLIED", "applied_at": "2026-09-28", "human_review_minutes": "3",
          "gen_minutes": "2"}])
    upd([{"job_id": jid, "accept_result": "accepted"}])
    assert tables()[1][jid]["status"] == "ACCEPTED"
    upd([{"job_id": jid, "new_status": "PAID", "production_human_minutes": "5", "production_ai_time": "2分",
          "revision_count": "0", "actual_net_reward": "352"}])
    q, m = tables()
    assert q[jid]["application_preparation_ai_time"] == "2" and q[jid]["production_human_minutes"] == "5"
    assert q[jid]["actual_net_per_human_min"] == "44.0", q[jid]  # 352 / (3 + 5)
    # a rejected application earns 0 for its review time
    upd([{"job_id": READY3[0], "new_status": "APPLIED", "human_review_minutes": "2"}])
    upd([{"job_id": READY3[0], "result": "rejected"}])
    assert tables()[1][READY3[0]]["status"] == "NOT_SELECTED"
    out = run(tmp, "metrics").stdout
    s = json.loads(out)["poc_actual"]
    assert s["measured"] == 3 and s["net_jpy"] == 352 and s["human_minutes"] == 14, s
    print("actual Net/Human Minutes:", s["net_per_human_min"], "(352 JPY / 14 min over paid+rejected+skipped)")

    # only Astra-signed rows count; a repeated verdict never moves a progressed job back
    r = upd([{"job_id": READY3[1], "astra_verdict": "REJECT", "updated_by": "本人"}])
    assert tables()[1][READY3[1]]["status"] == "PAID" and "Astra名義でない" in r["errors"][0], r
    upd([{"job_id": READY3[1], "astra_verdict": "PASS"}])
    assert tables()[1][READY3[1]]["status"] == "PAID"
    pend = next(j for j, row in tables()[1].items() if row["status"] == "ASTRA_QA_PENDING")
    upd([{"job_id": pend, "astra_verdict": "SKIPPED", "astra_reason": "今回見送り"}])
    assert tables()[1][pend]["status"] == "SKIPPED"
    print("Astra-only verdicts; SKIPPED verdict; no regression of progressed jobs")

    # post-QA routine: only PASS rows new in this import become targets; re-import and non-Astra rows give none
    # still open: an expired PASS is never a target, and the live queue ages day by day
    today = subprocess.run([sys.executable, "-c", "import pipeline as P; print(P.today())"], cwd=tmp,
                           capture_output=True, text=True, check=True).stdout.strip()
    eligible = json.loads(subprocess.run([sys.executable, "-c", (
        "import pipeline as P, json; m=P.vault_load()['master']; print(json.dumps([j for j, x in m.items() "
        "if x.get('status')=='ASTRA_QA_PENDING' and not x.get('application') "
        "and not (x.get('reward_check') or {}).get('changes') "
        "and ((x.get('reward_check') or {}).get('deadline') or x.get('expired_on') or '9999') >= %r]))" % today)],
        cwd=tmp, capture_output=True, text=True, check=True).stdout)
    pend2 = [j for j in tables()[1] if j in eligible][:2]
    pq = lambda stage: last_json(run(tmp, "postqa", stage, "--date", date).stdout)
    rows = [{"job_id": pend2[0], "astra_verdict": "PASS", "astra_reason": "t"},
            {"job_id": pend2[1], "astra_verdict": "PASS", "updated_by": "someone"}]
    upd(rows)
    t = pq("targets")
    assert t["targets"] == [pend2[0]] and t["action"] == "prepare", t
    upd(rows)  # same sheet again (e.g. the 06:00 run and the 07:1x run both import it)
    # no new status change; the PASS still without a draft stays a (carried-over) target, listed once
    t = pq("targets")
    assert t["new_status_changes"] == 0 and t["targets"].count(pend2[0]) == 1, t
    upd([])  # Astra QA did not run: nothing new
    t = pq("targets")
    assert t["new_status_changes"] == 0 and pend2[1] not in t["targets"] and \
        tables()[1][pend2[1]]["status"] == "ASTRA_QA_PENDING"
    setst(tmp, pend2[0], "SKIPPED")  # once it is no longer an undrafted PASS, nothing is left to prepare
    assert pend2[0] not in pq("targets")["targets"]
    rp = os.path.join(tmp, "state", "runs.jsonl")
    keep = open(rp, encoding="utf-8").read()
    open(rp, "a", encoding="utf-8").write(json.dumps({"date": "2099-01-02"}) + "\n")
    assert last_json(run(tmp, "postqa", "guard", "--date", "2099-01-02").stdout)["ok"] is True
    open(rp, "w", encoding="utf-8").write(keep)
    assert last_json(run(tmp, "postqa", "guard", "--date", "2099-01-02").stdout)["ok"] is False
    # 06:30 routine: only Astra rows written today after the 05:00 Scout run count as today's QA
    open(rp, "a", encoding="utf-8").write(json.dumps({"date": "2099-01-03", "run_at": "2099-01-03T05:20+09:00"}) + "\n")
    pend3 = [j for j, row in tables()[1].items() if row["status"] == "ASTRA_QA_PENDING"]
    def astra(rows):
        return last_json(run(tmp, "postqa", "astra", "--csv", write_csv(os.path.join(tmp, "su.csv"), rows),
                             "--date", "2099-01-03").stdout)
    base_rows = [{"job_id": "1", "astra_verdict": "PASS", "updated_by": "Astra", "updated_at": "2099-01-02 06:00 JST"}]
    assert astra(base_rows)["ok"] is False  # only yesterday's rows: Astra QA not (yet) run today
    assert astra([{**base_rows[0], "updated_at": "2099-01-03 04:00 JST"}])["ok"] is False  # before today's Scout
    assert astra([{**base_rows[0], "updated_at": "2099-01-03 06:05 JST", "updated_by": "本人"}])["ok"] is False
    r = astra([{"job_id": j, "astra_verdict": "REJECT", "updated_by": "Astra", "updated_at": "2099-01-03 06:00 JST"}
               for j in pend3[:1]])
    assert r["ok"] is True and r["complete"] is (len(pend3) == 1), r
    r = astra([{"job_id": j, "astra_verdict": "REJECT", "updated_by": "Astra", "updated_at": "2099-01-03"}
               for j in pend3])
    assert r["ok"] and r["complete"], r
    open(rp, "w", encoding="utf-8").write(keep)
    assert last_json(run(tmp, "postqa", "astra", "--csv", os.path.join(tmp, "su.csv"), "--date", "2099-01-03").stdout)["ok"] is False
    print("06:30 guard: needs Astra rows written today after the Scout run; partial QA reported as incomplete")
    print("post-QA routine: new Astra PASS only, re-import/non-Astra/no-QA give no targets, guard needs today's Scout")

    # batch human time: kept as one total, never split per job; per-job minutes for it are ignored
    ids = ",".join(READY3)
    run(tmp, "app-batch", "--ids", ids, "--minutes", "3", "--source", "本人報告")
    k = json.loads(run(tmp, "metrics").stdout)["kpi"]
    assert k["total"]["actual"]["human_review_minutes"] == 3 + 2 + 4 + 3, k["total"]  # per-job rows + batch
    r = upd([{"job_id": READY3[2], "human_review_minutes": "1"}])
    assert any("バッチ実績" in e for e in r["errors"]), r
    assert {"auto", "professional", "total"} <= set(k) and "estimated" in k["auto"] and "actual" in k["auto"]
    print("batch minutes kept as a batch; KPI split auto/professional, estimated vs actual")

    # Status Updates: missing tracking columns are appended; existing cells/columns untouched; idempotent
    # (fixture = the live sheet as it was before the tracking columns, whatever it has gained since)
    def write_csv_rows(p, rows):
        with open(p, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(rows)
        return p
    track = ("application_preparation_ai_time", "human_review_minutes", "applied_at")
    grid = list(csv.reader(open(su, encoding="utf-8-sig")))
    keep = [i for i, c in enumerate(grid[0]) if c not in track]
    su = write_csv_rows(os.path.join(tmp, "su_old.csv"), [[r[i] for i in keep] for r in grid])
    r = last_json(run(tmp, "su-columns", "--csv", su).stdout)
    assert r["missing"] == ["application_preparation_ai_time", "human_review_minutes", "applied_at"], r
    synced = r["file"]
    old, new = list(csv.reader(open(su, encoding="utf-8-sig"))), list(csv.reader(open(synced, encoding="utf-8")))
    n = len(old[0])
    assert new[0] == old[0] + r["missing"] and all(b[:n] == a[:n] for a, b in zip(old, new))
    assert run(tmp, "su-columns", "--csv", su, "--verify", synced, "--recheck", su).returncode == 0
    assert last_json(run(tmp, "su-columns", "--csv", synced).stdout)["action"] == "none"
    bad = [row[:] for row in new]
    bad[1][2] += "x"

    assert run(tmp, "su-columns", "--csv", su, "--verify", write_csv_rows(os.path.join(tmp, "b.csv"), bad),
               ok=False).returncode == 1
    assert run(tmp, "su-columns", "--csv", su, "--verify", synced, "--recheck",
               write_csv_rows(os.path.join(tmp, "c.csv"), bad), ok=False).returncode == 1
    before = tables()[1]
    rr = last_json(run(tmp, "apply-updates", "--csv", synced).stdout)
    assert tables()[1] == before, rr  # widened sheet re-applies nothing
    print("Status Updates columns: +%d missing, re-run no-op, tampering rejected" % len(r["missing"]))

    # Drive copies stay within the upload budget; the full master stays local; drive-status flags stale sheets
    run(tmp, "export")
    man = json.load(open(os.path.join(tmp, "out", "sync_manifest.json"), encoding="utf-8"))
    full = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "job_master_full.csv"),
                                                        encoding="utf-8"))}
    q, m = tables()
    assert all(x["status"] not in ("CLAUDE_REJECTED", "RULE_REJECTED", "CLOSED") for x in m.values())
    assert any(x["status"] == "CLAUDE_REJECTED" for x in full.values()) and set(m) < set(full)
    keep = {"READY_TO_APPLY", "APPLIED", "SKIPPED", "ACCEPTED", "IN_PROGRESS", "READY_FOR_QA"}
    assert {i for i, x in full.items() if x["status"] in keep} <= set(m)  # in-flight jobs are never hidden
    assert "key_excerpt" not in next(iter(q.values())) and all(
        x["application_draft"] for x in q.values() if x["status"] == "READY_TO_APPLY")
    # the Drive copy is the compact one; going over the budget is never silent (RUNBOOK 5: upload anyway
    # and report it), whatever size the live data has reached
    assert os.path.getsize(os.path.join(tmp, "out", "job_master.csv")) < \
        os.path.getsize(os.path.join(tmp, "out", "job_master_full.csv"))
    over = [k for k, b in man["bytes"].items() if b > man.get("budget_by_file", {}).get(k, man["budget_bytes"])]
    assert last_json(run(tmp, "drive-status", ok=False).stdout)["over_budget"] == over
    keys = "job_master,application_queue"
    assert run(tmp, "drive-status", "--keys", keys, ok=False).returncode == 1
    run(tmp, "set-drive", "job_master_sheet", "x1")
    assert last_json(run(tmp, "drive-status", "--keys", keys, ok=False).stdout)["stale"] == ["application_queue"]
    run(tmp, "set-drive", "application_queue_sheet", "x2")
    assert last_json(run(tmp, "drive-status", "--keys", keys).stdout)["ok"]
    print("Drive copies compact, over-budget reported (%s); drive-status catches a skipped upload" % man["bytes"])
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
