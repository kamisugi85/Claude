# データ形式(他AIエージェント/外部ツール連携用)

このツールが出力するデータはすべて**プレーンなJSON**で、Claude専用の形式には依存していません。
Codexなど他のAIエージェントやスクリプトから直接読み込んで利用できます。ファイルはすべて
`data/`(gitignore対象、ローカルのみ)以下にあります。

## `data/snapshots/latest.json` -- A8 Program Master

プログラムID(`program_id`)をキーにした辞書。全プログラムを蓄積し、削除しない。

```json
{
  "<program_id>": {
    "program_id": "s00000000018043",
    "name": "SoftBank Air",
    "category": "回線",
    "reward": "新規開通＋利用12000円",
    "epc": "74.77",
    "conversion_rate": "43.37%",
    "start_date": "2022年09月05日",
    "detail_url": "https://media-console.a8.net/program/detail-not-partnered?programId=...",
    "url": "https://media-console.a8.net/program/detail-not-partnered?programId=...",

    "成果条件": "...",
    "否認条件": "...",
    "備考": "...",

    "excluded": true,
    "exclusion_reason": "status_keyword:募集停止",
    "judged_at": "2026-09-22T00:21:21+09:00"
  }
}
```

- `program_id` / `name` / `category` / `reward` / `epc` / `conversion_rate` / `start_date` /
  `detail_url` / `url`: 検索結果一覧から取得する固定フィールド。
- それ以外のキー(`成果条件`・`否認条件`・`備考`・`リスティングNGワード` 等): 詳細ページの
  見出しをそのままキー名として使っている(★A8側の項目名に依存するため、プログラムごとに
  存在するキーが異なる)。**詳細ページは新規プログラムのみ取得するため、全レコードにこれらの
  キーがあるとは限らない**(一覧レベルの基本フィールドしか無いレコードも多い)。
- `excluded` / `exclusion_reason` / `judged_at`: Python/ルールによる除外判定(AI不使用)。
  `excluded` が無ければ除外されていない。除外されてもレコード自体は削除しない。

## `data/state/needs_review.json` -- AIスクリーニング用キュー

**その回の巡回で新規・変更があったプログラムだけ**を抽出したもの。変化のない既存案件や、
除外されたままで報酬関連の変更がない案件は入らない。AIによるスクリーニングはこのファイルを
入力にすることを想定している(カタログ全件を毎回AIに渡さないため)。

```json
{
  "generated_at": "2026-09-22T00:21:21+09:00",
  "count": 400,
  "items": [
    { "program_id": "s00000000018043", "name": "SoftBank Air", "reason": "new" },
    { "program_id": "...", "name": "...", "reason": "changed", "changed_fields": ["reward"] },
    { "program_id": "...", "name": "...", "reason": "reactivated", "changed_fields": ["reward"], "reward_related_fields": ["reward"] }
  ]
}
```

- `reason`: `"new"`(新規)/ `"changed"`(既存案件の条件変更)/ `"reactivated"`(除外済みだが
  報酬関連の変更で再スクリーニング候補に復帰)。
- 各アイテムの詳細な現在値は `data/snapshots/latest.json` を `program_id` で引く。

## `data/state/shortlist.json` -- ルールベースの期待値ランキング(AI不使用)

カタログ全体を対象に、EPC(無ければ 報酬×確定率)でランキングした上位N件。無料・機械的な
一次フィルタとしての参考用。

```json
{
  "generated_at": "...",
  "count": 200,
  "items": [
    { "program_id": "...", "score": 74.77, "name": "...", "reward": "...", "epc": "74.77", "...": "..." }
  ]
}
```

## `data/state/crawl_progress.json` -- 巡回の再開位置

```json
{ "last_completed_page": 224, "total_pages": 224, "updated_at": "..." }
```

## `data/state/alert.json` -- 異常検知(存在する場合のみ)

直近の実行が異常検知で停止した場合のみ存在する(正常終了時は削除される)。

```json
{
  "timestamp": "...",
  "kind": "session_expired | captcha_or_mfa_required | unexpected_navigation | consecutive_http_errors | unexpected_error",
  "message": "...",
  "context": { "last_url": "...", "run_id": "..." }
}
```

## 差分ファイル `data/diffs/diff-<run_id>.json`

その回で新規・変更となったプログラムの詳細(`new_items` / `changed_items`、各フィールドの
old/new値)。`needs_review.json` の元データ。
