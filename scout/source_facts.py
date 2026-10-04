"""Source-backed facts for the 05:00 application drafts (no LLM): what the posting really says about
application questions and rewards, and what each client number really means.

Why: on 2026-10-01 Astra found drafts without the posting's questions, trial and regular rewards mixed
into one amount, and a count of postings turned into "27件納品済み". Everything here is read from the
posting text (app-check) or from labelled Client Master fields, and every value keeps its source.
"""
import re

# ---- application questions / instructions for the application ----------------------------------------
# A line that opens the "tell us when you apply" part of a posting
Q_HEAD_RE = re.compile(r"応募(?:時|の際|する際|にあたって|に際して|方法|時の|の方法|前に)|ご応募(?:の際|時|方法|いただく際|に際して)|"
                       r"応募用?(?:の)?(?:フォーマット|フォーム|テンプレート)|コピペで(?:ご)?回答|(?:以下|下記)(?:の)?(?:項目|内容)?に(?:ご)?回答|"
                       r"以下(?:の内容)?を(?:ご)?(?:記入|記載|明記|添えて)|ご(?:記入|記載)の(?:うえ|上)|"
                       r"^\s*(?:【|■|◆|◎|▼|●)?\s*[^。]{0,12}(?:教えて|お知らせ)(?:ください|下さい)\s*(?:】)?\s*$|"
                       r"ご送付ください|添えて(?:ご)?応募|(?:コピー|複製)して(?:ご)?回答|"
                       r"^\s*(?:《|【|■|◆|〈|<)?\s*ご?(?:応募|お申し?込み)(?:について|方法)?\s*(?:》|】|〉|>)?\s*$")
# A line that is itself an instruction for the application (amount to enter, withholding box, what to write)
Q_LINE_RE = re.compile(r"応募時は|応募時に|応募の際は|入力(?:して|いただ)|金額設定|源泉徴収|チェックを(?:外|入れ)|"
                       r"(?:自己紹介|お名前|ご経験|ポートフォリオ|実績|志望動機|応募理由).{0,12}(?:添え|ご送付|ご記入|記載|教えて|お書き|ご提示|お送り)")
SEPARATOR_RE = re.compile(r"^\s*(?:ー{3,}|－{3,}|-{3,}|=+|＝+|―{3,}|─{3,})\s*$")
SECTION_RE = re.compile(r"^\s*(?:【|■|◆|◎|▼|〈|<|［|\[|ー{3,}|－{3,}|-{3,}|=+|＝+)")
LIST_RE = re.compile(r"^\s*(?:[・\-‐－*●○]|[①-⑳]|\(?\d{1,2}[).．、]|[０-９]{1,2}[.．、])")
END_MARKERS = ("この仕事の特徴", "クライアント情報")
# a line that actually asks something of the applicant (vs. a heading or a context line inside the block)
ASK_RE = re.compile(r"[?？]|ください|ようお願い|下さい|教えて|お知らせ|ご記入|ご記載|添え|ご提示|ご回答|[:：]\s*$|(?:の)?か\s*$")
PROCESS_RE = re.compile(r"(?:実施|選考|審査|ご相談|ご連絡|お渡し|進み|お送りし)(?:します|いたします|ます)?[\s✨！!。]*$")
NOT_ASK_RE = re.compile(r"(?:以下|下記|こちら|次)(?:の(?:項目|内容))?を|ご不明|ご質問|お問い?合わせ|お気軽|お待ちして")


def question_items(lines):
    """The asks among the extracted lines (list items, form fields and sentences that ask / instruct);
    headings and context lines are left out. Used to check the draft against the posting."""
    out = []
    for l in lines:
        if SECTION_RE.match(l) and len(l) <= 30 and not re.search(r"[:：]\s*$", l):
            continue  # 【応募方法】 / ■応募に際して
        if ASK_RE.search(l) and not NOT_ASK_RE.search(l):
            out.append(l)
        elif LIST_RE.match(l) and not PROCESS_RE.search(l):  # "③ テストライティングを実施" is the client's step
            out.append(l)
    return out


def _lines_of(desc):
    """Postings use U+2028 / U+2029 as line breaks inside one paragraph."""
    return (desc or "").replace("\u2028", "\n").replace("\u2029", "\n").replace("\r", "\n")


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
        return {"status": "FETCH_FAILED", "lines": [], "items": [], "reason": "募集原文を取得できなかった（設問の有無は不明）"}
    lines = [l.strip() for l in _lines_of(desc).split("\n")]
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
        return {"status": "VERIFIED", "lines": out, "items": question_items(out),
                "reason": "" if st == "OK" else "募集原文が途中までの可能性あり（ほかにも設問がありうる）"}
    if st == "SOURCE_INCOMPLETE":
        return {"status": "SOURCE_INCOMPLETE", "lines": [], "items": [], "reason": "募集原文が不完全（設問の有無を確認できない）"}
    return {"status": "NONE_VERIFIED", "lines": [], "items": [], "reason": ""}


