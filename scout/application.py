"""Application Queue: Claude drafts for ASTRA_PASS jobs, for Astra final QA and user sign-off.

Nothing here submits, fills forms or contacts clients on CrowdWorks.
Source of truth stays the Vault master: each job gets ``reward_check`` (re-read from the
posting) and ``application`` (the draft); the queue CSV/JSON is only an export of those.
"""
import json
import os
import re

import pipeline as P

TARGET = {"ASTRA_PASS"}
RECHECK = {"ASTRA_PASS", "READY_TO_APPLY"}  # app-check also re-verifies right before applying
# Statuses that keep a draft visible, so actual results stay traceable through to payment.
QUEUE_VISIBLE = {"ASTRA_PASS", "READY_TO_APPLY", "APPLIED", "SKIPPED", "ACCEPTED", "NOT_SELECTED",
                 "IN_PROGRESS", "READY_FOR_QA", "READY_TO_DELIVER", "DELIVERED", "PAID"}
CONFIRM_PENALTY_MIN = 5  # user time for answering a confirmation question
# Wording Astra should look at: claims of track record / AI work the profile does not support.
CLAIM_RE = re.compile(r"実績(?!作り|づくり)|受注|納品経験|ライター(?:経験|として)|執筆経験|SEO|WordPress|"
                      r"AI(?:導入|コンサル|案件|開発)|自動化(?:システム|ツール)|Scout|スカウト|構築|運用して")


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def _yen(s):
    return int(s.replace(",", ""))


def parse_source(page):
    import collect
    t = collect.text_of(page)
    desc, client = collect.parse_detail(page)
    head = t[:t.find("仕事の詳細")] if "仕事の詳細" in t else t[:1500]
    m = re.search(r"(固定報酬制|時間単価制|タスク形式|記事単価制?|記事単価)\s*([\d,]+)\s*円(?:\s*〜\s*([\d,]+)\s*円)?", head)
    header_reward = None
    if not m:  # per-article jobs show the contract estimate instead
        m2 = re.search(r"契約金額（目安）\s*[:：]\s*([\d,]+)\s*円", head)
        if m2:
            header_reward = {"type": "契約金額（目安）", "min": _yen(m2.group(1)), "max": None}
    if m:
        header_reward = {"type": m.group(1), "min": _yen(m.group(2)),
                         "max": _yen(m.group(3)) if m.group(3) else None}
    d = re.search(r"応募期限\s*(\d{4})年(\d{2})月(\d{2})日", head)
    num = lambda k: (int(x.group(1)) if (x := re.search(k + r"\s*(\d+)\s*人", head)) else None)
    mentions = [re.sub(r"\s+", " ", ln).strip()[:140] for ln in desc.split("\n")
                if "円" in ln and re.search(r"報酬|記事|1本|１本|金額|単価|税込|税抜|謝礼|固定|入力|合計|テスト|トライアル", ln)]
    return {
        "desc": desc, "client": client,
        "header_reward": header_reward,
        "deadline": f"{d.group(1)}-{d.group(2)}-{d.group(3)}" if d else None,
        "applicants": num("応募した人"), "contracted": num("契約した人"), "capacity": num("募集人数"),
        "closed": bool(re.search(r"募集(?:は)?終了|この仕事の募集は終了", head)),
        "body_reward_mentions": mentions[:12],
    }


