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
import collections
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
    "SCOUTED", "RULE_REJECTED", "CLAUDE_CANDIDATE", "CLAUDE_REJECTED", "ASTRA_QA_PENDING",
    "ASTRA_PASS", "ASTRA_REJECT", "NEED_USER", "READY_TO_APPLY", "APPLIED",
    "ACCEPTED", "IN_PROGRESS", "READY_FOR_QA", "READY_TO_DELIVER", "DELIVERED",
    "PAID", "CLOSED",
    "SKIPPED",       # user/Astra chose not to apply this time (not a fit/condition rejection)
    "NOT_SELECTED",  # applied but the client did not accept
    "WITHDRAWN",     # applied, then the user withdrew (e.g. an interview asked for only after applying)
]
# Past the Astra QA / application step: a (re)sent Astra verdict must not move these back
PROGRESSED = {"READY_TO_APPLY", "APPLIED", "ACCEPTED", "IN_PROGRESS", "READY_FOR_QA",
              "READY_TO_DELIVER", "DELIVERED", "PAID", "NOT_SELECTED", "WITHDRAWN"}
# Statuses Claude may overwrite on re-evaluation; later ones belong to Astra/user.
CLAUDE_OWNED = {"SCOUTED", "CLAUDE_CANDIDATE", "CLAUDE_REJECTED", "ASTRA_QA_PENDING"}
ACTUAL_FIELDS = ["actual_human_minutes", "actual_ai_processing", "revision_count",
                 "actual_gross_reward", "actual_net_reward", "result",
                 "client_rating", "repeat_order"]
EVAL_FIELDS = ["classification", "ai_condition", "requirements", "fit", "profile_link",
               "ai_steps", "human_steps", "ai_completion", "human_minutes",
               "est_hourly", "repeatability", "client_risk", "user_questions",
               "verdict", "reason", "source_notes", "gross_jpy", "lane"]
LANES = ["Professional", "Experience", "Auto", "Human Premium"]


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
    v = json.loads(r.stdout)
    for j in v.get("master", {}).values():  # ASTRA_QUEUE was renamed ASTRA_QA_PENDING
        if j.get("status") == "ASTRA_QUEUE":
            j["status"] = "ASTRA_QA_PENDING"
    return v


def vault_save(v):
    if v.get("master") is not None:  # Client Master follows every Job Master change
        import client_master
        client_master.refresh(v)
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
COMMISSION = re.compile(r"成約|フルコミ|成果報酬|営業代行|営業パートナー|テレアポ|インサイドセールス|アポ(?:獲得|取り)|紹介(?:料|報酬)")
HEAVY_COMMIT = re.compile(r"\d{2,3}%(?:稼働|/|／)|稼働率|週\s*[3-5]日|週\s*(?:[2-9]\d|1[5-9])\s*時間|常駐|出社|フルタイム|1日\s*[4-9]\s*時間")
HARD_REQ = {
    "WordPress": r"(?:WordPress|ワードプレス)[^。\n]{0,25}(?:必須|できる方|直接入稿|入稿できる|経験(?:者|が)必要)",
    "ポートフォリオ": r"ポートフォリオ[^。\n]{0,15}(?:必須|提出|をお送り|を添付)",
    "執筆実績の提出": r"(?:執筆|取材)(?:実績|経験)[^。\n]{0,15}(?:必須|の(?:ご)?提出|URL)",
    "校正経験": r"校正[^。\n]{0,10}(?:経験|実務)[^。\n]{0,8}(?:必須|がある方のみ|者限定)",
    "取材経験": r"取材経験[^。\n]{0,10}(?:必須|がある方)",
}
# phrases that mention a profile word without being about that topic
NOT_TOPIC = re.compile(r"副業(?:OK|ＯＫ|歓迎|可|の方|として|でも)|在宅副業|投資家の方|英語不要|Excel(?:・|、)?(?:Word|ワード)?(?:が使える|操作)|受験生")
# Auto work: AI / scripts / rule processing can do it; the user only checks the result.
AUTO_RE = re.compile(r"データ(?:入力|収集|整理|作成|抽出|分類|クレンジング)|リスト(?:作成|アップ)|営業リスト|企業(?:情報|リスト)|"
                     r"情報収集|収集作業|スクレイピング|転記|Excel|エクセル|スプレッドシート|Google\s?(?:Sheets|シート)|CSV|"
                     r"PDF|Word(?:ファイル|形式|へ)|分類|仕分け|タグ付け|要約|リライト|集計|Web調査|ネット(?:で)?(?:検索|調査)|"
                     r"リサーチ|市場調査|競合(?:調査|分析)|業界(?:調査|分析)|企業分析|財務分析|資料作成|PowerPoint|パワポ|スライド")
# Professional work that the profile (finance / sales / analysis) can back
PRO_RE = re.compile(r"市場調査|競合(?:調査|分析)|業界(?:調査|分析)|企業分析|財務(?:分析)?|決算|事業計画|資金調達|金融|M&A|融資|資料作成|PowerPoint|パワポ")
# Work that ties the user down whatever the pay (excluded before Claude)
PHONE_WORK = re.compile(r"電話(?:営業|対応|での(?:ヒアリング|営業|対応)|をかけ|かけ)|架電|テレアポ|コールセンター|インバウンド対応")
MANUAL_ONLY = re.compile(r"(?:ツール|自動化|プログラム|マクロ|AI)(?:の)?(?:使用|利用)?(?:は)?(?:禁止|不可|NG|厳禁)|手入力(?:のみ|で(?:お願い|行って))|"
                         r"目視(?:のみ|で(?:全て|すべて))|コピー(?:&|＆|・)?ペースト(?:禁止|不可|NG)")
TOS_SCRAPE = re.compile(r"(?:スクレイピング|自動取得|クローリング)[^。\n]{0,20}(?:禁止|不可|規約違反)|"
                        r"ログイン(?:が必要な|して)[^。\n]{0,20}(?:取得|収集|スクレイピング)")
# SNS outreach by hand (DM / follow / like / comment sending): automating it breaks the SNS's terms of use,
# doing it by hand is pure manual repetition -> never Auto
SNS_OUTREACH = re.compile(r"(?:DM|ダイレクトメッセージ)(?:の)?(?:送信|送付|配信|作業)|(?:フォロー|いいね|コメント)(?:周り|回り|作業|送信|返し)|"
                          r"(?:Instagram|インスタ|TikTok|Twitter|X|Threads|SNS)[^。\n]{0,15}(?:DM|フォロー|いいね)|"
                          r"(?:問い合わせ|お問合せ|お問い合わせ)フォーム(?:から|へ|に)[^。\n]{0,10}(?:営業|送信)|フォーム営業")
# Open-ended roles (staff / assistant / secretary, hours per week or month): paid for time, not a deliverable,
# so the Auto "check only" minutes do not apply
ROLE_RE = re.compile(r"スタッフ募集|アシスタント|秘書|サポート(?:募集|スタッフ|メンバー)|メンバー募集|パートナー募集|"
                     r"事務(?:作業|サポート|スタッフ|担当)|週\s*\d+\s*(?:〜|~|-)?\s*\d*\s*時間|月\s*\d+\s*時間|新メンバー")
# Work that needs the user's attention during the day (penalised in priority, not excluded)
TIED_DOWN = re.compile(r"即レス|常時(?:連絡|対応|稼働)|リアルタイム(?:で)?(?:対応|連絡)|即日(?:返信|対応)|平日\s*\d+\s*時|"
                       r"日中(?:の|に)?(?:連絡|対応|稼働)|毎日(?:の)?(?:定例|ミーティング|朝会)|定例(?:会議|ミーティング)|長時間の?(?:Zoom|通話|会議)")
ADDITIONAL_HIRING = re.compile(r"追加募集|継続募集|随時募集|複数名(?:採用|契約|募集)|人数(?:に)?(?:関わらず|を超えて|制限なし)|"
                               r"上限(?:なし|はありません)|何名でも|定員(?:なし|に達しても)|募集人数(?:以上|を超えて)")
# AI-assisted production (images, slides, simple creatives, AI-drafted content): Auto when the posting is in the
# AI-BPO group (tier E) or explicitly allows AI generation - not limited to text work
AI_CREATIVE = re.compile(r"画像生成|AI画像|AIイラスト|生成AI|AI(?:で|を(?:使|活用|用い|利用))|AIツール|ChatGPT|Gemini|Claude|"
                         r"Canva|キャンバ|Midjourney|Stable\s?Diffusion|Firefly|動画生成|AI動画|サムネイル|バナー|"
                         r"スライド|投稿画像|インスタ(?:投稿|画像)|LP(?:作成|制作)|ノーコード|"
                         r"投稿(?:作成|文)|ポスト(?:作成|文)|記事(?:作成|執筆)|原稿|台本|文章作成|ライティング|"
                         r"コンテンツ(?:作成|制作)|画像(?:作成|制作)|動画(?:作成|制作|編集)|議事録|翻訳|文字起こし")
CREATIVE_OUT = re.compile(r"画像|イラスト|Canva|キャンバ|スライド|バナー|サムネ|動画|図解|インフォグラフィック")
# exclusions for AI-assisted production (tiers D / E): the user would have to pay, appear, or do it by hand
PAID_TOOL = re.compile(r"(?:Canva\s?Pro|Midjourney|Adobe|Photoshop|Illustrator|Premiere|After\s?Effects|有料(?:版|プラン|ツール|アカウント))"
                       r"[^。\n]{0,15}(?:必須|が必要|をお持ち|契約(?:して|が必要|済み)|ご用意|加入)")
SELF_APPEARANCE = re.compile(r"顔出し(?!(?:は)?(?:不要|なし|無し|NG|ＮＧ|しない|不可|一切))|"
                             r"出演(?:していただ|をお願い|者(?:を)?募集|いただける|が必要|できる方)|自撮り|声出し|音声収録|"
                             r"ナレーション(?:収録|録音)|ライブ配信(?:していただ|をお願い|に出演|を行って)|"
                             r"ご自身(?:で|の)?(?:撮影|写真|動画|声)|(?:撮影|収録)(?:していただ|をお願い|が必要|できる方)")
MEETING = re.compile(r"面談|面接|打ち合わせ|打合せ|ミーティング|ZOOM|Zoom|zoom|お話|説明")


def self_appearance(text):
    """The user must appear / take photos / record their voice for the deliverable (a face-on video meeting is
    the existing interview risk, not this)."""
    return any(not MEETING.search(text[max(0, m.start() - 20):m.end() + 20]) for m in SELF_APPEARANCE.finditer(text))


AI_GEN_BAN = re.compile(r"(?:AI|生成AI)(?:生成|画像|イラスト|で(?:の)?(?:生成|作成))[^。\n]{0,8}(?:禁止|不可|NG|ＮＧ|お断り|不可能)|"
                        r"手描き(?:のみ|限定)|AI(?:の)?(?:使用|利用)(?:は)?(?:禁止|不可|NG)")


def auto_able(r):
    """Deterministic Auto signal: the work type can be done by AI / scripts / rules with a final check.
    Backlog rows carry no text: their flag was stored when they were scouted."""
    if "desc" not in r:
        return bool(r.get("auto_able"))
    text = r["title"] + "\n" + r["desc"][:3000]
    # outside AI-BPO, only a non-text deliverable made with explicitly allowed AI counts (AI writing is unchanged)
    creative = bool(AI_CREATIVE.search(text)) and (
        "E" in r.get("tiers", []) or (r.get("ai_policy") == "A" and bool(CREATIVE_OUT.search(text))))
    return bool(AUTO_RE.search(text) or creative) and not MANUAL_ONLY.search(text) and not TOS_SCRAPE.search(text) \
        and not SNS_OUTREACH.search(text) and not AI_GEN_BAN.search(text) \
        and r.get("ai_policy") != "D"


def _text_flags(r):
    """Text-derived ranking flags kept on backlog rows (which do not store the posting text)."""
    if "desc" not in r:
        return {"auto_able": bool(r.get("auto_able")), "tied_down": bool(r.get("tied_down")),
                "role_like": bool(r.get("role_like"))}
    return {"auto_able": auto_able(r), "tied_down": bool(TIED_DOWN.search(r["title"] + r["desc"][:3000])),
            "role_like": bool(ROLE_RE.search(r["title"] + r["desc"][:1500]))}


