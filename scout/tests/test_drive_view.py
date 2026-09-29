"""Drive Job Master = lightweight operational index; the vault / job_master_full.csv keep everything.

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_drive_view.py
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG = ("claude_reason", "astra_reason", "hourly_est", "repeatability", "client_risk", "client",
        "actual_human_minutes", "app_prep_ai_time")


def run(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(r.stdout + r.stderr)
    return r


def rows(tmp, name):
    return {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", name), encoding="utf-8"))}


def main():
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests"))
    vault = json.loads(subprocess.run([sys.executable, "-c", "import pipeline as P,json; "
                                       "print(json.dumps(P.vault_load()['master'], ensure_ascii=False))"],
                                      cwd=tmp, capture_output=True, text=True, check=True).stdout)
    run(tmp, "export")
    drive, full = rows(tmp, "job_master.csv"), rows(tmp, "job_master_full.csv")
    dcols = next(iter(drive.values())).keys()
    fcols = next(iter(full.values())).keys()

    # 1. the full master keeps every column and every non-closed job of the vault
    assert set(full) == {k for k, j in vault.items() if j.get("status") != "CLOSED"}
    assert set(LONG) <= set(fcols) and {"delivery_artifact_url", "net_per_human_min", "lane"} <= set(fcols)
    # 2. long texts are not on Drive; the operational columns are
    assert not set(LONG) & set(dcols), set(LONG) & set(dcols)
    need = {"job_id", "title", "url", "lane", "gross_jpy", "net_est_jpy", "expired_on", "status", "verdict",
            "astra_verdict", "need_user", "next_action", "ai_completion", "human_minutes_est",
            "net_per_human_min", "app_final_qa_status", "worker_status", "delivery_artifact_url", "status_updated"}
    assert need <= set(dcols), need - set(dcols)
    print(f"Drive view {len(dcols)} columns / full {len(fcols)} columns; long texts vault-only")

    # 3. every Drive row leads back to the full record by job_id
    ids = list(drive)[:3]
    detail = json.loads(run(tmp, "job-detail", "--ids", ",".join(ids)).stdout)
    for i in ids:
        assert detail[i] == vault[i]
        assert drive[i]["status"] == full[i]["status"] and drive[i]["url"] == full[i]["url"]
    assert run(tmp, "job-detail", "--ids", "1", ok=False).returncode == 1
    print("job_id -> job-detail returns the whole vault record")

    # 8. delivery link kept on Drive for delivered / ready jobs
    for i, j in vault.items():
        url = ((j.get("worker") or {}).get("delivery") or {}).get("url")
        if url and i in drive:
            assert drive[i]["delivery_artifact_url"] == url
    # 9. the Drive copy is much smaller than the full column set for the same rows
    size = os.path.getsize(os.path.join(tmp, "out", "job_master.csv"))
    tmp_full = os.path.join(tmp, "same_rows_full.csv")
    with open(tmp_full, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, list(fcols))
        w.writeheader()
        w.writerows(full[i] for i in drive)
    assert size < 0.6 * os.path.getsize(tmp_full), (size, os.path.getsize(tmp_full))
    print(f"Drive Job Master {size} bytes vs {os.path.getsize(tmp_full)} bytes with all columns")

    # 10. size monitoring: delta from the previous export, near / over budget flags
    st = json.loads(run(tmp, "drive-status", ok=False).stdout)
    assert "job_master" in st["delta_bytes"] and "near_budget" in st and "over_budget" in st
    r = subprocess.run([sys.executable, "-c", "import pipeline as P; P.DRIVE_BUDGET = 1000; "
                        "v = P.vault_load(); P.export(v['master'], v.get('meta', {}))"],
                       cwd=tmp, capture_output=True, text=True, check=True)
    assert "WARNING: job_master.csv exceeds" in r.stderr
    st = json.loads(run(tmp, "drive-status", ok=False).stdout)
    assert "job_master" in st["over_budget"] and st["delta_bytes"]["job_master"] == 0
    print("drive-status: size, delta and over/near budget reported; export warns over budget")
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