def cmd_app_check(a):
    """Re-read reward / deadline / open slots / AI terms from the live posting; report changes."""
    import collect
    v = P.vault_load()
    master = v["master"]
    sdir = os.path.join(P.ROOT, "data", a.date, "app_source")
    os.makedirs(sdir, exist_ok=True)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()] if a.ids else \
        [j for j, job in master.items() if job.get("status") in RECHECK]
    out = []
    for jid in ids:
        job = master.get(jid)
        if not job or job.get("status") not in RECHECK:
            out.append({"job_id": jid, "skip": f"status={job and job.get('status')}（ASTRA_PASS/READY_TO_APPLYのみ対象）"})
            continue
        page = collect.curl(f"{collect.BASE}/{jid}")
        if not page:
            out.append({"job_id": jid, "skip": "取得失敗"})
            continue
        src = parse_source(page)
        P.save_json(os.path.join(sdir, f"{jid}.json"), src)
        prev = job.get("reward_check") or {}
        rc = {k: src[k] for k in ("header_reward", "deadline", "applicants", "contracted",
                                  "capacity", "closed", "body_reward_mentions")}
        rc["ai_policy"] = collect.ai_policy(src["desc"])[0]
        rc["desc_hash"] = P.hashlib.md5(_norm(src["desc"]).encode()).hexdigest()[:12]
        rc["checked_at"] = P.now_iso()
        rc["listing_gross"] = prev.get("listing_gross", job.get("gross"))
        changes = [f"{k}: {prev.get(k)} → {rc[k]}" for k in
                   ("header_reward", "deadline", "closed", "capacity", "body_reward_mentions", "ai_policy", "desc_hash")
                   if k in prev and prev.get(k) != rc[k]]
        if rc["closed"]:
            changes.append("募集終了")
        if rc["capacity"] and (rc["contracted"] or 0) >= rc["capacity"]:
            changes.append(f"募集枠充足 {rc['contracted']}/{rc['capacity']}")
        if rc["ai_policy"] != (job.get("eval") or {}).get("ai_condition"):
            changes.append(f"AI条件 評価時{(job.get('eval') or {}).get('ai_condition')} → 原文判定{rc['ai_policy']}")
        rc["changes"] = changes
        job["reward_check"] = rc
        out.append({"job_id": jid, "title": job["title"][:40], "status": job.get("status"),
                    **{k: rc[k] for k in rc if k not in ("checked_at", "body_reward_mentions")}})
    P.vault_save(v)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def _profile_value(profile, ref):
    cur = profile
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", ref):
        if part.startswith("["):
            cur = cur[int(part[1:-1])]
        else:
            cur = cur[part]
    if isinstance(cur, (dict, list)):
        raise KeyError(ref)
    return cur


def _validate(d, job, src, profile):
    errs = []
    if job.get("status") not in TARGET:
        errs.append(f"status={job.get('status')}（ASTRA_PASSのみ）")
    rc = job.get("reward_check")
    if not rc or not src:
        errs.append("app-check未実行（報酬を原文で再確認していない）")
        return errs
    if rc.get("closed"):
        errs.append("募集終了")
    if rc.get("deadline") and rc["deadline"] < P.today():
        errs.append(f"応募期限切れ {rc['deadline']}")
    body = _norm(src["desc"])
    ev = d.get("reward_evidence", "")
    if not ev or _norm(ev) not in body:
        errs.append("reward_evidenceが原文に無い")
    else:
        # actual_reward is tax-included; bodies often state the pre-tax amount
        amounts = {_yen(x) for x in re.findall(r"([\d,]+)\s*円", ev)}
        if not {d["actual_reward"], round(d["actual_reward"] / 1.1)} & amounts:
            errs.append("actual_rewardがreward_evidence内の金額（税込/税抜）と一致しない")
    qs, ans = d.get("application_questions", []), d.get("application_answers", [])
    if len(qs) != len(ans):
        errs.append("質問と回答の数が不一致")
    for q in qs:
        if _norm(q) not in body:
            errs.append(f"質問原文が本文に無い: {q[:30]}")
    for f in d.get("facts_used", []):
        try:
            f["profile_value"] = _profile_value(profile, f["profile_ref"])
        except (KeyError, IndexError, TypeError):
            errs.append(f"facts_usedの参照先がプロフィールに無い: {f.get('profile_ref')}")
    if d.get("unverified_facts") and d.get("user_confirmation_required") != "yes":
        errs.append("unverified_factsがあるのにuser_confirmation_required≠yes")
    return errs


