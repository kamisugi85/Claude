"""User-designated jobs (manual-add): no network, no Vault.

A job the user picked by hand stands in for Astra's application QA only while it is
CLAUDE_CANDIDATE with a designation; normal CLAUDE_CANDIDATE jobs stay out of the application flow.
Run: python3 scout/tests/test_manual.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import application as A  # noqa: E402


def main():
    plain = {"status": "CLAUDE_CANDIDATE"}
    picked = {"status": "CLAUDE_CANDIDATE", "designation": {"by": "本人"}}
    assert not A.eligible(plain, A.TARGET) and A.eligible(picked, A.TARGET)
    assert A.eligible({"status": "ASTRA_PASS"}, A.TARGET)
    assert not A.eligible({"status": "APPLIED", "designation": {"by": "本人"}}, A.TARGET)
    print("only designated CLAUDE_CANDIDATE jobs join the application flow")

    base = {"application": {"application_draft": "はじめまして。応募いたします。", "application_answers": [],
                            "user_confirmation_required": "no", "claim_flags": [], "conflict_risk": "低"},
            "reward_check": {"checked_at": "2026-10-01T09:00", "changes": []}, "status_history": []}
    ready = {**base, "status": "CLAUDE_CANDIDATE", "designation": {"by": "本人"}}
    A._apply_readiness(ready, "2026-10-01")
    assert ready["status"] == "READY_TO_APPLY"
    ready["reward_check"] = {"checked_at": "2026-10-02T09:00", "changes": ["募集終了"], "closed": True}
    A._apply_readiness(ready, "2026-10-02")
    assert ready["status"] == "CLAUDE_CANDIDATE"  # pulled back to its own gate, never to a fake ASTRA_PASS
    held = {**base, "status": "CLAUDE_CANDIDATE"}
    A._apply_readiness(held, "2026-10-01")
    assert held["status"] == "CLAUDE_CANDIDATE"  # not designated: untouched
    print("readiness: designated jobs go READY and fall back to CLAUDE_CANDIDATE; others untouched")
    print("OK")


if __name__ == "__main__":
    main()
