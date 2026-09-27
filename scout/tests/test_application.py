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
    cols = list(rows[0].keys()) + ["final_qa_status", "user_confirmed", "applied_at", "gen_minutes"]
    with open(wide, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)
    r = last_json(run(tmp, "apply-updates", "--csv", wide, "--date", date).stdout)
    assert r["applied"] == 0, r
    print("status updates re-import: 0 applied (also with added columns)")

    # Astra final QA write-back goes to the application, not to the Astra verdict/status
    qa = os.path.join(tmp, "qa.csv")
    with open(qa, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["job_id", "final_qa_status", "gen_minutes", "updated_by"])
        w.writeheader()
        w.writerow({"job_id": "13481649", "final_qa_status": "PASS", "gen_minutes": "3", "updated_by": "Astra"})
    r = last_json(run(tmp, "apply-updates", "--csv", qa, "--date", date).stdout)
    assert not any(r["tally"].values()), r
    q = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "application_queue.csv"), encoding="utf-8"))}
    m = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "job_master.csv"), encoding="utf-8"))}
    assert q["13481649"]["final_qa_status"] == "PASS" and q["13481649"]["gen_minutes"] == "3"
    assert m["13481649"]["status"] == "ASTRA_PASS" and m["13481649"]["app_final_qa_status"] == "PASS"
    print("final QA write-back: application updated, status unchanged")
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