# ---- reward structure: this application vs. later work -----------------------------------------------
INITIAL_RE = re.compile(r"テスト|トライアル|初回|お試し|試用|応募時は|最初の|最初は|1本目|１本目|研修")
ONGOING_RE = re.compile(r"本契約|本採用|本番|継続|2回目以降|２回目以降|2本目以降|以降|通常|レギュラー|採用後")
PERIODIC_RE = re.compile(r"/月|／月|月額|月\s*\d|毎月|週\s*\d")
UNIT_RATE_RE = re.compile(r"文字単価|1文字|１文字|字単価")
# a sub-heading inside a section ("① 初回（テスト）", "《 条件 》", "(2) 継続の場合"): its cue holds for the lines under it
SUBHEAD_RE = re.compile(r"^\s*(?:[①-⑳]|[（(]\d{1,2}[)）]|《|〈|<|\d{1,2}[.．)]\s)")
# where to type the amount on the contract screen (not a reward: "契約金額の欄：税抜146円")
ENTRY_LINE_RE = re.compile(r"契約金額の欄|金額(?:の)?欄|金額設定|と入力|入力(?:して|いただ|をお願い|ください)")
HOURLY_RE = re.compile(r"時給|時間単価|1時間あたり|１時間あたり|時間あたり")
QUOTE_RE = re.compile(r"見積(?:も)?り?(?:金額|額)?(?:を|の)?(?:お願い|入力|ご提示|提示|ください|ご提案)|お見積|条件提示")
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
    hourly, entry, taxes = [], [], set()
    ctx, tax_ctx, lead, prev_line = None, False, None, ""  # cue / 税抜 of the heading; cue of the line before
    for raw in _lines_of(desc).split("\n"):
        l = raw.strip()
        if not l:
            continue
        listing_price = prev_line in ("記事単価", "タスク単価") and re.fullmatch(r"[\d,０-９，]+\s*円", l)
        prev_line = l
        prev_lead, lead = lead, None
        heading = SECTION_RE.match(l) or (SUBHEAD_RE.match(l) and not YEN_RE.search(l))
        if heading or not YEN_RE.search(l):
            cue = "init" if INITIAL_RE.search(l) else ("ongo" if ONGOING_RE.search(l) else None)
            if heading:
                ctx = cue
                if SECTION_RE.match(l):
                    tax_ctx = bool(re.search(r"税抜|税別", l))
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
        if HOURLY_RE.search(l):  # an hourly rate is not the amount of this job
            hourly.append(amts[0])
            lines.append(l)
            continue
        if ENTRY_LINE_RE.search(l) and not re.search(r"報酬|謝礼", l) and not INITIAL_RE.search(l) \
                and not ONGOING_RE.search(l):
            entry.append(amts[0])  # what to type on the contract screen; the reward lines say what is paid
            lines.append(l)
            continue
        m_in = re.search(r"([\d,０-９，]+)\s*円\s*[（(]\s*入力(?:金額|額)\s*[）)]", l)
        # the page's own "記事単価 / 200円" footer is the pre-tax article price (contract estimate = x1.1)
        tax_ex = bool(re.search(r"税抜|税別|入力金額|入力額", l)) or (tax_ctx and "税込" not in l) or bool(listing_price)
        if m_in:  # "200円（入力金額）+ 20円（消費税）": the entered amount is pre-tax
            a, tax_ex = round(_yen(m_in.group(1)) * 1.1), True
        else:
            a = round(amts[0] * 1.1) if tax_ex and "合計" not in l else amts[0]
        taxes.add("税抜→税込換算" if tax_ex else ("税込" if re.search(r"税込|消費税込|込み", l) else "税区分の記載なし（記載額のまま）"))
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
           "reward_source_excerpt": " / ".join(lines),
           "reward_tax": "・".join(sorted(taxes)) or None, "hourly_rate": hourly[0] if hourly else None,
           "entry_amount": entry[0] if entry else None}
    quote = bool(QUOTE_RE.search(_lines_of(desc)))
    if quote and not init and not ongo and not base:
        res.update(reward_status="QUOTE_REQUIRED", applicable_reward=None,
                   reward_basis="見積依頼（今回報酬は応募者の見積額で決まる）"
                                + (f"。原文の目安：時給{hourly[0]:,}円" if hourly else "")
                                + "。目安額を固定報酬として扱わない")
        return res
    if hourly and not init and not ongo and not base:
        res.update(reward_status="AMBIGUOUS", applicable_reward=None,
                   reward_basis=f"時給{hourly[0]:,}円の記載のみ（1件あたりの報酬は作業時間次第で確定できない）")
        return res
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


