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
# Daily flow (RUNBOOK 5.7): every job Claude sends to Astra (ASTRA_QA_PENDING) is drafted *before* Astra's
# second QA. Such a draft is only a draft for Astra to review: it never becomes READY_TO_APPLY here and
# nothing is read back from Astra. `pre_draft_due` (set by merge when a job newly enters the Astra Queue)
# scopes this to today's queue, so jobs queued before this flow existed are never re-evaluated.
PRE_STAGE = "ASTRA_QA_PENDING"
PRE_FINAL_OK, PRE_FINAL_FLAGGED = "CLAUDE_QA_PASSED", "CLAUDE_QA_FLAGGED"
# Statuses that keep a draft visible, so actual results stay traceable through to payment.
QUEUE_VISIBLE = {"ASTRA_PASS", "READY_TO_APPLY", "APPLIED", "SKIPPED", "ACCEPTED", "NOT_SELECTED", "WITHDRAWN",
                 "IN_PROGRESS", "READY_FOR_QA", "READY_TO_DELIVER", "DELIVERED", "PAID"}
CONFIRM_PENALTY_MIN = 5  # user time for answering a confirmation question
# Asking the user is worth it only when the job pays for the question: below this net JPY per human
# minute (review + one confirmation), a draft that waits only on personal / preference questions is
# not sent to the user but flagged to Astra as a REJECT candidate (RUNBOOK 5.7.2).
CONFIRM_WORTH_NET_PER_MIN = 100
# What really needs the user: contract / payment, external sending, unregistered own experience,
# confidentiality / conflict of interest, final delivery, the name to sign with.
ESSENTIAL_CONFIRM_RE = re.compile(r"契約|支払|報酬|金額|振込|口座|請求|送信|外部|連絡先|LINE|Chatwork|守秘|秘密|NDA|"
                                  r"利益相反|勤務先|本業|競業|納品|最終確認|経験|実績|資格|スキル")
# Typed by the user on the CrowdWorks screen (profile name, nickname ...): never a reason to hold or ask.
DIRECT_ENTRY_RE = re.compile(r"お名前|氏名|本名|ニックネーム|ユーザー名|表示名|署名")
ESSENTIAL_NOT_NAME_RE = ESSENTIAL_CONFIRM_RE
# Personal details the posting asks for only to colour a low-priced piece (never worth a round trip)
PERSONAL_CONFIRM_RE = re.compile(r"好き|好み|嗜好|趣味|おすすめ|お気に入り|将来|目標|夢|なりたい|理想|きっかけ|感想|"
                                 r"思い出|エピソード|家族|私生活|生活|休日|性格|悩み|価値観|住環境|一人暮らし|動機|理由|年齢|年代")
# Wording Astra should look at: claims of track record / AI work the profile does not support.
CLAIM_RE = re.compile(r"実績(?!作り|づくり)|受注|納品経験|ライター(?:経験|として)|執筆経験|SEO|WordPress|"
                      r"AI(?:導入|コンサル|案件|開発)|自動化(?:システム|ツール)|Scout|スカウト|構築|運用して")


_CLIENTS = {}  # Client Master of the current command (set by _use_clients)


def _use_clients(v):
    import client_master
    _CLIENTS.clear()
    _CLIENTS.update(clients=client_master.refresh(v), master=v["master"])


def _opening_errors(job, draft):
    """The draft's opening must match what we really have with this client (Client Master)."""
    import client_master
    if not _CLIENTS:
        return []
    lvl = client_master.level(_CLIENTS["clients"], _CLIENTS["master"], job)
    return client_master.opening_errors(draft, lvl)


def reusable_facts(profile):
    """Facts the user has confirmed once and that are reused from then on (never asked again).
    Each has `question_re` (the questions it answers) and optionally `job_re` (only for such jobs)."""
    return [(i, f) for i, f in enumerate(profile.get("confirmed_facts", [])) if f.get("reuse") and f.get("question_re")]


