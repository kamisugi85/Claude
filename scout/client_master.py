"""Client Master: what we know about each CrowdWorks client across all its jobs.

Key       : the CrowdWorks client userId (stable), never the display name.
Storage   : vault meta["clients"][client_id], rebuilt from the Job Master on every vault save, so
            Status Updates / Worker changes (applied, accepted, delivered, paid, revisions,
            withdrawals) always reach it. Only `client_notes` is kept by hand (`client-note`).
Unknowns  : counts come from jobs recorded in this system; what the system cannot know (payments
            without a PAID row, interview terms that were never observed) stays "UNKNOWN".
Used by   : `prepare` (Claude's first pass), the Astra Queue (`client_history`), `app-plan`
            (relationship + the opening to use) and `app-merge` / READY checks (opening QA).
"""
import re

import pipeline as P

APPLIED = {"APPLIED", "ACCEPTED", "IN_PROGRESS", "READY_FOR_QA", "READY_TO_DELIVER", "DELIVERED", "PAID",
           "NOT_SELECTED", "WITHDRAWN"}
ACCEPTED = {"ACCEPTED", "IN_PROGRESS", "READY_FOR_QA", "READY_TO_DELIVER", "DELIVERED", "PAID"}
DELIVERED = {"DELIVERED", "PAID"}
INTERVIEW_RE = re.compile(r"面談|面接|ビデオ通話|通話|Zoom|ミーティング")
# the relationship a client has with us, weakest first
OPENINGS = {
    "NONE": "はじめまして。",
    "APPLIED": "先日は別のご募集にも応募させていただきました。",
    "ORDERED": "以前はお仕事をご依頼いただき、ありがとうございました。",
    "DELIVERED": "以前はお仕事をご依頼いただき、ありがとうございました。",
}
FIRST_MEET_RE = re.compile(r"はじめまして|初めまして|初めてご連絡")
PAST_ORDER_RE = re.compile(r"ご依頼いただき|ご依頼(?:を)?いただいた|ご依頼ありがとう|お取引|お仕事をいただ|"
                           r"(?:以前|前回|先日)[^。]{0,20}(?:ありがとう|お世話になりました)|納品させていただいた")


def client_id(job):
    return str((job.get("client") or {}).get("userId") or "") or None


def _ever(job, statuses):
    return job.get("status") in statuses or any(h.get("status") in statuses for h in job.get("status_history") or [])


def interview_event(job):
    """A withdrawal because the client asked for an interview only after the application."""
    if job.get("status") != "WITHDRAWN":
        return None
    h = next((h for h in reversed(job.get("status_history") or []) if h.get("status") == "WITHDRAWN"), {})
    text = " ".join(str(x or "") for x in (h.get("note"), (job.get("astra") or {}).get("astra_reason"),
                                           (job.get("astra") or {}).get("note")))
    if not INTERVIEW_RE.search(text):
        return None
    in_posting = "面談必須（募集文に明記）" in (job.get("risk_rule") or [])
    return {"job_id": str(job["job_id"]), "at": h.get("at"), "disclosed_in_posting": in_posting,
            "found_after_application": not in_posting, "note": h.get("note")}


def build(master, old=None):
    """Client Master from the Job Master (+ hand-kept notes of the previous build)."""
    old = old or {}
    by = {}
    for j in master.values():
        cid = client_id(j)
        if cid:
            by.setdefault(cid, []).append(j)
    out = {}
    for cid, jobs in by.items():
        jobs.sort(key=lambda j: j.get("first_seen") or "")
        c = jobs[-1].get("client") or {}
        seen = [x for j in jobs for x in [j.get("first_seen")] + [h.get("at") for h in j.get("status_history") or []] if x]
        interviews = [e for e in (interview_event(j) for j in jobs) if e]
        disclosed = [str(j["job_id"]) for j in jobs if "面談必須（募集文に明記）" in (j.get("risk_rule") or [])]
        applied = [j for j in jobs if _ever(j, APPLIED)]
        rel = [j for j in jobs if j.get("status") in APPLIED]
        last = max(rel, key=lambda j: (j.get("status_history") or [{}])[-1].get("at") or "", default=None)
        out[cid] = {
            "client_id": cid, "client_name": c.get("userDisplayName") or "UNKNOWN",
            "first_seen_at": min(seen) if seen else "UNKNOWN", "last_seen_at": max(seen) if seen else "UNKNOWN",
            "application_count": len(applied),
            "accepted_count": sum(_ever(j, ACCEPTED) for j in jobs),
            "delivered_count": sum(_ever(j, DELIVERED) for j in jobs),
            "paid_count": sum(_ever(j, {"PAID"}) for j in jobs),
            "repeat_order_count": _repeat(jobs),
            "past_job_ids": [str(j["job_id"]) for j in jobs],
            "last_relationship_status": (f"{last['status']}（{last['job_id']}）" if last else "NONE"),
            "interview_required_history": interviews,
            "interview_disclosed_in_posting": disclosed or "UNKNOWN",
            "post_application_interview_request_count": sum(e["found_after_application"] for e in interviews),
            "revision_history": [{"job_id": str(j["job_id"]), "revision_count": (j.get("actual") or {}).get("revision_count")}
                                 for j in jobs if (j.get("actual") or {}).get("revision_count") not in (None, "")],
            "payment_history": [{"job_id": str(j["job_id"]), "status": j.get("status"),
                                 "actual_net_reward": (j.get("actual") or {}).get("actual_net_reward") or "UNKNOWN"}
                                for j in jobs if _ever(j, DELIVERED)],
            "client_notes": (old.get(cid) or {}).get("client_notes", []),
            "client_score": c.get("averageScore"), "client_verified": c.get("isIdentityVerified"),
            "updated_at": P.now_iso(),
        }
    for cid, o in old.items():  # a client whose jobs are gone keeps its notes
        out.setdefault(cid, o)
    return out


