"""Claude Worker: accepted job -> internal work record -> Astra QA -> client deliverable (no submission).

Two artifacts per job, never mixed:
  internal record  : vault master[<id>].worker (+ a Drive doc for Astra). Instructions, angle, drafts,
                     self-QA, revisions, Astra QA. Never handed to the client.
  client deliverable: created only after Astra QA PASS of the current final; holds the deliverable only
                     (title + body). Its direct URL is kept in worker.delivery and exported to Job Master.
Claude never submits, messages or delivers anything to CrowdWorks / Chatwork.
"""
import datetime as dt
import hashlib
import json
import re
import sys

import pipeline as P

WORKER_PHASE = {"ACCEPTED", "IN_PROGRESS", "READY_FOR_QA"}
RECORD_KEYS = ["client_instructions", "angle", "profile_facts_used", "draft_v1", "self_qa_final", "final"]
QA_PASS = {"PASS", "合格", "OK"}
QA_FIX = {"FIX", "REVISE", "修正", "要修正", "差し戻し"}
QA_HOLD = {"HOLD", "保留", "停止", "NEED_USER", "要確認"}
ESCROW_RE = re.compile(r"仮払い(?:は)?(?:完了|確認済)")
# Words that must never reach a client file (title or content)
INTERNAL_RE = re.compile(r"Claude|Astra|AIチーム|(?<![A-Za-z])QA(?![A-Za-z])|Worker|プロンプト|修正履歴|内部メモ|"
                         r"ステータス|Status Updates|Job Master|Vault|job_id|ASTRA_|READY_|文字数|Scout|"
                         r"(?<!\d)1\d{7}(?!\d)", re.I)
TITLE_BAN_RE = re.compile(r"CW\s|Worker|ASTRA|(?<![A-Za-z])QA(?![A-Za-z])|PASS|PENDING|Final|Draft|(?<!\d)\d{8}(?!\d)", re.I)
FORMATS = {"gdoc": ("Googleドキュメント", r"https://docs\.google\.com/document/d/([\w-]{20,})"),
           "gsheet": ("Googleスプレッドシート", r"https://docs\.google\.com/spreadsheets/d/([\w-]{20,})"),
           "docx": ("Word", r"https://(?:drive|docs)\.google\.com/(?:file|document)/d/([\w-]{20,})"),
           "xlsx": ("Excel", r"https://(?:drive|docs)\.google\.com/(?:file|spreadsheets)/d/([\w-]{20,})")}
FORMAT_WORDS = [("gdoc", r"Google\s*ドキュメント|Googleドキュメント|Google\s*Docs?"), ("docx", r"Word|ワード"),
                ("gsheet", r"スプレッドシート|Google\s*Sheets?"), ("xlsx", r"Excel|エクセル")]
PREFERENCE = ["gdoc", "gsheet", "docx", "xlsx"]  # no cost, no extra step for the user, easy for the client


def sha(text):
    return hashlib.sha256(_norm(text).encode()).hexdigest()[:16]


def _norm(t):
    return re.sub(r"\s+", "", t or "")


def allowed_formats(instructions):
    text = " ".join(str(v) for v in (instructions or {}).values())
    return [k for k, rx in FORMAT_WORDS if re.search(rx, text, re.I)]


def choose_format(instructions):
    ok = allowed_formats(instructions)
    return next((f for f in PREFERENCE if f in ok), None)


def apply_astra_row(job, row):
    """Worker-phase Astra row (called by apply-updates). Returns a note, or None if not a worker row.
    Astra never sets READY_TO_DELIVER directly: that needs a verified client deliverable (worker-deliver)."""
    if job.get("status") not in WORKER_PHASE:
        return None
    text = " ".join(row.get(k, "") for k in ("astra_reason", "note", "next_action"))
    verdict = row.get("astra_verdict", "").strip().upper()
    target = row.get("new_status", "").upper()
    if not (verdict in QA_PASS | QA_FIX | QA_HOLD or ESCROW_RE.search(text)
            or target in ("READY_TO_DELIVER", "IN_PROGRESS", "READY_FOR_QA", "HOLD")):
        return None  # e.g. DELIVERED / PAID / actual results: the normal import handles them
    w = job.setdefault("worker", {})
    notes = []
    if ESCROW_RE.search(text) or row.get("new_status", "").upper() == "IN_PROGRESS":
        if not w.get("escrow_confirmed"):
            w["escrow_confirmed"] = {"at": row.get("updated_at"), "by": row.get("updated_by")}
            notes.append("仮払い確認")
    if verdict in QA_PASS or target == "READY_TO_DELIVER":
        if job["status"] != "READY_FOR_QA" or not w.get("final"):
            notes.append("Astra QA PASS（Worker最終稿が未提出のため保留）")
        else:
            w["astra_qa"] = {"result": "PASS", "final_sha": w.get("final_sha") or sha(w["final"]),
                             "at": row.get("updated_at"), "reason": row.get("astra_reason"), "note": row.get("note")}
            w["status"] = "ASTRA_QA_PASS"
            notes.append("Astra QA PASS")
    elif verdict in QA_FIX:
        w["astra_qa"] = {"result": "FIX", "at": row.get("updated_at"), "reason": row.get("astra_reason")}
        w["status"] = "REVISE"
        P.set_status(job, "IN_PROGRESS", row.get("updated_by") or "Astra", "Astra QA FIX：" + (row.get("astra_reason") or ""))
        notes.append("Astra QA FIX → Worker修正")
    elif verdict in QA_HOLD or target == "HOLD":
        w["astra_qa"] = {"result": "HOLD", "at": row.get("updated_at"), "reason": row.get("astra_reason")}
        w["status"] = "HOLD"
        notes.append("Astra QA HOLD → 停止")
    elif target in ("IN_PROGRESS", "READY_FOR_QA") and target != job["status"]:
        P.set_status(job, target, row.get("updated_by") or "Astra", row.get("astra_reason") or row.get("note", ""))
    return "、".join(notes) or "Worker記録のみ"


