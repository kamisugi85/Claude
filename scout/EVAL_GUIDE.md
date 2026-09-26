# Claude一次評価ガイド（Scout）

`prepare` が出力した `scout/data/<date>/pending_eval.json` の各案件を評価し、
`merge --evals <file>` に渡すJSON配列を作る。本人プロフィールは
`python3 scout/pipeline.py show-profile` で確認する（リポジトリには置かない）。

## 原則
- 本人作業時間あたりの期待純利益（Expected Net Profit ÷ Human Minutes）を最重視する。本人の作業は1日5〜10分と最終確認が目標。
- 案件名だけで候補にしない。`desc`（募集本文）で必須条件・稼働・納期・AI条件を確認する。
- プロフィールにない経験・資格・執筆歴・商品の利用歴は絶対に創作・推定しない。Webライター、SEO、WordPress、校正の経験は「未確認」として扱う。
- 実務経験（例：金融）と、そのテーマの記事執筆経験は別物として扱う。
- 本人の体験が必要な案件
  - プロフィールにある経験の範囲なら「要確認」とし、本人への質問を最大3問つける。
  - プロフィールにない経験なら「除外」にする。
- AIで回答を作ると虚偽の回答やレビューになる案件（アンケート、口コミなど）では、AIに回答を作らせない。
- 評価0の発注者は、それだけでは除外しない。リスクとして記録する。

## AI利用条件（ai_condition）
- A: AIによる文章生成まで明示的に許可（「丸投げ・そのまま納品NG」の条件付きを含む）
- B: 参考・補助としての利用のみ許可 → AIによる全文生成を前提に作業時間を見積もらない
- C: 記載なし・不明 → AIを使えると決めつけない
- D: 禁止 → 除外

## 分類（classification）
- A: 超短時間Task（5〜100円程度）
- B: AIレバレッジ（400〜8,000円程度が目安。価格帯で機械的に限定しない）
- C: Professional / Human Premium

## 出力スキーマ（1案件=1オブジェクト）
```json
{
  "job_id": 12345678,
  "classification": "A|B|C",
  "ai_condition": "A|B|C|D",
  "requirements": ["必須条件を本文から列挙"],
  "fit": "適合|要確認|不適合",
  "profile_link": "本人プロフィールとの接点",
  "ai_steps": ["AIで処理できる工程"],
  "human_steps": ["本人が行う工程"],
  "ai_completion": "85-90%",
  "human_minutes": "3-5分",
  "est_hourly": "約4,000-7,000円（推定）",
  "repeatability": "高|中|低",
  "client_risk": "低|中|高（理由）",
  "user_questions": ["最大3問。不要なら空配列"],
  "verdict": "候補|要確認|除外",
  "reason": "判定理由",
  "source_notes": "報酬の実額・締切など原文確認メモ",
  "gross_jpy": 3000
}
```
- `gross_jpy`：一覧の予算表示と本文の実単価が違う場合に、本文の1件あたり報酬（税込）を入れる。想定手取額はこの値から手数料20%を引いて計算される。
- 作業時間と時給は推定値として書く（「推定」と明記する）。受注後の実測値が集まると `pending_eval.json` の `calibration`（実測÷予測の比率）に反映されるので、次回以降の推定を補正する。
- `候補` と `要確認` はAstra Queueに入る。`除外` はJob Masterに記録され、Astra Queueには入らない。
