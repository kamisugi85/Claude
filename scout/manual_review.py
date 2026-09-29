"""Astra Manual Review Queue: CrowdWorks URLs the user hands to Astra, evaluated by Claude.

Queue    : vault meta["manual_review"][job_id] = {job_id, job_url, requested_by, requested_at,
           status PENDING | REVIEWED | ERROR, review_result, reviewed_at, error}
Intake   : an Astra-signed Status Updates row with new_status=MANUAL_REVIEW (apply-updates), or
           `manual-request` (same record, requested_by=Astra) when the URL reaches Claude directly.
Fetch    : `manual-fetch` reads only the queued job pages (no Scout-wide search) into
           data/<date>/manual_pending.json for Claude's evaluation.
Merge    : `manual-merge --evals` stores Claude's first-pass evaluation and hands the job to Astra
           through the existing Astra Queue (ASTRA_QA_PENDING). Astra's PASS then goes through the
           normal Application Queue flow. Nothing here applies, messages or delivers.
"""
import json
import os
import re
import sys

import pipeline as P

TRIAGE = ("応募候補", "PoC応募候補", "見送り候補")
LANES = ("Professional", "Experience", "Auto", "Human Premium")
URL_RE = re.compile(r"crowdworks\.jp/public/jobs/(\d+)")


def job_id_of(text):
    m = URL_RE.search(text or "") or re.fullmatch(r"\s*(\d{6,9})\s*", text or "")
    return m.group(1) if m else None


def enqueue(v, jid, url, by, at, note=""):
    """Add a PENDING request unless the job is already queued (never duplicates)."""
    q = v.setdefault("meta", {}).setdefault("manual_review", {})
    if jid in q and q[jid]["status"] in ("PENDING", "REVIEWED"):
        return q[jid], False
    q[jid] = {"job_id": jid, "job_url": url or f"https://crowdworks.jp/public/jobs/{jid}",
              "requested_by": by, "requested_at": at, "status": "PENDING", "review_result": None,
              "reviewed_at": None, "note": note, "existing_status": (v["master"].get(jid) or {}).get("status")}
    return q[jid], True


def cmd_manual_request(a):
    v = P.vault_load()
    out = []
    for u in a.urls:
        jid = job_id_of(u)
        if not jid:
            out.append({"input": u, "error": "CrowdWorksの案件URL/IDではない"})
            continue
        rec, added = enqueue(v, jid, u.split("?")[0] if "http" in u else None, a.by, P.now_iso(), a.note)
        out.append({"job_id": jid, "added": added, "status": rec["status"], "existing_status": rec["existing_status"]})
    P.vault_save(v)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def fetch_row(jid):
    """Latest posting of one job as a Scout row (same parser and screen as the daily Scout)."""
    import application as A
    import collect
    page = collect.curl(f"{collect.BASE}/{jid}")
    if not page:
        raise ValueError("案件ページを取得できない")
    src = A.parse_source(page)
    if not src["desc"]:
        raise ValueError("募集要項を読み取れない（非公開・削除の可能性）")
    title = re.search(r"<h1[^>]*>\s*(.*?)\s*<", page, re.S)
    hr = src["header_reward"] or {}
    pay = {"fixed_price_payment": {"min_budget": hr.get("min"), "max_budget": hr.get("max")}} \
        if hr.get("type") == "固定報酬制" else {"other": hr}
    jo = {"job_offer": {"title": title.group(1).strip() if title else "", "expired_on": src["deadline"]},
          "payment": pay}
    f = collect.screen(jo, src["desc"], src["client"])
    t = collect.text_of(page)
    cat = re.search(r"/public/jobs/category/(\d+)", page)
    return {"id": int(jid), "url": f"{collect.BASE}/{jid}", "title": jo["job_offer"]["title"],
            "category_id": int(cat.group(1)) if cat else None, "expired_on": src["deadline"],
            "tiers": ["manual"], "client": src["client"], "desc": src["desc"], "client_open_jobs": None,
            "entry": {"applicants": src["applicants"], "contracted": src["contracted"], "capacity": src["capacity"]},
            "header_reward": hr, "closed": src["closed"], "body_reward_mentions": src["body_reward_mentions"],
            "features": re.findall(r"(スキル不要|継続あり|スキマ時間歓迎|初心者歓迎|急募)", t[t.find("この仕事の特徴"):][:200]),
            **f}


