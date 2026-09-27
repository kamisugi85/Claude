"""Re-check classification and readiness rules (no network, no Vault).

Run: python3 scout/tests/test_recheck.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import application as A  # noqa: E402
import pipeline as P  # noqa: E402

TODAY = P.today()
DESC = "【報酬】\n金額は、400円（400、と打ち込みください）\n文字数は400～500文字程度\nAI利用OK"


def job(status="ASTRA_PASS", **app):
    j = {"job_id": "1", "title": "t", "status": status, "gross": 440,
         "eval": {"ai_condition": "A"}, "status_history": [],
         "application": {"actual_reward": 440, "reward_evidence": "金額は、400円（400、と打ち込みください）",
                         "application_draft": "はじめまして。", "application_answers": [],
                         "user_confirmation_required": "no", "claim_flags": [], "conflict_risk": "低：一般論",
                         "final_qa_status": "HOLD", "next_action": ""}}
    j["application"].update(app)
    return j


def rc(**kw):
    r = {"header_reward": {"type": "契約金額（目安）", "min": 440, "max": None}, "deadline": "2099-10-10",
         "contracted": 0, "capacity": 3, "closed": False, "ai_policy": "A",
         "body_reward_mentions": ["金額は、400円（400、と打ち込みください）"],
         "desc_hash": "h1", "key_lines": A._key_lines(DESC), "checked_at": TODAY + "T06:46+09:00"}
    r.update(kw)
    return r


def recheck(j, prev, new, desc=DESC):
    ch, notes = A.classify_changes(prev, new, j, desc)
    new["changes"], new["notes"] = ch, notes
    j["reward_check"] = new
    A._apply_readiness(j, TODAY)
    return ch, notes


def main():
    # A: header reward newly extracted and consistent with the known 400円+tax = 440円 → not a change
    j = job()
    ch, notes = recheck(j, rc(header_reward=None), rc())
    assert ch == [] and any("新たに取得" in n for n in notes), (ch, notes)
    assert j["status"] == "READY_TO_APPLY", j["status"]
    print("A ok: header_reward None→440 with actual 440 is a note, job READY_TO_APPLY")

    # B: reward decrease 440 → 220 (header and body) → hold; a READY job is pulled back
    j = job(status="READY_TO_APPLY")
    ch, _ = recheck(j, rc(), rc(header_reward={"type": "契約金額（目安）", "min": 220, "max": None},
                              body_reward_mentions=["金額は、200円"]),
                    desc=DESC.replace("400円（400、", "200円（200、"))
    assert any("下回る" in c for c in ch) and any("報酬記載が本文から消えた" in c for c in ch), ch
    assert j["status"] == "ASTRA_PASS" and j["application"]["final_qa_status"] == "HOLD"
    print("B ok: 440→220 is material; READY_TO_APPLY pulled back to hold")

    # C: closed → never READY
    j = job()
    ch, _ = recheck(j, rc(), rc(closed=True))
    assert "募集終了" in ch and j["status"] == "ASTRA_PASS"
    j2 = job()
    recheck(j2, rc(), rc(contracted=3))
    assert j2["status"] == "ASTRA_PASS"
    print("C ok: closed / slots filled stay on hold")

    # D: user confirmation needed / unfinished answer → never READY
    j = job(user_confirmation_required="yes")
    recheck(j, rc(), rc())
    assert j["status"] == "ASTRA_PASS" and "本人確認" in j["application"]["next_action"]
    j = job(application_answers=["→ 【本人記入】"])
    recheck(j, rc(), rc())
    assert j["status"] == "ASTRA_PASS"
    print("D ok: user confirmation / unfinished answers stay on hold")

    j = job(application_draft="NISAで投資を始めるなら、つみたて投資枠の活用をおすすめします。")  # article body
    recheck(j, rc(), rc())
    assert j["status"] == "ASTRA_PASS" and "応募メッセージ" in j["application"]["next_action"]
    print("D2 ok: an article body in place of the application message is never READY")

    # E/F: APPLIED and SKIPPED are never moved
    for st in ("APPLIED", "SKIPPED"):
        j = job(status=st)
        recheck(j, rc(header_reward=None), rc())
        assert j["status"] == st, (st, j["status"])
        recheck(j, rc(), rc(closed=True))
        assert j["status"] == st
    print("E/F ok: APPLIED / SKIPPED not moved by re-checks")

    # other material changes still hold: stricter AI, earlier deadline, new contact-condition line
    j = job()
    ch, _ = recheck(j, rc(), rc(ai_policy="D"))
    assert any("AI条件" in c for c in ch) and j["status"] == "ASTRA_PASS"
    j = job()
    ch, _ = recheck(j, rc(deadline="2099-10-10"), rc(deadline="2099-10-01"))
    assert any("前倒し" in c for c in ch)
    j = job()
    d2 = DESC + "\n詳細はLINEでご連絡ください"
    ch, _ = recheck(j, rc(), rc(desc_hash="h2", key_lines=A._key_lines(d2)), desc=d2)
    assert any("条件行" in c for c in ch) and j["status"] == "ASTRA_PASS", ch
    base = rc(desc_hash="h2", key_lines=A._key_lines(d2))
    ch, _ = recheck(j, base, copy.deepcopy(base), desc=d2)  # next check: new baseline, flag must persist
    assert any("条件行" in c for c in ch) and j["status"] == "ASTRA_PASS", ch
    j = job()
    ch, notes = recheck(j, rc(), rc(desc_hash="h2"))  # body edited, no new condition line
    assert ch == [] and j["status"] == "READY_TO_APPLY", (ch, notes)
    j = job()
    ch, _ = recheck(j, rc(key_lines=None), rc(desc_hash="h2"))  # no baseline to compare → conservative
    assert ch and j["status"] == "ASTRA_PASS"
    j = job()
    ch, notes = recheck(j, rc(deadline="2099-10-01"), rc(deadline="2099-10-10"))
    assert ch == [] and any("延長" in n for n in notes)
    print("material changes (AI stricter, deadline earlier, new contact line, unknown body change) hold; "
          "minor edits and extensions do not")
    print("OK")


if __name__ == "__main__":
    main()
