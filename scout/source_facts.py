"""Source-backed facts for the 05:00 application drafts (no LLM): what the posting really says about
application questions and rewards, and what each client number really means.

Why: on 2026-10-01 Astra found drafts without the posting's questions, trial and regular rewards mixed
into one amount, and a count of postings turned into "27件納品済み". Everything here is read from the
posting text (app-check) or from labelled Client Master fields, and every value keeps its source.
"""
import re

# ---- application questions / instructions for the application ----------------------------------------
# A line that opens the "tell us when you apply" part of a posting
Q_HEAD_RE = re.compile(r"応募(?:時|の際|する際|にあたって|方法|時の|の方法|前に)|ご応募(?:の際|時|方法|いただく際)|"
                       r"以下(?:の内容)?を(?:ご)?(?:記入|記載|明記|添えて)|ご(?:記入|記載)の(?:うえ|上)|"
                       r"^\s*(?:【|■|◆|◎|▼|●)?\s*[^。]{0,12}(?:教えて|お知らせ)(?:ください|下さい)\s*(?:】)?\s*$|"
                       r"ご送付ください|添えて(?:ご)?応募|(?:コピー|複製)して(?:ご)?回答")
# A line that is itself an instruction for the application (amount to enter, withholding box, what to write)
Q_LINE_RE = re.compile(r"応募時は|応募時に|応募の際は|入力(?:して|いただ)|金額設定|源泉徴収|チェックを(?:外|入れ)|"
                       r"(?:自己紹介|お名前|ご経験|ポートフォリオ|実績).{0,12}(?:添えて|ご送付|ご記入|記載|教えて)")
SEPARATOR_RE = re.compile(r"^\s*(?:ー{3,}|－{3,}|-{3,}|=+|＝+|―{3,}|─{3,})\s*$")
SECTION_RE = re.compile(r"^\s*(?:【|■|◆|◎|▼|〈|<|［|\[|ー{3,}|－{3,}|-{3,}|=+|＝+)")
LIST_RE = re.compile(r"^\s*(?:[・\-‐－*●○]|[①-⑳]|\(?\d{1,2}[).．、]|[０-９]{1,2}[.．、])")
END_MARKERS = ("この仕事の特徴", "クライアント情報")


def source_state(desc, complete, fetched=True):
    """FETCH_FAILED / SOURCE_INCOMPLETE / OK for the posting text that questions and rewards are read from."""
    if not fetched or desc is None:
        return "FETCH_FAILED"
    if not complete or len(desc.strip()) < 30 or re.search(r"(?:続きを読む|…|\.\.\.)\s*$", desc.strip()):
        return "SOURCE_INCOMPLETE"
    return "OK"


def questions(desc, complete=True, fetched=True):
    """Application questions / instructions quoted from the posting (full lines, never shortened).
    status: VERIFIED (found) / NONE_VERIFIED (complete text, none found) / SOURCE_INCOMPLETE / FETCH_FAILED."""
    st = source_state(desc, complete, fetched)
    if st == "FETCH_FAILED":
        return {"status": "FETCH_FAILED", "lines": [], "reason": "募集原文を取得できなかった（設問の有無は不明）"}
    lines = [l.strip() for l in desc.split("\n")]
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        if l and Q_HEAD_RE.search(l):
            block = [l]
            j = i + 1
            while j < len(lines) and len(block) < 15:
                n = lines[j]
                if not n or SEPARATOR_RE.match(n):  # blank lines and rulers inside the block
                    j += 1
                    continue
                if SECTION_RE.match(n) and not Q_HEAD_RE.search(n):
                    break
                if not LIST_RE.match(n) and not Q_LINE_RE.search(n) and not Q_HEAD_RE.search(n) and len(block) > 1:
                    break
                block.append(n)
                j += 1
            out += [b for b in block if b not in out]
            i = j
            continue
        if l and Q_LINE_RE.search(l) and l not in out:
            out.append(l)
        i += 1
    if out:
        return {"status": "VERIFIED", "lines": out,
                "reason": "" if st == "OK" else "募集原文が途中までの可能性あり（ほかにも設問がありうる）"}
    if st == "SOURCE_INCOMPLETE":
        return {"status": "SOURCE_INCOMPLETE", "lines": [], "reason": "募集原文が不完全（設問の有無を確認できない）"}
    return {"status": "NONE_VERIFIED", "lines": [], "reason": ""}