# ---- AI terms, work conditions, completeness: "not stated" is never "not fetched" -----------------------
AI_LABELS = {
    "A": "A:AI利用明示可",
    "B": "B:AI禁止・制限あり",
    "C": "C:AI記載なし（AI禁止記載なし／利用条件不明）",
    "D": "D:原文取得不足で確認不能",
}


AI_ALLOW_RE = re.compile(r"(?:AI|ＡＩ|生成AI|ChatGPT|Gemini|Claude)[^。\n]{0,15}(?:歓迎|可能|OK|ＯＫ|可(?!能性)|構いません|推奨|活用(?:して|ください)|使って(?:も|いただ))")


def ai_condition(desc, complete=True, fetched=True):
    """The posting's AI terms in four classes. C (nothing about AI in a complete text) is neither "allowed"
    nor "banned" and is no reason to hold the application by itself; only D needs a source repair."""
    import collect
    st = source_state(desc, complete, fetched)
    text = _lines_of(desc)
    if st == "FETCH_FAILED":
        return {"code": "D", "label": AI_LABELS["D"], "lines": [], "reason": "募集原文を取得できなかった"}
    pol, ev = collect.ai_policy(text)
    if pol == "A":
        code = "A"
    elif pol in ("B", "D"):
        code = "B"  # assist-only or banned: a restriction either way
    elif ev and any(AI_ALLOW_RE.search(l) and not collect.BAN.search(l) for l in ev):
        code = "A"  # "生成AIの利用も歓迎" / "AI利用可能"
    elif ev:
        code = "C"  # AI is mentioned but nothing says whether it may be used
    else:
        code = "C" if st == "OK" else "D"
    out = {"code": code, "label": AI_LABELS[code], "lines": ev,
           "reason": "AIへの言及はあるが利用可否の記載なし" if code == "C" and ev else ""}
    if code == "D":
        out["reason"] = "募集原文が不完全（AI記載の有無を確認できない）"
    return out


WORK_PATTERNS = {  # field -> lines of the posting that state it
    "work": r"作業内容|仕事内容|依頼内容|お仕事の(?:内容|概要)|業務内容|作業[:：]|お願い(?:したい|する)(?:こと|内容|お仕事)|していただくお仕事|をお願いします",
    "volume": r"件数|分量|作業量|仕事量|ボリューム|\d+\s*(?:件|ファイル|本|記事|枚|ページ|文字|字)(?:程度|ほど|分|×|x|まで|以上)?|所要時間",
    "deadline": r"納期|期限|締切|〆切|日以内|までに(?:納品|提出)",
    "continuity": r"継続|長期|毎月|毎週|定期|単発|今後も|リピート",
    "requirements": r"必須|条件|求める人物|こんな方|この様な方|募集します|歓迎|応募資格|歳以上|歳以下|不可|限定|できる方|お持ちの方",
    "tools": r"Word|ワード|Excel|エクセル|スプレッドシート|Google\s?(?:ドキュメント|ドライブ|スプレッド)|Canva|PDF|ツール|ソフト|アプリ|パソコン|PC|スマホ",
    "external": r"守秘|秘密保持|機密|個人情報|社外秘|外部(?:への|に)?(?:持ち出し|共有|公開|送信|サービス)|第三者|NDA|AI(?:へ|に)(?:入力|投入|アップロード)",
}


def work_facts(desc, complete=True, fetched=True):
    """Source lines for each work condition, with status VERIFIED (lines found) / NONE_VERIFIED (complete text,
    nothing about it) / SOURCE_INCOMPLETE / FETCH_FAILED."""
    st = source_state(desc, complete, fetched)
    lines = [l.strip() for l in _lines_of(desc).split("\n") if l.strip()]
    out = {}
    for k, pat in WORK_PATTERNS.items():
        if st == "FETCH_FAILED":
            out[k] = {"status": "FETCH_FAILED", "lines": []}
            continue
        hit = [l[:160] for l in lines if re.search(pat, l) and not re.fullmatch(r"(?:【|■|◆|●|《)[^。]{0,20}(?:】|》)?", l)]
        out[k] = {"status": "VERIFIED" if hit else ("NONE_VERIFIED" if st == "OK" else "SOURCE_INCOMPLETE"),
                  "lines": hit[:6]}
    return out