def facts_answering(profile, text, job_text=""):
    """Confirmed facts that answer `text` (a question or an item the draft wants to ask the user)."""
    return [(i, f) for i, f in reusable_facts(profile)
            if re.search(f["question_re"], text or "") and (not f.get("job_re") or re.search(f["job_re"], job_text or ""))]


def confirm_cost(app, unverified):
    """REJECT_CANDIDATE when the only open items are personal / preference details and the job does not
    pay for asking; None otherwise (essential items always go to the user)."""
    if app.get("user_confirmation_required") != "yes" or not unverified or app.get("actual_net") is None:
        return None
    if any(ESSENTIAL_CONFIRM_RE.search(u) or not PERSONAL_CONFIRM_RE.search(u) for u in unverified):
        return None
    worth = app["actual_net"] / (float(app.get("review_minutes_est") or 3) + CONFIRM_PENALTY_MIN)
    return "REJECT_CANDIDATE" if worth < CONFIRM_WORTH_NET_PER_MIN else None


def designated(job):
    """A job the user picked by hand: Claude's evaluation + the user's own decision stand in for
    the Astra application QA (Astra's verdict is never faked; job["astra"] stays empty)."""
    return bool(job.get("designation")) and job.get("status") == "CLAUDE_CANDIDATE"


def pre_stage(job):
    return job.get("status") == PRE_STAGE and bool(job.get("pre_draft_due") or job.get("application"))


def eligible(job, statuses):
    return job.get("status") in statuses or designated(job)


def drafting_target(job):
    """Jobs an application draft may be attached to: ASTRA_PASS (legacy), designated, or today's Astra Queue."""
    return eligible(job, TARGET) or (job.get("status") == PRE_STAGE and bool(job.get("pre_draft_due")))


def recheck_target(job):
    return eligible(job, RECHECK) or pre_stage(job)


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
    _use_clients(v)
    master = v["master"]
    sdir = os.path.join(P.ROOT, "data", a.date, "app_source")
    os.makedirs(sdir, exist_ok=True)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()] if a.ids else \
        [j for j, job in master.items() if recheck_target(job)]
    out = []
    for jid in ids:
        job = master.get(jid)
        if not job or not recheck_target(job):
            out.append({"job_id": jid, "skip": f"status={job and job.get('status')}（Astra Queue当日分/ASTRA_PASS/READY_TO_APPLY/本人指定のみ対象）"})
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
        rc["key_lines"] = _key_lines(src["desc"])
        changes, notes = classify_changes(prev, rc, job, src["desc"])
        rc["notes"] = notes
        rc["changes"] = changes
        job["reward_check"] = rc
        if job.get("application"):
            _apply_readiness(job, a.date)
        out.append({"job_id": jid, "title": job["title"][:40], "status": job.get("status"),
                    **{k: rc[k] for k in rc if k not in ("checked_at", "body_reward_mentions")}})
    P.vault_save(v)
    print(json.dumps(out, ensure_ascii=False, indent=1))


# ---- re-check: material condition changes vs. mere extraction improvements -----------------
AI_RANK = {"A": 0, "B": 1, "C": 2, "D": 3}  # higher = more restrictive
RISK_LINE = re.compile(r"LINE|ライン|Chatwork|チャットワーク|Slack|Zoom|外部|直接(?:連絡|取引|やり取り)|メールアドレス|電話|"
                       r"応募資格|必須|条件|限定|年齢|性別|在住|本人確認|身分証|顔出し|納期|期限|締切|〆|日以内|"
                       r"文字数|字以上|字程度|AI|ＡＩ|生成|ChatGPT|報酬|円|単価|テスト|トライアル|無償|研修|振込|手数料|秘密|NDA")