def lane_guess(r, hits):
    """Pre-Claude lane (only for ranking and run metrics; Claude's first pass decides the real lane)."""
    if hits and PRO_RE.search(r["title"] + r["desc"][:3000]):
        return "Professional"
    return "Auto" if auto_able(r) else ("Professional" if hits else "Other")


EXCLUDE_RISK = {"勧誘兆候", "同一文面を複数アカウントが投稿", "購入/費用要求", "面談必須（募集文に明記）"}


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
    auto = auto_able(r)
    if "C" in tiers and tiers <= {"C"} and not hits and not auto:
        reasons.append("専門キーワードのみ一致・プロフィール接点なし")
    if "D" in tiers and tiers <= {"C", "D"} and not hits and not auto:
        reasons.append("データ・調査系だがAuto処理の手掛かりなし・プロフィール接点なし")
    if "E" in tiers and tiers <= {"C", "D", "E"} and not hits and not auto:
        reasons.append("AI-BPOだがAI・自動処理で本人作業を減らせる手掛かりなし・プロフィール接点なし")
    added = tiers & {"D", "E"}  # search entries added 2026-10-03: data / research and AI-BPO
    if PHONE_WORK.search(text):
        reasons.append("電話対応・架電あり（本人拘束）")
    if added and MANUAL_ONLY.search(text):
        reasons.append("手作業指定・ツール使用不可（Auto不可）")
    if TOS_SCRAPE.search(text):
        reasons.append("規約上許されない自動取得")
    if added and SNS_OUTREACH.search(text):
        reasons.append("SNS・フォーム経由の送信作業（自動化は規約違反・手作業の反復）")
    if added and AI_GEN_BAN.search(text):
        reasons.append("AI生成禁止")
    if added and PAID_TOOL.search(text):
        reasons.append("有料ツールの追加契約が必要")
    if added and self_appearance(text):
        reasons.append("本人の撮影・出演・音声が必要")
    if "B" in tiers and r["ai_policy"] == "C" and not hits and pay_type != "task":
        reasons.append("AI利用条件不明・プロフィール接点なし")
    if COMMISSION.search(r["title"]):
        reasons.append("成果報酬型・営業代行（AI短縮の利益なし）")
    if pay_type in ("hourly", "fixed") and (HEAVY_COMMIT.search(r["desc"]) or HEAVY_COMMIT.search(r["title"])):
        reasons.append("稼働条件が重い")
    if (r.get("expired_on") or "9999") < today():
        reasons.append("募集期限切れ")
    if fill_risk(r):
        reasons.append("FILLED_CAPACITY（契約済み人数が募集人数以上）")
    if not reasons and low_value(r, len(hits)):
        reasons.append("低価値（推定手取/本人分が基準未満）")
    return ("RULE_REJECTED" if reasons else "PASS"), reasons, hits


def gross_of(pay):
    return pay.get("price") or pay.get("max") or pay.get("min") or 0


def _days_left(r, date):
    try:
        return (dt.date.fromisoformat(r["expired_on"]) - dt.date.fromisoformat(date)).days
    except (TypeError, ValueError, KeyError):
        return 30


def est_human_minutes(r):
    """Rough human-minutes proxy used only for pre-LLM ranking (not reported as an estimate)."""
    pay, pol = r["pay"], r.get("ai_policy")
    if pay["type"] == "task":
        return max(float(pay.get("minutes") or 5), 1.0)
    if pay["type"] == "article":
        k = (pay.get("chars") or 1000) / 1000
        return {"A": 3 + 3 * k, "B": 10 + 20 * k}.get(pol, 15 + 25 * k)
    if pay["type"] == "hourly":
        return 600.0  # hourly pay does not reward AI speed-up
    if auto_able(r) and ({"D", "E"} & set(r.get("tiers", [])) or AUTO_RE.search(r["title"])) and not r.get("role_like") \
            and not ("desc" in r and ROLE_RE.search(r["title"] + r["desc"][:1500])):
        # Auto: the user's minutes are the check after AI / script processing, not the manual work
        return max(15.0, gross_of(pay) / 3000 * 60 * 0.15)
    # fixed budget: assume the client prices ~3,000 JPY per human-equivalent hour,
    # then apply the AI reduction expected from the AI condition
    base = max(45.0, gross_of(pay) / 3000 * 60)
    return base * {"A": 0.4, "B": 0.7}.get(pol, 0.85)


def est_net(r):
    pay = r["pay"]
    g = gross_of(pay)
    if pay["type"] == "fixed" and not pay.get("min"):
        g *= 0.6  # budget ceiling only
    return g * (1 - FEE_RATE)


def accept_prob(r):
    e = r.get("entry") or {}
    if "task_entry" in e:
        t = e["task_entry"]
        return 1.0 if (t.get("num_tasks") or 0) > (t.get("num_completed_tasks") or 0) else 0.0
    pe = e.get("project_entry") or {}
    hope = pe.get("project_contract_hope_number") or 1
    apps = pe.get("num_application_conditions") or 0
    return max(0.05, min(1.0, 2 * hope / (apps + 1)))


def fill_risk(r):
    """True when the posting is effectively closed (all slots contracted / tasks used up)."""
    e = r.get("entry") or {}
    if "task_entry" in e:
        t = e["task_entry"]
        return (t.get("num_tasks") or 0) > 0 and (t.get("num_completed_tasks") or 0) >= t["num_tasks"] * 0.97
    pe = e.get("project_entry") or {}
    hope = pe.get("project_contract_hope_number") or 0  # 0 / missing = unknown -> never excluded
    if not (hope > 0 and (pe.get("num_contracts") or 0) >= hope):
        return False
    # the posting says it keeps hiring beyond the stated number: not filled
    return not ADDITIONAL_HIRING.search((r.get("title") or "") + "\n" + (r.get("desc") or ""))


def priority(r, n_hits, date, change=None):
    """Expected net JPY per human minute x acceptance x urgency x fit/tier/repeat factors."""
    ev = est_net(r) * accept_prob(r) / est_human_minutes(r)
    d = _days_left(r, date)
    urgency = 1.6 if d <= 2 else 1.25 if d <= 5 else 1.0
    tiers = set(r.get("tiers", []))
    auto = auto_able(r)
    tier_f = 1.25 if "C" in tiers and n_hits else 1.1 if "B" in tiers or ({"D", "E"} & tiers and auto) else 0.8
    ai_f = {"A": 1.3, "B": 1.0, "C": 1.0 if auto else 0.8}.get(r.get("ai_policy"), 0.5)
    repeat_f = 1 + 0.05 * min(r.get("client_open_jobs") or 1, 10)
    c = r.get("client") or {}
    risk_f = 0.7 if (c.get("averageScore") or 0) == 0 else 1.0
    s = ev * urgency * tier_f * ai_f * repeat_f * risk_f * (1 + 0.35 * min(n_hits, 3))
    if not n_hits and TARGETED.search(r.get("title", "")):
        s *= 0.3  # aimed at a demographic / owners the profile does not confirm
    if r.get("tied_down") if "desc" not in r else TIED_DOWN.search(r["title"] + r["desc"][:3000]):
        s *= 0.4  # needs the user's attention during the day
    if {"D", "E"} & tiers and EASY_BAIT.search(r.get("title", "")) and gross_of(r["pay"]) >= 50000:
        s *= 0.3  # "easy / no experience" + a large budget: typical recruitment bait, ranked low (not excluded)
    if change and change != ["バックログ"]:
        s *= 1.5  # new / changed first
    return round(s, 3)


EASY_BAIT = re.compile(r"未経験|初心者|かんたん|簡単|カンタン|スキマ|隙間|誰でも|スマホ(?:だけ|で)")
TARGETED = re.compile(r"[1-2]0代|学生|主婦|ママ|女性|オーナー|お持ちの方|住んでいる|在住|看護|保育|介護")
LOW_VALUE_EV = 8.0  # JPY per human minute (~480 JPY/h) after fee, before fit bonuses


def low_value(r, n_hits):
    ev = est_net(r) * accept_prob(r) / est_human_minutes(r)
    return ev < LOW_VALUE_EV and n_hits == 0 and (r.get("client_open_jobs") or 1) < 5


def prescore(r, hits, date=None, change=None):
    return priority(r, len(hits), date or today(), change)


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


KEY_LINE = re.compile(r"必須|応募条件|条件|資格|経験|報酬|単価|円|文字|納期|期限|AI|ＡＩ|ChatGPT|生成|禁止|NG|不可|稼働|テスト|継続|応募時|提出")


def key_excerpt(desc, limit=700):
    """Requirement/pay/AI/deadline sentences from the body, enough for Astra's source QA."""
    out, n = [], 0
    for line in re.split(r"[\n。]", desc):
        line = line.strip()
        if len(line) < 4 or not KEY_LINE.search(line):
            continue
        out.append(line[:160] + ("…" if len(line) > 160 else ""))
        n += len(out[-1]) + 3
        if n >= limit:
            out.append("（truncated=true：抜粋のみ。全文は募集ページ・reward_source_excerpt・source_questions）")
            break
    return " / ".join(out)


