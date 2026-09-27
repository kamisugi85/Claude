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
                if os.path.isdir(os.path.join(tmp, "data", d, "app_source"))))
    date = os.path.basename(ddir)
    good = json.load(open(os.path.join(ddir, "app_drafts.json"), encoding="utf-8"))
    base = copy.deepcopy(good[1])  # a 400-char article draft
    setst(tmp, base["job_id"], "ASTRA_PASS")  # drafts are only accepted for ASTRA_PASS jobs
    p = os.path.join(tmp, "ok.json")
    json.dump([base], open(p, "w", encoding="utf-8"), ensure_ascii=False)
    run(tmp, "app-merge", "--drafts", p, "--date", date)
    print("valid draft accepted")

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
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