def _key_lines(desc):
    """Lines that carry conditions (reward, deadline, eligibility, AI, contact); kept to diff re-checks."""
    return sorted({re.sub(r"\s+", "", ln)[:160] for ln in desc.split("\n") if RISK_LINE.search(ln) and ln.strip()})


def _amounts(texts):
    return sorted({_yen(x) for t in texts or [] for x in re.findall(r"([\d,]{2,})\s*円", t) if _yen(x) > 0})


def _known_reward(job):
    """Tax-included reward already established for the job (draft evidence first, else Claude's evaluation)."""
    return (job.get("application") or {}).get("actual_reward") or job.get("gross")


def classify_changes(prev, rc, job, desc):
    """Split re-check differences into material changes (block READY) and notes (no effect).

    Material: reward decrease, closed, slots filled, deadline brought forward / passed, AI terms stricter
    than evaluated, reward evidence gone from the body, new condition lines (contact/eligibility/AI/
    deadline/reward wording) in the body.  A field that was missing before and now matches what is
    already known (e.g. header "契約金額（目安）440円" for a 400円+tax job) is only a note."""
    changes, notes = [], []
    known = _known_reward(job)
    new_h, old_h = (rc.get("header_reward") or {}).get("min"), (prev.get("header_reward") or {}).get("min")
    if new_h is not None:
        if known and new_h < known and round(new_h * 1.1) < known:  # checked every time, not only once
            src_label = "応募文の実報酬" if job.get("application") else "評価時の想定報酬（Astra QA時の前提）"
            changes.append(f"実報酬が{src_label}{known:g}円を下回る（原文{new_h}円）→ 再QA")
        elif old_h is not None and new_h < old_h:
            changes.append(f"報酬減額（見出し）{old_h}円 → {new_h}円")
        elif old_h is None and known and new_h > round(known * 1.1) + 1:
            notes.append(f"見出しの目安額{new_h}円を新たに取得（本文の単価{known:g}円を採用、減額ではない）")
        elif old_h is None and not known:  # per-unit reward UNKNOWN: the listing budget is not the reward
            notes.append(f"見出しの目安額{new_h}円を取得（本文の単価は不明のまま。報酬として採用しない）")
        elif old_h is None:
            notes.append(f"見出し報酬を新たに取得 {new_h}円（既知の実報酬{known:g}円と整合）")
        elif new_h != old_h:
            notes.append(f"見出し報酬 {old_h}円 → {new_h}円（増額）")
    elif old_h is not None:
        notes.append("見出し報酬が取得できず（本文の報酬で判断）")
    ev = (job.get("application") or {}).get("reward_evidence")
    if ev and not ev.startswith("UNKNOWN") and _norm(ev) not in _norm(desc):
        changes.append("応募文の根拠にした報酬記載が本文から消えた")
    old_amt, new_amt = _amounts(prev.get("body_reward_mentions")), _amounts(rc.get("body_reward_mentions"))
    if prev.get("body_reward_mentions") is not None and old_amt and new_amt and max(new_amt) < max(old_amt):
        changes.append(f"本文の報酬額が減少 {max(old_amt)}円 → {max(new_amt)}円")
    if rc.get("closed"):
        changes.append("募集終了")
    if rc.get("capacity") and (rc.get("contracted") or 0) >= rc["capacity"]:
        changes.append(f"募集枠充足 {rc['contracted']}/{rc['capacity']}")
    if rc.get("deadline"):
        if rc["deadline"] < P.today():
            changes.append(f"応募期限切れ {rc['deadline']}")
        elif prev.get("deadline") and rc["deadline"] < prev["deadline"]:
            changes.append(f"応募期限の前倒し {prev['deadline']} → {rc['deadline']}")
        elif prev.get("deadline") and rc["deadline"] != prev["deadline"]:
            notes.append(f"応募期限の延長 {prev['deadline']} → {rc['deadline']}")
    ev_ai = (job.get("eval") or {}).get("ai_condition")
    # the rule verdict at evaluation time is the baseline for the rule verdict now: Claude reading
    # "AI歓迎" where the rule said C is not a change in the posting
    rule_then = AI_RANK.get(job.get("ai_policy_rule"), AI_RANK.get(ev_ai, 2))
    if AI_RANK.get(rc.get("ai_policy"), 2) > max(AI_RANK.get(ev_ai, 2), rule_then):
        changes.append(f"AI条件が厳しくなった 評価時{ev_ai} → 原文判定{rc.get('ai_policy')}")
    elif rc.get("ai_policy") != ev_ai:
        notes.append(f"AI条件の判定差 評価時{ev_ai} → 原文判定{rc.get('ai_policy')}（緩和方向）")
    if prev.get("desc_hash") and prev["desc_hash"] != rc["desc_hash"]:
        if prev.get("key_lines") is None:
            changes.append("本文が変更された（比較用の条件行が前回ないため要確認）")
        else:
            added = [l for l in rc["key_lines"] if l not in set(prev["key_lines"])]
            if added:
                changes.append("本文の条件行が変更: " + " / ".join(x[:60] for x in added[:3]))
            else:
                notes.append("本文の軽微な変更（条件行の追加なし）")
    # relative changes are only visible once (the next check has a new baseline): keep them until
    # Astra decides again (apply-updates clears recheck_flags on a new Astra verdict)
    sticky = [c for c in changes if re.search(r"減額|減少|前倒し|条件行|本文が変更|消えた|AI条件", c)]
    flags = job.setdefault("recheck_flags", [])
    flags += [c for c in sticky if c not in flags]
    changes += [c for c in flags if c not in changes]
    return changes, notes