def job_facts(r):
    c = r["client"]
    gross = gross_of(r["pay"])
    return {
        "job_id": r["id"], "url": r["url"], "title": r["title"], "tiers": r["tiers"],
        "category_id": r["category_id"], "released_at": r.get("released_at"), "pay": r["pay"], "gross": gross,
        "net_est": round(gross * (1 - FEE_RATE)) if gross else None,
        "expired_on": r["expired_on"], "entry": r.get("entry"),
        "client": {k: c.get(k) for k in ("userId", "userDisplayName", "isIdentityVerified",
                                         "averageScore", "jobOfferAchievementCount")},
        "client_open_jobs": r.get("client_open_jobs"), "ai_policy_rule": r["ai_policy"],
        "ai_evidence": r.get("ai_evidence", []), "requirements_rule": r["requirements"],
        "risk_rule": r["risk"], "desc_hash": r["desc_hash"], "detail_fp": detail_fp(r),
        "desc_excerpt": re.sub(r"\s+", " ", r["desc"])[:300],
        "key_excerpt": key_excerpt(r["desc"]),
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


def cmd_profile_fact(a):
    """Add (or update, keyed by the fact text) a user-confirmed fact that is reused without asking again."""
    re.compile(a.question_re)
    a.job_re and re.compile(a.job_re)
    v = vault_load()
    facts = v.setdefault("profile", {}).setdefault("confirmed_facts", [])
    rec = next((f for f in facts if f.get("fact") == a.fact), None)
    if rec is None:
        rec = {"fact": a.fact}
        facts.append(rec)
    rec.update({"source": a.source, "scope": "全案件で再利用（同じ質問を本人に再確認しない）", "reuse": True,
                "question_re": a.question_re, "confirmed_at": now_iso()})
    if a.job_re:
        rec["job_re"] = a.job_re
    if a.ref:
        rec["profile_ref"] = a.ref
    vault_save(v)
    print(json.dumps({"index": facts.index(rec), **rec}, ensure_ascii=False))


# Two runs a day (RUNBOOK 2.1): 05:00 morning_full (unchanged) and 17:00 evening_delta, a light run over what
# appeared or changed since 05:00 only. Its outputs live under data/<date>/evening/ so the morning files stay.
RUN_TYPES = {"morning": "morning_full", "evening": "evening_delta"}
RUN_LIMITS = {"morning": {"cap": 60, "budget_chars": 70000},
              # sized on real data (RUNBOOK 2.1): 2026-10-03 05:19-17:00 had 32 rule-passed new postings (1,295 chars
              # each on average; the top 15 by priority = 17,416 chars); 9/30-10/3 had ~7 candidates/day posted then
              "evening": {"cap": 15, "budget_chars": 20000}}
IMPORTANT_CHANGES = {"報酬変更", "募集条件変更", "AI利用条件変更"}  # re-evaluated at 17:00; others wait for 05:00
CONTINUING = re.compile(r"継続(?:的|して|案件|依頼|発注)|長期|定期(?:的|発注)|毎月|毎週")


def run_dir(date, run="morning"):
    d = os.path.join(ROOT, "data", date)
    return d if run == "morning" else os.path.join(d, run)


def _hours(later, earlier):
    try:
        return (dt.datetime.fromisoformat(later) - dt.datetime.fromisoformat(earlier)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def delay_stats(hours):
    """n / mean / median / max of release -> Scout hours (negative or unknown values are left out)."""
    h = sorted(x for x in hours if x is not None and x >= 0)
    if not h:
        return {"n": 0}
    mid = len(h) // 2
    med = h[mid] if len(h) % 2 else (h[mid - 1] + h[mid]) / 2
    return {"n": len(h), "mean": round(sum(h) / len(h), 1), "median": round(med, 1), "max": round(h[-1], 1)}


def speed_factor(r, now):
    """17:00 ranking only: applying early matters while the posting is fresh and the slots are still open,
    and repeat work is worth more than a one-off."""
    f = 1.0
    h = _hours(now, r.get("released_at"))
    if h is not None and h <= 12 and accept_prob(r) >= 0.5:
        f *= 1.2
    if "desc" in r and CONTINUING.search(r["desc"][:3000]):
        f *= 1.1
    return f


def cmd_prepare(a):
    date = a.date
    run = getattr(a, "run", "morning")
    evening = run == "evening"
    for k, v in RUN_LIMITS[run].items():
        if getattr(a, k, None) is None:
            setattr(a, k, v)
    ddir = run_dir(date, run)
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
    counts = {"rule_rejected": 0, "unchanged_evaluated": 0, "delta": 0, "new_pass": 0, "deferred_to_morning": 0}
    pending = []
    before = {}  # index entries as they were before this run (17:00: restored for what it leaves to 05:00)
    new_hours, new_ids = [], set()
    for r in rows:
        jid = str(r["id"])
        status, reasons, hits = rule_filter(r, profile)
        prev_status = (index.get(jid) or {}).get("status")
        before[jid] = json.loads(json.dumps(index[jid])) if jid in index else None
        if before[jid] is None and jid not in master:
            new_ids.add(jid)
            new_hours.append(_hours(ts, r.get("released_at")))
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
            if evening and (old.get("status") == "ASTRA_QA_PENDING" or not IMPORTANT_CHANGES & set(why)):
                # already handed to Astra (its 06:00 QA may have used it), or a minor change: noted on the job,
                # re-evaluated by the 05:00 run as before (the index is restored below)
                old.setdefault("change_log", []).append({"at": ts, "changes": why, "run": RUN_TYPES[run],
                                                         "note": "17:00で検知・05:00で再評価"})
                counts["deferred_to_morning"] += 1
                continue
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
            pending.append((prescore(r, hits, date, why), r, hits, why or ["詳細変更"]))
            continue
        if evening and before[jid] is not None and before[jid].get("rule_fp") == fp:
            counts["unchanged_evaluated"] += 1  # handled at 05:00 and unchanged since
            continue
        if status == "RULE_REJECTED":
            ent.update({"status": "RULE_REJECTED", "reasons": reasons, "rule_fp": fp})
            counts["rule_rejected"] += 1
            continue
        ent.update({"status": "SCOUTED", "rule_fp": fp})
        if evening:
            label = ["新規"] if before[jid] is None else ["条件変更"]
        else:
            label = ["バックログ"] if prev_status == "SCOUTED" else ["新規"]
        counts["new_pass"] += label == ["新規"]
        score = prescore(r, hits, date, label)
        if evening:
            score = round(score * speed_factor(r, ts), 3)
        pending.append((score, r, hits, label))
    # backlog: rule-passed jobs not yet evaluated (public-safe, no personal data)
    backlog_path = os.path.join(STATE, "backlog.json")
    backlog = load_json(backlog_path, {})
    for score, r, hits, why in ([] if evening else pending):  # 17:00 leaves what it does not send to 05:00
        if str(r["id"]) not in master:
            backlog[str(r["id"])] = {**{k: r.get(k) for k in BACKLOG_KEYS}, "prescore": score,
                                     "fit_n": min(len(hits), 3), **_text_flags(r)}
    # 1) new / changed jobs first (by priority), 2) backlog by re-computed priority,
    # both limited by the AI budget (input chars) and the job cap.
    pending.sort(key=lambda x: -x[0])
    budget = a.budget_chars
    chosen, used = [], 0
    for p in pending:
        cost = min(len(p[1]["desc"]), a.desc_chars) + 600
        if len(chosen) >= a.cap or used + cost > budget:
            break
        if p[0] < a.min_priority and p[3] != ["新規"] and "報酬変更" not in p[3]:
            continue
        chosen.append(p)
        used += cost
    new_selected = sum(1 for c in chosen if c[3] != ["バックログ"])
    chosen_ids = {str(c[1]["id"]) for c in chosen}
    if not evening and len(chosen) < a.cap and used < budget:  # backlog refill: 05:00 only
        for b in backlog.values():  # re-rank with today's urgency
            b["prescore"] = priority(b, b.get("fit_n", 0), date)
        extra = sorted((b for k, b in backlog.items() if k not in chosen_ids and k not in master
                        and (b.get("expired_on") or "") >= date and not fill_risk(b)
                        and b["prescore"] >= a.min_priority), key=lambda b: -b["prescore"])
        for b in extra:
            if len(chosen) >= a.cap or used + a.desc_chars + 600 > budget:
                break
            r = refetch(b)
            if r is None:
                continue
            status, reasons, hits = rule_filter(r, profile)
            if status == "RULE_REJECTED":  # conditions changed since it was scouted
                index.setdefault(str(r["id"]), {}).update({"status": "RULE_REJECTED", "reasons": reasons})
                backlog.pop(str(r["id"]), None)
                continue
            chosen.append((b["prescore"], r, hits, ["バックログ"]))
            used += min(len(r["desc"]), a.desc_chars) + 600
    if evening:
        # what 17:00 does not send to Claude goes back to the 05:00 run exactly as if 17:00 had not seen it:
        # the index entry is restored (or removed), so the 05:00 delta scan lists it as new / changed again
        sent = {str(c[1]["id"]) for c in chosen}
        for _, r, _, _ in pending:
            jid = str(r["id"])
            if jid in sent:
                continue
            counts["deferred_to_morning"] += 1
            if before.get(jid) is None:
                index.pop(jid, None)
            else:
                index[jid] = before[jid]
        for r in rows:  # changes noted for 05:00 (Astra Queue jobs / minor changes)
            jid = str(r["id"])
            if jid in master and jid not in sent and before.get(jid) is not None \
                    and master[jid].get("detail_fp") != detail_fp(r):
                index[jid] = before[jid]
    for c in chosen:  # keep until merged, so an interrupted run does not lose them
        r = c[1]
        backlog.setdefault(str(r["id"]), {**{k: r.get(k) for k in BACKLOG_KEYS}, "prescore": c[0],
                                          "fit_n": min(len(c[2]), 3), **_text_flags(r)})
    backlog = {k: b for k, b in backlog.items()
               if (b.get("expired_on") or "") >= date and k not in master and not fill_risk(b)}
    # prune expired, non-evaluated index entries (closed postings never reappear in search)
    for k in [k for k, e in index.items() if (e.get("expired_on") or "9999") < date and k not in master]:
        del index[k]
    save_json(backlog_path, backlog)
    carried = list(backlog)
    import client_master
    clients = client_master.build(master, v.get("meta", {}).get("clients"))
    out = []
    for score, r, hits, why in chosen:
        out.append({
            "client_history": client_master.summary(clients, master, {"job_id": r["id"], "client": r["client"]}),
            "job_id": r["id"], "url": r["url"], "title": r["title"], "tiers": r["tiers"],
            "change": why, "priority": score, "profile_hits": hits, "pay": r["pay"],
            "net_est": round(gross_of(r["pay"]) * (1 - FEE_RATE)),
            "expired_on": r["expired_on"], "entry": r.get("entry"),
            "client": r["client"], "client_open_jobs": r.get("client_open_jobs"),
            "ai_policy_rule": r["ai_policy"], "ai_evidence": r.get("ai_evidence", []),
            "requirements_rule": r["requirements"], "risk_rule": r["risk"],
            "needs_experience": r.get("needs_experience"), "desc": r["desc"][:a.desc_chars],
        })
    save_json(os.path.join(ddir, "pending_eval.json"),
              {"date": date, "run_type": RUN_TYPES[run], "calibration": calibration(master), "jobs": out})
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
    new_entry = lambda r: set(r.get("tiers", [])) <= {"D", "E"}  # reached only via the 2026-10-03 entries
    run = {"run_at": ts, "date": date, "run_type": RUN_TYPES[run], "mode": summ.get("mode"),
           "limits": {"cap": a.cap, "budget_chars": a.budget_chars}, "listed": summ.get("listed"),
           "processed_details": summ.get("processed"), "new": summ.get("new"),
           "dedupe_skipped": (summ.get("unchanged_skipped") or 0) + counts["unchanged_evaluated"],
           "rule_rejected": counts["rule_rejected"], "delta_reeval": counts["delta"],
           "changed": counts["delta"], "new_rule_passed": counts["new_pass"],
           "claude_eval_requested": len(out), "eval_from_new_or_changed": new_selected,
           "eval_from_backlog": len(out) - new_selected, "backlog_scouted": len(carried),
           "closed": closed, "errors": summ.get("errors", []),
           "est_ai_usage": {"eval_jobs": len(out),
                            "eval_input_chars": sum(len(x["desc"]) + 600 for x in out)},
           # pre-Claude mix of what was sent (rule estimates; Claude decides the real lane / tier)
           "candidate_mix": candidate_mix(chosen),
           "deferred_to_morning": counts["deferred_to_morning"],
           # posting (last_released_at) -> this run, for postings first seen by this run
           "detect_delay_h": delay_stats(new_hours),
           "new_entry": {"listed_only": summ.get("listed_new_entry_only"), "listed_ai_bpo_only": summ.get("listed_ai_bpo_only"),
                         "new": sum(1 for r in rows if str(r["id"]) in new_ids and new_entry(r)),
                         "sent_to_claude": sum(1 for c in chosen if new_entry(c[1])),
                         "ai_bpo_sent": sum(1 for c in chosen if "E" in c[1].get("tiers", []))}}
    save_json(os.path.join(ddir, "run.json"), run)
    print(json.dumps(run, ensure_ascii=False))


def candidate_mix(chosen):
    mix = {"Professional": 0, "Auto": 0, "Other": 0, "main_net_1000": 0, "micro": 0, "from_data_categories": 0}
    for _, r, hits, _ in chosen:
        mix[lane_guess(r, hits)] += 1
        mix["main_net_1000" if est_net(r) >= 1000 else "micro"] += 1
        mix["from_data_categories"] += "D" in r.get("tiers", [])
    return mix


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
    run_slot = getattr(a, "run", "morning")
    ddir = run_dir(a.date, run_slot)
    run_type = RUN_TYPES[run_slot]
    evals = load_json(a.evals, [])
    rows = {str(json.loads(l)["id"]): json.loads(l)
            for l in open(os.path.join(ddir, "pending_rows.jsonl"), encoding="utf-8")}
    index = load_json(os.path.join(STATE, "index.json"), {})
    v = vault_load()
    master = v["master"]
    queued = rejected = candidates = 0
    added_ids, lanes = [], collections.Counter()
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
        job["eval"]["run_type"] = run_type
        lanes[e.get("lane") if e.get("lane") in LANES else "未記入"] += 1
        job.setdefault("actual", {k: None for k in ACTUAL_FIELDS})
        if job.get("status") not in CLAUDE_OWNED and job.get("status") is not None:
            continue
        block = queue_block_reason(job, e)
        if e.get("verdict") in ("候補", "要確認") and block:
            set_status(job, "CLAUDE_REJECTED", "claude", "Queue除外: " + block)
            rejected += 1
        elif e.get("verdict") in ("候補", "要確認"):
            candidates += e.get("verdict") == "候補"
            if job.get("status") != "ASTRA_QA_PENDING":
                set_status(job, "CLAUDE_CANDIDATE", "claude", e.get("verdict"))
                set_status(job, "ASTRA_QA_PENDING", "claude", run_type)
                if not job.get("application"):
                    job["pre_draft_due"] = True  # application.py drafts it before Astra's QA (RUNBOOK 5.7)
                # which run put it in the Astra Queue (Astra's same-day QA of the 17:00 additions keys on this)
                job["queued_run"] = f"{a.date} {run_type}"
                job["queued_at"] = now_iso()
                added_ids.append(jid)
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
    upd = load_json(os.path.join(ddir, "updates_summary.json"), {})
    added = [master[j] for j in added_ids]
    tiers = collections.Counter(job_tier(j).split("（")[0] for j in added)
    run.update({"run_type": run_type, "claude_evaluated": len(evals), "claude_candidates": candidates,
                "claude_needs_check": queued - candidates, "claude_rejected": rejected,
                "astra_queue_added": len(added), "astra_queue_requeued": queued - len(added),
                "lane_mix": dict(lanes), "tier_mix": {k: tiers.get(k, 0) for k in ("主力", "マイクロ", "基準外", "UNKNOWN")},
                # posting -> entering the Astra Queue (what decides how early Astra can QA it)
                "queue_delay_h": delay_stats([_hours(j["queued_at"], j.get("released_at")) for j in added]),
                "astra_queue_added_new_entry": sum(1 for j in added if set(j.get("tiers") or []) <= {"D", "E"}), "astra_pass": upd.get("astra_pass", 0),
                "astra_reject": upd.get("astra_reject", 0), "need_user": upd.get("need_user", 0),
                "scout_misses_reported": upd.get("scout_miss", 0),
                "astra_queue_total": sum(1 for j in master.values() if j.get("status") == "ASTRA_QA_PENDING")})
    save_json(run_path, run)
    runs_path = os.path.join(STATE, "runs.jsonl")
    runs = [json.loads(l) for l in open(runs_path, encoding="utf-8")] if os.path.exists(runs_path) else []
    runs = [x for x in runs if x.get("run_at") != run.get("run_at")] + [run]
    with open(runs_path, "w", encoding="utf-8") as fo:
        fo.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in runs)
    print(json.dumps(run, ensure_ascii=False))


def queue_block_reason(job, e):
    """Code-checkable reasons not to send a Claude candidate to Astra."""
    if (job.get("expired_on") or "9999") < today():
        return "募集終了"
    if e.get("ai_condition") == "D":
        return "AI利用禁止"
    if e.get("fit") == "不適合":
        return "必須条件不一致"
    if str(e.get("client_risk") or "").startswith("高") and e.get("classification") != "C":
        return "高リスク"
    return ""


def _fmt_list(x):
    return " / ".join(map(str, x)) if isinstance(x, list) else ("" if x is None else str(x))


MASTER_COLS = [
    ("job_id", lambda j: j["job_id"]), ("status", lambda j: j.get("status")),
    ("verdict", lambda j: j.get("eval", {}).get("verdict")),
    ("classification", lambda j: j.get("eval", {}).get("classification")),
    ("lane", lambda j: _lane(j)),
    ("title", lambda j: j["title"]), ("url", lambda j: j["url"]),
    ("claude_reason", lambda j: j.get("eval", {}).get("reason")),
    ("gross_jpy", lambda j: j.get("gross")), ("net_est_jpy", lambda j: j.get("net_est")),
    ("expired_on", lambda j: j.get("expired_on")),
    ("ai_condition", lambda j: j.get("eval", {}).get("ai_condition")),
    ("fit", lambda j: j.get("eval", {}).get("fit")),
    ("ai_completion", lambda j: j.get("eval", {}).get("ai_completion")),
    ("human_minutes_est", lambda j: j.get("eval", {}).get("human_minutes")),
    ("net_per_human_min", lambda j: _net_per_min(j)),
    ("hourly_est", lambda j: j.get("eval", {}).get("est_hourly")),
    ("repeatability", lambda j: j.get("eval", {}).get("repeatability")),
    ("client_risk", lambda j: j.get("eval", {}).get("client_risk")),
    ("client", lambda j: j["client"].get("userDisplayName")),
    ("client_score", lambda j: j["client"].get("averageScore")),
    ("client_verified", lambda j: j["client"].get("isIdentityVerified")),
    ("first_seen", lambda j: j.get("first_seen")),
    ("status_updated", lambda j: (j.get("status_history") or [{}])[-1].get("at")),
    ("astra_verdict", lambda j: j.get("astra", {}).get("astra_verdict")),
    ("astra_reason", lambda j: j.get("astra", {}).get("astra_reason")),
    ("need_user", lambda j: j.get("astra", {}).get("need_user")),
    ("next_action", lambda j: j.get("astra", {}).get("next_action")),
    ("astra_updated_at", lambda j: j.get("astra", {}).get("updated_at")),
] + [(k, (lambda k: lambda j: j.get("actual", {}).get(k))(k)) for k in ACTUAL_FIELDS] + [
    ("app_final_qa_status", lambda j: (j.get("application") or {}).get("final_qa_status")),
    ("app_user_confirmation_required", lambda j: (j.get("application") or {}).get("user_confirmation_required")),
    ("app_prep_ai_time", lambda j: (j.get("application") or {}).get("application_preparation_ai_time")),
    ("app_user_confirmed", lambda j: (j.get("application") or {}).get("user_confirmed")),
    ("app_applied_at", lambda j: (j.get("application") or {}).get("applied_at")),
    ("app_actual_net_per_human_min", lambda j: __import__("application").realized(j).get("net_per_min")),
    # Worker (accepted jobs): Astra reads the deliverable link here, no Drive search needed
    ("worker_status", lambda j: (j.get("worker") or {}).get("status")),
    ("worker_astra_qa", lambda j: ((j.get("worker") or {}).get("astra_qa") or {}).get("result")),
    ("delivery_artifact_url", lambda j: ((j.get("worker") or {}).get("delivery") or {}).get("url")),
    ("delivery_artifact_type", lambda j: ((j.get("worker") or {}).get("delivery") or {}).get("type_label")),
    ("delivery_deadline", lambda j: ((j.get("worker") or {}).get("delivery") or {}).get("deadline")),
    ("ready_to_deliver_at", lambda j: ((j.get("worker") or {}).get("delivery") or {}).get("verified_at")),
    ("client_id", lambda j: (j.get("client") or {}).get("userId")),
    ("client_history", lambda j: _client_history(j)),
]

def _lane(j):
    e = j.get("eval") or {}
    return e.get("lane") or {"A": "Auto", "B": "Auto", "C": "Professional"}.get(e.get("classification"))


def _net_per_min(j):
    """Expected net JPY per human minute (evaluation estimate; the Manual Review value when present)."""
    e = j.get("eval") or {}
    if e.get("net_per_human_min") is not None:
        return e["net_per_human_min"]
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(e.get("human_minutes") or ""))]
    return round(j["net_est"] / max(nums), 1) if j.get("net_est") and nums and max(nums) > 0 else None