def _hold_reasons(job, date):
    """Why a validated draft is not READY_TO_APPLY yet (empty = ready)."""
    app, rc = job["application"], job.get("reward_check") or {}
    why = []
    if not str(rc.get("checked_at", "")).startswith(date):
        why.append("本日の原文再確認なし（app-check未実行）")
    if rc.get("changes"):
        why.append("原文の変化：" + "、".join(rc["changes"]))
    if app["user_confirmation_required"] == "yes":
        why.append("本人確認が必要な項目あり")
    if app["claim_flags"]:
        why.append("実績の表現を確認：" + "、".join(app["claim_flags"]))
    if not app["conflict_risk"].startswith("低"):
        why.append("利益相反リスク：" + (app["conflict_risk"][:40] or "未記載"))
    return why


def cmd_app_merge(a):
    v = P.vault_load()
    master, profile = v["master"], v.get("profile", {})
    drafts = P.load_json(a.drafts, [])
    sdir = os.path.join(P.ROOT, "data", a.date, "app_source")
    ok, failed = [], {}
    for d in drafts:
        jid = str(d["job_id"])
        job = master.get(jid)
        if job is None:
            failed[jid] = ["Job Masterに無い"]
            continue
        src = P.load_json(os.path.join(sdir, f"{jid}.json"), None)
        errs = _validate(d, job, src, profile)
        if errs:
            failed[jid] = errs
            continue
        text = d["application_draft"] + " ".join(d.get("application_answers", []))
        confirm = d.get("user_confirmation_required") == "yes"
        net = round(d["actual_reward"] * (1 - P.FEE_RATE))
        review = float(d.get("review_minutes_est") or d.get("human_review_minutes") or 3)
        job["gross"], job["net_est"] = d["actual_reward"], net
        job["application"] = {
            "actual_reward": d["actual_reward"], "actual_net": net,
            "reward_evidence": d["reward_evidence"],
            "application_draft": d["application_draft"],
            "application_questions": d.get("application_questions", []),
            "application_answers": d.get("application_answers", []),
            "facts_used": d.get("facts_used", []),
            "unverified_facts": d.get("unverified_facts", []),
            "conflict_risk": d.get("conflict_risk", ""),
            "user_confirmation_required": "yes" if confirm else "no",
            "review_minutes_est": review,
            "app_priority": round(net / (review + (CONFIRM_PENALTY_MIN if confirm else 0)), 1),
            "claim_flags": sorted(set(CLAIM_RE.findall(text))),
            "final_qa_status": "PENDING_ASTRA",
            "next_action": d.get("next_action") or "Astra最終QA",
            "generated_at": P.now_iso(),
            # measured times come back via Status Updates (application_preparation_ai_time etc.)
            "application_preparation_ai_time": d.get("application_preparation_ai_time"),
        }
        hold = _hold_reasons(job, a.date)
        app = job["application"]
        if hold:
            app["final_qa_status"], app["next_action"] = "HOLD", "応募準備保留：" + " / ".join(hold)
        else:  # Claude re-read the live posting today and nothing needs the user → ready
            app["final_qa_status"], app["next_action"] = "CLAUDE_CHECKED", "本人が応募 → Astraへ報告"
            P.set_status(job, "READY_TO_APPLY", "claude",
                         "応募準備完了（原文再確認 %s）" % (job.get("reward_check") or {}).get("checked_at"))
        ok.append(jid)
    P.vault_save(v)
    P.export(master, v.get("meta", {}))
    print(json.dumps({"merged": ok, "failed": failed}, ensure_ascii=False, indent=1))
    if failed:
        raise SystemExit(1)


def _pairs(app):
    return "\n".join(f"{q}\n→ {an}" for q, an in
                     zip(app["application_questions"], app["application_answers"]))


def _facts(app):
    return "\n".join(f"{f['fact']}（{f['profile_ref']}＝{f.get('profile_value')}）" for f in app["facts_used"])


def _entry(j):
    rc = j.get("reward_check") or {}
    return f"応募{rc.get('applicants')}人・契約{rc.get('contracted')}/{rc.get('capacity')}人" + ("・募集終了" if rc.get("closed") else "")