def _apply_readiness(job, date):
    """After a re-check: ready drafts go READY_TO_APPLY, material changes pull READY back to hold.
    Never touches APPLIED or later, SKIPPED, or drafts that need the user."""
    app = job["application"]
    if pre_stage(job):  # draft for Astra's QA: flagged for Astra, never READY_TO_APPLY
        hold = _hold_reasons(job, date)
        app["final_qa_status"] = PRE_FINAL_FLAGGED if hold else PRE_FINAL_OK
        app["next_action"] = "Astra二次QA（応募文・設問回答を含む）" + ("。確認事項：" + " / ".join(hold) if hold else "")
        return
    if not eligible(job, ("ASTRA_PASS", "READY_TO_APPLY")):
        return
    hold = _hold_reasons(job, date)
    back = "CLAUDE_CANDIDATE" if job.get("designation") else "ASTRA_PASS"
    if hold:
        app["final_qa_status"], app["next_action"] = "HOLD", "応募準備保留：" + " / ".join(hold)
        if job["status"] == "READY_TO_APPLY":
            P.set_status(job, back, "claude", "再確認で条件変更を検知: " + " / ".join(hold)[:200])
    elif job["status"] in ("ASTRA_PASS", "CLAUDE_CANDIDATE"):
        app["final_qa_status"], app["next_action"] = "CLAUDE_CHECKED", "本人が応募 → Astraへ報告"
        P.set_status(job, "READY_TO_APPLY", "claude",
                     "応募準備完了（原文再確認 %s）" % (job.get("reward_check") or {}).get("checked_at"))


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


COVER_RE = re.compile(r"はじめまして|初めまして|こんにちは|ご担当者|応募|担当させて|書かせて|執筆を希望|よろしくお願い")


def _is_cover_letter(text):
    return bool(COVER_RE.search(text or ""))


AI_RE = re.compile(r"AI|ＡＩ|人工知能|ChatGPT|Claude|Gemini|生成系?ツール", re.I)