def cmd_worker_save(a):
    """Store the internal work record after Claude's self-QA; stop at READY_FOR_QA / ASTRA_QA_PENDING."""
    rec = json.load(open(a.record, encoding="utf-8"))
    v = P.vault_load()
    job = v["master"].get(a.id)
    errs = []
    if not job:
        sys.exit(f"unknown job_id {a.id}")
    if job.get("status") not in WORKER_PHASE:
        errs.append(f"status={job.get('status')}（受注後の制作中のみ）")
    missing = [k for k in RECORD_KEYS if not rec.get(k)]
    if missing:
        errs.append("内部記録の不足：" + ", ".join(missing))
    fixes = [k for k, x in (rec.get("self_qa_final") or {}).items() if x != "PASS"]
    if fixes:
        errs.append("自己QAにFIXが残っている：" + ", ".join(fixes))
    w = job.get("worker") or {}
    if not (w.get("escrow_confirmed") or rec.get("escrow_confirmed")):
        errs.append("仮払い未確認")
    if errs:
        print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))
        sys.exit(1)
    new_sha = sha(rec["final"])
    if w.get("astra_qa", {}).get("final_sha") not in (None, new_sha):
        w.pop("astra_qa")  # a changed final needs a new Astra QA
    w.update(rec, job_id=a.id, final_sha=new_sha, status="ASTRA_QA_PENDING",
             final_chars_excl_newlines=len(rec["final"].replace("\n", "")))
    w.setdefault("history", []).append({"at": P.now_iso(), "event": "worker_save", "final_sha": new_sha})
    job["worker"] = w
    P.set_status(job, "IN_PROGRESS", "claude-worker", "Worker制作")
    P.set_status(job, "READY_FOR_QA", "claude-worker", "Worker自己QA済み → Astra QA待ち（ASTRA_QA_PENDING）")
    P.vault_save(v)
    P.export(v["master"], v.get("meta", {}))
    print(json.dumps({"ok": True, "status": job["status"], "worker_status": w["status"], "final_sha": new_sha},
                     ensure_ascii=False))


def deliver_errors(job, title, content, url, ftype, file_id):
    w = job.get("worker") or {}
    errs = []
    if job.get("status") != "READY_FOR_QA":
        errs.append(f"status={job.get('status')}（READY_FOR_QAのみ）")
    if not any(h.get("status") == "ACCEPTED" for h in job.get("status_history", [])):
        errs.append("受注記録がない")
    if not w.get("escrow_confirmed"):
        errs.append("仮払い未確認")
    if not w.get("self_qa_final") or any(x != "PASS" for x in w["self_qa_final"].values()):
        errs.append("Worker自己QA未完了")
    qa = w.get("astra_qa") or {}
    if qa.get("result") != "PASS":
        errs.append("Astra QA PASSがない")
    elif qa.get("final_sha") != sha(w.get("final", "")):
        errs.append("Astra QA PASS後にWorker最終稿が変わっている")
    allowed = allowed_formats(w.get("client_instructions"))
    if ftype not in FORMATS:
        errs.append(f"未対応の形式 {ftype}")
    elif ftype not in allowed:
        errs.append(f"クライアント指定形式外（{ftype}／指定：{allowed or '不明'}）")
    if TITLE_BAN_RE.search(title or ""):
        errs.append(f"ファイル名に内部表現：{title}")
    # the file must hold exactly the title and the Astra-PASS final, nothing else
    body = content or ""
    if _norm(body).startswith(_norm(title)):
        body = _norm(body)[len(_norm(title)):]
    if _norm(body) != _norm(w.get("final")):
        errs.append("納品物の本文がAstra QA PASS版と一致しない")
    hit = INTERNAL_RE.findall(content or "") + INTERNAL_RE.findall(title or "")
    if hit:
        errs.append("内部情報の混入：" + ", ".join(sorted(set(hit))))
    m = re.match(FORMATS.get(ftype, ("", r"$^"))[1], url or "")
    if not m:
        errs.append("納品物そのものを開く直接URLではない（フォルダ・検索URL不可）")
    elif m.group(1) != file_id:
        errs.append("URLのファイルIDが検証したファイルと一致しない")
    if file_id and file_id == w.get("record_doc"):
        errs.append("内部管理用ドキュメントのURLである")
    return errs