MAIN_NET_MIN = 1000     # 主力: expected net JPY per job
MICRO_AI_MIN = 80       # マイクロ: AI completion (%, lower bound of the estimate) ...
MICRO_NPM_MIN = 30      # ... and expected net JPY per human minute


def job_tier(j):
    """主力 (net >= 1,000 JPY) / マイクロ (under 1,000 JPY but high AI completion and net per human minute)."""
    app = j.get("application") or {}
    rs = (j.get("reward_check") or {}).get("reward_struct") or {}
    if rs.get("reward_status") in ("AMBIGUOUS", "SOURCE_INCOMPLETE"):
        return "UNKNOWN（今回適用報酬が不明）"
    if app and app.get("actual_reward") is None and str(app.get("reward_evidence", "")).startswith("UNKNOWN"):
        return "UNKNOWN（報酬不明）"
    net = app.get("actual_net") if app.get("actual_reward") is not None else j.get("net_est")
    if rs.get("applicable_reward") is not None:  # this application's (initial) reward, not the later one
        net = round(rs["applicable_reward"] * (1 - FEE_RATE))
    if net is None:
        return "UNKNOWN（報酬不明）"
    if net >= MAIN_NET_MIN:
        return "主力"
    ai = re.findall(r"\d+", str((j.get("eval") or {}).get("ai_completion") or ""))
    npm = _applicable_net_per_min(j)
    if ai and int(ai[0]) >= MICRO_AI_MIN and npm is not None and npm >= MICRO_NPM_MIN:
        return "マイクロ"
    return "基準外（1,000円未満・AI完結率または手取り/分が基準未満）"


def _applicable_net_per_min(j):
    """Expected net per human minute on this application's reward (initial / trial when the posting has one)."""
    rs = (j.get("reward_check") or {}).get("reward_struct") or {}
    if rs.get("applicable_reward") is None:
        return None if rs.get("reward_status") in ("AMBIGUOUS", "SOURCE_INCOMPLETE") else _net_per_min(j)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str((j.get("eval") or {}).get("human_minutes") or ""))]
    return round(rs["applicable_reward"] * (1 - FEE_RATE) / max(nums), 1) if nums and max(nums) > 0 else None


def _rs(j, k):
    rs = (j.get("reward_check") or {}).get("reward_struct")
    return "（原文未確認）" if rs is None and k == "reward_status" else (rs or {}).get(k)


def _source_questions(j):
    q = (j.get("reward_check") or {}).get("questions")
    return "\n".join(q["lines"]) if q else ""


def _questions_status(j):
    q = (j.get("reward_check") or {}).get("questions")
    if not q:
        return "UNCHECKED（原文未確認：設問の有無は不明）"
    return q["status"] + (f"：{q['reason']}" if q.get("reason") else "")


def _client_facts_col(j):
    import client_master
    import source_facts as SF
    clients = _CLIENT_CTX.get("clients") or {}
    return SF.client_facts_text(SF.client_facts(j, clients.get(client_master.client_id(j))))


def _provenance(j):
    p = dict(j.get("provenance") or {})
    p.pop("drive", None)
    return json.dumps(p, ensure_ascii=False, separators=(",", ":")) if p else ""


def _clip(n):
    return lambda v: v if not isinstance(v, str) or len(v) <= n else v[:n - 1] + "…"


# Drive Job Master = operational index (what Astra and the user check day to day). The source of truth
# is the vault; every column of MASTER_COLS stays in out/job_master_full.csv and `job-detail --id` shows
# the whole vault record. Long texts (reasons, client notes, drafts, actual logs) are not on Drive.
DRIVE_MASTER_COLS = {  # column -> display transform (None = as is)
    "job_id": None, "status": None, "lane": None, "classification": None, "title": _clip(60), "url": None,
    "gross_jpy": None, "net_est_jpy": None, "expired_on": None, "verdict": None,
    "ai_completion": _clip(30), "human_minutes_est": _clip(30), "net_per_human_min": None,
    "astra_verdict": None, "need_user": None, "next_action": _clip(60), "status_updated": None,
    "app_final_qa_status": None, "app_user_confirmation_required": None, "app_actual_net_per_human_min": None,
    "worker_status": None, "worker_astra_qa": None, "delivery_artifact_url": None,
    "delivery_artifact_type": None, "delivery_deadline": None, "ready_to_deliver_at": None,
}