APP_COLS = [
    ("job_id", lambda j: j["job_id"]), ("url", lambda j: j["url"]), ("title", lambda j: j["title"]),
    ("classification", lambda j: (j.get("eval") or {}).get("classification")),
    ("actual_reward", lambda j: j["application"]["actual_reward"]),
    ("actual_net", lambda j: j["application"]["actual_net"]),
    ("listing_reward", lambda j: (j.get("reward_check") or {}).get("listing_gross")),
    ("reward_evidence", lambda j: j["application"]["reward_evidence"]),
    ("astra_verdict", lambda j: (j.get("astra") or {}).get("astra_verdict")),
    ("astra_reason", lambda j: (j.get("astra") or {}).get("astra_reason")),
    ("deadline", lambda j: (j.get("reward_check") or {}).get("deadline") or j.get("expired_on")),
    ("entry_status", _entry),
    ("application_draft", lambda j: j["application"]["application_draft"]),
    ("application_questions", lambda j: P._fmt_list(j["application"]["application_questions"])),
    ("application_answers", lambda j: _pairs(j["application"])),
    ("facts_used", lambda j: _facts(j["application"])),
    ("unverified_facts", lambda j: P._fmt_list(j["application"]["unverified_facts"])),
    ("conflict_risk", lambda j: j["application"]["conflict_risk"]),
    ("claim_flags", lambda j: P._fmt_list(j["application"]["claim_flags"])),
    ("final_qa_status", lambda j: j["application"]["final_qa_status"]),
    ("user_confirmation_required", lambda j: j["application"]["user_confirmation_required"]),
    ("next_action", lambda j: j["application"]["next_action"]),
    ("app_priority", lambda j: j["application"]["app_priority"]),
    ("review_minutes_est", lambda j: j["application"]["review_minutes_est"]),
    ("ai_condition", lambda j: (j.get("eval") or {}).get("ai_condition")),
    ("key_excerpt", lambda j: j.get("key_excerpt") or j.get("desc_excerpt", "")),
    ("status", lambda j: j.get("status")),
    ("status_reason", lambda j: (j.get("status_history") or [{}])[-1].get("note")),
    ("generated_at", lambda j: j["application"]["generated_at"]),
    ("recheck_at", lambda j: (j.get("reward_check") or {}).get("checked_at")),
    ("recheck_changes", lambda j: P._fmt_list((j.get("reward_check") or {}).get("changes"))),
    ("app_note", lambda j: j["application"].get("app_note")),
    ("user_confirmed", lambda j: j["application"].get("user_confirmed")),
    # actual PoC tracking (filled from Status Updates)
    ("application_preparation_ai_time", lambda j: j["application"].get("application_preparation_ai_time")),
    ("human_review_minutes", lambda j: j["application"].get("human_review_minutes")),
    ("applied_at", lambda j: j["application"].get("applied_at")),
    ("result", lambda j: (j.get("actual") or {}).get("result")),
    ("production_ai_time", lambda j: (j.get("actual") or {}).get("actual_ai_processing")),
    ("production_human_minutes", lambda j: (j.get("actual") or {}).get("actual_human_minutes")),
    ("revision_count", lambda j: (j.get("actual") or {}).get("revision_count")),
    ("actual_net_reward", lambda j: (j.get("actual") or {}).get("actual_net_reward")),
    ("actual_net_per_human_min", lambda j: realized(j).get("net_per_min")),
]


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("分", "").replace("円", ""))
    except (TypeError, ValueError):
        return None