def _validate(d, job, src, profile):
    errs = []
    if not drafting_target(job):
        errs.append(f"status={job.get('status')}（当日のAstra Queue・ASTRA_PASS・本人指定のみ）")
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
    if d.get("actual_reward") is None:
        # per-unit reward not stated: recorded as UNKNOWN, never filled in from the listing budget
        if not ev.startswith("UNKNOWN"):
            errs.append("actual_rewardがnullならreward_evidenceは「UNKNOWN…」")
        if _amounts([src["desc"]]):
            errs.append("本文に金額の記載があるのに報酬をUNKNOWNにしている")
    elif not ev or _norm(ev) not in body:
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
    # The draft is the message sent when applying, not the article itself
    if not _is_cover_letter(d.get("application_draft", "")):
        errs.append("application_draftが応募メッセージになっていない（記事本文などは不可）")
    # Do not bring up AI use in the cover text; answer it only where the posting asks (answers are exempt)
    # quoting the theme (e.g. 「AIの発展」 from the job title) is not a statement about AI use
    title = job.get("title", "")
    draft = re.sub(r"「([^」]*)」", lambda mm: "" if mm.group(1) and mm.group(1) in title else mm.group(0),
                   d.get("application_draft", ""))
    if AI_RE.search(draft) and not any(AI_RE.search(q) for q in qs):
        errs.append("応募文でAI利用に自分から言及している（設問で聞かれた場合のみ回答欄で答える）")
    if d.get("unverified_facts") and d.get("user_confirmation_required") != "yes":
        errs.append("unverified_factsがあるのにuser_confirmation_required≠yes")
    # a fact the user has already confirmed is reused, never asked again
    job_text = " ".join([job.get("title", ""), src.get("desc", "")])
    asks = list(d.get("unverified_facts", [])) + [q for q, an in zip(qs, ans) if "【本人記入" in (an or "")]
    for u in asks:
        for i, f in facts_answering(profile, u, job_text):
            errs.append(f"本人確認済みの事実で答えられる項目を再確認している: {u[:30]} → confirmed_facts[{i}].fact（{f['fact'][:30]}）")
    # what the user types on the CrowdWorks screen himself (name etc.) is no reason to hold the draft
    for u in asks:
        if DIRECT_ENTRY_RE.search(u) and not ESSENTIAL_NOT_NAME_RE.search(u):
            errs.append(f"CrowdWorks画面で本人が直接入力する情報は確認事項にしない: {u[:30]}")
    errs += _opening_errors(job, d.get("application_draft", ""))  # Client Master: relationship QA
    return errs


def _hold_reasons(job, date):
    """Why a validated draft is not READY_TO_APPLY yet (empty = ready)."""
    app, rc = job["application"], job.get("reward_check") or {}
    why = []
    if not str(rc.get("checked_at", "")).startswith(date):
        why.append("本日の原文再確認なし（app-check未実行）")
    if rc.get("changes"):
        why.append("原文の変化：" + "、".join(rc["changes"]))
    if rc.get("closed"):
        why.append("募集終了")
    if not _is_cover_letter(app.get("application_draft", "")):
        why.append("応募文が応募メッセージになっていない（記事本文など）")
    if not app.get("application_draft", "").strip() or "【本人記入" in app["application_draft"] + \
            "".join(app.get("application_answers", [])):
        why.append("応募文・回答が未完成")
    if app["user_confirmation_required"] == "yes":
        why.append("本人確認が必要な項目あり")
    if app.get("confirm_cost") == "REJECT_CANDIDATE":
        why.append("Astra REJECT候補：確認コストが報酬に見合わない（私的・嗜好の確認のみ。本人確認は求めない）")
    if app["claim_flags"]:
        why.append("実績の表現を確認：" + "、".join(app["claim_flags"]))
    why += _opening_errors(job, app.get("application_draft", ""))
    if not app["conflict_risk"].startswith("低"):
        why.append("利益相反リスク：" + (app["conflict_risk"][:40] or "未記載"))
    return why


