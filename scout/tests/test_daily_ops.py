"""Daily operation end to end on a temporary copy of scout/ (the real Vault is never modified):
Astra verdicts -> Application Queue -> READY_TO_APPLY, Client Master openings, reuse of confirmed facts,
confirmation cost, idempotency, the catch-up pass after 06:30 and the latest Drive sheet.

Run: SCOUT_VAULT_KEY=... python3 scout/tests/test_daily_ops.py
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
CID_NEW, CID_PAST = "99000011", "99000012"  # clients made up for the test
DESC = ("記事作成のお仕事です。\n【報酬】1記事あたり2,000円（税抜）\n【応募時の質問】\n"
        "・NISAの利用経験はありますか\n・好きな旅行先を教えてください\n・納期の目安を教えてください\n")
TRANSCRIBE = "【文字起こし】1ファイル3,000字程度のPDFをWordへ。\n報酬：1ファイル5,000円\n・納期の目安を教えてください\n"
SETUP = r"""
import json, os, pipeline as P
v = P.vault_load(); m = v["master"]; d = P.today()
def job(jid, cid, status, hist, desc=DESC, deadline="2099-12-31", title=None, gross=2200):
    m[jid] = {"job_id": int(jid), "url": "https://crowdworks.jp/public/jobs/" + jid, "title": title or "テスト記事" + jid,
              "client": {"userId": int(cid), "userDisplayName": "c" + cid}, "first_seen": d + "T05:00+09:00",
              "eval": {"verdict": "候補", "reason": "t", "classification": "B", "human_minutes": "5分"},
              "gross": gross, "net_est": round(gross * 0.8), "risk_rule": [], "actual": {}, "expired_on": deadline,
              "status": status, "status_history": [{"status": s, "at": d + "T05:1%d+09:00" % i, "by": "test", "note": ""}
                                                   for i, s in enumerate(hist)]}
    if status == "ASTRA_QA_PENDING":
        m[jid]["reward_check"] = {"checked_at": d + "T06:31+09:00", "changes": [], "deadline": deadline, "closed": False}
        os.makedirs(os.path.join(P.ROOT, "data", d, "app_source"), exist_ok=True)
        P.save_json(os.path.join(P.ROOT, "data", d, "app_source", jid + ".json"), {"desc": desc})