def requirement_checks(req_lines, profile, year):
    """Requirements the profile already answers (no question to the user): age range, students."""
    notes = []
    text = " ".join(req_lines or [])
    by = profile.get("birth_year")
    m = re.search(r"(\d{2})歳以上(?:[^。\d]{0,4}(\d{2})歳(?:以下|まで))?", text) or re.search(r"(\d{2})歳(?:〜|～|-)(\d{2})歳", text)
    if m and by:
        age = int(year) - int(by)
        lo, hi = int(m.group(1)), int(m.group(2) or 200)
        notes.append(f"年齢条件{lo}〜{hi if hi < 200 else ''}歳：プロフィール生年{by}→{age}歳で"
                     + ("該当" if lo <= age <= hi else "非該当"))
    if re.search(r"学生(?:さん)?(?:不可|NG|お断り|はご遠慮)", text) and (profile.get("professional") or {}).get("employer"):
        notes.append("学生不可：プロフィール上は会社員（学生ではない）")
    return notes


def completeness(rc, client_id, entry):
    """Per-field state of the facts Astra needs, from what app-check recorded. VERIFIED / NONE_VERIFIED
    (stated as absent in a complete text) / SOURCE_INCOMPLETE / FETCH_FAILED / QUOTE_REQUIRED / AMBIGUOUS."""
    state = rc.get("source_state") or "UNCHECKED"
    fail = state if state in ("FETCH_FAILED", "SOURCE_INCOMPLETE") else None
    q, rs, wf = rc.get("questions") or {}, rc.get("reward_struct") or {}, rc.get("work_facts") or {}
    rstat = rs.get("reward_status") or "UNCHECKED"
    f = {
        "questions": q.get("status") or "UNCHECKED",
        "applicable_reward": "VERIFIED" if rstat == "CONFIRMED" else rstat,
        "reward_basis": "VERIFIED" if rs.get("reward_basis") and rstat in ("CONFIRMED", "QUOTE_REQUIRED") else rstat,
        "work": (wf.get("work") or {}).get("status") or "UNCHECKED",
        "requirements": (wf.get("requirements") or {}).get("status") or "UNCHECKED",
        "ai_condition": {"D": fail or "SOURCE_INCOMPLETE"}.get((rc.get("ai_condition") or {}).get("code"), "VERIFIED")
                        if rc.get("ai_condition") else "UNCHECKED",
        "client_id": "VERIFIED" if client_id else (fail or "NOT_FOUND"),
        "capacity_contracted": "VERIFIED" if entry.get("capacity") is not None and entry.get("contracted") is not None
                               else (fail or "NOT_FOUND"),
    }
    # NONE_VERIFIED is a verified answer ("the posting says nothing"); QUOTE_REQUIRED is the posting's own terms
    ok = {"VERIFIED", "NONE_VERIFIED", "QUOTE_REQUIRED"}
    gaps = [k for k, v in f.items() if v not in ok]
    return {"fields": f, "status": "COMPLETE" if not gaps else "INCOMPLETE", "gaps": gaps}


# ---- recruitment-funnel risk: combined signals, never one word -----------------------------------------
# What is counted is what the posting ASKS of the applicant (application questions / form lines) and what it
# requires of them; words that are only the theme of the work ("一人暮らしの節約術", "ライフスタイルメディア",
# "実家の片付けアンケート", "ライフスタイルに合わせて働けます") are not signals.
ASK_LINE_RE = re.compile(r"[：:？?]\s*$|教えて|お聞かせ|ご記入|ご記載|お知らせ|ご回答|^\s*(?:[①-⑳]|\(?\d{1,2}[).．、])")
RECRUIT_S1 = {  # S1: the applicant's personal situation, asked without being needed for the work
    "住まい": r"一人暮らし\s*[・／/、]|実家暮らし|同棲|お住まいの状況|生活スタイル|ライフスタイル\s*[（(]|家族構成",
    "家族": r"配偶者|お子さん|子ども(?:さん)?の有無|家族構成|既婚・未婚|未婚・既婚",
    "本業時間": r"本業の?[^。\n]{0,6}(?:時間|労働)|週合計労働|勤務時間・曜日",
    "雇用形態": r"雇用形態|就業形態",
    "収入": r"月収|年収|収入(?:面|状況|額)",
}
# S2: the applicant's aspirations / ideal future, asked in the application
RECRUIT_S2 = re.compile(r"理想|目指(?:し|す)(?:て)?(?:みたい|たい)?働き方|働き方[^。\n]{0,10}(?:興味|目指|理想)|3年後|5年後|将来|"
                        r"今後[^。\n]{0,15}(?:活か|挑戦|目標|身につけ|どのように)|目標|フリーランス|稼げるように|副業[^。\n]{0,10}きっかけ")