def cmd_app_merge(a):
    v = P.vault_load()
    _use_clients(v)
    master, profile = v["master"], v.get("profile", {})
    drafts = P.load_json(a.drafts, [])
    sdir = os.path.join(P.ROOT, "data", a.date, "app_source")
    ok, failed, skipped = [], {}, []
    for d in drafts:
        jid = str(d["job_id"])
        job = master.get(jid)
        if job is None:
            failed[jid] = ["Job Masterに無い"]
            continue
        if job.get("status") == PRE_STAGE and job.get("application"):
            skipped.append(jid)  # a draft is never rebuilt (same job_id twice = no change)
            continue
        src = P.load_json(os.path.join(sdir, f"{jid}.json"), None)
        errs = _validate(d, job, src, profile)
        if errs:
            failed[jid] = errs
            continue
        text = d["application_draft"] + " ".join(d.get("application_answers", []))
        confirm = d.get("user_confirmation_required") == "yes"
        net = None if d["actual_reward"] is None else round(d["actual_reward"] * (1 - P.FEE_RATE))
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
            "app_priority": None if net is None else round(net / (review + (CONFIRM_PENALTY_MIN if confirm else 0)), 1),
            "claim_flags": sorted(set(CLAIM_RE.findall(text))),
            "final_qa_status": "PENDING_ASTRA",
            "next_action": d.get("next_action") or "Astra最終QA",
            "generated_at": P.now_iso(),
            # measured times come back via Status Updates (application_preparation_ai_time etc.)
            "application_preparation_ai_time": d.get("application_preparation_ai_time"),
        }
        cost = confirm_cost(job["application"], job["application"]["unverified_facts"])
        if cost:  # not worth a question to the user: Astra decides (REJECT candidate), the user is not asked
            job["application"].update(confirm_cost=cost, user_confirmation_required="no")
        if job.get("status") == PRE_STAGE:
            job["application"]["stage"] = "PRE_ASTRA"
            job.pop("pre_draft_due", None)
        _apply_readiness(job, a.date)
        ok.append(jid)
    P.vault_save(v)
    P.export(master, v.get("meta", {}))
    print(json.dumps({"merged": ok, "already_drafted": skipped, "failed": failed}, ensure_ascii=False, indent=1))
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
    master = {k: j for k, j in master.items() if j.get("status") != PRE_STAGE}  # pre-Astra drafts are not applications
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


def _undrafted_view(j):
    """Export-only row for an ASTRA_PASS job without a draft, so every Astra PASS shows up with its reason."""
    rc = j.get("reward_check") or {}
    why = "、".join(rc.get("changes") or []) or ("本日の原文再確認待ち" if rc else "原文再確認（app-check）待ち")
    net = j.get("net_est") or 0
    stub = {"actual_reward": j.get("gross"), "actual_net": net, "reward_evidence": "", "application_draft": "",
            "application_questions": [], "application_answers": [], "facts_used": [], "unverified_facts": [],
            "conflict_risk": "", "claim_flags": [], "final_qa_status": "NO_DRAFT",
            "user_confirmation_required": "", "next_action": "応募文未作成：" + why, "app_priority": 0,
            "review_minutes_est": None, "generated_at": None}
    return {**j, "application": stub}


# Drive copy (pasted into one upload call): no posting excerpt (the url has it), and once a job is past
# the application step its draft/answers stay in the vault and application_queue.json only.
DRIVE_APP_COLS = [c for c in APP_COLS if c[0] not in ("key_excerpt", "status_reason")]
DONE_DRAFT_FIELDS = ("application_draft", "application_answers", "facts_used", "reward_evidence")


def _drive_row(j):
    if j.get("status") in ("ASTRA_PASS", "READY_TO_APPLY"):
        return j
    app = dict(j["application"], application_draft="（応募済み・見送り等のため省略。application_queue.jsonに保存）",
               application_answers=[], facts_used=[], reward_evidence="")
    return {**j, "application": app}


