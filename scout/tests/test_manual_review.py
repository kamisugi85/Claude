"""Astra Manual Review Queue on a temporary copy of scout/ (sandbox: code only, a fresh test Vault with one
made-up applied job; no network, the live Vault / state / data are never read).

Run: python3 scout/tests/test_manual_review.py
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
NEW, OLD = "19990001", "99480001"  # a job unknown to the master, and an applied one (made up, seeded below)
DATE = "2026-10-01"


def run(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r


def last_json(out):
    i = min(x for x in (out.find("{"), out.find("[")) if x >= 0)
    return json.loads(out[i:])


def vault(tmp, code):
    return json.loads(subprocess.run([sys.executable, "-c", "import pipeline as P,json; v=P.vault_load(); " + code],
                                     cwd=tmp, capture_output=True, text=True, check=True).stdout)


def row(jid):
    return {"id": int(jid), "url": f"https://crowdworks.jp/public/jobs/{jid}", "title": "動画編集テスト案件",
            "category_id": 300, "expired_on": "2099-12-31", "tiers": ["manual"], "client_open_jobs": None,
            "client": {"userId": 1, "userDisplayName": "c", "isIdentityVerified": False, "averageScore": 0,
                       "jobOfferAchievementCount": 0},
            "entry": {"applicants": 1, "contracted": 0, "capacity": 3}, "desc": "素材支給。1本500円。",
            "pay": {"type": "fixed", "min": 30000, "max": 50000}, "ai_policy": "C", "ai_evidence": [],
            "requirements": [], "risk": [], "desc_hash": "x" + jid}


def ev(jid, **over):
    e = {"job_id": int(jid), "lane": "Auto", "triage": "PoC応募候補", "ai_completion": "90%",
         "human_minutes": "5-10分", "reason": "素材支給の短尺動画", "verdict": "候補", "gross_jpy": 500}
    e.update(over)
    return e


def main():
    tmp = sandbox.make()
    subprocess.run([sys.executable, "-c", "import pipeline as P; v=P.vault_load(); d=P.today(); v['master'][%r]={"
                    "'job_id': %s, 'url': 'https://crowdworks.jp/public/jobs/%s', 'title': '応募済みの動画編集', "
                    "'client': {'userId': 2, 'userDisplayName': 'c2'}, 'first_seen': '2026-09-27T05:00+09:00', "
                    "'expired_on': '2099-12-31', 'gross': 3000, 'net_est': 2400, 'risk_rule': [], 'actual': {}, "
                    "'eval': {'verdict': '候補', 'reason': 't', 'classification': 'B'}, 'status': 'APPLIED', "
                    "'status_history': [{'status': s, 'at': '2026-09-27T05:1%%d+09:00' %% i, 'by': 'test', 'note': ''} "
                    "for i, s in enumerate(['CLAUDE_CANDIDATE', 'ASTRA_QA_PENDING', 'ASTRA_PASS', 'APPLIED'])]}; "
                    "j=dict(v['master'][%r]); j.update(job_id=99480002, url='https://crowdworks.jp/public/jobs/99480002', "
                    "title='Astra見送りの記事', status='ASTRA_REJECT', astra={'astra_verdict': 'REJECT', 'astra_reason': 't', "
                    "'updated_by': 'Astra'}, status_history=[{'status': s, 'at': d + 'T05:1%%d+09:00' %% i, 'by': 'test', "
                    "'note': ''} for i, s in enumerate(['CLAUDE_CANDIDATE', 'ASTRA_QA_PENDING', 'ASTRA_REJECT'])]); "
                    "v['master']['99480002']=j; P.vault_save(v)" % (OLD, OLD, OLD, OLD)], cwd=tmp, check=True)
    # intake 1: Claude receives the URL (requested_by Astra); duplicates and non-job URLs are refused
    r = last_json(run(tmp, "manual-request", f"https://crowdworks.jp/public/jobs/{NEW}?ref=apiv1",
                      "https://example.com/x").stdout)
    assert r[0]["added"] and r[0]["status"] == "PENDING" and "error" in r[1]
    assert not last_json(run(tmp, "manual-request", NEW).stdout)[0]["added"]
    # intake 2: an Astra-signed Status Updates row; a non-Astra row is refused
    p = os.path.join(tmp, "su.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["job_id", "new_status", "updated_by", "updated_at", "note"])
        w.writeheader()
        w.writerow({"job_id": OLD, "new_status": "MANUAL_REVIEW", "updated_by": "Astra",
                    "updated_at": "2026-10-01 07:00 JST", "note": "本人がURLを指定"})
        w.writerow({"job_id": "19990002", "new_status": "MANUAL_REVIEW", "updated_by": "本人",
                    "updated_at": "2026-10-01 07:01 JST"})
    r = last_json(run(tmp, "apply-updates", "--csv", p, "--date", DATE).stdout)
    assert any("Astra名義" in e for e in r["errors"]), r
    q = vault(tmp, "print(json.dumps(v['meta']['manual_review']))")
    assert set(q) >= {NEW, OLD} and "19990002" not in q and q[OLD]["requested_by"] == "Astra"
    print("intake: URL or Astra row -> PENDING once; non-Astra rows and non-job URLs refused")

    # evaluation (fixture instead of fetching the page)
    ddir = os.path.join(tmp, "data", DATE)
    os.makedirs(ddir, exist_ok=True)
    json.dump([row(NEW), row(OLD)], open(os.path.join(ddir, "manual_pending.json"), "w", encoding="utf-8"))
    ep = os.path.join(tmp, "e.json")
    json.dump([ev(NEW, triage="応募する")], open(ep, "w", encoding="utf-8"), ensure_ascii=False)
    assert run(tmp, "manual-merge", "--evals", ep, "--date", DATE, ok=False).returncode == 1  # bad triage
    json.dump([ev(NEW), ev(OLD, triage="見送り候補", gross_jpy=None)], open(ep, "w", encoding="utf-8"),
              ensure_ascii=False)
    r = last_json(run(tmp, "manual-merge", "--evals", ep, "--date", DATE).stdout)
    st = vault(tmp, f"print(json.dumps({{k: v['master'][k]['status'] for k in ({NEW!r}, {OLD!r})}}))")
    assert st == {NEW: "ASTRA_QA_PENDING", OLD: "APPLIED"}, st  # applied job is never moved back
    q = vault(tmp, "print(json.dumps(v['meta']['manual_review']))")
    assert q[NEW]["status"] == q[OLD]["status"] == "REVIEWED" and "変更なし" in q[OLD]["review_result"]
    aq = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "astra_queue.csv"), encoding="utf-8"))}
    assert aq[NEW]["source"] == "manual(Astra)" and aq[NEW]["claude_triage"] == "PoC応募候補"
    assert aq[NEW]["lane"] == "Auto" and aq[NEW]["net_per_human_min"] == "40.0"  # 400 / 10 min
    assert OLD not in aq
    # a second merge of the same job is refused (already REVIEWED)
    assert run(tmp, "manual-merge", "--evals", ep, "--date", DATE, ok=False).returncode == 1
    print("first pass: new job -> Astra Queue with lane/triage/net per minute; applied job untouched")

    # Astra's PASS takes it into the existing application flow (no separate notification path);
    # a verdict relayed by the user is not Astra's own record, even when written under Astra's name
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["job_id", "astra_verdict", "updated_by", "updated_at", "note"])
        w.writeheader()
        w.writerow({"job_id": NEW, "astra_verdict": "PASS", "updated_by": "Astra", "updated_at": "2026-10-01 07:59 JST",
                    "note": "本人がチャットでClaudeへ転記"})
    r = last_json(run(tmp, "apply-updates", "--csv", p, "--date", DATE).stdout)
    assert any("転記・代理記録" in e for e in r["errors"]), r
    assert vault(tmp, f"print(json.dumps(v['master'][{NEW!r}]['status']))") == "ASTRA_QA_PENDING"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["job_id", "astra_verdict", "updated_by", "updated_at"])
        w.writeheader()
        w.writerow({"job_id": NEW, "astra_verdict": "PASS", "updated_by": "Astra", "updated_at": "2026-10-01 08:00 JST"})
    run(tmp, "apply-updates", "--csv", p, "--date", DATE)
    assert vault(tmp, f"print(json.dumps(v['master'][{NEW!r}]['status']))") == "ASTRA_PASS"
    print("Astra PASS -> ASTRA_PASS (Application Queue flow as usual)")

    # an earlier Astra REJECT goes back to Astra only on an explicit request, with the old reason shown;
    # a same-day deadline is not an application target
    rej = vault(tmp, "print(json.dumps(next(k for k, j in v['master'].items() if j['status'] == 'ASTRA_REJECT'"
                     " and k not in v['meta']['manual_review'])))")
    same = "19990003"
    run(tmp, "manual-request", rej, same, "--intent", "応募したい")
    today = vault(tmp, "print(json.dumps(P.today()))")
    rs = [row(rej), dict(row(same), expired_on=today)]
    json.dump(rs, open(os.path.join(ddir, "manual_pending.json"), "w", encoding="utf-8"))
    json.dump([ev(rej), ev(same)], open(ep, "w", encoding="utf-8"), ensure_ascii=False)
    run(tmp, "manual-merge", "--evals", ep, "--date", DATE)
    st = vault(tmp, f"print(json.dumps({{k: v['master'][k]['status'] for k in ({rej!r}, {same!r})}}))")
    assert st == {rej: "ASTRA_QA_PENDING", same: "CLAUDE_REJECTED"}, st
    q = vault(tmp, "print(json.dumps(v['meta']['manual_review']))")
    assert q[same]["review_result"].startswith("SAME_DAY_DEADLINE")
    aq = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "astra_queue.csv"), encoding="utf-8"))}
    assert aq[rej]["confirm_items"].startswith("前回Astra REJECT") and "本人応募意向：応募したい" in aq[rej]["source"]
    assert same not in aq
    print("re-review: earlier REJECT -> Astra again (reason shown, intent recorded); same-day deadline excluded")

    # UNKNOWN per-unit reward: the listing budget is noted, never used as the reward; a rule verdict that
    # was already C at evaluation time is not "AI terms got stricter"
    sys.path.insert(0, tmp)
    os.chdir(tmp)
    import application as A
    job = {"gross": None, "ai_policy_rule": "C", "eval": {"ai_condition": "A"}}
    rc = {"header_reward": {"min": 30000}, "ai_policy": "C", "desc_hash": "h", "key_lines": []}
    ch, notes = A.classify_changes({}, rc, job, "本文")
    assert ch == [] and any("単価は不明" in n for n in notes), (ch, notes)
    assert A.classify_changes({}, rc, dict(job, ai_policy_rule="A"), "本文")[0]  # rule A -> C is a change
    print("recheck: UNKNOWN reward noted only; rule baseline for AI terms")
    os.chdir(SRC)
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
