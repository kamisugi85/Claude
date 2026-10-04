"""Client Master: relationship-aware openings and client risk history (temp copy; real vault untouched).

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_client_master.py
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CID = "99000001"  # a client made up for the test

SETUP = r"""
import pipeline as P
v = P.vault_load()
m = v["master"]
def job(jid, name, status, hist, note=""):
    m[jid] = {"job_id": int(jid), "url": "https://crowdworks.jp/public/jobs/" + jid, "title": "テスト案件" + jid,
              "client": {"userId": int(CID), "userDisplayName": name}, "first_seen": "2026-09-20T05:00+09:00",
              "eval": {"verdict": "候補", "reason": "t", "classification": "B", "human_minutes": "5分"},
              "gross": 440, "net_est": 352, "risk_rule": [], "actual": {},
              "status": status, "status_history": [{"status": s, "at": "2026-09-2%dT07:00+09:00" % i, "by": "Astra",
                                                    "note": note} for i, s in enumerate(hist, 1)]}
CID = "__CID__"
job("99100001", "旧表示名", "WITHDRAWN", ["APPLIED", "WITHDRAWN"], "面談必須と判明したためユーザーが辞退")
job("99100002", "新表示名", "ASTRA_QA_PENDING", ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"])
job("99100003", "新表示名", "ASTRA_PASS", ["ASTRA_PASS"])
m["99100002"]["client"]["userId"] = int(CID)
P.vault_save(v)
"""


def py(tmp, code):
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r.stdout


def main():
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests"))
    py(tmp, SETUP.replace("__CID__", CID))
    lvl = lambda jid: json.loads(py(tmp, "import pipeline as P, client_master as C, json; v = P.vault_load(); "
                                         "c = C.refresh(v); print(json.dumps(C.level(c, v['master'], v['master'][%r])))"
                                         % jid))
    qa = lambda l, text: json.loads(py(tmp, "import client_master as C, json; "
                                            "print(json.dumps(C.opening_errors(%r, %r), ensure_ascii=False))" % (text, l)))

    # 6. same client id -> one Client Master record, whatever the display name
    c = json.loads(subprocess.run([sys.executable, "pipeline.py", "client-show", "--id", CID], cwd=tmp,
                                  capture_output=True, text=True, check=True).stdout)
    assert set(c["past_job_ids"]) == {"99100001", "99100002", "99100003"} and c["client_id"] == CID
    assert c["application_count"] == 1 and c["accepted_count"] == 0 and c["repeat_order_count"] == "UNKNOWN"
    print("one record per client id across jobs and display names; unknowns stay UNKNOWN")

    # 1. first contact -> はじめまして (a client with no other applied job)
    py(tmp, "import pipeline as P; v = P.vault_load(); v['master']['99100001']['status'] = 'ASTRA_REJECT'; "
            "v['master']['99100001']['status_history'] = [{'status': 'ASTRA_REJECT', 'at': 'x', 'by': 'Astra'}]; "
            "P.vault_save(v)")
    assert lvl("99100003") == "NONE"
    assert qa("NONE", "はじめまして。記事作成に応募いたします。") == []
    assert qa("NONE", "記事作成に応募いたします。")  # a first contact without the greeting fails
    print("first contact: opening はじめまして required")

    # 2. applied before -> no first-meeting phrase, no thanks for orders that never happened
    py(tmp, SETUP.replace("__CID__", CID))
    assert lvl("99100003") == "APPLIED"
    assert qa("APPLIED", "はじめまして。記事作成に応募いたします。")
    assert qa("APPLIED", "先日は別のご募集にも応募させていただきました。よろしくお願いいたします。") == []
    # 4. no past order -> "以前はご依頼ありがとうございました" is an exaggeration
    assert qa("APPLIED", "以前はご依頼いただき、ありがとうございました。")
    assert qa("NONE", "はじめまして。以前はお仕事をご依頼いただき、ありがとうございました。")
    print("applied before: no はじめまして, no invented past orders")

    # 3. accepted and delivered before -> opening acknowledges the past work; はじめまして fails QA
    py(tmp, "import pipeline as P; v = P.vault_load(); j = v['master']['99100001']; j['status'] = 'PAID'; "
            "j['status_history'] = [{'status': s, 'at': 'x', 'by': 'Astra', 'note': ''} for s in "
            "('APPLIED', 'ACCEPTED', 'DELIVERED', 'PAID')]; j['actual'] = {'revision_count': '1', "
            "'actual_net_reward': '352'}; P.vault_save(v)")
    assert lvl("99100003") == "DELIVERED"
    assert qa("DELIVERED", "はじめまして。記事作成に応募いたします。")
    assert qa("DELIVERED", "以前はお仕事をご依頼いただき、ありがとうございました。今回も応募いたします。") == []
    c = json.loads(subprocess.run([sys.executable, "pipeline.py", "client-show", "--id", CID], cwd=tmp,
                                  capture_output=True, text=True, check=True).stdout)
    assert c["delivered_count"] == 1 and c["paid_count"] == 1 and c["revision_history"][0]["revision_count"] == "1"
    rel = json.loads(subprocess.run([sys.executable, "pipeline.py", "client-show", "--job", "99100003"], cwd=tmp,
                                    capture_output=True, text=True, check=True).stdout)
    assert rel["opening"].startswith("以前はお仕事をご依頼いただき")
    print("delivered / paid before: past-work opening, はじめまして fails; Worker results reach the client")

    # 5. an interview asked for only after applying is shown to the next evaluation (never an auto-reject)
    py(tmp, SETUP.replace("__CID__", CID))
    subprocess.run([sys.executable, "pipeline.py", "export"], cwd=tmp, capture_output=True, text=True, check=True)
    # queued on 2026-09-2x: outside the Drive window (today / yesterday), so it is in the full local queue
    aq = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "astra_queue_full.csv"), encoding="utf-8"))}
    assert "応募後に面談要求1回（99100001" in aq["99100002"]["client_history"], aq["99100002"]["client_history"]
    st = json.loads(py(tmp, "import pipeline as P, json; print(json.dumps(P.vault_load()['master']['99100002']['status']))"))
    assert st == "ASTRA_QA_PENDING"
    c = json.loads(subprocess.run([sys.executable, "pipeline.py", "client-show", "--id", CID], cwd=tmp,
                                  capture_output=True, text=True, check=True).stdout)
    assert c["post_application_interview_request_count"] == 1
    assert c["interview_required_history"][0]["found_after_application"] is True
    summ = json.loads(py(tmp, "import pipeline as P, client_master as C, json; v = P.vault_load(); "
                              "print(json.dumps(C.summary(C.refresh(v), v['master'], "
                              "{'job_id': 1, 'client': {'userId': %s}}), ensure_ascii=False))" % CID))
    assert "応募後に面談要求" in summ  # the same line goes into Claude's first-pass input (prepare)
    print("post-application interview request: counted, shown in Astra Queue / first pass, not auto-rejected")

    # a withdrawal row from Status Updates reaches WITHDRAWN (and never regresses)
    p = os.path.join(tmp, "su.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["job_id", "new_status", "astra_reason", "updated_by", "updated_at"])
        w.writeheader()
        w.writerow({"job_id": "99100003", "new_status": "APPLIED", "astra_reason": "応募済み", "updated_by": "Astra",
                    "updated_at": "2026-09-30 08:00 JST"})
        w.writerow({"job_id": "99100003", "new_status": "WITHDRAWN", "astra_reason": "応募後に面談必須と判明し辞退",
                    "updated_by": "Astra", "updated_at": "2026-09-30 09:00 JST"})
    subprocess.run([sys.executable, "pipeline.py", "apply-updates", "--csv", p], cwd=tmp, capture_output=True,
                   text=True, check=True)
    c = json.loads(subprocess.run([sys.executable, "pipeline.py", "client-show", "--id", CID], cwd=tmp,
                                  capture_output=True, text=True, check=True).stdout)
    assert c["post_application_interview_request_count"] == 2
    print("Status Updates WITHDRAWN row -> WITHDRAWN, post_application_interview_request_count updated")

    # interview stated in the posting: rule exclusion flag (Scout)
    import re
    sys.path.insert(0, tmp)
    import collect
    pat = collect.RISK_PATTERNS["面談必須（募集文に明記）"]
    assert re.search(pat, "採用前にZoomで面談を実施します") and not re.search(pat, "面談なしで進めます")
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
