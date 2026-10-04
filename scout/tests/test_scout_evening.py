"""AI-BPO search entry and the 17:00 light delta run (sandbox: code only and a fresh test Vault; the real
Vault, state/runs.jsonl and CrowdWorks are never touched - the search and detail pages are served by a stub).
1. the AI-BPO categories are search entries, and a 13502286-like posting (category 371, AI images + Canva)
   enters the search population through them only
2. AI-assisted production is Auto (not only text); paid tools / appearance / AI bans are excluded
3. 05:00 -> 17:00 -> 05:00: 17:00 does not re-evaluate what 05:00 handled, picks up postings and important
   changes since 05:00, keeps to its own Claude limit, hands what it leaves back to 05:00, and the Astra Queue
   has no duplicates and tells the 17:00 additions apart

Run: python3 scout/tests/test_scout_evening.py
"""
import csv
import html
import json
import os
import shutil
import subprocess
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sandbox  # noqa: E402
import collect  # noqa: E402
import pipeline as P  # noqa: E402

PROFILE = {"birth_year": 1993, "keywords_strong": ["簿記", "財務", "市場調査"], "keywords_title_only": [],
           "unconfirmed_skills": []}
RECIPE = ("おうち時短レシピの紹介投稿用に、AI画像生成ツールで料理画像を作り、Canvaで投稿画像にまとめていただくお仕事です。\n"
          "・1投稿：5〜7枚程度\n・週1〜2投稿程度\n・報酬：1投稿1,500〜3,000円\n・納期：5〜7日程度\n"
          "・使用ツール：AI画像生成ツール＋Canva（無料版で可）\n未経験の方も歓迎です。")
LIFESTYLE = ("\n【応募時に教えてください】\n① お名前：\n② ご年齢：\n③ 現在のご職業・雇用形態：\n④ 本業の週合計労働時間（目安）：\n"
             "⑤ 現在のライフスタイル（一人暮らし・実家暮らし・同棲など）：\n⑥ 3年後に理想としている働き方・ライフスタイルを教えてください。")


def jo(jid, title, cat, released, pay=(30000, 50000), hope=5, contracts=0):
    return {"job_offer": {"id": jid, "title": title, "category_id": cat, "expired_on": "2099-12-31",
                          "last_released_at": released, "status": "released"},
            "payment": {"fixed_price_payment": {"min_budget": pay[0], "max_budget": pay[1]}},
            "client": {"user_id": 99000300 + jid % 100, "is_employer_certification": False},
            "entry": {"project_entry": {"num_contracts": contracts, "project_contract_hope_number": hope,
                                        "num_application_conditions": 3}}}


def detail(desc, uid):
    c = {"userId": uid, "userDisplayName": "c", "isIdentityVerified": True, "averageScore": 4.8,
         "jobOfferAchievementCount": 30}
    return (f"<div>仕事の詳細</div><div>{html.escape(desc)}</div><div>この仕事の特徴</div>"
            f'<div data="{html.escape(json.dumps(c))}"></div>' + " " * 5000)


def row(j, desc, tiers, seen_at):
    """A collect.py jobs.jsonl row for a stub posting (same fields collect.main writes)."""
    o, client = j["job_offer"], json.loads(html.unescape(detail(desc, 1).split('data="')[1].split('"')[0]))
    client["userId"] = j["client"]["user_id"]
    f = collect.screen(j, desc, client)
    return {"id": o["id"], "url": f"{collect.BASE}/{o['id']}", "title": o["title"], "category_id": o["category_id"],
            "expired_on": o["expired_on"], "released_at": o["last_released_at"], "tiers": tiers,
            "entry": j["entry"], "client": client, "first_seen": seen_at, "is_new": True,
            "listing_fp": collect.listing_fp(j), "desc": desc, "same_text_count": 1, "client_open_jobs": 1, **f}