def ready_notice(master, ids=None):
    """07:30 notice body: READY_TO_APPLY jobs only, as `案件URL | 完成した応募文` (+ answers to the posting's
    questions), so the user can paste and apply from a phone. Nothing else is listed."""
    out = []
    for j in master.values():
        if j.get("status") != "READY_TO_APPLY" or not j.get("application") or (ids and str(j["job_id"]) not in ids):
            continue
        app = j["application"]
        block = f"{j['url']} | {app['application_draft'].strip()}"
        if app.get("application_questions"):
            block += "\n【応募時の回答】\n" + _pairs(app)
        out.append(block)
    return "\n\n".join(out)


def cmd_ready_notice(a):
    v = P.vault_load()
    ids = {x.strip() for x in a.ids.split(",") if x.strip()} if a.ids else None
    print(ready_notice(v["master"], ids) or "（READY_TO_APPLYの案件なし）")


def export_queue(master):
    jobs = [j for j in master.values() if j.get("application") and eligible(j, QUEUE_VISIBLE)]
    jobs += [_undrafted_view(j) for j in master.values() if j.get("status") == "ASTRA_PASS" and not j.get("application")]
    # app_priority already charges user-confirmation time (net JPY per human minute)
    order = {"READY_TO_APPLY": 0, "ASTRA_PASS": 1, "APPLIED": 2}
    jobs.sort(key=lambda j: (order.get(j.get("status"), 3), j["application"]["final_qa_status"] == "NO_DRAFT",
                             -(j["application"]["app_priority"] or 0)))
    P._write_csv(os.path.join(P.OUT, "application_queue.csv"), DRIVE_APP_COLS, [_drive_row(j) for j in jobs])
    with open(os.path.join(P.OUT, "ready_notice.txt"), "w", encoding="utf-8") as f:  # local; the 07:30 notice body
        f.write(ready_notice(master))
    P.save_json(os.path.join(P.OUT, "application_queue.json"),
                {"generated_at": P.now_iso(), "count": len(jobs),
                 "jobs": [{c: fn(j) for c, fn in APP_COLS} for j in jobs]})
    return len(jobs)


# ---- KPI (estimated vs actual, split by lane) -------------------------------------------------
LANES = {"A": "auto", "B": "auto", "C": "professional"}  # C = Professional / Human Premium
DONE = ("PAID", "DELIVERED", "NOT_SELECTED", "SKIPPED", "WITHDRAWN")


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


def cmd_app_plan(a):
    """Which ASTRA_PASS jobs to draft now. Cheap rule step after `app-check` (no LLM):
    closed / filled / expired / changed postings are excluded and never use the draft cap;
    the rest are ordered by application deadline, then estimated net per human minute."""
    import client_master
    v = P.vault_load()
    master = v["master"]
    clients = client_master.refresh(v)
    todo, excluded = [], {}
    for jid, j in master.items():
        if not drafting_target(j) or j.get("application"):
            continue
        rc = j.get("reward_check") or {}
        if not str(rc.get("checked_at", "")).startswith(a.date):
            excluded[jid] = "本日のapp-check未実行"
        elif rc.get("changes"):
            excluded[jid] = "、".join(rc["changes"])
        elif (rc.get("deadline") or j.get("expired_on") or "9999") < a.date:
            excluded[jid] = "応募期限切れ"
        elif rc.get("closed"):
            excluded[jid] = "募集終了"
        else:
            todo.append(j)
    todo.sort(key=lambda j: ((j.get("reward_check") or {}).get("deadline") or "9999",
                             -((j.get("net_est") or 0) / max(_est_min(j), 1))))
    ids = [str(j["job_id"]) for j in todo]
    # the drafts must open according to the real relationship with each client (Client Master)
    client = {}
    for j in todo[:a.cap]:
        lvl = client_master.level(clients, master, j)
        client[str(j["job_id"])] = {"client_id": client_master.client_id(j), "relationship": lvl,
                                    "opening": client_master.OPENINGS[lvl],
                                    "history": client_master.summary(clients, master, j)}
    # confirmed facts are reused as they are (refs for facts_used); they are never asked again
    facts = [{"ref": f"confirmed_facts[{i}].fact", "fact": f["fact"], "only_for_jobs_matching": f.get("job_re")}
             for i, f in reusable_facts(v.get("profile", {}))]
    print(json.dumps({"draft_now": ids[:a.cap], "carry_over": ids[a.cap:], "excluded": excluded,
                      "client": client, "confirmed_facts": facts}, ensure_ascii=False, indent=1))