def realized(j):
    """Actual (not estimated) net JPY per human minute, once the outcome is known.

    Human minutes = review/confirmation minutes for the application + production minutes.
    A rejected or skipped application still costs its review minutes and earns 0.
    """
    app, act = j.get("application") or {}, j.get("actual") or {}
    review = _num(app.get("human_review_minutes"))
    prod = _num(act.get("actual_human_minutes"))
    status = j.get("status")
    if status == "PAID" or _num(act.get("actual_net_reward")) is not None:
        net, done = _num(act.get("actual_net_reward")), status in ("PAID", "DELIVERED")
    elif status in ("NOT_SELECTED", "SKIPPED"):
        net, done = 0.0, True
    else:
        return {}
    if net is None or review is None or not done:
        return {"net": net, "human_min": (review or 0) + (prod or 0), "complete": False}
    mins = review + (prod or 0)
    return {"net": net, "human_min": mins, "complete": True,
            "net_per_min": round(net / mins, 1) if mins else None}


def poc_summary(master):
    rows = [realized(j) for j in master.values() if j.get("application")]
    done = [r for r in rows if r.get("complete")]
    net, mins = sum(r["net"] for r in done), sum(r["human_min"] for r in done)
    st = [j.get("status") for j in master.values() if j.get("application")]
    return {"applications": len(st), "ready": st.count("READY_TO_APPLY"), "skipped": st.count("SKIPPED"),
            "applied": sum(s in ("APPLIED", "ACCEPTED", "NOT_SELECTED", "IN_PROGRESS", "READY_FOR_QA",
                                 "READY_TO_DELIVER", "DELIVERED", "PAID") for s in st),
            "not_selected": st.count("NOT_SELECTED"), "paid": st.count("PAID"),
            "measured": len(done), "net_jpy": net, "human_minutes": mins,
            "net_per_human_min": round(net / mins, 1) if mins else None}


def export_queue(master):
    jobs = [j for j in master.values() if j.get("application") and j.get("status") in QUEUE_VISIBLE]
    # app_priority already charges user-confirmation time (net JPY per human minute)
    jobs.sort(key=lambda j: (j.get("status") in ("SKIPPED", "NOT_SELECTED"), -j["application"]["app_priority"]))
    P._write_csv(os.path.join(P.OUT, "application_queue.csv"), APP_COLS, jobs)
    P.save_json(os.path.join(P.OUT, "application_queue.json"),
                {"generated_at": P.now_iso(), "count": len(jobs),
                 "jobs": [{c: fn(j) for c, fn in APP_COLS} for j in jobs]})
    return len(jobs)


# ---- KPI (estimated vs actual, split by lane) -------------------------------------------------
LANES = {"A": "auto", "B": "auto", "C": "professional"}  # C = Professional / Human Premium
DONE = ("PAID", "DELIVERED", "NOT_SELECTED", "SKIPPED")


def _hist(j):
    return {h["status"] for h in j.get("status_history", [])}


def _est_min(j):
    """Estimated human minutes: production estimate (upper bound of e.g. "3-5分") + application review estimate."""
    nums = re.findall(r"\d+(?:\.\d+)?", str((j.get("eval") or {}).get("human_minutes") or ""))
    return (float(nums[-1]) if nums else 0) + ((j.get("application") or {}).get("review_minutes_est") or 0)