for x, st in (("99200001", "PASS"), ("99200002", "REJECT"), ("99200003", "HOLD"), ("99200004", None),
              ("99200005", "SKIPPED"), ("99200007", "LOW")):
    job(x, CID_NEW if x != "99200007" else "99000013", "ASTRA_QA_PENDING", ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"])
job("99200006", CID_NEW, "ASTRA_QA_PENDING", ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"], deadline="2020-01-01")
m["99200007"]["gross"], m["99200007"]["net_est"] = 220, 176
job("99200008", "99000014", "ASTRA_QA_PENDING", ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"], desc=TRANSCRIBE,
    title="PDF→Word文字起こし", gross=5000)
job("99300001", CID_PAST, "PAID", ["APPLIED", "ACCEPTED", "DELIVERED", "PAID"])  # past delivery to this client
job("99300002", CID_PAST, "ASTRA_QA_PENDING", ["CLAUDE_CANDIDATE", "ASTRA_QA_PENDING"])
P.vault_save(v)
with open(os.path.join(P.STATE, "runs.jsonl"), "a", encoding="utf-8") as f:  # today's 05:00 Scout hand-off
    f.write(json.dumps({"date": d, "run_at": d + "T05:05+09:00"}) + "\n")
"""


def run(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(" ".join(args) + "\n" + r.stdout + r.stderr)
    return r


def js(r):
    out = r.stdout
    return json.loads(out[out.index("{"):])


def py(tmp, code):
    return subprocess.run([sys.executable, "-c", code], cwd=tmp, capture_output=True, text=True, check=True).stdout


def status(tmp, jid):
    return json.loads(py(tmp, "import pipeline as P, json; print(json.dumps(P.vault_load()['master'][%r]['status']))" % jid))


def su(path, rows):
    cols = ["job_id", "astra_verdict", "astra_reason", "new_status", "updated_at", "updated_by", "note"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in rows:
            w.writerow({"updated_by": "Astra", **r})
    return path


def draft(jid, opening, answers=None, unverified=None, questions=None, reward=2200, ev="1記事あたり2,000円（税抜）",
          facts=None):
    qs = questions if questions is not None else []
    return {"job_id": jid, "actual_reward": reward, "reward_evidence": ev,
            "application_draft": opening + "記事作成のご募集を拝見し、応募いたします。構成を整えて期限内に納品いたします。"
                                            "どうぞよろしくお願いいたします。",
            "application_questions": qs, "application_answers": answers or [],
            "facts_used": facts or [], "unverified_facts": unverified or [], "conflict_risk": "低：該当なし",
            "user_confirmation_required": "yes" if unverified else "no", "review_minutes_est": 2}


def merge(tmp, drafts, ok=True):
    p = os.path.join(tmp, "drafts.json")
    json.dump(drafts, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return run(tmp, "app-merge", "--drafts", p, ok=ok)


def main():
    tmp = sandbox.make()
    os.remove(os.path.join(tmp, "routine.json"))  # this test covers the legacy 06:30 / 12:30 import path itself
    py(tmp, "DESC=%r\nTRANSCRIBE=%r\nCID_NEW=%r\nCID_PAST=%r\n" % (DESC, TRANSCRIBE, CID_NEW, CID_PAST) + SETUP)
    date = json.loads(py(tmp, "import pipeline as P, json; print(json.dumps(P.today()))"))
    at = lambda hm: f"{date} {hm}"

    # --- 06:00 Astra QA -> 06:30 import: only Astra's own rows count, HOLD/REJECT/SKIPPED are not prepared
    rows = [{"job_id": "99200001", "astra_verdict": "PASS", "updated_at": at("06:05")},
            {"job_id": "99200002", "astra_verdict": "REJECT", "astra_reason": "条件不一致", "updated_at": at("06:05")},
            {"job_id": "99200003", "astra_verdict": "HOLD", "updated_at": at("06:06")},
            {"job_id": "99200005", "astra_verdict": "SKIPPED", "updated_at": at("06:06")},
            {"job_id": "99200006", "astra_verdict": "PASS", "updated_at": at("06:06")},
            {"job_id": "99200007", "astra_verdict": "PASS", "updated_at": at("06:07")},
            {"job_id": "99200008", "astra_verdict": "PASS", "updated_at": at("06:07")},
            {"job_id": "99300002", "astra_verdict": "PASS", "updated_at": at("06:07")},
            # a later row wins over an earlier one for the same job, whatever the sheet order
            {"job_id": "99200002", "astra_verdict": "PASS", "updated_at": at("06:01")},
            # proxy / non-Astra rows are never taken as Astra's verdict
            {"job_id": "99200004", "astra_verdict": "PASS", "updated_by": "Claude", "updated_at": at("06:08")},
            {"job_id": "99200004", "astra_verdict": "PASS", "note": "本人経由で転記", "updated_at": at("06:08")}]
    csv1 = su(os.path.join(tmp, "su1.csv"), rows)
    g = js(run(tmp, "postqa", "guard", "--now", "06:30"))
    assert g["ok"] and g["mode"] == "main", g
    assert js(run(tmp, "postqa", "astra", "--csv", csv1))["ok"]
    res = js(run(tmp, "apply-updates", "--csv", csv1))
    assert len(res["errors"]) == 2, res["errors"]
    st = {x: status(tmp, x) for x in ("99200001", "99200002", "99200003", "99200004", "99200005")}
    assert st == {"99200001": "ASTRA_PASS", "99200002": "ASTRA_REJECT", "99200003": "NEED_USER",
                  "99200004": "ASTRA_QA_PENDING", "99200005": "SKIPPED"}, st
    print("Astra rows: PASS / REJECT / HOLD(->NEED_USER) / SKIPPED applied, latest row wins; "
          "no Astra verdict (or a proxy row) -> stays ASTRA_QA_PENDING")

    t = js(run(tmp, "postqa", "targets"))
    assert {"99200001", "99200007", "99200008", "99300002"} <= set(t["targets"]), t
    assert not {"99200002", "99200003", "99200004", "99200005", "99200006"} & set(t["targets"]), t
    plan = js(run(tmp, "app-plan", "--cap", "50"))
    assert "99200001" in plan["draft_now"] and not {"99200002", "99200003", "99200004", "99200005"} & set(plan["draft_now"])
    assert "99200006" not in plan["draft_now"] and plan["excluded"]["99200006"] == "応募期限切れ"
    assert plan["client"]["99200001"]["relationship"] == "NONE"
    assert plan["client"]["99300002"]["relationship"] == "DELIVERED"
    assert plan["client"]["99300002"]["opening"].startswith("以前はお仕事をご依頼いただき")
    assert any(f["fact"] == "NISAの利用経験がある" for f in plan["confirmed_facts"])
    print("targets / app-plan: only ASTRA_PASS, not expired; Client Master relationship + opening per job")

    # --- Astra PASS -> Application Queue -> READY_TO_APPLY (first contact opens with はじめまして)
    merge(tmp, [draft("99200001", "はじめまして。")])
    assert status(tmp, "99200001") == "READY_TO_APPLY"
    q = {r["job_id"]: r for r in csv.DictReader(open(os.path.join(tmp, "out", "application_queue.csv"), encoding="utf-8"))}
    assert q["99200001"]["status"] == "READY_TO_APPLY"
    print("Astra PASS -> Application Queue -> READY_TO_APPLY")

    # --- expired / REJECT / HOLD are never drafted
    for x in ("99200006", "99200002", "99200003"):
        r = merge(tmp, [draft(x, "はじめまして。")], ok=False)
        assert x in js(r)["failed"], r.stdout
        assert status(tmp, x) != "READY_TO_APPLY"
    print("REJECT / HOLD / expired PASS: app-merge refuses, never READY")

    # --- past client: はじめまして is stopped by QA; a natural re-application opening passes
    r = merge(tmp, [draft("99300002", "はじめまして。")], ok=False)
    assert any("はじめまして" in e for e in js(r)["failed"]["99300002"]), r.stdout
    assert status(tmp, "99300002") == "ASTRA_PASS"
    merge(tmp, [draft("99300002", plan["client"]["99300002"]["opening"])])
    assert status(tmp, "99300002") == "READY_TO_APPLY"
    print("past delivery client: はじめまして -> QA FAIL; past-work opening -> READY_TO_APPLY")

    # --- confirmed facts are reused, never asked again; standard answer for transcription deadlines
    q3 = ["・NISAの利用経験はありますか", "・納期の目安を教えてください"]
    bad = draft("99200007", "はじめまして。", questions=q3[:1], answers=["【本人記入】"], unverified=["NISAの利用経験"],
                reward=220, ev="1記事あたり2,000円（税抜）")
    r = merge(tmp, [bad], ok=False)
    assert any("本人確認済み" in e for e in js(r)["failed"]["99200007"]), r.stdout
    ok = draft("99200008", "はじめまして。", questions=["・納期の目安を教えてください"],
               answers=["1ファイル3日程度で納品いたします。"], reward=5000, ev="1ファイル5,000円",
               facts=[{"fact": "納期3日", "profile_ref": "confirmed_facts[1].fact"}])
    merge(tmp, [ok])
    assert status(tmp, "99200008") == "READY_TO_APPLY"
    print("confirmed facts: re-asking NISA experience -> FAIL; transcription deadline answered from the standard answer")

    # --- confirmation cost: a 220-yen job waiting only on a personal preference is not sent to the user
    low = draft("99200007", "はじめまして。", questions=["・好きな旅行先を教えてください"], answers=["【本人記入】"],
                unverified=["好きな旅行先（本人の好み）"], reward=2200)
    low["actual_reward"], low["reward_evidence"] = 2200, "1記事あたり2,000円（税抜）"
    py(tmp, "import pipeline as P; v=P.vault_load(); v['master']['99200007']['reward_check']['checked_at']=P.today()+'T06:31'; "
            "P.vault_save(v)")
    # the posting pays 2,000 yen, but make the job cheap: 220 yen tax incl. is below the confirmation worth
    open(os.path.join(tmp, "data", date, "app_source", "99200007.json"), "w", encoding="utf-8").write(
        json.dumps({"desc": DESC.replace("2,000円", "200円")}, ensure_ascii=False))
    low["actual_reward"], low["reward_evidence"] = 220, "1記事あたり200円（税抜）"
    merge(tmp, [low])
    app = json.loads(py(tmp, "import pipeline as P, json; print(json.dumps(P.vault_load()['master']['99200007']['application'], ensure_ascii=False))"))
    assert app["confirm_cost"] == "REJECT_CANDIDATE" and app["user_confirmation_required"] == "no", app
    assert "Astra REJECT候補" in app["next_action"] and status(tmp, "99200007") == "ASTRA_PASS"
    print("low price + personal-only question -> Astra REJECT candidate, the user is not asked")

    # --- idempotent: the same Status Updates and the same drafts do nothing twice
    before = py(tmp, "import pipeline as P, json; m=P.vault_load()['master']; "
                     "print(json.dumps({j: [m[j]['status'], len(m[j]['status_history'])] for j in m if j.startswith('99')}))")
    assert js(run(tmp, "apply-updates", "--csv", csv1))["applied"] == 0
    r = merge(tmp, [draft("99200001", "はじめまして。")], ok=False)  # already READY: not registered again
    assert "99200001" in js(r)["failed"]
    assert "99200001" not in js(run(tmp, "app-plan", "--cap", "50"))["draft_now"]
    after = py(tmp, "import pipeline as P, json; m=P.vault_load()['master']; "
                    "print(json.dumps({j: [m[j]['status'], len(m[j]['status_history'])] for j in m if j.startswith('99')}))")
    assert before == after
    print("same job_id / same rows again: no second import, no second draft")

    # --- Astra PASS written after 06:30: the catch-up pass picks it up the same day, once
    csv2 = su(os.path.join(tmp, "su2.csv"), rows + [{"job_id": "99200004", "astra_verdict": "PASS", "updated_at": at("07:10")}])
    g = js(run(tmp, "postqa", "guard", "--now", "12:30"))
    assert g["ok"] and g["mode"] == "catchup"
    c = js(run(tmp, "postqa", "catchup", "--csv", csv1))
    assert "99200004" not in c["new_astra_rows_for"]
    c = js(run(tmp, "postqa", "catchup", "--csv", csv2))
    assert c["ok"] and c["new_astra_rows_for"] == ["99200004"], c
    run(tmp, "apply-updates", "--csv", csv2)
    assert status(tmp, "99200004") == "ASTRA_PASS"
    assert "99200004" in js(run(tmp, "postqa", "targets"))["targets"]
    merge(tmp, [draft("99200004", "はじめまして。")])
    assert status(tmp, "99200004") == "READY_TO_APPLY"
    c = js(run(tmp, "postqa", "catchup", "--csv", csv2))
    assert c["new_astra_rows_for"] == [] and "99200004" not in c["carry_over"], c
    print("PASS after 06:30 -> catch-up pass -> READY_TO_APPLY the same day; a second pass finds nothing new")

    # --- 07:30 notice: READY_TO_APPLY only, `URL | 応募文`
    n = run(tmp, "ready-notice", "--ids", "99200001,99200004,99200007,99300002").stdout
    assert "https://crowdworks.jp/public/jobs/99200001 | はじめまして。" in n
    assert "https://crowdworks.jp/public/jobs/99300002 | 以前はお仕事をご依頼いただき" in n
    assert "99200007" not in n and "【本人記入" not in n
    print("ready-notice: READY_TO_APPLY only, URL | finished application text")

    # --- Drive: the latest sheet comes from the folder listing, never from a remembered ID
    folder = json.loads(py(tmp, "import pipeline as P, json; print(json.dumps(P.vault_load()['meta']['drive']['folder']))"))
    sheet = lambda i, title, created: {"id": i, "title": title, "createdTime": created, "parentId": folder,
                                       "mimeType": "application/vnd.google-apps.spreadsheet"}
    listing = {"files": [
        sheet("OLD_JM", "CW Scout - Job Master｜2026-09-29 06:40 JST", "2026-09-28T21:41:00Z"),
        sheet("NEW_JM", "CW Scout - Job Master｜2026-09-30 06:40 JST", "2026-09-29T21:41:00Z"),
        sheet("LEFTOVER_JM", "CW Scout - Job Master｜2026-09-30 05:26 JST", "2026-09-29T20:29:00Z"),
        sheet("SU", "CW Scout - Status Updates (記入用)", "2026-09-27T21:25:00Z"),
        {**sheet("ELSEWHERE", "CW Scout - Job Master｜2026-10-09 06:40 JST", "2026-10-08T21:41:00Z"), "parentId": "x"}]}
    lp = os.path.join(tmp, "listing.json")
    json.dump(listing, open(lp, "w", encoding="utf-8"), ensure_ascii=False)
    py(tmp, "import pipeline as P; v=P.vault_load(); v['meta']['drive']['job_master_sheet']='OLD_JM'; P.vault_save(v)")
    d = js(run(tmp, "drive-resolve", "--listing", lp))["sheets"]
    assert d["job_master_sheet"]["id"] == "NEW_JM" and not d["job_master_sheet"]["recorded_is_current"]
    assert d["status_updates_sheet"]["id"] == "SU" and d["astra_queue_sheet"]["missing"]
    d = js(run(tmp, "drive-resolve", "--listing", lp, "--keep", "job_master_sheet=NEW_JM"))["sheets"]
    assert sorted(d["job_master_sheet"]["trash"]) == ["LEFTOVER_JM", "OLD_JM"]
    print("drive-resolve: newest sheet in the folder (not the recorded old ID); leftovers listed for trash")

    shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