# ---- reward structure: this application vs. later work -----------------------------------------------
INITIAL_RE = re.compile(r"テスト|トライアル|初回|お試し|試用|応募時は|最初の|最初は|1本目|１本目|研修")
ONGOING_RE = re.compile(r"本契約|本採用|本番|継続|2回目以降|２回目以降|2本目以降|以降|通常|レギュラー|採用後")
PERIODIC_RE = re.compile(r"/月|／月|月額|月\s*\d|毎月|週\s*\d")
UNIT_RATE_RE = re.compile(r"文字単価|1文字|１文字|字単価")
YEN_RE = re.compile(r"([\d,０-９，]+)\s*円")
AMOUNT_CUE = re.compile(r"報酬|円|単価|謝礼|金額|費用")


def _yen(s):
    return int(s.translate(str.maketrans("０１２３４５６７８９，", "0123456789,")).replace(",", ""))


def rewards(desc, complete=True, fetched=True):
    """initial_reward (what this application is paid: trial / first job) and ongoing_reward (after that),
    from the posting only. Amounts are tax-included JPY (税抜 amounts x1.1). reward_status:
    CONFIRMED (the applicable amount is known) / AMBIGUOUS (e.g. a paid trial without amount, or several
    amounts for the same step) / UNKNOWN (no amount in the text) / FETCH_FAILED / SOURCE_INCOMPLETE."""
    st = source_state(desc, complete, fetched)
    if st == "FETCH_FAILED":
        return {"reward_status": "FETCH_FAILED", "initial_reward": None, "ongoing_reward": None,
                "applicable_reward": None, "reward_basis": "募集原文を取得できなかった", "reward_source_excerpt": ""}
    init, ongo, base, lines, trial_no_amount = [], [], [], [], False
    ctx, tax_ctx, lead = None, False, None  # cue / 税抜 of the heading; cue of the line just before
    for raw in desc.split("\n"):
        l = raw.strip()
        if not l:
            continue
        prev_lead, lead = lead, None
        if SECTION_RE.match(l) or not YEN_RE.search(l):
            cue = "init" if INITIAL_RE.search(l) else ("ongo" if ONGOING_RE.search(l) else None)
            if SECTION_RE.match(l):
                ctx, tax_ctx = cue, bool(re.search(r"税抜|税別", l))
            else:
                lead = cue  # e.g. "最初は500文字程度のテストをお願いします。" -> the next amount is the trial's
        if not AMOUNT_CUE.search(l) and not INITIAL_RE.search(l):
            continue
        amts = [_yen(x) for x in YEN_RE.findall(re.sub(r"文字単価[\s:：]*[\d.,０-９]+\s*円[～〜~]?", "", l)) if _yen(x) > 0]
        if PERIODIC_RE.search(l):
            amts = []  # monthly / weekly totals are not the amount of one job
        if not amts:
            if INITIAL_RE.search(l) and re.search(r"報酬あり|有償|報酬が発生", l):
                trial_no_amount = True
                lines.append(l)
            continue
        tax_ex = bool(re.search(r"税抜|税別|入力金額|入力額", l)) or (tax_ctx and "税込" not in l)
        a = round(amts[0] * 1.1) if tax_ex and "合計" not in l else amts[0]
        lines.append(l)
        near = prev_lead or ctx
        if INITIAL_RE.search(l) or (near == "init" and not ONGOING_RE.search(l)):
            init.append(a)
        elif ONGOING_RE.search(l) or near == "ongo":
            ongo.append(a)
        else:
            base.append(a)
    if not ongo and base:  # amounts without an "ongoing" cue are the regular reward (kept in the excerpt too)
        ongo = base
    res = {"initial_reward": init[0] if len(set(init)) == 1 else None,
           "ongoing_reward": ongo[0] if len(set(ongo)) == 1 else (min(ongo) if ongo else None),
           "reward_source_excerpt": " / ".join(lines)}
    if init and len(set(init)) == 1:
        res.update(reward_status="CONFIRMED", applicable_reward=init[0],
                   reward_basis="初回（テスト・トライアル等）の報酬を今回適用" + ("。継続時は別額" if ongo else ""))
    elif init:
        res.update(reward_status="AMBIGUOUS", applicable_reward=None, reward_basis="初回報酬の記載が複数あり確定できない")
    elif trial_no_amount:
        res.update(reward_status="AMBIGUOUS", applicable_reward=None,
                   reward_basis="有償テスト等の記載はあるが金額の記載なし（今回適用報酬は不明。継続報酬を今回分にしない）")
    elif ongo and len(set(ongo)) == 1 and not re.search(INITIAL_RE, " ".join(lines)):
        res.update(reward_status="CONFIRMED", applicable_reward=ongo[0], reward_basis="単一の報酬（初回・継続の区別なし）")
    elif ongo:
        res.update(reward_status="AMBIGUOUS", applicable_reward=None, reward_basis="報酬の記載が複数あり今回分を確定できない")
    else:
        res.update(reward_status="UNKNOWN" if st == "OK" else st, applicable_reward=None,
                   reward_basis="本文に金額の記載なし" if st == "OK" else "募集原文が不完全")
    return res