def write_run(tmp, date, run, rows, summary=None):
    d = os.path.join(tmp, "data", date) if run == "morning" else os.path.join(tmp, "data", date, run)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "jobs.jsonl"), "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    json.dump({str(r["id"]): r["listing_fp"] for r in rows}, open(os.path.join(d, "listed.json"), "w"))
    json.dump(summary or {"mode": "delta", "run": run, "listed": len(rows), "processed": len(rows),
                          "new": len(rows), "unchanged_skipped": 0, "errors": []},
              open(os.path.join(d, "summary.json"), "w"))
    return d


def py(tmp, *args, ok=True):
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=tmp, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(" ".join(args) + "\n" + r.stdout + r.stderr)
    return r


def last_json(r):
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_ai_bpo_search(tmp):
    # entries: the AI-BPO group 368-372 (source-checked names), tier E; earlier entries unchanged
    q = {label: (qs, tier) for label, qs, tier in collect.QUERIES}
    assert set(collect.AI_BPO_CATEGORIES) == {368, 369, 370, 371, 372}
    for cid, name in collect.AI_BPO_CATEGORIES.items():
        assert q[f"cat:{cid}:{name}"] == (f"category_id={cid}", "E")
    assert q["writing_all"][0] == "category_id=228" and q["task_all"][0] == "payment_type=task"
    # a stubbed CrowdWorks: 13502286 is listed only under category 371 (as on 2026-10-03), detail page served
    stub = r'''
import html, json, sys, collect
J = json.loads(sys.argv[1]); D = sys.argv[2]
def page(offers):
    return '<div data="' + html.escape(json.dumps({"isMobile": False, "searchResult": {
        "job_offers": offers, "page": {"total_page": 1}}})) + '"></div>'
def curl(url, retries=4):
    if "/search?" in url:
        return page([J] if "category_id=371&" in url else [])
    return D
collect.curl = curl
collect.time.sleep = lambda s: None
sys.argv = ["collect.py", "--mode", "delta", "--run", "evening", "--date", "2026-10-03"]
collect.main()
'''
    j = jo(13502286, "【未経験OK】おうち時短レシピをAIで画像生成するお仕事", 371, "2026-10-03T13:24:52+09:00")
    r = subprocess.run([sys.executable, "-c", stub, json.dumps(j), detail(RECIPE + LIFESTYLE, 99000386)],
                       cwd=tmp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    rows = [json.loads(l) for l in open(os.path.join(tmp, "data", "2026-10-03", "evening", "jobs.jsonl"), encoding="utf-8")]
    hit = [x for x in rows if x["id"] == 13502286]
    assert hit and hit[0]["tiers"] == ["E"] and hit[0]["category_id"] == 371, rows
    assert "cat:371:AIメディア・コンテンツ構築支援: 1" in r.stderr
    summ = json.load(open(os.path.join(tmp, "data", "2026-10-03", "evening", "summary.json")))
    assert summ["run"] == "evening" and summ["listed_ai_bpo_only"] == 1
    print("AI-BPO 368-372 searched (tier E); a 13502286-like posting enters the search population via category 371")


def test_ai_bpo_rules():
    base = row(jo(13502286, "おうち時短レシピをAIで画像生成するお仕事", 371, "2026-10-03T13:24+09:00"),
               RECIPE, ["E"], "2026-10-03T17:00+09:00")
    st, why, hits = P.rule_filter(dict(base), PROFILE)
    assert st == "PASS" and P.lane_guess(base, hits) == "Auto", why  # images + Canva with AI: Auto, not only text
    manual = dict(base, tiers=["B"], title="レシピ記事作成", desc="ご自身の言葉でレシピ記事を書いてください。")
    assert P.est_human_minutes(base) < P.est_human_minutes(manual) * 0.5  # review time after AI, not hand work
    # the real 13502286 asks for living situation + main-job hours + employment + "ideal way to work in 3 years":
    # several strong recruitment signals together -> rejected (the generic combined rule, no job-specific case)
    st, why, _ = P.rule_filter(dict(base, desc=RECIPE + LIFESTYLE), PROFILE)
    assert st == "RULE_REJECTED" and any(w.startswith("勧誘リスク（複合）") for w in why), why
    # one personal question alone is not a reason to reject: it goes on to Claude with a flag
    one = dict(base, desc=RECIPE + "\n【応募時に教えてください】\n① 現在のライフスタイル（一人暮らし・実家暮らしなど）：")
    st, why, _ = P.rule_filter(one, PROFILE)
    assert st == "PASS" and one["recruit_risk"]["decision"] == "FLAG", why
    cases = {
        "有料ツール": "Canva Proのご契約が必須です。",
        "撮影・出演": "ご自身で料理を撮影していただきます。",
        "AI生成禁止": "AI生成画像は不可です。手描きのみでお願いします。",
        "送信作業": "作成後、インスタでDM送信もお願いします。",
    }
    for key, extra in cases.items():
        st, why, _ = P.rule_filter(dict(base, desc=RECIPE.replace("（無料版で可）", "") + "\n" + extra), PROFILE)
        assert st == "RULE_REJECTED" and any(key in w for w in why), (key, why)
    # not the user appearing: "no face shown", a face-on video interview, watching live streams (seen 2026-10-03)
    for extra in ("顔出し不要・完全在宅です。", "事前にZOOMにて顔出しのお打ち合わせが出来る方。", "ライブ配信を見ながら作業できます。",
                  "ワーカー様が投稿に出演するなどのことはございません。"):
        st, why, _ = P.rule_filter(dict(base, desc=RECIPE + "\n" + extra), PROFILE)
        assert not any("撮影・出演" in w for w in why), (extra, why)
    # an AI-BPO posting with nothing AI / automation can shorten and no profile link is dropped before Claude
    st, why, _ = P.rule_filter(dict(base, title="営業メンバー募集", desc="週3回の打ち合わせに参加し、顧客対応をお願いします。"), PROFILE)
    assert st == "RULE_REJECTED", why
    # outside AI-BPO, AI writing is not newly turned into Auto (05:00 rules for writing posts unchanged)
    w = dict(base, tiers=["C"], title="記事作成", desc="AIで記事を作成していただいて構いません。文字数3000字。", ai_policy="A")
    assert not P.auto_able(w)
    print("AI-assisted production is Auto; paid tools / appearance / AI ban / DM sending / no AI lever excluded")


def auto_job(jid, released, title="PDFからExcelへのデータ入力", extra="", pay=(20000, 30000)):
    return jo(jid, title, 52, released, pay=pay), \
        f"PDFの表をExcelへ転記していただきます。1ファイル約20ページ、全10ファイルです。{extra}"


def test_runs(tmp):
    d = P.today()
    m_at, e_at = f"{d}T05:15+09:00", f"{d}T17:05+09:00"
    # ---- 05:00: M1 / M2 pass, R1 is rejected (AI ban)
    M1, M2, R1 = auto_job(99800001, f"{d}T03:00+09:00"), auto_job(99800002, f"{d}T02:00+09:00"), \
        auto_job(99800003, f"{d}T01:00+09:00", extra="AIの使用は禁止です。")
    morning = [row(j, t, ["D"], m_at) for j, t in (M1, M2, R1)]
    write_run(tmp, d, "morning", morning)
    pre = last_json(py(tmp, "prepare"))
    assert pre["run_type"] == "morning_full" and pre["limits"] == {"cap": 60, "budget_chars": 70000}
    ids = {x["job_id"] for x in json.load(open(os.path.join(tmp, "data", d, "pending_eval.json")))["jobs"]}
    assert {99800001, 99800002} <= ids and 99800003 not in ids
    ev = [{"job_id": 99800001, "verdict": "候補", "classification": "B", "ai_condition": "C", "fit": "適合",
           "ai_completion": "90-95%", "human_minutes": "15分", "reason": "t", "lane": "Auto"},
          {"job_id": 99800002, "verdict": "除外", "classification": "B", "reason": "t", "lane": "Auto"}]
    json.dump(ev, open(os.path.join(tmp, "m_evals.json"), "w"))
    mm = last_json(py(tmp, "merge", "--evals", "m_evals.json"))
    assert mm["run_type"] == "morning_full" and mm["astra_queue_added"] == 1 and mm["lane_mix"] == {"Auto": 2}

    idx0 = json.load(open(os.path.join(tmp, "state", "index.json")))
    # ---- 17:00: M2 unchanged (handled at 05:00); M1 changed but already in the Astra Queue; R1 lifts its AI ban
    # (important change); 18 new postings since 05:00 (more than the 17:00 limit of 15)
    M1c = (dict(M1[0], payment={"fixed_price_payment": {"min_budget": 30000, "max_budget": 40000}}),
           M1[1] + "報酬を1ファイル3,000円に変更しました。")
    R1c = (R1[0], R1[1].replace("AIの使用は禁止です。", "AIツールの利用も可です。"))
    # released early today so the release -> run delay is positive whatever time the test runs
    news = [auto_job(99810000 + i, f"{d}T00:{i:02d}+09:00", title=f"企業リストのデータ入力{i}",
                     pay=(20000 + 1000 * i, 30000 + 1000 * i)) for i in range(18)]
    evening = [row(j, t, ["D"], e_at) for j, t in [M2, M1c, R1c] + news]
    for r in evening[:3]:
        r["first_seen"] = m_at
    write_run(tmp, d, "evening", evening)
    pre = last_json(py(tmp, "prepare", "--run", "evening"))
    pend = json.load(open(os.path.join(tmp, "data", d, "evening", "pending_eval.json")))
    sent = {x["job_id"]: x["change"] for x in pend["jobs"]}
    assert pend["run_type"] == "evening_delta" and pre["limits"] == P.RUN_LIMITS["evening"]
    assert 99800002 not in sent, "05:00-handled and unchanged: not re-evaluated"
    assert 99800001 not in sent, "already in the Astra Queue: change noted for 05:00, not re-evaluated at 17:00"
    assert sent.get(99800003) == ["条件変更"], sent
    assert len(sent) == P.RUN_LIMITS["evening"]["cap"] == 15 and pre["est_ai_usage"]["eval_input_chars"] <= 20000
    assert sum(1 for k in sent if k >= 99810000) == 14 and pre["deferred_to_morning"] == 4 + 1  # 4 new + M1
    assert pre["detect_delay_h"]["n"] == 18 and pre["new_entry"]["new"] == 18, (pre["detect_delay_h"], pre["new_entry"])
    idx = json.load(open(os.path.join(tmp, "state", "index.json")))
    left = [k for k in (str(99810000 + i) for i in range(18)) if int(k) not in sent]
    assert len(left) == 4 and not any(k in idx for k in left), "left for 05:00: the delta scan sees them as new"
    assert idx["99800001"]["listing_fp"] == idx0["99800001"]["listing_fp"] != evening[1]["listing_fp"], \
        "M1's index entry restored: the 05:00 delta scan lists the change again"
    backlog = json.load(open(os.path.join(tmp, "state", "backlog.json")))
    assert not any(k in backlog for k in left)

    # Claude at 17:00: one new posting and R1 become candidates; M1 is re-sent by mistake -> no duplicate row
    top = max(k for k in sent if k >= 99810000)
    ev = [{"job_id": top, "verdict": "候補", "classification": "B", "ai_condition": "C", "fit": "適合",
           "ai_completion": "90-95%", "human_minutes": "15分", "reason": "t", "lane": "Auto"},
          {"job_id": 99800003, "verdict": "要確認", "classification": "B", "ai_condition": "A", "fit": "適合",
           "ai_completion": "85-90%", "human_minutes": "20分", "reason": "t", "lane": "Professional"}] + \
        [{"job_id": k, "verdict": "除外", "classification": "B", "reason": "t"} for k in sent if k not in (top, 99800003)]
    json.dump(ev, open(os.path.join(tmp, "e_evals.json"), "w"))
    em = last_json(py(tmp, "merge", "--run", "evening", "--evals", "e_evals.json"))
    assert em["run_type"] == "evening_delta" and em["astra_queue_added"] == 2 and em["queue_delay_h"]["n"] == 2
    assert em["lane_mix"] == {"Auto": 1, "Professional": 1, "未記入": 13}
    assert sum(em["tier_mix"].values()) == 2
    q = list(csv.DictReader(open(os.path.join(tmp, "out", "astra_queue.csv"), encoding="utf-8")))
    ids = [r["job_id"] for r in q]
    assert len(ids) == len(set(ids)), "no duplicate rows in the Astra Queue"
    by = {r["job_id"]: r for r in q}
    assert by["99800001"]["scout_run"] == f"{d} morning_full" and by["99800001"]["condition_change"]
    assert by[str(top)]["scout_run"] == f"{d} evening_delta" and by["99800003"]["scout_run"] == f"{d} evening_delta"
    assert by[str(top)]["released_at"].startswith(d)
    # today's runs picked by run_type / run_at, whatever else (rescreen, manual runs) is in runs.jsonl
    runs = [json.loads(l) for l in open(os.path.join(tmp, "state", "runs.jsonl"), encoding="utf-8")]
    day = {r.get("run_type"): r for r in sorted((r for r in runs if r.get("date") == d), key=lambda r: r.get("run_at") or "")}
    assert day["morning_full"]["run_at"] <= day["evening_delta"]["run_at"], day.keys()  # same minute in a fast test
    assert day["evening_delta"]["est_ai_usage"]["eval_jobs"] == 15
    print("17:00: 05:00-handled jobs skipped, new + important changes picked up, cap 15 / 20,000 chars kept, "
          "Queue rows unique with scout_run morning / evening")

    # ---- next 05:00: the 4 left by 17:00 come back as new; 17:00-evaluated ones are not re-evaluated
    nd = P.today()
    back = [r for r in evening if str(r["id"]) in left] + [r for r in evening if r["id"] in (top, 99800001)]
    write_run(tmp, nd, "morning", back)  # same date in the test: the morning files are simply overwritten
    pre = last_json(py(tmp, "prepare"))
    pend = {x["job_id"]: x["change"] for x in
            json.load(open(os.path.join(tmp, "data", nd, "pending_eval.json")))["jobs"]}
    assert all(pend.get(int(k)) == ["新規"] for k in left) and top not in pend, pend
    assert "報酬変更" in pend.get(99800001, []), "the change 17:00 noted is re-evaluated at 05:00 as before"
    print("next 05:00: what 17:00 left is evaluated as new; nothing evaluated at 17:00 is sent again")

    # a rescreen run between 05:00 and 17:00 (as on 2026-10-04) and a run after 17:00 change nothing here
    rp = os.path.join(tmp, "state", "runs.jsonl")
    with open(rp, "a", encoding="utf-8") as fo:
        fo.write(json.dumps({"date": d, "run_type": "rescreen", "run_at": f"{d}T13:39+09:00",
                             "est_ai_usage": {"eval_jobs": 11, "eval_input_chars": 18940}}) + "\n")
    dm = last_json(py(tmp, "daily-metrics", "--date", d))
    assert "rescreen" in dm["runs"] and dm["claude_usage"]["other_runs"]["rescreen"]["jobs"] == 11
    assert dm["evening_only_added"] == 2 and dm["claude_usage"]["evening_extra"]["jobs"] == 15
    assert dm["queue_delay_h_if_0500_only"]["n"] == dm["queue_delay_h_actual"]["n"] >= 3
    assert dm["queue_delay_h_if_0500_only"]["mean"] > dm["queue_delay_h_actual"]["mean"]
    assert os.path.exists(os.path.join(tmp, "state", "daily_metrics.jsonl"))
    print("daily-metrics: 05:00-only vs 05:00+17:00 delay, 17:00-only additions, extra Claude usage")


def main():
    # code only: no live Vault / runs.jsonl / index (fresh test Vault); "Excel" in the titles is a profile hit
    tmp = sandbox.make(dict(PROFILE, keywords_title_only=["Excel"]))
    try:
        test_ai_bpo_rules()
        test_ai_bpo_search(tmp)
        test_runs(tmp)
    finally:
        shutil.rmtree(tmp)
    print("OK")


if __name__ == "__main__":
    main()