def kpi(master, meta, runs):
    """Funnel rates, estimated and actual net per human minute, per lane (auto / professional)."""
    batches = meta.get("review_batches", [])
    in_batch = {jid for b in batches for jid in b["job_ids"]}
    groups = {"total": list(master.values())}
    for j in master.values():
        c = (j.get("eval") or {}).get("classification")
        if c in LANES:
            groups.setdefault(LANES[c], []).append(j)
            groups.setdefault("class_" + c, []).append(j)
    lane_of = {jid: LANES.get((master.get(jid, {}).get("eval") or {}).get("classification")) for jid in in_batch}
    rate = lambda n, d: round(n / d, 3) if d else None
    out = {"discovered": sum(r.get("new") or 0 for r in runs)}
    for g, jobs in groups.items():
        ids = {j["job_id"] for j in jobs}
        ev = [j for j in jobs if j.get("eval")]
        cand = [j for j in jobs if "CLAUDE_CANDIDATE" in _hist(j)]
        decided = [j for j in cand if _hist(j) & {"ASTRA_PASS", "ASTRA_REJECT", "NEED_USER", "SKIPPED"}]
        passed = [j for j in cand if "ASTRA_PASS" in _hist(j)]
        applied = [j for j in passed if _hist(j) & {"APPLIED"}]
        acc = [j for j in applied if "ACCEPTED" in _hist(j)]
        rej = [j for j in applied if "NOT_SELECTED" in _hist(j)]
        est_net = sum(j.get("net_est") or 0 for j in passed)
        est_min = sum(_est_min(j) for j in passed)
        a = {"actual_net_reward": 0.0, "application_preparation_ai_time": 0.0, "production_ai_time": 0.0,
             "human_review_minutes": 0.0, "production_human_minutes": 0.0, "revision_count": 0.0}
        done_net = done_min = 0.0
        for j in {x["job_id"]: x for x in applied + [x for x in passed if x.get("status") == "SKIPPED"]}.values():
            app, act = j.get("application") or {}, j.get("actual") or {}
            vals = {"actual_net_reward": _num(act.get("actual_net_reward")),
                    "application_preparation_ai_time": _num(app.get("application_preparation_ai_time")),
                    "production_ai_time": _num(act.get("actual_ai_processing")),
                    "human_review_minutes": _num(app.get("human_review_minutes")),
                    "production_human_minutes": _num(act.get("actual_human_minutes")),
                    "revision_count": _num(act.get("revision_count"))}
            for k, x in vals.items():
                a[k] += x or 0
            if j.get("status") in DONE:
                done_net += vals["actual_net_reward"] or 0
                done_min += (vals["human_review_minutes"] or 0) + (vals["production_human_minutes"] or 0)
        for b in batches:  # batch totals are never split per job
            if g == "total" or ({lane_of.get(x) for x in b["job_ids"]} == {g}) or \
                    (g.startswith("class_") and all((master[x].get("eval") or {}).get("classification") == g[6:]
                                                    for x in b["job_ids"])):
                a["human_review_minutes"] += b["human_review_minutes"]
                if all(master[x].get("status") in DONE for x in b["job_ids"]):
                    done_min += b["human_review_minutes"]
        out[g] = {"jobs": len(ids), "claude_evaluated": len(ev), "claude_candidates": len(cand),
                  "claude_candidate_rate": rate(len(cand), len(ev)),
                  "astra_decided": len(decided), "astra_pass": len(passed),
                  "astra_pass_rate": rate(len(passed), len(decided)),
                  "applied": len(applied), "application_rate": rate(len(applied), len(passed)),
                  "accepted": len(acc), "not_selected": len(rej), "acceptance_rate": rate(len(acc), len(acc) + len(rej)),
                  "estimated": {"net_jpy": est_net, "human_minutes": est_min,
                                "net_per_human_min": round(est_net / est_min, 1) if est_min else None},
                  "actual": {**a, "outcome_known_net": done_net, "outcome_known_human_minutes": done_min,
                             "net_per_human_min": round(done_net / done_min, 1) if done_min else None}}
    return out


def cmd_app_batch(a):
    """Record a batch-level human time reported by the user (never split per job); optional status."""
    v = P.vault_load()
    master, meta = v["master"], v.setdefault("meta", {})
    ids = sorted({re.sub(r"\D", "", x) for x in a.ids.split(",") if x.strip()})
    missing = [x for x in ids if x not in master]
    if missing:
        raise SystemExit(f"unknown job_id {missing}")
    bs = [b for b in meta.get("review_batches", []) if b["job_ids"] != ids]
    bs.append({"job_ids": ids, "human_review_minutes": a.minutes, "source": a.source,
               "recorded_at": P.now_iso()})
    meta["review_batches"] = bs
    for x in ids:
        if a.status:
            P.set_status(master[x], a.status, a.source, a.note or "")
        if master[x].get("application"):
            master[x]["application"]["next_action"] = a.next_action or master[x]["application"]["next_action"]
    P.vault_save(v)
    P.export(master, meta)
    print(json.dumps({"batch": bs[-1], "statuses": {x: master[x]["status"] for x in ids}}, ensure_ascii=False))
