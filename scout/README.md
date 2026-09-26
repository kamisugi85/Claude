# CrowdWorks Scout collector

CrowdWorksの公開検索から募集中の案件を収集し、各案件の募集文を取得して
ルールで一次選別する（AI利用条件・必須条件・発注者リスク・本人経験の要否・反復可能性）。
最終的な判定は、出力を読んで人とAIが行う。

```
python3 scout/collect.py            # 当日分（JST）を収集
python3 scout/collect.py --date 2026-09-26
```

- 収集対象: ライティング全体（category_id=228）、タスク形式全体（payment_type=task）、専門キーワード検索（`PRO_KEYWORDS`）
- 出力: `scout/data/<date>/jobs.jsonl`, `summary.json`（gitの管理外）
- 状態: `scout/state/seen.json`（案件ID → 初回発見日時。「新規」の判定に使う）

AI利用条件の一次分類（`ai_policy`）は文言ベースの推定であり、判定前に必ず募集文で確認すること。
- A: AIによる文章生成まで明示的に許可（「丸投げ・そのまま納品NG」の条件付きを含む）
- B: 参考・補助としての利用のみ許可
- C: 記載なし・不明
- D: 禁止