def drive_master_cols():
    by = dict(MASTER_COLS)
    return [(c, (lambda fn, t: (lambda j: t(fn(j))) if t else fn)(by[c], t)) for c, t in DRIVE_MASTER_COLS.items()]


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
                               f"AI記載:{_fmt_list(j.get('ai_evidence'))[:120]}"),
    ("key_excerpt", lambda j: j.get("key_excerpt") or j.get("desc_excerpt", "")),
    ("first_seen", lambda j: j.get("first_seen")),
    # Manual Review Queue (URLs the user gave Astra): Claude's first-pass triage for Astra's second pass
    ("source", lambda j: "manual(" + j["manual_review"]["requested_by"] + ")"
     + ("・本人応募意向：" + j["manual_review"]["user_intent"] if j["manual_review"].get("user_intent") else "")
     if j.get("manual_review") else "scout"),
    ("lane", lambda j: j["eval"].get("lane")),
    ("claude_triage", lambda j: j["eval"].get("triage")),
    ("net_per_human_min", lambda j: j["eval"].get("net_per_human_min")),
    ("confirm_items", lambda j: _fmt_list(j["eval"].get("confirm_items"))),
    ("client_history", lambda j: _client_history(j)),
    # Application draft made before Astra's QA (no Astra verdict needed; never READY_TO_APPLY at this stage)
    ("application_draft", lambda j: _app_field(j, "application_draft")),
    ("application_questions", lambda j: _app_field(j, "application_questions")),
    ("application_answers", lambda j: _app_field(j, "application_answers")),
    ("facts_used", lambda j: _app_field(j, "facts_used")),
    ("unverified_facts", lambda j: _app_field(j, "unverified_facts")),
    ("final_qa_status", lambda j: _app_field(j, "final_qa_status")),
    ("user_confirmation_required", lambda j: _app_field(j, "user_confirmation_required")),
    ("draft_next_action", lambda j: _app_field(j, "next_action")),
    ("claude_qa_result", lambda j: _qa_result(j)),
    ("tier", lambda j: job_tier(j)),
    # source-backed facts (RUNBOOK 4.7): questions and rewards as the posting states them, with their status
    ("application_questions_status", _questions_status),
    ("source_questions", _source_questions),
    ("reward_status", lambda j: _rs(j, "reward_status")),
    ("applicable_reward", lambda j: _rs(j, "applicable_reward")),
    ("initial_reward", lambda j: _rs(j, "initial_reward")),
    ("ongoing_reward", lambda j: _rs(j, "ongoing_reward")),
    ("reward_basis", lambda j: _rs(j, "reward_basis")),
    ("reward_source_excerpt", lambda j: _rs(j, "reward_source_excerpt")),
    ("applicable_net_per_human_min", _applicable_net_per_min),
    ("client_facts", _client_facts_col),
    ("provenance", _provenance),
    # which Scout run queued it (05:00 morning_full / 17:00 evening_delta) and when: Astra's same-day QA of the
    # 17:00 additions selects `scout_run` = "<today> evening_delta"; rows already in the Queue keep their run
    ("scout_run", lambda j: _queued(j)[0]),
    ("queued_at", lambda j: _queued(j)[1]),
    ("released_at", lambda j: j.get("released_at") or ""),
    ("condition_change", lambda j: _condition_change(j)),
    ("truncated", lambda j: ""),  # filled by export when the Drive copy had to drop low-priority text
]


def _queued(j):
    """(run, time) the job entered the Astra Queue; jobs queued before 2026-10-03 carry no run tag and were all
    queued by the 05:00 run (the only Scout run then)."""
    if j.get("queued_run"):
        return j["queued_run"], j.get("queued_at", "")
    at = next((h["at"] for h in reversed(j.get("status_history", [])) if h["status"] == "ASTRA_QA_PENDING"), "")
    return (f"{at[:10]} morning_full" if at else ""), at


def _condition_change(j):
    """Changes Scout saw after the job entered the Astra Queue (not re-evaluated until the next 05:00 run)."""
    since = _queued(j)[1]
    ch = [c for c in j.get("change_log", []) if c.get("at", "") >= since]
    if not ch:
        return ""
    c = ch[-1]
    return f"{c['at']} {'/'.join(c.get('changes') or ['詳細変更'])}（{c.get('note', '')}）"
# Dropped first (blanked, and named in `truncated`) when the Astra Queue exceeds its Drive budget; the
# questions, this application's reward and unverified facts are never among them.
QUEUE_LOW_PRIORITY = ["ai_steps", "human_steps", "hourly_est", "repeatability", "source_check", "pay_detail",
                      "user_questions", "profile_link", "key_excerpt", "claude_reason", "requirements", "provenance"]


def _qa_result(j):
    """Claude QA of the pre-Astra draft in one cell: PASS / FLAGGED：<what Astra should look at> / NO_DRAFT."""
    app = j.get("application")
    if not app:
        return "NO_DRAFT"
    if app.get("final_qa_status") == "CLAUDE_QA_PASSED":
        return "PASS"
    return "FLAGGED：" + str(app.get("next_action", "")).split("確認事項：", 1)[-1]


def _app_field(j, k):
    app = j.get("application")
    if not app:
        return "NO_DRAFT" if k == "final_qa_status" else ""
    import application
    if k == "application_answers":
        return application._pairs(app)
    if k == "facts_used":
        return application._facts(app)
    v = app.get(k)
    return _fmt_list(v) if isinstance(v, list) else ("" if v is None else v)


_CLIENT_CTX = {}  # set by export(): (clients, master) for the client_history column


def _client_history(j):
    import client_master
    if not _CLIENT_CTX:
        return ""
    return client_master.summary(_CLIENT_CTX["clients"], _CLIENT_CTX["master"], j)


def _write_csv(path, cols, jobs):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in cols])
        for j in jobs:
            w.writerow(["" if (v := fn(j)) is None else v for _, fn in cols])


QUEUE_CRITICAL = ["application_questions", "application_answers", "application_questions_status", "source_questions",
                  "applicable_reward", "initial_reward", "ongoing_reward", "reward_status", "reward_source_excerpt",
                  "unverified_facts", "user_confirmation_required", "claude_qa_result", "application_draft"]


def write_astra_queue(path, queue, budget=None):
    """Astra Queue CSV within its Drive budget. When too large, whole low-priority columns are blanked (never
    cut mid-text) and named in `truncated`; the critical columns are checked to be complete afterwards."""
    budget = budget or DRIVE_BUDGET_BY_FILE.get("astra_queue", DRIVE_BUDGET)
    cols = [c for c, _ in QUEUE_COLS]
    rows = [{c: ("" if (v := fn(j)) is None else v) for c, fn in QUEUE_COLS} for j in queue]
    full = [dict(r) for r in rows]
    dropped = []

    def dump():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
        return buf.getvalue()
    text = dump()
    for c in QUEUE_LOW_PRIORITY:
        if len(text.encode("utf-8")) <= budget:
            break
        dropped.append(c)
        for r in rows:
            r[c] = ""
            r["truncated"] = "truncated=true：Drive容量のため省略した列＝" + ",".join(dropped) + "（全文はVault / job-detail）"
        text = dump()
    lost = sorted({c for r, f in zip(rows, full) for c in QUEUE_CRITICAL if str(r[c]) != str(f[c])})
    for j in queue:
        j.setdefault("provenance", {})["drive"] = {"at": now_iso(), "dropped_columns": dropped,
                                                   "critical_intact": not lost, "bytes": len(text.encode("utf-8"))}
    if lost:
        print(f"WARNING: astra_queue.csv lost critical columns {lost}", file=sys.stderr)
    open(path, "w", encoding="utf-8", newline="").write(text)
    return dropped


# Drive copies are uploaded by pasting the CSV into one tool call, so they must stay small.
# The full master (incl. Claude/rule rejections) stays local in out/job_master_full.csv and in the vault.
DRIVE_BUDGET = 50000
# The Astra Queue also carries each job's application draft (RUNBOOK 5), so it gets a larger budget.
DRIVE_BUDGET_BY_FILE = {"astra_queue": 80000}
DRIVE_WARN_RATIO = 0.8  # drive-status flags a file as near_budget above this share
DRIVE_FILES = {"job_master": "job_master.csv", "astra_queue": "astra_queue.csv",
               "application_queue": "application_queue.csv"}
DRIVE_MASTER_HIDDEN = {"CLOSED", "CLAUDE_REJECTED", "RULE_REJECTED"}
REJECT_KEEP_DAYS = 1  # today and yesterday


def _in_drive_master(j):
    st = j.get("status")
    if st in DRIVE_MASTER_HIDDEN:
        return False
    if st == "ASTRA_REJECT":  # recent rejections only (older ones: job_master_full.csv)
        at = ((j.get("status_history") or [{}])[-1].get("at") or "")[:10]
        cut = (dt.datetime.now(JST).date() - dt.timedelta(days=REJECT_KEEP_DAYS)).isoformat()
        return at >= cut
    return True


def export(master, meta):
    os.makedirs(OUT, exist_ok=True)
    import client_master
    clients = client_master.build(master, meta.get("clients"))
    _CLIENT_CTX.update(clients=clients, master=master)
    save_json(os.path.join(OUT, "client_master.json"), clients)  # local only (personal relationship data)
    order = {s: i for i, s in enumerate(["ASTRA_QA_PENDING", "NEED_USER", "ASTRA_PASS", "READY_TO_APPLY",
                                          "APPLIED", "ACCEPTED", "IN_PROGRESS", "READY_FOR_QA",
                                          "READY_TO_DELIVER", "DELIVERED", "PAID"])}
    jobs = sorted(master.values(), key=lambda j: (order.get(j.get("status"), 99), -(j.get("gross") or 0)))
    active = [j for j in jobs if j.get("status") not in ("CLOSED",)]
    _write_csv(os.path.join(OUT, "job_master_full.csv"), MASTER_COLS, active)  # local only
    drive_view = [j for j in active if _in_drive_master(j)]
    _write_csv(os.path.join(OUT, "job_master.csv"), drive_master_cols(), drive_view)  # operational index
    queue = [j for j in jobs if j.get("status") == "ASTRA_QA_PENDING"]
    write_astra_queue(os.path.join(OUT, "astra_queue.csv"), queue)
    save_json(os.path.join(OUT, "astra_queue.json"),
              {"generated_at": now_iso(), "count": len(queue),
               "jobs": [{c: fn(j) for c, fn in QUEUE_COLS} for j in queue]})
    import application
    n_app = application.export_queue(master)
    stamp = dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    prev = load_json(os.path.join(OUT, "sync_manifest.json"), {})
    save_json(os.path.join(OUT, "sync_manifest.json"),
              {"generated_at": now_iso(), "drive": meta.get("drive", {}),
               "titles": {"job_master": f"CW Scout - Job Master｜{stamp}",
                          "astra_queue": f"CW Scout - Astra Queue｜{stamp}",
                          "application_queue": f"CW Scout - Application Queue｜{stamp}"},
               "files": {"job_master": "job_master.csv", "astra_queue": "astra_queue.csv",
                         "application_queue": "application_queue.csv"},
               "counts": {"master_active": len(active), "master_drive": len(drive_view),
                          "astra_queue": len(queue), "application_queue": n_app},
               "bytes": {k: os.path.getsize(os.path.join(OUT, f)) for k, f in DRIVE_FILES.items()},
               "budget_bytes": DRIVE_BUDGET, "budget_by_file": DRIVE_BUDGET_BY_FILE,
               "prev_bytes": prev.get("bytes", {}), "prev_generated_at": prev.get("generated_at")})
    print(f"exported: master={len(active)} (drive {len(drive_view)}) queue={len(queue)} applications={n_app}")
    for k, f in DRIVE_FILES.items():
        size = os.path.getsize(os.path.join(OUT, f))
        budget = DRIVE_BUDGET_BY_FILE.get(k, DRIVE_BUDGET)
        if size > budget:
            print(f"WARNING: {f} exceeds the Drive upload budget ({budget} bytes)", file=sys.stderr)
        elif size > budget * DRIVE_WARN_RATIO:
            print(f"NOTICE: {f} is {size} bytes (over {DRIVE_WARN_RATIO:.0%} of the Drive budget)", file=sys.stderr)


def cmd_export(a):
    v = vault_load()
    export(v["master"], v.get("meta", {}))


def cmd_job_detail(a):
    """The full vault record for job ids seen in the Drive index (Drive Job Master -> source of truth)."""
    master = vault_load()["master"]
    out = {i: master.get(re.sub(r"\D", "", i)) for i in a.ids.split(",") if i}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if any(x is None for x in out.values()):
        sys.exit(1)


