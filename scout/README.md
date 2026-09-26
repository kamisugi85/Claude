# CrowdWorks Scout

CrowdWorksから「AIで本人の作業時間を大きく減らせる案件」を毎日発掘し、Astra（ChatGPT上の事業責任者）が二次評価できる形でGoogle Driveに渡す仕組み。

```
collect.py (Delta Scan) → pipeline.py prepare (重複除外・ルール除外・差分検出)
  → Claude一次評価 (EVAL_GUIDE.md) → pipeline.py merge (Job Master / Astra Queue)
  → Google Sheets (Drive MCP) ← Status Updates シート（Astra・本人が記入）
```

| 場所 | 内容 | 公開 |
|---|---|---|
| `scout/state/index.json` | 全取得案件のJob ID・指紋・ルール判定（重複排除用） | 公開可（個人情報なし） |
| `scout/state/backlog.json` | ルールを通過し、Claude評価待ちの案件 | 公開可 |
| `scout/state/runs.jsonl` | 実行ログ（件数・エラー・推定AI利用量） | 公開可 |
| `scout/state/vault.enc` | Job Master・本人プロフィール・Driveのファイル・フォルダのID（AES-256、`SCOUT_VAULT_KEY`で暗号化） | 暗号化 |
| `scout/state/seen.json` | 初回版の既知ID一覧（互換のため残す） | 公開可 |
| `scout/out/` | Sheets用のCSV・Astra Queue JSON | gitignore |
| `scout/data/<date>/` | その日の取得データ・評価入出力 | gitignore |

- 実行手順：[`RUNBOOK.md`](RUNBOOK.md)
- 評価基準と出力スキーマ：[`EVAL_GUIDE.md`](EVAL_GUIDE.md)
- ステータス：`SCOUTED → RULE_REJECTED | CLAUDE_CANDIDATE → ASTRA_QUEUE → ASTRA_PASS/ASTRA_REJECT/NEED_USER → READY_TO_APPLY → APPLIED → ACCEPTED → IN_PROGRESS → READY_FOR_QA → READY_TO_DELIVER → DELIVERED → PAID`
  - `CLAUDE_REJECTED` と `CLOSED`（募集期限切れ）は追加したステータス。
- 実測値：`actual_human_minutes` などの項目をStatus Updatesシートに記入すると、次回の実行でJob Masterに取り込まれ、推定の補正（`calibration`）に使われる。