# ---- client numbers: each value with its own meaning and source ---------------------------------------
def client_facts(job, cm):
    """Labelled client values. `cm` = Client Master record (or None). Nothing is derived from another field."""
    c = job.get("client") or {}
    f = {
        "cw_job_offer_count": {"value": c.get("jobOfferAchievementCount"), "label": "CW公開：募集実績（発注者の募集数。当方の取引ではない）",
                               "source": "CrowdWorks公開プロフィール"},
        "cw_average_score": {"value": c.get("averageScore"), "label": "CW公開：評価平均（件数ではない）",
                             "source": "CrowdWorks公開プロフィール"},
        "cw_identity_verified": {"value": c.get("isIdentityVerified"), "label": "CW公開：本人確認",
                                 "source": "CrowdWorks公開プロフィール"},
        "cw_review_count": {"value": "UNKNOWN", "label": "CW公開：評価件数（取得していない）", "source": "未取得"},
    }
    if cm:
        others = [x for x in cm.get("past_job_ids", []) if x != str(job.get("job_id"))]
        f.update({
            "scout_seen_postings": {"value": len(others), "label": "Scout検知の同じ発注者の他の募集（当方の応募・取引ではない）",
                                    "source": "Client Master（Scout記録）"},
            "our_applications": {"value": cm.get("application_count"), "label": "当方の応募", "source": "Client Master（当方記録）"},
            "our_accepted": {"value": cm.get("accepted_count"), "label": "当方の受注", "source": "Client Master（当方記録）"},
            "our_delivered": {"value": cm.get("delivered_count"), "label": "当方の納品", "source": "Client Master（当方記録）"},
            "our_paid": {"value": cm.get("paid_count"), "label": "当方への支払", "source": "Client Master（当方記録）"},
            "our_repeat_orders": {"value": cm.get("repeat_order_count", "UNKNOWN"), "label": "当方へのリピート",
                                  "source": "Client Master（当方記録）"},
        })
    else:
        for k, lab in (("our_applications", "当方の応募"), ("our_accepted", "当方の受注"), ("our_delivered", "当方の納品"),
                       ("our_paid", "当方への支払"), ("our_repeat_orders", "当方へのリピート")):
            f[k] = {"value": "UNKNOWN", "label": lab, "source": "Client Master参照不可"}
    return f


def client_facts_text(f):
    return " / ".join(f"{v['label']}={v['value']}" for v in f.values() if v["value"] not in (None, "UNKNOWN"))


# "27件納品済み", "納品実績27件", "過去27件の取引" ...: a count claimed about our relationship with the client
CLAIM_RE = re.compile(r"(\d+)\s*件[^。\n、,]{0,6}?(納品|受注|取引|契約|支払|リピート|依頼(?:いただ|を受)|ご依頼)|"
                      r"(納品|受注|取引|契約|支払|リピート)(?:実績|済み|済|数|歴)?[^。\n\d]{0,4}(\d+)\s*件")
CLAIM_FIELD = {"納品": "our_delivered", "受注": "our_accepted", "取引": "our_accepted", "契約": "our_accepted",
               "依頼": "our_accepted", "支払": "our_paid", "リピート": "our_repeat_orders"}


def client_claim_errors(text, f):
    """Counts in `text` that claim a relationship with the client but do not match the labelled value."""
    errs = []
    for m in CLAIM_RE.finditer(text or ""):
        n = int(m.group(1) or m.group(4))
        word = m.group(2) or m.group(3)
        key = next((v for k, v in CLAIM_FIELD.items() if word.startswith(k) or k in word), "our_accepted")
        known = (f.get(key) or {}).get("value")
        if known != n:
            hint = " / ".join(f"{v['label']}={v['value']}" for k, v in f.items()
                              if v["value"] == n and k != key) or "該当する記録なし"
            errs.append(f"クライアント実績値の誤変換疑い：「{m.group(0)}」（{f.get(key, {}).get('label', key)}={known}。"
                        f"{n}に一致する項目：{hint}）")
    return errs
