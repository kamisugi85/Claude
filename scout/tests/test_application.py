"""Application Queue checks on a temporary copy of scout/ (the real Vault is never modified).

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_application.py
Needs scout/data/<date>/app_source/ from a prior `app-check` and the latest Status Updates CSV
at scout/data/status_updates.csv.
"""
import copy
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(r.stdout + r.stderr)
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


def main():
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests"))
    ddir = max((os.path.join(tmp, "data", d) for d in os.listdir(os.path.join(tmp, "data"))
                if os.path.exists(os.path.join(tmp, "data", d, "app_drafts.json"))))
    date = os.path.basename(ddir)
    # fixed starting point regardless of the real Vault's progress: 3 jobs ready, no batch record
    subprocess.run([sys.executable, "-c", "import pipeline as P; v=P.vault_load(); "
                    "v['meta'].pop('review_batches', None); P.vault_save(v)"], cwd=tmp, check=True)
    for x in ("13481662", "13480694", "13481649"):
        setst(tmp, x, "READY_TO_APPLY")
    # a few jobs waiting for Astra QA (the real queue may be empty right after Astra's run)
    code = ("import pipeline as P; v=P.vault_load(); ids=[j for j,x in v['master'].items() "
            "if x.get('status')=='CLAUDE_REJECTED'][:3]; [P.set_status(v['master'][j],'ASTRA_QA_PENDING','test') "
            "for j in ids]; P.vault_save(v)")
    subprocess.run([sys.executable, "-c", code], cwd=tmp, check=True)
    good = json.load(open(os.path.join(ddir, "app_drafts.json"), encoding="utf-8"))
    base = copy.deepcopy(good[1])  # a 400-char article draft
    base["application_draft"] = "\n".join(l for l in base["application_draft"].split("\n") if "AI" not in l)
    setst(tmp, base["job_id"], "ASTRA_PASS")  # drafts are only accepted for ASTRA_PASS jobs
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
    expect_fail("not_astra_pass", lambda d: d.update(job_id=13468802))  # NEED_USER

    # Status Updates re-import stays idempotent, also when the sheet gains columns
    su = os.path.join(SRC, "data", "status_updates.csv")
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
    r = upd([{"job_id": "13481649", "new_status": "SKIPPED", "final_qa_status": "SKIP", "note": "今回見送り",
              "human_review_minutes": "4"}])
    assert r["tally"]["astra_reject"] == 0, r
    q, m = tables()
    assert m["13481649"]["status"] == "SKIPPED" and m["13481649"]["astra_verdict"] == "PASS"
    assert q["13481649"]["final_qa_status"] == "SKIP" and q["13481649"]["actual_net_per_human_min"] == "0.0"
    print("SKIP: status SKIPPED, Astra verdict kept, counted as 0 JPY for its review minutes")

    # actual tracking: applied -> accepted (via result) -> paid; aliases map to existing fields
    jid = "13480694"
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
    upd([{"job_id": "13481662", "new_status": "APPLIED", "human_review_minutes": "2"}])
    upd([{"job_id": "13481662", "result": "rejected"}])
    assert tables()[1]["13481662"]["status"] == "NOT_SELECTED"
    out = run(tmp, "metrics").stdout
    s = json.loads(out)["poc_actual"]
    assert s["measured"] == 3 and s["net_jpy"] == 352 and s["human_minutes"] == 14, s
    print("actual Net/Human Minutes:", s["net_per_human_min"], "(352 JPY / 14 min over paid+rejected+skipped)")

    # only Astra-signed rows count; a repeated verdict never moves a progressed job back
    r = upd([{"job_id": "13480694", "astra_verdict": "REJECT", "updated_by": "本人"}])
    assert tables()[1]["13480694"]["status"] == "PAID" and "Astra名義でない" in r["errors"][0], r
    upd([{"job_id": "13480694", "astra_verdict": "PASS"}])
    assert tables()[1]["13480694"]["status"] == "PAID"
    pend = next(j for j, row in tables()[1].items() if row["status"] == "ASTRA_QA_PENDING")
    upd([{"job_id": pend, "astra_verdict": "SKIPPED", "astra_reason": "今回見送り"}])
    assert tables()[1][pend]["status"] == "SKIPPED"
    print("Astra-only verdicts; SKIPPED verdict; no regression of progressed jobs")

    # post-QA routine: only PASS rows new in this import become targets; re-import and non-Astra rows give none
    pend2 = [j for j, row in tables()[1].items() if row["status"] == "ASTRA_QA_PENDING"][:2]
    pq = lambda stage: last_json(run(tmp, "postqa", stage, "--date", date).stdout)
    rows = [{"job_id": pend2[0], "astra_verdict": "PASS", "astra_reason": "t"},
            {"job_id": pend2[1], "astra_verdict": "PASS", "updated_by": "someone"}]
    upd(rows)
    t = pq("targets")
    assert t["targets"] == [pend2[0]] and t["action"] == "prepare", t
    upd(rows)  # same sheet again (e.g. the 06:00 run and the 07:1x run both import it)
    assert pq("targets") == {"new_status_changes": 0, "targets": [], "action": "none"}
    upd([])  # Astra QA did not run: nothing new
    assert pq("targets")["action"] == "none" and tables()[1][pend2[1]]["status"] == "ASTRA_QA_PENDING"
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
    ids = "13481662,13480694,13481649"
    run(tmp, "app-batch", "--ids", ids, "--minutes", "3", "--source", "本人報告")
    k = json.loads(run(tmp, "metrics").stdout)["kpi"]
    assert k["total"]["actual"]["human_review_minutes"] == 3 + 2 + 4 + 3, k["total"]  # per-job rows + batch
    r = upd([{"job_id": "13481649", "human_review_minutes": "1"}])
    assert any("バッチ実績" in e for e in r["errors"]), r
    assert {"auto", "professional", "total"} <= set(k) and "estimated" in k["auto"] and "actual" in k["auto"]
    print("batch minutes kept as a batch; KPI split auto/professional, estimated vs actual")

    # Status Updates: missing tracking columns are appended; existing cells/columns untouched; idempotent
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

    def write_csv_rows(p, rows):
        with open(p, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(rows)
        return p
    assert run(tmp, "su-columns", "--csv", su, "--verify", write_csv_rows(os.path.join(tmp, "b.csv"), bad),
               ok=False).returncode == 1
    assert run(tmp, "su-columns", "--csv", su, "--verify", synced, "--recheck",
               write_csv_rows(os.path.join(tmp, "c.csv"), bad), ok=False).returncode == 1
    before = tables()[1]
    rr = last_json(run(tmp, "apply-updates", "--csv", synced).stdout)
    assert tables()[1] == before, rr  # widened sheet re-applies nothing
    print("Status Updates columns: +%d missing, re-run no-op, tampering rejected" % len(r["missing"]))
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