# Application Queue fields Astra/the user may write back (final QA, confirmation, applied date)
APP_FIELDS = ["final_qa_status", "user_confirmed", "applied_at", "app_note",
              # actual PoC tracking (application side)
              "application_preparation_ai_time", "human_review_minutes"]
# Sheet column aliases: new PoC names map onto the existing actual/app fields
FIELD_ALIASES = {"gen_minutes": "application_preparation_ai_time",
                 "production_human_minutes": "actual_human_minutes",
                 "production_ai_time": "actual_ai_processing",
                 "accept_result": "result"}
RESULT_STATUS = {"withdrawn": "WITHDRAWN", "辞退": "WITHDRAWN", "accepted": "ACCEPTED", "受注": "ACCEPTED", "採用": "ACCEPTED",
                 "rejected": "NOT_SELECTED", "不採用": "NOT_SELECTED", "落選": "NOT_SELECTED"}
UPDATE_COLS = ["job_id", "astra_verdict", "astra_reason", "new_status", "need_user", "next_action",
               "updated_at", "updated_by"] + ACTUAL_FIELDS + APP_FIELDS + \
              ["production_ai_time", "production_human_minutes", "note"]
PROXY_RE = re.compile(r"転記|代理|代行|本人経由|チャットで|relay|proxy", re.I)
ASTRA_FIELDS = ["astra_verdict", "astra_reason", "need_user", "next_action", "updated_at", "updated_by"]
VERDICT_MAP = {"PASS": "ASTRA_PASS", "採用": "ASTRA_PASS", "合格": "ASTRA_PASS", "応募": "ASTRA_PASS",
               "REJECT": "ASTRA_REJECT", "不採用": "ASTRA_REJECT", "除外": "ASTRA_REJECT",
               "SKIPPED": "SKIPPED", "SKIP": "SKIPPED", "見送り": "SKIPPED", "今回見送り": "SKIPPED",
               "NEED_USER": "NEED_USER", "要確認": "NEED_USER", "HOLD": "NEED_USER"}


# Actual-PoC tracking items the Status Updates sheet must accept (an alias column counts as present)
TRACK_COLS = ["application_preparation_ai_time", "human_review_minutes", "applied_at", "result",
              "production_ai_time", "production_human_minutes", "revision_count", "actual_net_reward"]


def _su_rows(path):
    return list(csv.reader(io.StringIO(open(path, encoding="utf-8-sig").read())))


def _su_norm(c):
    c = (c or "").strip()
    if c in ("True", "TRUE"):
        return "t"
    if c in ("False", "FALSE"):
        return "f"
    try:
        return str(float(c))
    except ValueError:
        return " ".join(c.split())


def _su_same(a, b, ncols):
    """Rows of a and b hold the same values in the first ncols columns (Sheets pads/trims trailing blanks)."""
    pad = lambda r: [_su_norm(x) for x in (r + [""] * ncols)[:ncols]]
    strip = lambda rows: [r for r in rows if any(x.strip() for x in r)]
    a, b = strip(a), strip(b)
    return len(a) == len(b) and all(pad(x) == pad(y) for x, y in zip(a, b))


