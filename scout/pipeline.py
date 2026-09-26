#!/usr/bin/env python3
"""Scout pipeline: rule filter -> delta detection -> Claude eval I/O -> Job Master.

Data layout
-----------
scout/state/index.json   Public-safe index of every scouted job (no personal data):
                         fingerprints, rule status, first/last seen. Drives dedupe
                         and delta scans.
scout/state/runs.jsonl   Public-safe run log (counts only).
scout/state/vault.enc    Encrypted private bundle (AES-256, key in SCOUT_VAULT_KEY):
                           profile      user profile used for fit judgement
                           master       Job Master: every Claude-evaluated job with
                                        evaluation, status history and actuals
                           meta         Drive file ids, applied status-update rows
scout/out/               Exports for Google Sheets / Astra (gitignored, plain text).

Commands
--------
init-vault --profile P.json      create the vault (first time only)
prepare [--date D] [--cap N]     rule-filter collected jobs, update index, write
                                 scout/data/D/pending_eval.json for Claude
merge --evals E.json [--date D]  merge Claude evaluations into the master, export
apply-updates --csv U.csv        apply status/actual rows from the Sheets update log
export                           rebuild scout/out/* from the master
show-profile                     print the decrypted profile (for the eval step)
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state")
OUT = os.path.join(ROOT, "out")
JST = dt.timezone(dt.timedelta(hours=9))
FEE_RATE = 0.20  # CrowdWorks system fee assumed for contracts up to 100,000 JPY

STATUSES = [
    "SCOUTED", "RULE_REJECTED", "CLAUDE_CANDIDATE", "CLAUDE_REJECTED", "ASTRA_QUEUE",
    "ASTRA_PASS", "ASTRA_REJECT", "NEED_USER", "READY_TO_APPLY", "APPLIED",
    "ACCEPTED", "IN_PROGRESS", "READY_FOR_QA", "READY_TO_DELIVER", "DELIVERED",
    "PAID", "CLOSED",
]
# Statuses Claude may overwrite on re-evaluation; later ones belong to Astra/user.
CLAUDE_OWNED = {"SCOUTED", "CLAUDE_CANDIDATE", "CLAUDE_REJECTED", "ASTRA_QUEUE"}
ACTUAL_FIELDS = ["actual_human_minutes", "actual_ai_processing", "revision_count",
                 "actual_gross_reward", "actual_net_reward", "result",
                 "client_rating", "repeat_order"]
EVAL_FIELDS = ["classification", "ai_condition", "requirements", "fit", "profile_link",
               "ai_steps", "human_steps", "ai_completion", "human_minutes",
               "est_hourly", "repeatability", "client_risk", "user_questions",
               "verdict", "reason", "source_notes", "gross_jpy"]


def now_iso():
    return dt.datetime.now(JST).isoformat(timespec="minutes")


def today():
    return dt.datetime.now(JST).strftime("%Y-%m-%d")


def load_json(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- vault
VAULT = os.path.join(STATE, "vault.enc")


def _key():
    k = os.environ.get("SCOUT_VAULT_KEY")
    if not k:
        sys.exit("SCOUT_VAULT_KEY is not set")
    return k


def vault_load():
    if not os.path.exists(VAULT):
        sys.exit("vault not found; run init-vault first")
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                        "-a", "-A", "-in", VAULT, "-pass", "env:SCOUT_VAULT_KEY"],
                       capture_output=True, env={**os.environ, "SCOUT_VAULT_KEY": _key()})
    if r.returncode:
        sys.exit("vault decrypt failed: " + r.stderr.decode())
    return json.loads(r.stdout)


def vault_save(v):
    data = json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode()
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
                        "-a", "-A", "-out", VAULT + ".tmp", "-pass", "env:SCOUT_VAULT_KEY"],
                       input=data, capture_output=True,
                       env={**os.environ, "SCOUT_VAULT_KEY": _key()})
    if r.returncode:
        sys.exit("vault encrypt failed: " + r.stderr.decode())
    os.replace(VAULT + ".tmp", VAULT)


# ---------------------------------------------------------------- rules
RESTRICTED = re.compile(
    r"学生限定|女性限定|男性限定|女性の方(?:限定|のみ)|男性の方(?:限定|のみ)|ママ限定|プレママ|主婦限定|"
    r"看護師|保育士|介護(?:職|士)|医療従事者|歯科医師|薬剤師|教師|塾講師|"
    r"(?:都|道|府|県|市|区|地方)(?:在住|に住んだ経験)?[^】]{0,6}限定|在住の方(?:限定|のみ)")
AGE_LIMIT = re.compile(r"(\d)0代(?:限定|の方限定|のみ|女性|男性|独身)|(\d)0代〜(\d)0代(?:限定|前半)")
CONSUMER_SURVEY = re.compile(
    r"利用(?:経験)?者限定|使っている方|使っていた方|利用した方|利用したことがある方|経験者限定|"
    r"お持ちの方|飼って|購入(?:経験|した)|契約した方|加入している方|受けている方|行ったことがある方")
HEAVY_COMMIT = re.compile(r"週\s*[3-5]日|週\s*(?:[2-9]\d|1[5-9])\s*時間|常駐|出社|フルタイム|1日\s*[4-9]\s*時間")
HARD_REQ = {
    "WordPress": r"(?:WordPress|ワードプレス)[^。\n]{0,25}(?:必須|できる方|直接入稿|入稿できる|経験(?:者|が)必要)",
    "ポートフォリオ": r"ポートフォリオ[^。\n]{0,15}(?:必須|提出|をお送り|を添付)",
    "執筆実績の提出": r"(?:執筆|取材)(?:実績|経験)[^。\n]{0,15}(?:必須|の(?:ご)?提出|URL)",
    "校正経験": r"校正[^。\n]{0,10}(?:経験|実務)[^。\n]{0,8}(?:必須|がある方のみ|者限定)",
    "取材経験": r"取材経験[^。\n]{0,10}(?:必須|がある方)",
}
# phrases that mention a profile word without being about that topic
NOT_TOPIC = re.compile(r"副業(?:OK|ＯＫ|歓迎|可|の方|として|でも)|在宅副業|投資家の方|英語不要|Excel(?:・|、)?(?:Word|ワード)?(?:が使える|操作)|受験生")
EXCLUDE_RISK = {"勧誘兆候", "同一文面を複数アカウントが投稿", "購入/費用要求"}


def rescreen_risk(r):
    """Re-apply the current text risk patterns so rule changes take effect on stored rows."""
    import collect
    keep = [x for x in r["risk"] if x not in collect.RISK_PATTERNS]
    r["risk"] = keep + [k for k, p in collect.RISK_PATTERNS.items() if re.search(p, r["desc"])]


def rule_filter(r, profile):
    """Return (status, reasons, profile_hits)."""
    rescreen_risk(r)
    text = r["title"] + "\n" + r["desc"][:3000]
    clean = NOT_TOPIC.sub("", text)
    title = NOT_TOPIC.sub("", r["title"])
    hits = sorted({k for k in profile.get("keywords_strong", profile.get("keywords", [])) if k in clean}
                  | {k for k in profile.get("keywords_title_only", []) if k in title})
    reasons = []
    if r["ai_policy"] == "D":
        reasons.append("AI利用禁止")
    bad = EXCLUDE_RISK & set(r["risk"])
    if bad:
        reasons.append("リスク:" + "/".join(sorted(bad)))
    if RESTRICTED.search(r["title"]):
        reasons.append("属性・地域限定（本人未確認）")
    m = AGE_LIMIT.search(r["title"])
    if m:
        age = int(today()[:4]) - int(profile.get("birth_year", 1900))
        decades = {int(x) * 10 for x in m.groups() if x}
        if decades and not any(d <= age < d + 10 for d in range(min(decades), max(decades) + 10, 10)):
            reasons.append("年齢条件不一致")
    unconfirmed = set(profile.get("unconfirmed_skills", []))
    for name, pat in HARD_REQ.items():
        if name in unconfirmed and re.search(pat, r["desc"]):
            reasons.append(f"必須条件:{name}")
    pay_type = r["pay"]["type"]
    if pay_type == "task" and CONSUMER_SURVEY.search(text) and not hits:
        reasons.append("利用者限定アンケート（プロフィール外）")
    if r.get("needs_experience") and not hits:
        reasons.append("本人体験が必要（プロフィール外）")
    tiers = set(r["tiers"])
    if "C" in tiers and tiers <= {"C"} and not hits:
        reasons.append("専門キーワードのみ一致・プロフィール接点なし")
    if "B" in tiers and r["ai_policy"] == "C" and not hits and pay_type != "task":
        reasons.append("AI利用条件不明・プロフィール接点なし")
    if pay_type in ("hourly", "fixed") and HEAVY_COMMIT.search(r["desc"]):
        reasons.append("稼働条件が重い")
    return ("RULE_REJECTED" if reasons else "PASS"), reasons, hits


def gross_of(pay):
    return pay.get("price") or pay.get("max") or pay.get("min") or 0


def prescore(r, hits):
    c = r["client"]
    s = {"A": 3, "B": 1.5, "C": 0.5}.get(r["ai_policy"], 0)
    s += min(len(hits), 3) * 1.5
    s += (c.get("averageScore") or 0) / 2 + (1 if c.get("isIdentityVerified") else 0)
    s += min(r.get("client_open_jobs", 1), 20) / 10
    s -= 0.5 * len([x for x in r["risk"] if x in ("評価0", "発注実績<=2")])
    return round(s, 2)


BACKLOG_KEYS = ["id", "url", "title", "tiers", "category_id", "expired_on", "released_at", "entry",
                "client", "pay", "ai_policy", "requirements", "risk", "needs_experience",
                "client_open_jobs", "same_text_count", "first_seen", "listing_fp", "desc_hash",
                "price_mentions"]


def refetch(b):
    """Rebuild a full row for a backlog job by re-fetching its detail page."""
    import collect
    page = collect.curl(f"{collect.BASE}/{b['id']}")
    if not page:
        return None
    desc, client = collect.parse_detail(page)
    if not desc:
        return None
    r = dict(b)
    r["desc"] = desc
    r["client"] = client or b.get("client", {})
    r["ai_policy"], r["ai_evidence"] = collect.ai_policy(desc)
    r["requirements"] = [k for k, p in collect.REQ_PATTERNS.items() if re.search(p, desc)]
    r["needs_experience"] = len(collect.EXPERIENCE.findall(r["title"] + desc)) >= 2
    r["desc_hash"] = hashlib.md5(re.sub(r"\s", "", desc)[:400].encode()).hexdigest()
    return r


def detail_fp(r):
    c = r["client"]
    key = [r["title"], json.dumps(r["pay"], sort_keys=True), r["desc_hash"], r["ai_policy"],
           r["expired_on"], bool(c.get("isIdentityVerified")),
           round((c.get("averageScore") or 0) * 2) / 2]
    return hashlib.md5(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:12]


def change_reasons(old, r):
    out = []
    if old.get("pay") != r["pay"]:
        out.append("報酬変更")
    if old.get("desc_hash") != r["desc_hash"]:
        out.append("募集条件変更")
    if old.get("ai_policy") != r["ai_policy"]:
        out.append("AI利用条件変更")
    if old.get("expired_on") != r["expired_on"]:
        out.append("募集状態変更")
    oc, nc = old.get("client", {}), r["client"]
    if bool(oc.get("isIdentityVerified")) != bool(nc.get("isIdentityVerified")) or \
            abs((oc.get("averageScore") or 0) - (nc.get("averageScore") or 0)) >= 0.3:
        out.append("発注者情報変化")
    return out


def job_facts(r):
    c = r["client"]
    gross = gross_of(r["pay"])
    return {
        "job_id": r["id"], "url": r["url"], "title": r["title"], "tiers": r["tiers"],
        "category_id": r["category_id"], "pay": r["pay"], "gross": gross,
        "net_est": round(gross * (1 - FEE_RATE)) if gross else None,
        "expired_on": r["expired_on"], "entry": r.get("entry"),
        "client": {k: c.get(k) for k in ("userId", "userDisplayName", "isIdentityVerified",
                                         "averageScore", "jobOfferAchievementCount")},
        "client_open_jobs": r.get("client_open_jobs"), "ai_policy_rule": r["ai_policy"],
        "ai_evidence": r.get("ai_evidence", []), "requirements_rule": r["requirements"],
        "risk_rule": r["risk"], "desc_hash": r["desc_hash"], "detail_fp": detail_fp(r),
        "desc_excerpt": re.sub(r"\s+", " ", r["desc"])[:600],
    }


def set_status(job, status, by, note=""):
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    if job.get("status") != status:
        job.setdefault("status_history", []).append(
            {"status": status, "at": now_iso(), "by": by, "note": note})
        job["status"] = status


# ---------------------------------------------------------------- commands
def cmd_init_vault(a):
    if os.path.exists(VAULT) and not a.force:
        sys.exit("vault exists (use --force to overwrite)")
    vault_save({"profile": load_json(a.profile, {}), "master": {}, "meta": {}})
    print("vault created")


def cmd_show_profile(a):
    print(json.dumps(vault_load()["profile"], ensure_ascii=False, indent=1))


def cmd_prepare(a):
    date = a.date
    ddir = os.path.join(ROOT, "data", date)
    rows = [json.loads(l) for l in open(os.path.join(ddir, "jobs.jsonl"), encoding="utf-8")]
    listed = load_json(os.path.join(ddir, "listed.json"), {})
    summ = load_json(os.path.join(ddir, "summary.json"), {})
    index = load_json(os.path.join(STATE, "index.json"), {})
    v = vault_load()
    profile, master = v["profile"], v["master"]
    ts = now_iso()
    for jid in listed:
        if jid in index:
            index[jid]["last_seen"] = ts
    counts = {"rule_rejected": 0, "unchanged_evaluated": 0, "delta": 0, "new_pass": 0}
    pending = []
    for r in rows:
        jid = str(r["id"])
        status, reasons, hits = rule_filter(r, profile)
        ent = index.setdefault(jid, {"first_seen": r.get("first_seen", ts)})
        ent.update({"last_seen": ts, "listing_fp": r.get("listing_fp"), "desc_hash": r["desc_hash"],
                    "client_id": r["client"].get("userId"), "expired_on": r["expired_on"]})
        fp = detail_fp(r)
        if jid in master:
            old = master[jid]
            if old.get("detail_fp") == fp:
                counts["unchanged_evaluated"] += 1
                continue
            why = change_reasons(old, r)
            master[jid].update({k: val for k, val in job_facts(r).items() if k != "detail_fp"})
            if old.get("status") not in CLAUDE_OWNED:
                old.setdefault("change_log", []).append({"at": ts, "changes": why})
                old["detail_fp"] = fp
                continue  # already in Astra/user hands: log only
            if status == "RULE_REJECTED":
                set_status(old, "CLAUDE_REJECTED", "rule", "; ".join(reasons))
                old["detail_fp"] = fp
                counts["rule_rejected"] += 1
                continue
            counts["delta"] += 1
            pending.append((prescore(r, hits), r, hits, why or ["詳細変更"]))
            continue
        if status == "RULE_REJECTED":
            ent.update({"status": "RULE_REJECTED", "reasons": reasons, "rule_fp": fp})
            counts["rule_rejected"] += 1
            continue
        ent.update({"status": "SCOUTED", "rule_fp": fp})
        counts["new_pass"] += 1
        pending.append((prescore(r, hits), r, hits, ["新規"]))
    # backlog: rule-passed jobs not yet evaluated (public-safe, no personal data)
    backlog_path = os.path.join(STATE, "backlog.json")
    backlog = load_json(backlog_path, {})
    for score, r, hits, why in pending:
        if str(r["id"]) not in master:
            backlog[str(r["id"])] = {**{k: r.get(k) for k in BACKLOG_KEYS}, "prescore": score}
    pending.sort(key=lambda x: -x[0])
    chosen = pending[:a.cap]
    chosen_ids = {str(c[1]["id"]) for c in chosen}
    if len(chosen) < a.cap:
        extra = sorted((b for k, b in backlog.items() if k not in chosen_ids and k not in master
                        and (b.get("expired_on") or "") >= date), key=lambda b: -b["prescore"])
        for b in extra[:a.cap - len(chosen)]:
            r = refetch(b)
            if r is None:
                continue
            status, reasons, hits = rule_filter(r, profile)
            if status == "RULE_REJECTED":  # conditions changed since it was scouted
                index.setdefault(str(r["id"]), {}).update({"status": "RULE_REJECTED", "reasons": reasons})
                backlog.pop(str(r["id"]), None)
                continue
            chosen.append((b["prescore"], r, hits, ["バックログ"]))
    for c in chosen:  # keep until merged, so an interrupted run does not lose them
        r = c[1]
        backlog.setdefault(str(r["id"]), {**{k: r.get(k) for k in BACKLOG_KEYS}, "prescore": c[0]})
    backlog = {k: b for k, b in backlog.items() if (b.get("expired_on") or "") >= date and k not in master}
    # prune expired, non-evaluated index entries (closed postings never reappear in search)
    for k in [k for k, e in index.items() if (e.get("expired_on") or "9999") < date and k not in master]:
        del index[k]
    save_json(backlog_path, backlog)
    carried = list(backlog)
    out = []
    for score, r, hits, why in chosen:
        out.append({
            "job_id": r["id"], "url": r["url"], "title": r["title"], "tiers": r["tiers"],
            "change": why, "prescore": score, "profile_hits": hits, "pay": r["pay"],
            "net_est": round(gross_of(r["pay"]) * (1 - FEE_RATE)),
            "expired_on": r["expired_on"], "entry": r.get("entry"),
            "client": r["client"], "client_open_jobs": r.get("client_open_jobs"),
            "ai_policy_rule": r["ai_policy"], "ai_evidence": r.get("ai_evidence", []),
            "requirements_rule": r["requirements"], "risk_rule": r["risk"],
            "needs_experience": r.get("needs_experience"), "desc": r["desc"][:a.desc_chars],
        })
    save_json(os.path.join(ddir, "pending_eval.json"),
              {"date": date, "calibration": calibration(master), "jobs": out})
    # keep the full rows for jobs sent to Claude so merge can build master entries
    with open(os.path.join(ddir, "pending_rows.jsonl"), "w", encoding="utf-8") as fo:
        for _, r, _, _ in chosen:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    # expire / close
    closed = 0
    for jid, job in master.items():
        if job.get("expired_on", "9999") < date and job.get("status") in CLAUDE_OWNED | {"ASTRA_PASS", "ASTRA_REJECT", "NEED_USER", "READY_TO_APPLY"}:
            set_status(job, "CLOSED", "system", "募集期限切れ")
            closed += 1
    save_json(os.path.join(STATE, "index.json"), index)
    v["master"] = master
    vault_save(v)
    run = {"run_at": ts, "date": date, "mode": summ.get("mode"), "listed": summ.get("listed"),
           "processed_details": summ.get("processed"), "new": summ.get("new"),
           "dedupe_skipped": (summ.get("unchanged_skipped") or 0) + counts["unchanged_evaluated"],
           "rule_rejected": counts["rule_rejected"], "delta_reeval": counts["delta"],
           "claude_eval_requested": len(out), "backlog_scouted": len(carried),
           "closed": closed, "errors": summ.get("errors", []),
           "est_ai_usage": {"eval_jobs": len(out),
                            "eval_input_chars": sum(len(x["desc"]) + 600 for x in out)}}
    save_json(os.path.join(ddir, "run.json"), run)
    print(json.dumps(run, ensure_ascii=False))


def calibration(master):
    """Predicted vs actual human minutes by classification (feedback loop)."""
    agg = {}
    for job in master.values():
        act, ev = job.get("actual", {}), job.get("eval", {})
        try:
            am = float(act.get("actual_human_minutes"))
            pm = float(re.findall(r"\d+(?:\.\d+)?", str(ev.get("human_minutes")))[-1])
        except (TypeError, ValueError, IndexError):
            continue
        k = ev.get("classification", "?")
        a = agg.setdefault(k, {"n": 0, "ratio_sum": 0.0})
        a["n"] += 1
        a["ratio_sum"] += am / pm if pm else 0
    return {k: {"n": v["n"], "actual_over_predicted": round(v["ratio_sum"] / v["n"], 2)}
            for k, v in agg.items() if v["n"]}


def cmd_merge(a):
    ddir = os.path.join(ROOT, "data", a.date)
    evals = load_json(a.evals, [])
    rows = {str(json.loads(l)["id"]): json.loads(l)
            for l in open(os.path.join(ddir, "pending_rows.jsonl"), encoding="utf-8")}
    index = load_json(os.path.join(STATE, "index.json"), {})
    v = vault_load()
    master = v["master"]
    queued = rejected = 0
    for e in evals:
        jid = str(e["job_id"])
        r = rows.get(jid)
        job = master.get(jid)
        if job is None:
            if r is None:
                print(f"skip {jid}: not in pending rows", file=sys.stderr)
                continue
            job = master[jid] = job_facts(r)
            job["first_seen"] = r.get("first_seen")
        elif r is not None:
            job.update(job_facts(r))
        job["eval"] = {k: e.get(k) for k in EVAL_FIELDS}
        if e.get("gross_jpy"):  # per-unit reward read from the body overrides the listing budget
            job["gross"] = e["gross_jpy"]
            job["net_est"] = round(e["gross_jpy"] * (1 - FEE_RATE))
        job["eval"]["evaluated_at"] = now_iso()
        job.setdefault("actual", {k: None for k in ACTUAL_FIELDS})
        if job.get("status") not in CLAUDE_OWNED and job.get("status") is not None:
            continue
        if e.get("verdict") in ("候補", "要確認"):
            if job.get("status") != "ASTRA_QUEUE":
                set_status(job, "CLAUDE_CANDIDATE", "claude", e.get("verdict"))
                set_status(job, "ASTRA_QUEUE", "claude")
            queued += 1
        else:
            set_status(job, "CLAUDE_REJECTED", "claude", e.get("reason", ""))
            rejected += 1
        if jid in index:
            index[jid]["status"] = "CLAUDE_EVALUATED"
    save_json(os.path.join(STATE, "index.json"), index)
    backlog_path = os.path.join(STATE, "backlog.json")
    backlog = load_json(backlog_path, {})
    for e in evals:
        backlog.pop(str(e["job_id"]), None)
    save_json(backlog_path, backlog)
    vault_save(v)
    export(master, v.get("meta", {}))
    run_path = os.path.join(ddir, "run.json")
    run = load_json(run_path, {"date": a.date})
    run.update({"claude_evaluated": len(evals), "claude_rejected": rejected,
                "astra_queue_added": queued,
                "astra_queue_total": sum(1 for j in master.values() if j.get("status") == "ASTRA_QUEUE")})
    save_json(run_path, run)
    runs_path = os.path.join(STATE, "runs.jsonl")
    runs = [json.loads(l) for l in open(runs_path, encoding="utf-8")] if os.path.exists(runs_path) else []
    runs = [x for x in runs if x.get("run_at") != run.get("run_at")] + [run]
    with open(runs_path, "w", encoding="utf-8") as fo:
        fo.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in runs)
    print(json.dumps(run, ensure_ascii=False))


def _fmt_list(x):
    return " / ".join(map(str, x)) if isinstance(x, list) else ("" if x is None else str(x))


MASTER_COLS = [
    ("job_id", lambda j: j["job_id"]), ("status", lambda j: j.get("status")),
    ("verdict", lambda j: j.get("eval", {}).get("verdict")),
    ("classification", lambda j: j.get("eval", {}).get("classification")),
    ("title", lambda j: j["title"]), ("url", lambda j: j["url"]),
    ("claude_reason", lambda j: j.get("eval", {}).get("reason")),
    ("gross_jpy", lambda j: j.get("gross")), ("net_est_jpy", lambda j: j.get("net_est")),
    ("expired_on", lambda j: j.get("expired_on")),
    ("ai_condition", lambda j: j.get("eval", {}).get("ai_condition")),
    ("fit", lambda j: j.get("eval", {}).get("fit")),
    ("ai_completion", lambda j: j.get("eval", {}).get("ai_completion")),
    ("human_minutes_est", lambda j: j.get("eval", {}).get("human_minutes")),
    ("hourly_est", lambda j: j.get("eval", {}).get("est_hourly")),
    ("repeatability", lambda j: j.get("eval", {}).get("repeatability")),
    ("client_risk", lambda j: j.get("eval", {}).get("client_risk")),
    ("client", lambda j: j["client"].get("userDisplayName")),
    ("client_score", lambda j: j["client"].get("averageScore")),
    ("client_verified", lambda j: j["client"].get("isIdentityVerified")),
    ("first_seen", lambda j: j.get("first_seen")),
    ("status_updated", lambda j: (j.get("status_history") or [{}])[-1].get("at")),
] + [(k, (lambda k: lambda j: j.get("actual", {}).get(k))(k)) for k in ACTUAL_FIELDS]

QUEUE_COLS = [
    ("job_id", lambda j: j["job_id"]), ("url", lambda j: j["url"]), ("title", lambda j: j["title"]),
    ("classification", lambda j: j["eval"].get("classification")),
    ("gross_jpy", lambda j: j.get("gross")), ("net_est_jpy", lambda j: j.get("net_est")),
    ("pay_detail", lambda j: json.dumps(j.get("pay"), ensure_ascii=False)),
    ("ai_condition", lambda j: j["eval"].get("ai_condition")),
    ("requirements", lambda j: _fmt_list(j["eval"].get("requirements"))),
    ("fit", lambda j: j["eval"].get("fit")),
    ("claude_verdict", lambda j: j["eval"].get("verdict")),
    ("claude_reason", lambda j: j["eval"].get("reason")),
    ("ai_completion", lambda j: j["eval"].get("ai_completion")),
    ("human_minutes_est", lambda j: j["eval"].get("human_minutes")),
    ("hourly_est", lambda j: j["eval"].get("est_hourly")),
    ("repeatability", lambda j: j["eval"].get("repeatability")),
    ("client_risk", lambda j: j["eval"].get("client_risk")),
    ("user_questions", lambda j: _fmt_list(j["eval"].get("user_questions"))),
    ("ai_steps", lambda j: _fmt_list(j["eval"].get("ai_steps"))),
    ("human_steps", lambda j: _fmt_list(j["eval"].get("human_steps"))),
    ("profile_link", lambda j: j["eval"].get("profile_link")),
    ("source_check", lambda j: f"期限:{j.get('expired_on')} / 発注者:{j['client'].get('userDisplayName')}"
                               f"(評価{j['client'].get('averageScore')}・実績{j['client'].get('jobOfferAchievementCount')}"
                               f"・本人確認{'済' if j['client'].get('isIdentityVerified') else '未'}) / "
                               f"AI記載:{_fmt_list(j.get('ai_evidence'))[:100]}"),
    ("first_seen", lambda j: j.get("first_seen")),
]


def _write_csv(path, cols, jobs):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in cols])
        for j in jobs:
            w.writerow(["" if (v := fn(j)) is None else v for _, fn in cols])


def export(master, meta):
    os.makedirs(OUT, exist_ok=True)
    order = {s: i for i, s in enumerate(["ASTRA_QUEUE", "NEED_USER", "ASTRA_PASS", "READY_TO_APPLY",
                                          "APPLIED", "ACCEPTED", "IN_PROGRESS", "READY_FOR_QA",
                                          "READY_TO_DELIVER", "DELIVERED", "PAID"])}
    jobs = sorted(master.values(), key=lambda j: (order.get(j.get("status"), 99), -(j.get("gross") or 0)))
    active = [j for j in jobs if j.get("status") not in ("CLOSED",)]
    _write_csv(os.path.join(OUT, "job_master.csv"), MASTER_COLS, active)
    queue = [j for j in jobs if j.get("status") == "ASTRA_QUEUE"]
    _write_csv(os.path.join(OUT, "astra_queue.csv"), QUEUE_COLS, queue)
    save_json(os.path.join(OUT, "astra_queue.json"),
              {"generated_at": now_iso(), "count": len(queue),
               "jobs": [{c: fn(j) for c, fn in QUEUE_COLS} for j in queue]})
    save_json(os.path.join(OUT, "sync_manifest.json"),
              {"generated_at": now_iso(), "drive": meta.get("drive", {}),
               "files": {"job_master": "job_master.csv", "astra_queue": "astra_queue.csv"},
               "counts": {"master_active": len(active), "astra_queue": len(queue)}})
    print(f"exported: master={len(active)} queue={len(queue)}")


def cmd_export(a):
    v = vault_load()
    export(v["master"], v.get("meta", {}))


UPDATE_COLS = ["job_id", "new_status"] + ACTUAL_FIELDS + ["note", "updated_by"]


def cmd_apply_updates(a):
    v = vault_load()
    master, meta = v["master"], v.setdefault("meta", {})
    applied = set(meta.get("applied_update_rows", []))
    text = open(a.csv, encoding="utf-8-sig").read()
    n_ok, errs = 0, []
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip(): (val or "").strip() for k, val in row.items() if k}
        if not row.get("job_id"):
            continue
        sig = hashlib.md5(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        if sig in applied:
            continue
        job = master.get(row["job_id"])
        if job is None:
            errs.append(f"unknown job_id {row['job_id']}")
            continue
        st = row.get("new_status", "").upper()
        try:
            if st:
                set_status(job, st, row.get("updated_by") or "sheet", row.get("note", ""))
        except ValueError as e:
            errs.append(str(e))
            continue
        for k in ACTUAL_FIELDS:
            if row.get(k):
                job.setdefault("actual", {})[k] = row[k]
        applied.add(sig)
        n_ok += 1
    meta["applied_update_rows"] = sorted(applied)
    vault_save(v)
    export(master, meta)
    print(json.dumps({"applied": n_ok, "errors": errs}, ensure_ascii=False))


def cmd_set_meta(a):
    v = vault_load()
    meta = v.setdefault("meta", {})
    meta.setdefault("drive", {})[a.key] = a.value
    vault_save(v)
    print(json.dumps(meta["drive"], ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init-vault"); p.add_argument("--profile", required=True); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init_vault)
    p = sub.add_parser("show-profile"); p.set_defaults(fn=cmd_show_profile)
    p = sub.add_parser("prepare"); p.add_argument("--date", default=today())
    p.add_argument("--cap", type=int, default=40); p.add_argument("--desc-chars", type=int, default=1800)
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("merge"); p.add_argument("--date", default=today()); p.add_argument("--evals", required=True)
    p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("apply-updates"); p.add_argument("--csv", required=True); p.set_defaults(fn=cmd_apply_updates)
    p = sub.add_parser("export"); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("set-drive"); p.add_argument("key"); p.add_argument("value"); p.set_defaults(fn=cmd_set_meta)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