def cmd_worker_deliver(a):
    """Register the client deliverable (already created in Drive and re-downloaded by the caller)
    and move the job to READY_TO_DELIVER only if every condition holds."""
    v = P.vault_load()
    job = v["master"].get(a.id)
    if not job:
        sys.exit(f"unknown job_id {a.id}")
    content = open(a.exported, encoding="utf-8-sig").read()
    errs = deliver_errors(job, a.title, content, a.url, a.type, a.file_id)
    if errs:
        print(json.dumps({"ok": False, "errors": errs}, ensure_ascii=False))
        sys.exit(1)
    w = job["worker"]
    w["delivery"] = {"title": a.title, "url": a.url, "file_id": a.file_id, "type": a.type,
                     "type_label": FORMATS[a.type][0], "final_sha": sha(w["final"]),
                     "body_chars": len(w["final"].replace("\n", "")), "verified_at": P.now_iso(),
                     "deadline": a.deadline or w.get("delivery_deadline"), "deadline_basis": a.deadline_basis}
    w["status"] = "READY_TO_DELIVER"
    w.setdefault("history", []).append({"at": P.now_iso(), "event": "deliverable_verified", "file_id": a.file_id})
    P.set_status(job, "READY_TO_DELIVER", "claude-worker", "納品物検証済み（Astra QA PASS版と一致）→ 本人最終確認待ち")
    P.vault_save(v)
    P.export(v["master"], v.get("meta", {}))
    print(notice(job))


def notice(job):
    w, d = job["worker"], job["worker"]["delivery"]
    reward = f"{job.get('gross')}円（税込。手数料控除後の見込み {job.get('net_est')}円）"
    deadline = d.get("deadline") or "未確定"
    if d.get("deadline_basis"):
        deadline += f"（{d['deadline_basis']}）"
    return "\n".join([
        "【納品準備完了】", "",
        f"案件名：{d['title']}", f"job_id：{job['job_id']}", f"報酬：{reward}", f"納品期限：{deadline}", "",
        "納品物：", d["url"], "",
        f"納品形式：{d['type_label']}", f"Astra QA：{w['astra_qa']['result']}", f"ステータス：{job['status']}", "",
        "本人確認：", "「上記リンクを開いて最終確認 → 問題なければCrowdWorksで納品」"])


def cmd_worker_notice(a):
    job = P.vault_load()["master"][a.id]
    if job.get("status") != "READY_TO_DELIVER" or not (job.get("worker") or {}).get("delivery", {}).get("url"):
        sys.exit("READY_TO_DELIVERではない、または納品物URLがない")
    print(notice(job))


def delivery_deadline(accepted_at, days):
    d = dt.date.fromisoformat(accepted_at[:10]) + dt.timedelta(days=days)
    return d.isoformat()


NEXT = {"ASTRA_QA_PASS": "納品用成果物を生成 → worker-deliver", "REVISE": "Astra指摘を反映して修正 → worker-save",
        "ASTRA_QA_PENDING": "Astra QA待ち（Claudeは待機）", "HOLD": "停止（Astra/本人の指示待ち）",
        "READY_TO_DELIVER": "本人の最終確認・CrowdWorks納品待ち（worker-noticeで通知文）"}


def cmd_worker_status(a):
    """Accepted jobs and the next Worker step (for the routines and for Astra)."""
    rows = []
    for jid, j in P.vault_load()["master"].items():
        if j.get("status") not in WORKER_PHASE | {"READY_TO_DELIVER"}:
            continue
        w = j.get("worker") or {}
        st = w.get("status") or ("仮払い確認待ち" if not w.get("escrow_confirmed") else "制作待ち")
        rows.append({"job_id": jid, "title": j.get("title"), "status": j["status"], "worker_status": st,
                     "next": NEXT.get(st, "仮払い確認 → クライアント指示確認 → 制作" if st == "仮払い確認待ち"
                                      else "クライアント指示確認 → 制作 → 自己QA → worker-save"),
                     "delivery_artifact_url": (w.get("delivery") or {}).get("url")})
    print(json.dumps(rows, ensure_ascii=False, indent=1))