def cmd_su_columns(a):
    """Idempotently add missing tracking columns to Status Updates, never touching existing cells.
    Plan: --csv <current export> → writes out/status_updates_synced.csv only if columns are missing.
    Verify: add --verify <re-downloaded new sheet> and --recheck <re-downloaded current sheet>."""
    rows = _su_rows(a.csv)
    head = [h.strip() for h in rows[0]]
    present = set(head)
    missing = []
    for c in TRACK_COLS:
        names = {c, FIELD_ALIASES.get(c)} | {al for al, k in FIELD_ALIASES.items() if k == c}
        if not present & names:
            missing.append(c)
    out = os.path.join(OUT, "status_updates_synced.csv")
    if not a.verify:
        res = {"columns": len(head), "rows": len(rows) - 1, "missing": missing,
               "action": "replace" if missing else "none"}
        if missing:
            with open(out, "w", encoding="utf-8", newline="") as f:
                cw = csv.writer(f)
                cw.writerow(rows[0][:len(head)] + missing)
                for r in rows[1:]:
                    cw.writerow((r + [""] * len(head))[:len(head)] + [""] * len(missing))
            res["file"] = out
        print(json.dumps(res, ensure_ascii=False))
        return
    new, ok, why = _su_rows(a.verify), True, []
    if [h.strip() for h in new[0]][:len(head)] != head:
        ok = False; why.append("existing header changed")
    if not _su_same(rows, new, len(head)):
        ok = False; why.append("existing cells differ")
    if not set(missing) <= {h.strip() for h in new[0]}:
        ok = False; why.append("columns still missing")
    if any(any(x.strip() for x in r[len(head):]) for r in new[1:]):
        ok = False; why.append("new columns not empty")
    if a.recheck and not _su_same(rows, _su_rows(a.recheck), len(head)):
        ok = False; why.append("current sheet changed since snapshot (retry next run)")
    print(json.dumps({"ok": ok, "problems": why}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


def _truthy(x):
    return str(x).strip().lower() in ("yes", "y", "true", "1", "要", "必要", "はい")


def su_time(s):
    """Status Updates `updated_at` as a sortable "YYYY-MM-DD HH:MM" ("" when missing/unparsable)."""
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", s or "")
    if not m:
        return ""
    y, mo, d, h, mi = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d} {int(h or 0):02d}:{mi or '00'}"


def row_sig(row):
    """Signature of a Status Updates row over its non-empty cells (so adding columns does not re-apply it)."""
    return hashlib.md5(json.dumps({k: x for k, x in row.items() if x}, sort_keys=True,
                                  ensure_ascii=False).encode()).hexdigest()[:16]


def cmd_apply_updates(a):
    v = vault_load()
    master, meta = v["master"], v.setdefault("meta", {})
    index = load_json(os.path.join(STATE, "index.json"), {})
    applied = set(meta.get("applied_update_rows", []))
    batch_jobs = {j for b in meta.get("review_batches", []) for j in b["job_ids"]}
    changed = {}  # job_id -> new status, for this import only (drives the post-QA routine)
    text = open(a.csv, encoding="utf-8-sig").read()
    n_ok, errs = 0, []
    tally = {"astra_pass": 0, "astra_reject": 0, "need_user": 0, "skipped": 0, "scout_miss": 0, "other_status": 0}
    # oldest first, so the latest Astra verdict for a job is the one that stays (sheet order as tie-break)
    for row in sorted(csv.DictReader(io.StringIO(text)), key=lambda r: su_time((r.get("updated_at") or "").strip())):
        row = {k.strip(): (val or "").strip() for k, val in row.items() if k}
        for alias, k in FIELD_ALIASES.items():
            if row.get(alias) and not row.get(k):
                row[k] = row[alias]
            row.pop(alias, None)
        if not row.get("job_id"):
            continue
        # Signature over non-empty cells, so adding sheet columns does not re-apply old rows;
        # the legacy all-cells signature is still honoured for rows applied before.
        legacy = hashlib.md5(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        sig = row_sig(row)
        if sig in applied or legacy in applied:
            applied.add(sig)
            continue
        jid = re.sub(r"\D", "", row["job_id"])
        st = row.get("new_status", "").upper()
        verdict = row.get("astra_verdict", "")
        if st == "SCOUT_MISS" or verdict.upper() == "SCOUT_MISS":
            e = index.get(jid, {})
            meta.setdefault("scout_misses", []).append({
                "job_id": jid, "at": row.get("updated_at") or now_iso(), "note": row.get("note") or row.get("astra_reason"),
                "scout_state": e.get("status") or ("評価済み" if jid in master else "未取得"),
                "rule_reasons": e.get("reasons")})
            tally["scout_miss"] += 1
            applied.add(sig)
            n_ok += 1
            continue
        if st == "MANUAL_REVIEW" or verdict.upper() == "MANUAL_REVIEW":  # Astra hands a URL to Claude
            import manual_review
            if "astra" not in row.get("updated_by", "").lower():
                errs.append(f"{jid}: MANUAL_REVIEW はAstra名義の行のみ受け付ける")
                continue
            mid = manual_review.job_id_of(row.get("job_url") or row.get("note") or "") or jid
            rec, added = manual_review.enqueue(v, mid, None, "Astra", row.get("updated_at") or now_iso(),
                                               row.get("note") or row.get("astra_reason", ""))
            tally["other_status"] += added
            applied.add(sig)
            n_ok += 1
            continue
        job = master.get(jid)
        if job is None:
            errs.append(f"unknown job_id {row['job_id']}")
            continue
        # Status Updates is the Astra <-> Claude Code interface: only Astra-signed rows count
        if "astra" not in row.get("updated_by", "").lower():
            errs.append(f"{jid}: updated_by={row.get('updated_by') or '(空欄)'} はAstra名義でないため未反映")
            continue
        # a verdict relayed by the user (or written by Claude) is not Astra's own record
        if PROXY_RE.search(" ".join(row.get(k, "") for k in ("note", "astra_reason", "next_action"))):
            errs.append(f"{jid}: 転記・代理記録のAstra判定は取り込まない（Status UpdatesにAstra自身が記録した行のみ）")
            continue
        import worker  # accepted jobs: Astra's second-stage (deliverable) QA goes to the worker record
        wnote = worker.apply_astra_row(job, row)
        if wnote is not None:
            before = job.get("status")
            job.setdefault("worker", {}).setdefault("astra_rows", []).append(
                {k: row[k] for k in ("astra_verdict", "astra_reason", "new_status", "next_action", "updated_at", "note")
                 if row.get(k)})
            if job.get("status") != before:
                changed[jid] = job["status"]
            tally["other_status"] += 1
            applied.add(sig)
            n_ok += 1
            continue
        if st == "READY_TO_DELIVER":  # only set by worker-deliver once the client file is verified
            errs.append(f"{jid}: READY_TO_DELIVER は納品物の検証後にClaudeが設定するため未反映")
            continue
        st = {"ASTRA_QUEUE": "ASTRA_QA_PENDING", "SKIP": "SKIPPED", "HOLD": "NEED_USER"}.get(st, st)
        # an applied (or later) job never goes back to a pre-application verdict, whoever resends it
        if st in ("ASTRA_PASS", "ASTRA_REJECT", "NEED_USER", "SKIPPED") and job.get("status") in PROGRESSED - {"READY_TO_APPLY"}:
            errs.append(f"{jid}: {job['status']} のため {st} でステータスを戻さない")
            applied.add(sig)
            continue
        if not st:
            st = VERDICT_MAP.get(verdict.upper(), VERDICT_MAP.get(verdict, ""))
            if not st and _truthy(row.get("need_user")):
                st = "NEED_USER"
            if st and job.get("status") in PROGRESSED:  # e.g. a repeated PASS for an applied job
                errs.append(f"{jid}: {job['status']} のため判定 {verdict} でステータスを戻さない")
                st = ""
            if not st and job.get("status") == "APPLIED":
                st = RESULT_STATUS.get(row.get("result", "").lower(), "")
        if row.get("human_review_minutes") and jid in batch_jobs:  # batch total kept in meta only
            errs.append(f"{jid}: human_review_minutes はバッチ実績で記録済みのため案件別には保存しない")
            row.pop("human_review_minutes")
        before = job.get("status")
        if verdict and st in ("ASTRA_PASS", "ASTRA_REJECT", "NEED_USER", "SKIPPED"):
            job.pop("recheck_flags", None)  # Astra has re-judged: earlier re-check flags are settled
        try:
            if st:
                set_status(job, st, row.get("updated_by") or "sheet",
                           row.get("astra_reason") or row.get("note", ""))
        except ValueError as e:
            errs.append(str(e))
            continue
        astra = job.setdefault("astra", {})
        for k in ASTRA_FIELDS:
            if row.get(k):
                astra[k] = row[k]
        for k in ACTUAL_FIELDS:
            if row.get(k):
                job.setdefault("actual", {})[k] = row[k]
        if job.get("application"):
            for k in APP_FIELDS:
                if row.get(k):
                    job["application"][k] = row[k]
            if row.get("next_action"):
                job["application"]["next_action"] = row["next_action"]
        if job.get("status") != before:  # text-only corrections are not new decisions
            changed[jid] = job["status"]
            key = {"ASTRA_PASS": "astra_pass", "ASTRA_REJECT": "astra_reject", "NEED_USER": "need_user",
                   "SKIPPED": "skipped"}.get(st, "other_status")
            tally[key] += 1
        applied.add(sig)
        n_ok += 1
    meta["applied_update_rows"] = sorted(applied)
    vault_save(v)
    export(master, meta)
    ddir = os.path.join(ROOT, "data", a.date)
    prev = load_json(os.path.join(ddir, "updates_summary.json"), {})
    save_json(os.path.join(ddir, "updates_summary.json"), {k: prev.get(k, 0) + tally[k] for k in tally})
    save_json(os.path.join(ddir, "last_updates.json"), {"at": now_iso(), "changed": changed, "errors": errs})
    print(json.dumps({"applied": n_ok, "tally": tally, "errors": errs}, ensure_ascii=False))


CATCHUP_FROM = "09:00"  # a post-QA run at or after this JST time is the catch-up pass (see RUNBOOK 7.1)


def _astra_rows(path):
    """Astra-signed Status Updates rows (normalized like apply-updates), with their signatures."""
    out = []
    for r in csv.DictReader(io.StringIO(open(path, encoding="utf-8-sig").read())):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        for alias, k in FIELD_ALIASES.items():
            if r.get(alias) and not r.get(k):
                r[k] = r[alias]
            r.pop(alias, None)
        if r.get("job_id") and "astra" in r.get("updated_by", "").lower():
            out.append((r, row_sig(r)))
    return out


def _carry_over(master):
    """ASTRA_PASS jobs still without a draft whose posting showed no material change (e.g. over the draft cap)."""
    return sorted(j for j, x in master.items() if x.get("status") == "ASTRA_PASS" and not x.get("application")
                  and not (x.get("reward_check") or {}).get("changes")
                  and not ((x.get("reward_check") or {}).get("deadline") or "9999") < today())


def cmd_postqa(a):
    """Post-Astra-QA routine (06:30 JST, catch-up pass 12:30 JST): never scouts or evaluates.
    guard   : proceed only if today's 05:00 Scout run is recorded (it owns the Astra Queue hand-off);
              `mode` says whether this is the 06:30 main pass or the later catch-up pass.
    astra   : proceed only if the Status Updates CSV holds Astra-signed rows written today after that run;
              complete=false means some queued jobs are still unjudged (they stay ASTRA_QA_PENDING).
    catchup : proceed only if Astra wrote rows not imported yet (e.g. a PASS after 06:30), or drafts were
              carried over; otherwise stop without touching anything. Idempotent: rows are keyed by signature
              and jobs by job_id + current status.
    targets : ASTRA_PASS jobs without a draft: PASSes of the last apply-updates plus carried-over ones."""
    runs_path = os.path.join(STATE, "runs.jsonl")
    runs = [json.loads(l) for l in open(runs_path, encoding="utf-8")] if os.path.exists(runs_path) else []
    today_runs = [r for r in runs if r.get("date") == a.date]
    cfg = load_json(os.path.join(ROOT, "routine.json"), {})
    if a.stage in ("guard", "astra", "catchup") and cfg.get("postqa_import") == "retired":
        # Astra->Claude verdict import is retired (drafts are made at 05:00, before Astra's QA): stop, touch nothing
        print(json.dumps({"ok": False, "mode": "retired", "reason": cfg.get("reason", "postqa import retired")},
                         ensure_ascii=False))
        return
    if a.stage == "guard":
        done = bool(today_runs)
        now = a.now or dt.datetime.now(JST).strftime("%H:%M")
        print(json.dumps({"ok": done, "mode": "catchup" if now >= CATCHUP_FROM else "main",
                          "reason": "" if done else f"{a.date} のScout実行記録がないため何もしない"},
                         ensure_ascii=False))
        return
    if a.stage == "astra":
        # Proceed only on evidence that today's Astra QA ran after today's Scout hand-off:
        # Astra-signed rows dated today (and not earlier than the Scout run) in the current Status Updates.
        if not today_runs:
            print(json.dumps({"ok": False, "reason": f"{a.date} のScout実行記録がない"}, ensure_ascii=False))
            return
        scout_at = max(r.get("run_at", "") for r in today_runs)[11:16]  # HH:MM (JST)
        todays = set()
        for r, _ in _astra_rows(a.csv):
            t = su_time(r.get("updated_at", ""))
            if not t.startswith(a.date):
                continue
            if re.search(r"\d{1,2}:\d{2}", r.get("updated_at", "")) and t[11:] < scout_at:
                continue  # written before today's Astra Queue existed
            todays.add(re.sub(r"\D", "", r["job_id"]))
        pending = {j for j, x in vault_load()["master"].items() if x.get("status") == "ASTRA_QA_PENDING"}
        covered = pending & todays
        res = {"ok": bool(todays), "complete": bool(todays) and covered == pending,
               "astra_rows_today": len(todays), "pending": len(pending), "covered": len(covered),
               "reason": "" if todays else f"{a.date} {scout_at}以降のAstra名義の判定がStatus Updatesにないため何もしない"}
        print(json.dumps(res, ensure_ascii=False))
        return
    v = vault_load()
    master = v["master"]
    if a.stage == "catchup":
        applied = set(v.get("meta", {}).get("applied_update_rows", []))
        # rows apply-updates would really take: not yet imported, Astra's own (no relayed/proxy verdict), known job
        new = sorted({re.sub(r"\D", "", r["job_id"]) for r, sig in _astra_rows(a.csv) if sig not in applied
                      and not PROXY_RE.search(" ".join(r.get(k, "") for k in ("note", "astra_reason", "next_action")))
                      and (re.sub(r"\D", "", r["job_id"]) in master
                           or "MANUAL_REVIEW" in (r.get("new_status", "") + r.get("astra_verdict", "")).upper())})
        carry = _carry_over(master)
        ok = bool(new or carry)
        print(json.dumps({"ok": ok, "new_astra_rows_for": new, "carry_over": carry,
                          "pending": sum(x.get("status") == "ASTRA_QA_PENDING" for x in master.values()),
                          "reason": "" if ok else "06:30以降に新しいAstra判定がなく、応募準備の持ち越しもないため何もしない"},
                         ensure_ascii=False))
        return
    last = load_json(os.path.join(ROOT, "data", a.date, "last_updates.json"), {"changed": {}})
    expired = lambda j: ((master[j].get("reward_check") or {}).get("deadline") or master[j].get("expired_on")
                         or "9999") < today()
    targets = [j for j, st in last["changed"].items() if st == "ASTRA_PASS" and master[j].get("status") == "ASTRA_PASS"
               and not master[j].get("application") and not expired(j)]
    targets += [j for j in _carry_over(master) if j not in targets]
    print(json.dumps({"new_status_changes": len(last["changed"]), "targets": targets,
                      "action": "prepare" if targets else "none"}, ensure_ascii=False))


def cmd_metrics(a):
    """Cumulative Scout performance for Recall / Precision tracking (no personal data)."""
    runs = [json.loads(l) for l in open(os.path.join(STATE, "runs.jsonl"), encoding="utf-8")] \
        if os.path.exists(os.path.join(STATE, "runs.jsonl")) else []
    master = vault_load()["master"]
    passed = sum(1 for j in master.values() if any(h["status"] == "ASTRA_PASS" for h in j.get("status_history", [])))
    rejected = sum(1 for j in master.values() if any(h["status"] == "ASTRA_REJECT" for h in j.get("status_history", [])))
    misses = len(vault_load().get("meta", {}).get("scout_misses", []))
    keys = ["listed", "new", "changed", "dedupe_skipped", "rule_rejected", "claude_evaluated",
            "claude_candidates", "astra_queue_added", "astra_pass", "astra_reject", "scout_misses_reported"]
    tot = {k: sum((r.get(k) or 0) for r in runs) for k in keys}
    out = {"runs": len(runs), "totals": tot,
           "astra_decided": {"pass": passed, "reject": rejected},
           "precision_proxy": round(passed / (passed + rejected), 3) if passed + rejected else None,
           "recall_proxy": round(passed / (passed + misses), 3) if passed + misses else None,
           "scout_misses": misses}
    import application
    out["poc_actual"] = application.poc_summary(master)
    out["kpi"] = application.kpi(master, vault_load().get("meta", {}), runs)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def daily_metrics(date, runs, master):
    """One day of the 05:00 + 17:00 schedule vs the 05:00-only schedule (no personal data)."""
    day = [r for r in runs if r.get("date") == date]
    morning = next((r for r in day if r.get("run_type", "morning_full") == "morning_full"), {})
    evening = next((r for r in day if r.get("run_type") == "evening_delta"), {})
    # 05:00-only: a job queued at 17:00 would have entered the Queue with the next morning's run, at the same
    # time of day as today's (estimate; it assumes that run would have had room for it)
    m_at = morning.get("run_at") or f"{date}T05:15+09:00"
    next_morning = (dt.datetime.fromisoformat(m_at) + dt.timedelta(days=1)).isoformat(timespec="minutes")
    actual, only05, ev_only = [], [], collections.Counter()
    for j in master.values():
        run, at = _queued(j)
        if not run.startswith(date) or not at:
            continue
        h = _hours(at, j.get("released_at"))
        actual.append(h)
        if run.endswith("evening_delta"):
            only05.append(_hours(next_morning, j.get("released_at")))
            ev_only[job_tier(j).split("（")[0]] += 1
        else:
            only05.append(h)
    use = lambda r: (r.get("est_ai_usage") or {})
    return {"date": date, "runs": [r.get("run_type", "morning_full") for r in day],
            "queue_delay_h_actual": delay_stats(actual),
            "queue_delay_h_if_0500_only": delay_stats(only05),
            "evening_only_added": sum(ev_only.values()),
            "evening_only_high_value": {"主力": ev_only.get("主力", 0), "マイクロ": ev_only.get("マイクロ", 0)},
            "claude_usage": {"morning": {"jobs": use(morning).get("eval_jobs", 0), "chars": use(morning).get("eval_input_chars", 0)},
                             "evening_extra": {"jobs": use(evening).get("eval_jobs", 0), "chars": use(evening).get("eval_input_chars", 0)}}}


def cmd_daily_metrics(a):
    runs_path = os.path.join(STATE, "runs.jsonl")
    runs = [json.loads(l) for l in open(runs_path, encoding="utf-8")] if os.path.exists(runs_path) else []
    rec = daily_metrics(a.date, runs, vault_load()["master"])
    path = os.path.join(STATE, "daily_metrics.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8")] if os.path.exists(path) else []
    rows = [x for x in rows if x.get("date") != a.date] + [rec]
    with open(path, "w", encoding="utf-8") as fo:
        fo.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in sorted(rows, key=lambda x: x["date"]))
    print(json.dumps(rec, ensure_ascii=False))


def cmd_set_meta(a):
    v = vault_load()
    meta = v.setdefault("meta", {})
    meta.setdefault("drive", {})[a.key] = a.value
    if a.key.endswith("_sheet"):
        meta.setdefault("drive_synced", {})[a.key] = now_iso()
    vault_save(v)
    print(json.dumps(meta["drive"], ensure_ascii=False))


def cmd_drive_status(a):
    """Which Drive sheets are older than the latest local export (exit 1 if any)."""
    man = json.load(open(os.path.join(OUT, "sync_manifest.json"), encoding="utf-8"))
    synced = vault_load().get("meta", {}).get("drive_synced", {})
    keys = [k for k in (a.keys.split(",") if a.keys else DRIVE_FILES) if k]
    stale = [k for k in keys if (synced.get(k + "_sheet") or "") < man["generated_at"]]
    budget = man.get("budget_bytes", DRIVE_BUDGET)
    size, prev = man.get("bytes", {}), man.get("prev_bytes", {})
    per = man.get("budget_by_file", {})
    bud = lambda k: per.get(k, budget)
    over = [k for k, b in size.items() if b > bud(k)]
    near = [k for k, b in size.items() if bud(k) * DRIVE_WARN_RATIO < b <= bud(k)]
    print(json.dumps({"ok": not stale, "stale": stale, "over_budget": over, "near_budget": near,
                      "budget_bytes": budget, "exported_at": man["generated_at"],
                      "delta_bytes": {k: b - prev[k] for k, b in size.items() if k in prev},
                      "synced": {k: synced.get(k + "_sheet") for k in keys},
                      "bytes": man.get("bytes")}, ensure_ascii=False))
    if stale:
        sys.exit(1)


