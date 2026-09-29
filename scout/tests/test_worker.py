"""Worker flow checks on a temporary copy of scout/ (the real Vault is never modified).

accepted -> escrow -> worker-save (READY_FOR_QA / ASTRA_QA_PENDING) -> Astra QA (FIX / HOLD / PASS)
-> client deliverable verified -> READY_TO_DELIVER + direct-link notice. Nothing is sent anywhere.

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_worker.py
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JID = "13481662"  # an applied job in the real data; the test turns it into an accepted one
FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd"
URL = f"https://docs.google.com/document/d/{FILE_ID}/edit"
TITLE = "不動産投資は怖い"
FINAL = "不動産投資と聞くと身構える方も多いと思います。\n空室や金利の変化など、事前に知っておきたい点を整理しました。"


def run(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r


def last_json(out):
    return json.loads(out[out.index("{"):])


def job(tmp):
    code = "import pipeline as P,json; print(json.dumps(P.vault_load()['master'][%r], ensure_ascii=False))" % JID
    return json.loads(subprocess.run([sys.executable, "-c", code], cwd=tmp, capture_output=True, text=True,
                                     check=True).stdout)


def main():
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests"))
    n = [0]

    def upd(**row):
        n[0] += 1
        row = {"job_id": JID, "updated_by": "Astra", "updated_at": f"2026-10-01 09:{n[0]:02d} JST", **row}
        p = os.path.join(tmp, "u.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, sorted(row))
            w.writeheader()
            w.writerow(row)
        return last_json(run(tmp, "apply-updates", "--csv", p).stdout)

    def save(**over):
        rec = {"client_instructions": {"theme": TITLE, "length": "400文字程度",
                                       "delivery": "CrowdWorksでWordまたはGoogleドキュメント"},
               "angle": "リスクを知って備える", "profile_facts_used": ["投資用区分マンション経験"],
               "draft_v1": FINAL + "（初稿）", "final": FINAL,
               "self_qa_final": {"クライアント条件に適合": "PASS", "未確認の本人経験を創作していない": "PASS"},
               "record_doc": "1InternalRecordDocId000000000000000000000"}
        rec.update(over)
        p = os.path.join(tmp, "rec.json")
        json.dump(rec, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        return run(tmp, "worker-save", "--id", JID, "--record", p, ok=False)

    def deliver(title=TITLE, content=None, url=URL, ftype="gdoc", file_id=FILE_ID):
        p = os.path.join(tmp, "exported.txt")
        open(p, "w", encoding="utf-8").write("﻿" + (content if content is not None else f"{TITLE}\r\n\r\n{FINAL}"))
        return run(tmp, "worker-deliver", "--id", JID, "--title", title, "--url", url, "--file-id", file_id,
                   "--type", ftype, "--exported", p, "--deadline", "2026-10-08", ok=False)

    # accepted by Astra; no escrow yet -> the worker may not store a final
    upd(new_status="ACCEPTED", astra_reason="受注成立", next_action="仮払い確認後に制作")
    assert job(tmp)["status"] == "ACCEPTED"
    assert save().returncode == 1 and "仮払い未確認" in save().stdout
    upd(new_status="IN_PROGRESS", note="仮払い完了を本人確認済み")
    assert job(tmp)["worker"]["escrow_confirmed"] and job(tmp)["status"] == "IN_PROGRESS"
    print("escrow gate: no worker record before 仮払い confirmation")

    # self-QA with FIX left, or a missing record item, is refused
    assert save(self_qa_final={"冗長な表現なし": "FIX"}).returncode == 1
    assert save(angle="").returncode == 1
    r = save()
    assert r.returncode == 0, r.stdout + r.stderr
    j = job(tmp)
    assert j["status"] == "READY_FOR_QA" and j["worker"]["status"] == "ASTRA_QA_PENDING"
    queue = {x["job_id"] for x in csv.DictReader(open(os.path.join(tmp, "out", "astra_queue.csv"), encoding="utf-8"))}
    assert JID not in queue  # never put back into the pre-application Astra Queue
    print("worker-save: READY_FOR_QA with worker state ASTRA_QA_PENDING, not in the Astra Queue")

    # no deliverable before Astra QA PASS
    r = deliver()
    assert r.returncode == 1 and "Astra QA PASSがない" in r.stdout
    # a non-Astra PASS does not count
    upd(astra_verdict="PASS", updated_by="本人")
    assert job(tmp)["worker"].get("astra_qa") is None
    # FIX -> back to the worker; HOLD -> stop
    upd(astra_verdict="FIX", astra_reason="結びを具体的に")
    j = job(tmp)
    assert j["status"] == "IN_PROGRESS" and j["worker"]["status"] == "REVISE"
    assert save().returncode == 0
    upd(astra_verdict="HOLD", astra_reason="クライアント指示の確認待ち")
    assert job(tmp)["worker"]["status"] == "HOLD" and deliver().returncode == 1
    print("Astra QA: only Astra rows count; FIX returns to the worker, HOLD stops")

    # PASS (Astra may write READY_TO_DELIVER; Claude still waits for a verified client file)
    upd(astra_verdict="PASS", new_status="READY_TO_DELIVER", astra_reason="QA PASS")
    j = job(tmp)
    assert j["status"] == "READY_FOR_QA" and j["worker"]["astra_qa"]["result"] == "PASS"
    # a final changed after PASS needs a new PASS
    assert save(final=FINAL + "追記。").returncode == 0
    assert "Astra QA PASS" in deliver().stdout
    assert save().returncode == 0
    upd(astra_verdict="PASS", astra_reason="QA PASS（再）")
    print("PASS is tied to the final it judged; a changed final needs a new PASS")

    # client file checks
    bad = {
        "internal file name": deliver(title="CW Worker 13481662｜不動産投資は怖い｜ASTRA_QA_PENDING"),
        "internal info in file": deliver(content=f"{TITLE}\n\n{FINAL}\n\nAstra QA：PASS"),
        "body differs from PASS": deliver(content=f"{TITLE}\n\n{FINAL}追記"),
        "folder url": deliver(url="https://drive.google.com/drive/folders/1SPikc8hyKldOq_IZmj3_x48mSR7wxa4e"),
        "url of another file": deliver(url="https://docs.google.com/document/d/1Zz9999999999999999999999999999999/edit"),
        "internal record doc": deliver(file_id="1InternalRecordDocId000000000000000000000",
                                       url="https://docs.google.com/document/d/1InternalRecordDocId000000000000000000000/edit"),
        "format not asked for": deliver(ftype="xlsx", url=f"https://drive.google.com/file/d/{FILE_ID}/view"),
    }
    for name, r in bad.items():
        assert r.returncode == 1, (name, r.stdout)
        assert job(tmp)["status"] == "READY_FOR_QA", name
    print("deliverable refused:", ", ".join(bad))

    r = deliver()
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout[r.stdout.index("【納品準備完了】"):]
    assert URL in out and "READY_TO_DELIVER" in out and "Astra QA：PASS" in out and "Googleドキュメント" in out
    assert "1InternalRecordDocId" not in out  # the notice carries the deliverable link only
    j = job(tmp)
    assert j["status"] == "READY_TO_DELIVER" and j["worker"]["delivery"]["url"] == URL
    m = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "job_master.csv"), encoding="utf-8"))}
    assert m[JID]["delivery_artifact_url"] == URL and m[JID]["delivery_artifact_type"] == "Googleドキュメント"
    assert m[JID]["worker_astra_qa"] == "PASS" and m[JID]["ready_to_deliver_at"]
    assert URL in run(tmp, "worker-notice", "--id", JID).stdout
    # later sheet rows never move it back, and Astra cannot set READY_TO_DELIVER for other jobs
    r = upd(astra_verdict="PASS", new_status="READY_TO_DELIVER")
    assert job(tmp)["status"] == "READY_TO_DELIVER"
    print("READY_TO_DELIVER with a verified direct link; Job Master carries delivery_artifact_url")
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