def _repeat(jobs):
    vals = [str((j.get("actual") or {}).get("repeat_order") or "").lower() for j in jobs]
    known = [x for x in vals if x]
    if not known:
        return "UNKNOWN"
    return sum(x in ("yes", "true", "1", "あり", "継続") for x in known)


def refresh(v):
    meta = v.setdefault("meta", {})
    meta["clients"] = build(v.get("master", {}), meta.get("clients"))
    return meta["clients"]


def relation(clients, job):
    """Relationship with the client of `job`, from its OTHER jobs (the job itself does not count)."""
    cid = client_id(job)
    c = (clients or {}).get(cid) or {}
    me = str(job.get("job_id"))
    past = [x for x in c.get("past_job_ids", []) if x != me]
    return c, past


def level(clients, master, job):
    c, past = relation(clients, job)
    others = [master[x] for x in past if x in master]
    if any(_ever(j, DELIVERED) for j in others):
        return "DELIVERED"
    if any(_ever(j, ACCEPTED) for j in others):
        return "ORDERED"
    if any(_ever(j, APPLIED) for j in others):
        return "APPLIED"
    return "NONE"


def opening_errors(draft, lvl):
    """Application-message QA against the client relationship."""
    head = (draft or "").strip()
    errs = []
    if lvl == "NONE" and not FIRST_MEET_RE.search(head[:30]):
        errs.append("初回のクライアントなのに冒頭が「はじめまして。」でない")
    if lvl != "NONE" and FIRST_MEET_RE.search(head):
        errs.append(f"過去に接点のあるクライアント（{lvl}）に初対面の表現（はじめまして）を使っている")
    if lvl in ("NONE", "APPLIED") and PAST_ORDER_RE.search(head):
        errs.append("受注履歴がないのに過去の依頼・取引へのお礼を書いている（関係の誇張）")
    return errs


def summary(clients, master, job):
    """One line for Claude's first pass and the Astra Queue (history is shown, never auto-rejected)."""
    c, past = relation(clients, job)
    if not c:
        return "初回（過去の接点なし）"
    lvl = level(clients, master, job)
    # every count names what it counts: postings Scout saw are not deals (a "27" must never become "納品27件")
    parts = [f"関係:{lvl}", f"Scout検知の同発注者の他募集{len(past)}件（当方の取引数ではない）",
             f"当方の応募{c['application_count']}件・受注{c['accepted_count']}件・納品{c['delivered_count']}件"
             f"・支払{c['paid_count']}件"]
    if c["post_application_interview_request_count"]:
        ids = ",".join(e["job_id"] for e in c["interview_required_history"] if e["found_after_application"])
        parts.append(f"⚠応募後に面談要求{c['post_application_interview_request_count']}回（{ids}・辞退）")
    if isinstance(c["interview_disclosed_in_posting"], list):
        parts.append("募集文に面談必須の明記あり:" + ",".join(c["interview_disclosed_in_posting"]))
    if c["revision_history"]:
        parts.append("修正回数:" + ",".join(f"{r['job_id']}={r['revision_count']}" for r in c["revision_history"]))
    if c["client_notes"]:
        parts.append("メモ:" + " / ".join(n["note"] for n in c["client_notes"])[:120])
    return " / ".join(parts)


def cmd_client_show(a):
    v = P.vault_load()
    clients = refresh(v)
    if a.job:
        job = v["master"][a.job]
        cid = client_id(job)
        out = {"client": clients.get(cid), "relationship": level(clients, v["master"], job),
               "opening": OPENINGS[level(clients, v["master"], job)], "summary": summary(clients, v["master"], job)}
    else:
        out = clients.get(a.id) or {}
    print(P.json.dumps(out, ensure_ascii=False, indent=1))


def cmd_client_note(a):
    v = P.vault_load()
    clients = refresh(v)
    if a.id not in clients:
        raise SystemExit(f"client {a.id} は未登録")
    clients[a.id].setdefault("client_notes", []).append({"at": P.now_iso(), "by": a.by, "note": a.note})
    P.vault_save(v)
    print(P.json.dumps(clients[a.id]["client_notes"], ensure_ascii=False, indent=1))