DRIVE_KINDS = {  # Drive sheet key -> title prefix in the CW Scout folder
    "job_master_sheet": "CW Scout - Job Master｜",
    "astra_queue_sheet": "CW Scout - Astra Queue｜",
    "application_queue_sheet": "CW Scout - Application Queue｜",
    "status_updates_sheet": "CW Scout - Status Updates (記入用)",
}
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def drive_resolve(files, folder, recorded, keep=None):
    """The current sheet of each kind in the CW Scout folder, from a Drive file listing.

    Sheets are replaced on every sync (new file, old one trashed), so a remembered ID is never
    taken as "the latest" by itself:
    - dated kinds (Job Master / Astra Queue / Application Queue): the newest generation time in
      the title ("｜YYYY-MM-DD HH:MM JST"), then the newest createdTime;
    - Status Updates (one fixed title): the recorded sheet while it is still in the folder (a column
      replacement becomes official only after its verification), else the newest createdTime.
    `keep` = {key: id} of a sheet just uploaded and verified: every other sheet of that kind is
    listed under `trash` (leftovers from failed runs included)."""
    keep = keep or {}
    out = {}
    for key, prefix in DRIVE_KINDS.items():
        cand = [f for f in files if f.get("title", "").startswith(prefix) and f.get("mimeType", SHEET_MIME) == SHEET_MIME
                and (not folder or f.get("parentId") in (None, folder)) and not f.get("trashed")]
        stamp = lambda f: (re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) JST", f["title"]) or [None, ""])[1]
        if key == "status_updates_sheet":
            rec = [f for f in cand if f["id"] == recorded.get(key)]
            order = sorted(cand, key=lambda f: f.get("createdTime", ""))
            latest = keep.get(key) and next((f for f in cand if f["id"] == keep[key]), None) \
                or (rec[0] if rec else (order[-1] if order else None))
        else:
            order = sorted(cand, key=lambda f: (stamp(f), f.get("createdTime", "")))
            latest = next((f for f in cand if f["id"] == keep.get(key)), None) or (order[-1] if order else None)
        out[key] = {
            "id": latest and latest["id"], "title": latest and latest["title"],
            "generated": latest and (stamp(latest) or latest.get("createdTime")),
            "recorded": recorded.get(key), "recorded_is_current": bool(latest) and recorded.get(key) == latest["id"],
            "others": [{"id": f["id"], "title": f["title"]} for f in cand if not latest or f["id"] != latest["id"]],
            "trash": [f["id"] for f in cand if key in keep and f["id"] != keep[key]],
            "missing": latest is None}
    return out


def cmd_drive_resolve(a):
    """Resolve the current Drive sheets from `search_files` output (JSON saved to files)."""
    files = []
    for path in a.listing:
        d = json.load(open(path, encoding="utf-8"))
        files += d.get("files", []) if isinstance(d, dict) else d
    meta = vault_load().get("meta", {})
    drive = meta.get("drive", {})
    keep = dict(x.split("=", 1) for x in (a.keep or []))
    bad = [k for k in keep if k not in DRIVE_KINDS]
    if bad:
        raise SystemExit(f"unknown sheet key {bad}")
    res = drive_resolve(files, drive.get("folder"), drive, keep)
    print(json.dumps({"folder": drive.get("folder"), "sheets": res,
                      "missing": [k for k, r in res.items() if r["missing"]]}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init-vault"); p.add_argument("--profile", required=True); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init_vault)
    p = sub.add_parser("show-profile"); p.set_defaults(fn=cmd_show_profile)
    p = sub.add_parser("prepare"); p.add_argument("--date", default=today())
    p.add_argument("--run", choices=list(RUN_TYPES), default="morning", help="evening: 17:00 light delta run")
    p.add_argument("--cap", type=int, default=None, help="max jobs sent to Claude (default: RUN_LIMITS by run)")
    p.add_argument("--budget-chars", type=int, default=None, help="max eval input chars per run (default: RUN_LIMITS)")
    p.add_argument("--min-priority", type=float, default=3.0, help="backlog floor (priority units)")
    p.add_argument("--desc-chars", type=int, default=1800)
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("merge"); p.add_argument("--date", default=today()); p.add_argument("--evals", required=True)
    p.add_argument("--run", choices=list(RUN_TYPES), default="morning")
    p.set_defaults(fn=cmd_merge)
    p = sub.add_parser("daily-metrics", help="05:00+17:00 vs 05:00-only for one day -> state/daily_metrics.jsonl")
    p.add_argument("--date", default=today()); p.set_defaults(fn=cmd_daily_metrics)
    p = sub.add_parser("apply-updates"); p.add_argument("--csv", required=True); p.add_argument("--date", default=today())
    p.set_defaults(fn=cmd_apply_updates)
    p = sub.add_parser("su-columns", help="add missing tracking columns to Status Updates (idempotent)")
    p.add_argument("--csv", required=True); p.add_argument("--verify"); p.add_argument("--recheck")
    p.set_defaults(fn=cmd_su_columns)
    p = sub.add_parser("postqa", help="post-Astra-QA routine helpers (no scouting)")
    p.add_argument("stage", choices=["guard", "astra", "catchup", "targets"]); p.add_argument("--date", default=today())
    p.add_argument("--csv", help="Status Updates CSV (stages astra / catchup)")
    p.add_argument("--now", help="HH:MM JST override (tests)")
    p.set_defaults(fn=cmd_postqa)
    p = sub.add_parser("metrics"); p.set_defaults(fn=cmd_metrics)
    p = sub.add_parser("export"); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("set-drive"); p.add_argument("key"); p.add_argument("value"); p.set_defaults(fn=cmd_set_meta)
    p = sub.add_parser("drive-resolve", help="current Drive sheet of each kind from a folder listing (never a fixed ID)")
    p.add_argument("--listing", nargs="+", required=True, help="search_files JSON output(s) of the CW Scout folder")
    p.add_argument("--keep", nargs="*", help="key=id of a sheet just uploaded and verified; others of that kind -> trash")
    p.set_defaults(fn=cmd_drive_resolve)
    import client_master
    p = sub.add_parser("client-show", help="Client Master record (by client id, or for a job: relationship + opening)")
    p.add_argument("--id", default=""); p.add_argument("--job", default="")
    p.set_defaults(fn=client_master.cmd_client_show)
    p = sub.add_parser("client-note", help="add a hand-kept note to a client (kept across rebuilds)")
    p.add_argument("--id", required=True); p.add_argument("--note", required=True); p.add_argument("--by", default="Astra")
    p.set_defaults(fn=client_master.cmd_client_note)
    p = sub.add_parser("job-detail", help="full vault record for job ids (Drive Job Master is an index)")
    p.add_argument("--ids", required=True); p.set_defaults(fn=cmd_job_detail)
    p = sub.add_parser("drive-status", help="Drive sheets not yet re-uploaded since the last export (exit 1)")
    p.add_argument("--keys", default="", help="comma list, e.g. job_master,application_queue")
    p.set_defaults(fn=cmd_drive_status)
    import application
    p = sub.add_parser("app-check", help="re-read reward/deadline/slots from the posting (ASTRA_PASS only)")
    p.add_argument("--ids", default=""); p.add_argument("--date", default=today())
    p.set_defaults(fn=application.cmd_app_check)
    p = sub.add_parser("app-merge", help="store Claude application drafts (no submission)")
    p.add_argument("--drafts", required=True); p.add_argument("--date", default=today())
    p.set_defaults(fn=application.cmd_app_merge)
    p = sub.add_parser("app-plan", help="ASTRA_PASS jobs to draft now (after app-check; no LLM)")
    p.add_argument("--date", default=today()); p.add_argument("--cap", type=int, default=10)
    p.set_defaults(fn=application.cmd_app_plan)
    p = sub.add_parser("ready-notice", help="07:30 notice body: READY_TO_APPLY only, `URL | 応募文`")
    p.add_argument("--ids", default=""); p.set_defaults(fn=application.cmd_ready_notice)
    p = sub.add_parser("profile-fact", help="record a fact the user confirmed, reused from then on")
    p.add_argument("--fact", required=True); p.add_argument("--source", required=True)
    p.add_argument("--question-re", required=True, help="regex of the questions this fact answers")
    p.add_argument("--job-re", default="", help="only for jobs whose title/posting matches")
    p.add_argument("--ref", default="", help="where it already sits in the profile, e.g. professional.qualifications[2]")
    p.set_defaults(fn=cmd_profile_fact)
    p = sub.add_parser("repair-source", help="backfill source-backed questions / rewards of queued jobs (no re-evaluation)")
    p.add_argument("--ids", required=True); p.add_argument("--date", default=today())
    p.set_defaults(fn=application.cmd_repair_source)
    p = sub.add_parser("app-batch", help="record a batch-level human time reported by the user")
    p.add_argument("--ids", required=True); p.add_argument("--minutes", type=float, required=True)
    p.add_argument("--source", required=True); p.add_argument("--status"); p.add_argument("--note")
    p.add_argument("--next-action")
    p.set_defaults(fn=application.cmd_app_batch)
    p = sub.add_parser("manual-add", help="add one job the user picked by hand (evaluated by Claude)")
    p.add_argument("--id", required=True); p.add_argument("--eval", required=True)
    p.add_argument("--note", default="本人が応募を希望して手動指定")
    p.set_defaults(fn=application.cmd_manual_add)
    import manual_review
    p = sub.add_parser("manual-request", help="queue CrowdWorks URLs/IDs for Claude's first-pass review")
    p.add_argument("urls", nargs="+"); p.add_argument("--by", default="Astra"); p.add_argument("--note", default="")
    p.add_argument("--intent", help="the user's stated intent (e.g. 応募したい); recorded, never an Astra verdict")
    p.set_defaults(fn=manual_review.cmd_manual_request)
    p = sub.add_parser("manual-fetch", help="fetch the postings of PENDING manual review requests")
    p.add_argument("--date", default=today()); p.set_defaults(fn=manual_review.cmd_manual_fetch)
    p = sub.add_parser("manual-merge", help="store Claude's first-pass evaluation -> Astra Queue")
    p.add_argument("--evals", required=True); p.add_argument("--date", default=today())
    p.set_defaults(fn=manual_review.cmd_manual_merge)
    p = sub.add_parser("manual-status", help="show the Manual Review Queue")
    p.set_defaults(fn=manual_review.cmd_manual_status)
    import worker
    p = sub.add_parser("worker-save", help="store the Worker internal record after self-QA (-> READY_FOR_QA)")
    p.add_argument("--id", required=True); p.add_argument("--record", required=True)
    p.set_defaults(fn=worker.cmd_worker_save)
    p = sub.add_parser("worker-deliver", help="verify the client deliverable after Astra QA PASS (-> READY_TO_DELIVER)")
    p.add_argument("--id", required=True); p.add_argument("--title", required=True)
    p.add_argument("--url", required=True); p.add_argument("--file-id", required=True)
    p.add_argument("--type", required=True, choices=sorted(worker.FORMATS))
    p.add_argument("--exported", required=True, help="the file's content as downloaded back from Drive")
    p.add_argument("--deadline"); p.add_argument("--deadline-basis")
    p.set_defaults(fn=worker.cmd_worker_deliver)
    p = sub.add_parser("worker-status", help="accepted jobs and their next Worker step")
    p.set_defaults(fn=worker.cmd_worker_status)
    p = sub.add_parser("worker-notice", help="print the delivery-ready notice (direct link only)")
    p.add_argument("--id", required=True); p.set_defaults(fn=worker.cmd_worker_notice)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
