#!/usr/bin/env python3
"""CrowdWorks Scout collector.

Collects open job postings from CrowdWorks public search, fetches every
posting's detail page, and pre-screens each one with rule-based flags
(AI-usage policy, hard requirements, client risk, personal-experience need,
repeatability). The output is a shortlist that Claude reviews by hand before
writing the daily hand-off report for Astra.

Usage:
    python3 scout/collect.py [--date YYYY-MM-DD]

Outputs (under scout/data/<date>/):
    jobs.jsonl        every collected posting with parsed fields and flags
    summary.json      counts per tier / flag
State (committed):
    scout/state/seen.json   job id -> first-seen timestamp (for "new" diff)
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
BASE = "https://crowdworks.jp/public/jobs"
JST = dt.timezone(dt.timedelta(hours=9))

# (label, query string, tier hint)
QUERIES = [
    ("writing_all", "category_id=228", "B"),
    ("task_all", "payment_type=task", "A"),
]
PRO_KEYWORDS = [
    "金融", "銀行", "M&A", "事業計画", "財務", "決算", "企業分析", "市場調査",
    "競合調査", "業界調査", "リサーチ", "資料作成", "Excel", "PowerPoint",
    "パワーポイント", "NISA", "iDeCo", "住宅ローン", "不動産投資", "融資",
    "スタートアップ", "法人営業", "資金調達", "簿記", "FP", "英語", "要約",
]
for kw in PRO_KEYWORDS:
    QUERIES.append((f"kw:{kw}", "search%5Bkeywords%5D=" + urllib.parse.quote(kw), "C"))


def curl(url, retries=4):
    for i in range(retries):
        r = subprocess.run(["curl", "-sS", "-A", UA, "--max-time", "40", url],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", "replace")
        time.sleep(2 ** i)
    return ""


def search_json(text):
    m = re.search(r'data="(\{&quot;isMobile.*?)"', text)
    return json.loads(html.unescape(m.group(1)))["searchResult"] if m else None


def collect_search(qs):
    out, page, total = [], 1, 1
    while page <= total:
        sr = search_json(curl(f"{BASE}/search?order=new&hide_expired=true&{qs}&page={page}"))
        if not sr:
            break
        out += sr["job_offers"]
        total = sr["page"]["total_page"]
        page += 1
        time.sleep(0.4)
    return out


def text_of(fragment):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", fragment, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t))
    return re.sub(r"\n\s*\n+", "\n", t)


def parse_detail(page):
    t = text_of(page)
    a = t.find("仕事の詳細")
    b = t.find("この仕事の特徴", a)
    if b < 0:
        b = t.find("クライアント情報", a)
    desc = t[a + 5:b].strip() if a >= 0 else ""
    client = {}
    m = re.search(r'data="(\{&quot;userId.*?)"', page)
    if m:
        c = json.loads(html.unescape(m.group(1)))
        client = {k: c.get(k) for k in (
            "userId", "userDisplayName", "isIdentityVerified", "averageScore",
            "jobOfferAchievementCount", "projectFinishedRate", "isCertifiedEmployer",
            "isEmployerRuleCheckSucceeded")}
    return desc, client


# ---------- rule-based pre-screen ----------
AI = r"(?<![A-Za-z])(?:生成AI|AI|ＡＩ|ChatGPT|チャットGPT|GPT|Gemini|Claude|人工知能)(?![A-Za-z])"
AI_RE = re.compile(AI, re.I)
BAN = re.compile(r"禁止|不可|NG|ＮＧ|厳禁|お断り|ご遠慮|お控え|認めて(?:い|お)りません|使用できません|使わない|使用しない|推測される場合|非承認")
CONDITIONAL = re.compile(r"そのまま|丸投げ|丸写し|コピペ|全文|だけの|のみの|確認せず|未確認|確かめていない|架空|画像|イラスト|安易")
GEN = re.compile(r"(?:生成|執筆|作成|下書き|台本|出力|書かせ|ライティング)(?:[^。\n]{0,8})(?:OK|可|いただけ|いただき|いただいて|して下さい|してください|構いません|問題)|"
                 r"AI(?:で|を使って|を活用して|を用いて)[^。\n]{0,15}(?:生成|作成|執筆|下書き|出力)|AI執筆|AIライティング|AI生成(?:OK|可)|AI台本|AI下書き|使用OK|使用可|利用OK|利用可|活用OK|併用")
ASSIST = re.compile(r"補助|参考|アイデア|構成(?:まで|のみ)|リサーチ(?:のみ|で)|情報収集|情報整理|推敲|整理")


def ai_policy(desc):
    """A: generation allowed, B: assist/reference only, C: unknown, D: banned."""
    lines = [l for l in re.split(r"[。\n]", desc) if AI_RE.search(l)]
    if not lines:
        return "C", []
    tags, ev = set(), []
    for l in lines:
        if BAN.search(l):
            if CONDITIONAL.search(l) or re.search(r"OK|構いません|可能|補助", l):
                tags.add("cond")          # e.g. "AI丸投げNG" -> generation + human edit
            else:
                tags.add("ban")
        elif ASSIST.search(l) and not re.search(r"生成|下書き|執筆していただ|出力", l):
            tags.add("assist")
        elif GEN.search(l):
            tags.add("gen")
        ev.append(l.strip()[:120])
    if "ban" in tags and not (tags & {"gen", "assist"}):
        cls = "D"
    elif "assist" in tags and "gen" not in tags:
        cls = "B"
    elif "gen" in tags or "cond" in tags:
        cls = "A"
    else:
        cls = "C"
    return cls, ev[:4]


REQ_PATTERNS = {
    "Webライター経験": r"(?:Web)?ライ(?:ター|ティング)(?:経験|実績)(?:者|が|を|の|\d|半年|1年|必須)",
    "SEO経験": r"SEO(?:ライティング|記事)?(?:の)?(?:経験|実績|知識)(?:者|が|必須|のある)",
    "WordPress": r"WordPress|ワードプレス",
    "校正経験": r"校正(?:・校閲)?(?:の)?(?:経験|実務)",
    "ポートフォリオ/実績提出": r"ポートフォリオ|実績(?:URL|の(?:ご)?提出|をお送り|のわかる)|執筆実績",
    "資格": r"資格(?:保有|をお持ち|必須)|有資格",
    "年齢条件": r"\d0代(?:限定|の方|前半|後半|まで)|歳(?:以下|以上|まで)|学生(?:不可|NG|の方はご遠慮)",
    "性別条件": r"女性(?:限定|の方|ライター)|男性(?:限定|の方)|ママ|主婦(?:限定)",
    "稼働時間": r"(?:週|1日|月)\s*\d+\s*(?:時間|本|記事)(?:以上|程度)|稼働",
    "面談/通話": r"Zoom|面談|通話|ミーティング|Chatwork|Slack",
    "必須": r"必須|応募条件|必要条件",
}
RISK_PATTERNS = {
    "外部誘導": r"LINE|ライン(?:で|へ|に)|公式LINE|外部(?:サイト|ツール)(?:で|へ)の(?:やり取り|連絡)",
    "購入/費用要求": r"購入して|購入が必要|自己負担|初期費用|登録料|教材|講座|スクール|コンサル",
    "勧誘兆候": r"理想の未来|今後の目標|目指したい働き方|ライフスタイル|一人暮らし|実家|月収|収入面|稼げるように|自由な働き方|場所に縛られ",
}
EXPERIENCE = re.compile(r"体験談|実体験|ご自身の(?:経験|体験)|あなたの(?:経験|体験)|経験談|エッセイ|思い出|感想|口コミ|レビュー|実際に(?:使|利用|行|購入)|使ってみた|本音|不満")


def screen(job, desc, client):
    j = job["job_offer"]
    pay = job["payment"]
    f = {}
    f["ai_policy"], f["ai_evidence"] = ai_policy(desc)
    f["requirements"] = [k for k, p in REQ_PATTERNS.items() if re.search(p, desc)]
    f["risk"] = [k for k, p in RISK_PATTERNS.items() if re.search(p, desc)]
    if (client.get("averageScore") or 0) == 0:
        f["risk"].append("評価0")
    if (client.get("jobOfferAchievementCount") or 0) <= 2:
        f["risk"].append("発注実績<=2")
    if not client.get("isIdentityVerified"):
        f["risk"].append("本人確認未")
    f["needs_experience"] = len(EXPERIENCE.findall(j["title"] + desc)) >= 2
    f["desc_hash"] = hashlib.md5(re.sub(r"\s", "", desc)[:400].encode()).hexdigest()
    if "fixed_price_writing_payment" in pay:
        p = pay["fixed_price_writing_payment"]
        f["pay"] = {"type": "article", "price": p.get("article_price"),
                    "chars": p.get("min_articles_length")}
    elif "task_payment" in pay:
        p = pay["task_payment"]
        f["pay"] = {"type": "task", "price": p.get("task_price"),
                    "minutes": p.get("estimated_work_minutes")}
    elif "fixed_price_payment" in pay:
        p = pay["fixed_price_payment"]
        f["pay"] = {"type": "fixed", "min": p.get("min_budget"), "max": p.get("max_budget")}
    elif "hourly_payment" in pay:
        f["pay"] = {"type": "hourly", **pay["hourly_payment"]}
    else:
        f["pay"] = {"type": "other", "raw": pay}
    # reported price vs body mismatch (e.g. listing 400 but body says 200)
    nums = [int(n.replace(",", "")) for n in re.findall(r"(\d[\d,]{1,6})\s*円", desc)]
    lp = f["pay"].get("price")
    f["price_mentions"] = sorted(set(nums))[:12]
    if lp and nums and lp not in nums and all(n < lp for n in nums if n >= 50):
        f["risk"].append("報酬表示と本文が不一致の可能性")
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(JST).strftime("%Y-%m-%d"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    out_dir = os.path.join(ROOT, "data", args.date)
    cache = os.path.join(ROOT, "data", "cache")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache, exist_ok=True)
    state_path = os.path.join(ROOT, "state", "seen.json")
    seen = json.load(open(state_path)) if os.path.exists(state_path) else {}
    now = dt.datetime.now(JST).isoformat(timespec="minutes")

    jobs, sources = {}, collections.defaultdict(set)
    for label, qs, tier in QUERIES:
        res = collect_search(qs)
        print(f"{label}: {len(res)}", file=sys.stderr)
        for jo in res:
            jid = jo["job_offer"]["id"]
            jobs.setdefault(jid, jo)
            sources[jid].add(tier)

    def fetch(jid):
        p = os.path.join(cache, f"{jid}.html")
        if not os.path.exists(p) or os.path.getsize(p) < 5000:
            page = curl(f"{BASE}/{jid}")
            if page:
                open(p, "w", encoding="utf-8").write(page)
            time.sleep(0.3)
        return jid

    with cf.ThreadPoolExecutor(args.workers) as ex:
        list(ex.map(fetch, jobs))

    dup = collections.Counter()
    rows = []
    for jid, jo in jobs.items():
        p = os.path.join(cache, f"{jid}.html")
        if not os.path.exists(p):
            continue
        desc, client = parse_detail(open(p, encoding="utf-8").read())
        f = screen(jo, desc, client)
        dup[f["desc_hash"]] += 1
        j = jo["job_offer"]
        entry = jo.get("entry", {})
        rows.append({
            "id": jid, "url": f"{BASE}/{jid}", "title": j["title"].strip(),
            "category_id": j["category_id"], "expired_on": j["expired_on"],
            "released_at": j["last_released_at"], "tiers": sorted(sources[jid]),
            "entry": entry, "client": client, "first_seen": seen.get(str(jid), now),
            "is_new": str(jid) not in seen, "desc": desc, **f,
        })
        seen.setdefault(str(jid), now)
    by_client = collections.Counter(r["client"].get("userId") for r in rows)
    for r in rows:
        r["same_text_count"] = dup[r["desc_hash"]]
        r["client_open_jobs"] = by_client[r["client"].get("userId")]
        if r["same_text_count"] >= 3 and len({x["client"].get("userId") for x in rows if x["desc_hash"] == r["desc_hash"]}) >= 3:
            r["risk"].append("同一文面を複数アカウントが投稿")
    with open(os.path.join(out_dir, "jobs.jsonl"), "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    summ = {
        "date": args.date, "collected": len(rows),
        "new": sum(r["is_new"] for r in rows),
        "tiers": collections.Counter(t for r in rows for t in r["tiers"]),
        "ai_policy": collections.Counter(r["ai_policy"] for r in rows),
    }
    json.dump(summ, open(os.path.join(out_dir, "summary.json"), "w"), ensure_ascii=False, indent=1)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    json.dump(seen, open(state_path, "w"), ensure_ascii=False)
    print(json.dumps(summ, ensure_ascii=False))


if __name__ == "__main__":
    main()