def cmd_manual_add(a):
    """Add one job the user picked by hand (not found by the Scout). Claude evaluates it (--eval);
    it is marked as user-designated and becomes CLAUDE_CANDIDATE, so app-check / app-plan /
    app-merge treat it like an ASTRA_PASS job. Existing jobs are never duplicated or regressed."""
    import collect
    jid = str(a.id)
    v = P.vault_load()
    master = v["master"]
    if jid in master:
        print(json.dumps({"ok": False, "reason": f"既に登録済み（status={master[jid].get('status')}）"},
                         ensure_ascii=False))
        return
    page = collect.curl(f"{collect.BASE}/{jid}")
    src = parse_source(page) if page else None
    if not src or not src["desc"]:
        raise SystemExit("案件ページを取得できない")
    e = P.load_json(a.eval, {})
    title = re.search(r"<h1[^>]*>\s*(.*?)\s*<", page, re.S)
    hr = src["header_reward"] or {}
    pay = {"fixed_price_payment": {"min_budget": hr.get("min"), "max_budget": hr.get("max")}} \
        if hr.get("type") == "固定報酬制" else {"other": hr}
    jo = {"job_offer": {"title": title.group(1).strip() if title else e.get("title", ""), "category_id": e.get("category_id"),
                        "expired_on": src["deadline"]}, "payment": pay}
    f = collect.screen(jo, src["desc"], src["client"])
    now = P.now_iso()
    row = {"id": int(jid), "url": f"{collect.BASE}/{jid}", "title": jo["job_offer"]["title"],
           "category_id": e.get("category_id"), "expired_on": src["deadline"], "tiers": ["manual"],
           "entry": {"applicants": src["applicants"], "contracted": src["contracted"], "capacity": src["capacity"]},
           "client": src["client"], "first_seen": now, "desc": src["desc"], "client_open_jobs": None, **f}
    job = master[jid] = P.job_facts(row)
    job["first_seen"] = now
    job["eval"] = {k: e.get(k) for k in P.EVAL_FIELDS}
    job["eval"]["evaluated_at"] = now
    if e.get("gross_jpy"):
        job["gross"], job["net_est"] = e["gross_jpy"], round(e["gross_jpy"] * (1 - P.FEE_RATE))
    job["actual"] = {k: None for k in P.ACTUAL_FIELDS}
    job["designation"] = {"by": "本人", "at": now, "note": a.note}
    P.set_status(job, "SCOUTED", "本人指定", a.note)
    P.set_status(job, "CLAUDE_CANDIDATE", "claude", "本人指定（Astra一次QAは経ない）：" + (e.get("reason") or ""))
    index = P.load_json(os.path.join(P.STATE, "index.json"), {})
    index[jid] = {"first_seen": now, "last_seen": now, "status": "CLAUDE_EVALUATED", "desc_hash": f["desc_hash"],
                  "client_id": src["client"].get("userId"), "expired_on": src["deadline"], "manual": True}
    P.save_json(os.path.join(P.STATE, "index.json"), index)
    P.vault_save(v)
    P.export(master, v.get("meta", {}))
    print(json.dumps({"ok": True, "job_id": jid, "status": job["status"], "deadline": src["deadline"],
                      "entry": row["entry"], "closed": src["closed"]}, ensure_ascii=False))