# S3: leading the applicant off the platform (an online interview alone is NOT this: see INTERVIEW_RE)
RECRUIT_S3 = re.compile(r"(?<!オン)(?:LINE|ライン)\s*(?:で|へ|に|登録|追加|@|ID|を交換)|公式LINE|(?:個別|無料|オンライン)?説明会|"
                        r"(?:コミュニティ|サロン|スクール|講座)[^。\n]{0,10}(?:へ|に)?(?:ご?招待|ご?参加|ご?案内|入会)|"
                        r"外部(?:サイト|サービス|ツール)[^。\n]{0,8}(?:登録|連絡|やり取り)|(?:メール|電話番号)を(?:送|教え)")
# S4: money flowing from the worker
RECRUIT_S4 = re.compile(r"受講|入会金?|教材|有料(?:講座|プラン|会員|サポート|コミュニティ)|自己負担|初期費用|登録料(?![^。\n]{0,8}無料)|"
                        r"(?:スクール|講座|サロン|コンサル)[^。\n]{0,10}(?:費|料金|代)")
# S5: the posting targets aspiration itself ("future freelancer", "side job alongside your main job")
RECRUIT_S5 = re.compile(r"将来的にフリーランス|フリーランス志向|本業を続けながら副業に挑戦|自分で仕事をしていく働き方|"
                        r"場所や時間にとらわれず働|自由な働き方を(?:実現|手に入れ)")
# not S3 by itself: an online / video interview is also a normal selection step -> a flag for Claude / Astra
INTERVIEW_RE = re.compile(r"(?:Zoom|ZOOM|Google\s?Meet|オンライン|ビデオ|Web)[^。\n]{0,6}(?:面談|面接|通話|打ち合わせ|ミーティング)|"
                          r"(?<!不要の)(?:面談|面接)(?![^。\n]{0,6}(?:なし|不要|ありません|無し|しません))")


def recruit_signals(desc):
    """S1-S5 found in what the posting asks / requires, plus an interview flag (never S3 on its own)."""
    text = _lines_of(desc)
    q = questions(text)
    asks = [l.strip() for l in text.split("\n") if l.strip() and (ASK_LINE_RE.search(l.strip()) or l.strip() in q["lines"])]
    ask_text = "\n".join(asks)
    s1 = [k for k, pat in RECRUIT_S1.items() if re.search(pat, ask_text)]
    s2 = sorted(set(RECRUIT_S2.findall(ask_text)))
    s3 = sorted(set(RECRUIT_S3.findall(text)))
    s4 = sorted(set(RECRUIT_S4.findall(text)))
    s5 = sorted(set(RECRUIT_S5.findall(text)))
    iv = [l.strip()[:80] for l in text.split("\n") if INTERVIEW_RE.search(l)]
    return {"S1": s1, "S2": s2[:4], "S3": s3[:4], "S4": s4[:4], "S5": s5[:3], "interview": iv[:3]}


def recruit_decision(sig):
    """REJECT only on several strong signals together; anything else is a flag for Claude / Astra.
    - off-platform lead or money from the worker (S3 / S4), together with personal / aspiration questions
    - questions about where / with whom the applicant lives, with another personal item, plus aspiration
      questions or aspiration targeting (the beginner side-job funnel intake)"""
    living = bool({"住まい", "家族"} & set(sig["S1"]))
    if (sig["S3"] or sig["S4"]) and (sig["S1"] or sig["S2"]):
        return "REJECT"
    if living and len(sig["S1"]) >= 2 and (sig["S2"] or sig["S5"]):
        return "REJECT"
    if sig["S1"] or sig["S2"] or sig["S3"] or sig["S4"] or sig["S5"]:
        return "FLAG"
    return "NONE"


def recruit_summary(sig):
    parts = [f"S1個人状況:{'/'.join(sig['S1'])}" if sig["S1"] else "", f"S2将来像:{'/'.join(sig['S2'])}" if sig["S2"] else "",
             f"S3外部誘導:{'/'.join(sig['S3'])}" if sig["S3"] else "", f"S4金銭負担:{'/'.join(sig['S4'])}" if sig["S4"] else "",
             f"S5願望ターゲティング:{'/'.join(sig['S5'])}" if sig["S5"] else ""]
    return " ".join(p for p in parts if p)


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