def cmd_manual_fetch(a):
    v = P.vault_load()
    q = v.get("meta", {}).get("manual_review", {})
    ids = [j for j, r in q.items() if r["status"] == "PENDING"]
    ddir = os.path.join(P.ROOT, "data", a.date)
    os.makedirs(ddir, exist_ok=True)
    rows, errs = [], {}
    for jid in ids:
        try:
            r = fetch_row(jid)
        except ValueError as e:
            q[jid].update(status="ERROR", error=str(e), reviewed_at=P.now_iso())
            errs[jid] = str(e)
            continue
        r["existing_status"] = (v["master"].get(jid) or {}).get("status")
        rows.append(r)
    P.save_json(os.path.join(ddir, "manual_pending.json"), rows)
    P.vault_save(v)
    print(json.dumps({"pending": [r["id"] for r in rows], "errors": errs,
                      "file": os.path.join("data", a.date, "manual_pending.json")}, ensure_ascii=False))


def per_min(net, minutes_text):
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(minutes_text or ""))]
    return round(net / max(nums), 1) if net and nums and max(nums) > 0 else None


def check_eval(e):
    errs = [k for k in ("triage", "lane", "ai_completion", "human_minutes", "reason", "verdict") if not e.get(k)]
    if e.get("triage") not in TRIAGE:
        errs.append(f"triageは{'/'.join(TRIAGE)}のいずれか")
    if e.get("lane") not in LANES:
        errs.append(f"laneは{'/'.join(LANES)}のいずれか")
    return errs


def cmd_manual_merge(a):
    """Claude's first-pass evaluation -> Astra (ASTRA_QA_PENDING). Jobs already past Claude's hands
    (ASTRA_PASS, applied, ...) keep their status; only their facts are refreshed."""
    ddir = os.path.join(P.ROOT, "data", a.date)
    rows = {str(r["id"]): r for r in P.load_json(os.path.join(ddir, "manual_pending.json"), [])}
    evals = P.load_json(a.evals, [])
    v = P.vault_load()
    master, q = v["master"], v.setdefault("meta", {}).setdefault("manual_review", {})
    out, errs = [], {}
    for e in evals:
        jid = str(e["job_id"])
        r, rec = rows.get(jid), q.get(jid)
        bad = check_eval(e) + ([] if r else ["manual-fetchの取得結果がない"]) + \
            ([] if rec and rec["status"] == "PENDING" else ["Manual Review QueueでPENDINGではない"])
        if bad:
            errs[jid] = bad
            continue
        job = master.get(jid)
        fresh = P.job_facts(r)
        if job is None:
            job = master[jid] = fresh
            job["first_seen"] = rec["requested_at"]
            job["actual"] = {k: None for k in P.ACTUAL_FIELDS}
        else:
            job.update({k: x for k, x in fresh.items() if k != "detail_fp"})
        job["eval"] = {k: e.get(k) for k in P.EVAL_FIELDS}
        job["eval"].update(evaluated_at=P.now_iso(), lane=e["lane"], triage=e["triage"],
                           template_potential=e.get("template_potential"), repeat_reduction=e.get("repeat_reduction"),
                           worker_automation=e.get("worker_automation"), paid_tools=e.get("paid_tools"),
                           confirm_items=e.get("confirm_items") or [])
        if e.get("gross_jpy"):
            job["gross"], job["net_est"] = e["gross_jpy"], round(e["gross_jpy"] * (1 - P.FEE_RATE))
        elif "gross_jpy" in e:  # per-unit reward not stated: do not show the listing budget as the reward
            job["gross"] = job["net_est"] = None
        job["eval"]["net_per_human_min"] = per_min(job.get("net_est"), e["human_minutes"])
        job["manual_review"] = {"requested_by": rec["requested_by"], "requested_at": rec["requested_at"]}
        st = job.get("status")
        if st is None or st in P.CLAUDE_OWNED:
            P.set_status(job, "CLAUDE_CANDIDATE", "claude", f"Manual Review（{rec['requested_by']}依頼）：{e['triage']}")
            P.set_status(job, "ASTRA_QA_PENDING", "claude", "Astra二次評価待ち")
            result = e["triage"]
        else:
            result = f"{e['triage']}（既存ステータス{st}のため変更なし・最新情報のみ更新）"
        rec.update(status="REVIEWED", review_result=result, reviewed_at=P.now_iso())
        out.append({"job_id": jid, "status": job["status"], "review_result": result})
    P.vault_save(v)
    P.export(master, v.get("meta", {}))
    print(json.dumps({"reviewed": out, "errors": errs}, ensure_ascii=False, indent=1))
    if errs:
        sys.exit(1)


def cmd_manual_status(a):
    q = P.vault_load().get("meta", {}).get("manual_review", {})
    print(json.dumps(sorted(q.values(), key=lambda r: r["requested_at"]), ensure_ascii=False, indent=1))
