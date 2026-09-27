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
# Statuses that keep an existing draft visible in the queue until the job is applied/closed.
QUEUE_VISIBLE = {"ASTRA_PASS", "READY_TO_APPLY", "APPLIED"}
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
    """Re-read reward / deadline / open slots from the live posting for ASTRA_PASS jobs."""
    import collect
    v = P.vault_load()
    master = v["master"]
    sdir = os.path.join(P.ROOT, "data", a.date, "app_source")
    os.makedirs(sdir, exist_ok=True)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()] if a.ids else \
        [j for j, job in master.items() if job.get("status") in TARGET]
    out = []
    for jid in ids:
        job = master.get(jid)
        if not job or job.get("status") not in TARGET:
            out.append({"job_id": jid, "skip": f"status={job and job.get('status')}（ASTRA_PASSのみ対象）"})
            continue
        page = collect.curl(f"{collect.BASE}/{jid}")
        if not page:
            out.append({"job_id": jid, "skip": "取得失敗"})
            continue
        src = parse_source(page)
        P.save_json(os.path.join(sdir, f"{jid}.json"), src)
        rc = {k: src[k] for k in ("header_reward", "deadline", "applicants", "contracted",
                                  "capacity", "closed", "body_reward_mentions")}
        rc["checked_at"] = P.now_iso()
        rc["listing_gross"] = job.get("gross")
        job["reward_check"] = rc
        out.append({"job_id": jid, "title": job["title"][:40], **{k: rc[k] for k in rc if k != "checked_at"}})
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
        review = float(d.get("human_review_minutes") or 3)
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
            "human_review_minutes": review,
            "app_priority": round(net / (review + (CONFIRM_PENALTY_MIN if confirm else 0)), 1),
            "claim_flags": sorted(set(CLAIM_RE.findall(text))),
            "final_qa_status": "PENDING_ASTRA",
            "next_action": d.get("next_action") or "Astra最終QA",
            "generated_at": P.now_iso(),
            # drafting time is not measurable from here; recorded via Status Updates (gen_minutes)
            "gen_minutes": d.get("gen_minutes"),
        }
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
    ("human_review_minutes", lambda j: j["application"]["human_review_minutes"]),
    ("ai_condition", lambda j: (j.get("eval") or {}).get("ai_condition")),
    ("key_excerpt", lambda j: j.get("key_excerpt") or j.get("desc_excerpt", "")),
    ("status", lambda j: j.get("status")),
    ("generated_at", lambda j: j["application"]["generated_at"]),
    ("gen_minutes", lambda j: j["application"]["gen_minutes"]),
    ("user_confirmed", lambda j: j["application"].get("user_confirmed")),
    ("applied_at", lambda j: j["application"].get("applied_at")),
]


def export_queue(master):
    jobs = [j for j in master.values() if j.get("application") and j.get("status") in QUEUE_VISIBLE]
    # app_priority already charges user-confirmation time (net JPY per human minute)
    jobs.sort(key=lambda j: -j["application"]["app_priority"])
    P._write_csv(os.path.join(P.OUT, "application_queue.csv"), APP_COLS, jobs)
    P.save_json(os.path.join(P.OUT, "application_queue.json"),
                {"generated_at": P.now_iso(), "count": len(jobs),
                 "jobs": [{c: fn(j) for c, fn in APP_COLS} for j in jobs]})
    return len(jobs)
