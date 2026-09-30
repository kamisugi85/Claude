"""Astra Queue at 05:00: tier (主力 / マイクロ) and the one-cell Claude QA result next to the pre-Astra draft
(temporary copy of scout/; the real Vault is never modified).

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_queue_tier.py
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESC = "記事作成のお仕事です。\n【報酬】1記事あたり2,000円（税抜）\n・NISAの利用経験はありますか\n"
SETUP = r"""
import os, pipeline as P
v = P.vault_load(); m = v["master"]; d = P.today()
def job(jid, gross, ai, mins):
    m[jid] = {"job_id": int(jid), "url": "https://crowdworks.jp/public/jobs/" + jid, "title": "テスト記事" + jid,
              "client": {"userId": 99000041, "userDisplayName": "c"}, "first_seen": d + "T05:00+09:00",
              "eval": {"verdict": "候補", "reason": "t", "classification": "B", "human_minutes": mins, "ai_completion": ai},
              "gross": gross, "net_est": round(gross * 0.8), "risk_rule": [], "actual": {}, "expired_on": "2099-12-31",
              "status": "ASTRA_QA_PENDING", "pre_draft_due": True,
              "status_history": [{"status": "ASTRA_QA_PENDING", "at": d + "T05:10+09:00", "by": "test", "note": ""}],
              "reward_check": {"checked_at": d + "T05:31+09:00", "changes": [], "deadline": "2099-12-31", "closed": False}}
    os.makedirs(os.path.join(P.ROOT, "data", d, "app_source"), exist_ok=True)
    P.save_json(os.path.join(P.ROOT, "data", d, "app_source", jid + ".json"), {"desc": DESC})
job("99600001", 2200, "85-90%", "4-6分")    # net 1,760 -> 主力
job("99600002", 440, "85-90%", "4-6分")     # net 352, 58.7 JPY/min, AI 85% -> マイクロ
job("99600003", 220, "50-60%", "10-15分")   # net 176, AI 50% -> 基準外
job("99600004", 2200, "85-90%", "4-6分")    # drafted, needs the user -> FLAGGED
P.vault_save(v)
"""


def main():
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests"))
    subprocess.run([sys.executable, "-c", "DESC=%r\n" % DESC + SETUP], cwd=tmp, check=True)
    base = {"actual_reward": 2200, "reward_evidence": "1記事あたり2,000円（税抜）",
            "application_draft": "はじめまして。記事作成のご募集を拝見し、応募いたします。どうぞよろしくお願いいたします。",
            "application_questions": ["・NISAの利用経験はありますか"], "application_answers": ["はい、利用しています。"],
            "facts_used": [{"fact": "NISAの利用経験", "profile_ref": "confirmed_facts[1].fact"}],
            "unverified_facts": [], "conflict_risk": "低：該当なし", "user_confirmation_required": "no",
            "review_minutes_est": 2}
    drafts = [{**base, "job_id": "99600001"},
              {**base, "job_id": "99600004", "unverified_facts": ["執筆テーマに関する本人の経験"],
               "user_confirmation_required": "yes"}]
    p = os.path.join(tmp, "d.json")
    json.dump(drafts, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run([sys.executable, "pipeline.py", "app-merge", "--drafts", p], cwd=tmp, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    q = {x["job_id"]: x for x in csv.DictReader(open(os.path.join(tmp, "out", "astra_queue.csv"), encoding="utf-8"))}
    assert q["99600001"]["tier"] == "主力" and q["99600002"]["tier"] == "マイクロ", (q["99600001"]["tier"], q["99600002"]["tier"])
    assert q["99600003"]["tier"].startswith("基準外")
    assert q["99600001"]["claude_qa_result"] == "PASS" and q["99600002"]["claude_qa_result"] == "NO_DRAFT"
    assert q["99600004"]["claude_qa_result"].startswith("FLAGGED："), q["99600004"]["claude_qa_result"]
    assert all(q[j]["application_draft"] for j in ("99600001", "99600004"))
    st = subprocess.run([sys.executable, "-c", "import pipeline as P, json; m=P.vault_load()['master']; "
                         "print(json.dumps([m[j]['status'] for j in ('99600001','99600004')]))"],
                        cwd=tmp, capture_output=True, text=True, check=True).stdout
    assert json.loads(st) == ["ASTRA_QA_PENDING", "ASTRA_QA_PENDING"]  # never READY_TO_APPLY without Astra
    print("Astra Queue: tier 主力 / マイクロ / 基準外; claude_qa_result PASS / FLAGGED / NO_DRAFT; status stays ASTRA_QA_PENDING")
    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
