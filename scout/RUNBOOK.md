# Scout 日次実行手順（毎朝06:00 JST）

Claude Codeのセッションが上から順に実行する。応募・契約・納品・課金・外部公開は行わない。

## 0. 準備
```bash
git fetch origin claude/brave-lovelace-7n0flp && git checkout claude/brave-lovelace-7n0flp && git pull
export SCOUT_VAULT_KEY=...   # 定期実行のプロンプト（または環境変数）で渡される
```
- Driveのファイル・フォルダのIDは、`python3 scout/pipeline.py export` の後に `scout/out/sync_manifest.json` の `drive` で確認できる。

## 1. ステータス更新の取り込み（Sheets → Job Master）
1. Drive MCPの `download_file_content` で `status_updates_sheet` を `text/csv` として取得する。
2. 返ってきたbase64を `scout/data/status_updates.b64` に保存し、`base64 -d` でCSVに戻す。
3. `python3 scout/pipeline.py apply-updates --csv <csv>` を実行する。同じ行は二度反映されない。

## 2. 収集（Delta Scan）
```bash
python3 scout/collect.py --mode delta     # 日曜のみ --mode full（取りこぼしの回収）
python3 scout/pipeline.py prepare --cap 40
```
- `prepare` の処理：
  - 重複の除外：変化のない既知の案件はスキップする
  - ルールによる除外
  - 差分の再評価対象の抽出：報酬、条件、AI条件、募集状態、発注者の変化
  - バックログからの補充
- 出力：`scout/data/<date>/pending_eval.json`

## 3. Claude一次評価
1. `python3 scout/pipeline.py show-profile` で本人プロフィールを確認する。
2. `scout/EVAL_GUIDE.md` に従い、`pending_eval.json` の全件を評価する。
3. 結果をJSON配列として `scout/data/<date>/evals.json` に保存する。

## 4. Job Masterへの統合とAstra Queueの生成
```bash
python3 scout/pipeline.py merge --evals scout/data/<date>/evals.json
```
- 出力：
  - `scout/out/astra_queue.csv` / `.json`
  - `scout/out/job_master.csv`
  - `scout/state/runs.jsonl`（実行ログ）

## 5. Google Sheetsへの同期
現在のDrive MCPは既存ファイルの中身を書き換えられないため、次の手順でシートを差し替える。
1. フォルダ `CW Scout (ai×cloud works)` に、次の2つを `text/csv` でアップロードし、Googleシートに変換する。
   - `CW Scout - Job Master`
   - `CW Scout - Astra Queue`
2. 古いシート（manifestに記録された `job_master_sheet` と `astra_queue_sheet`）を `trash_file` でゴミ箱へ移す。
3. 新しいIDを記録する：`python3 scout/pipeline.py set-drive job_master_sheet <id>`（`astra_queue_sheet` も同様）
4. `CW Scout - Status Updates (記入用)` は固定のシートなので、差し替えない。

## 6. 保存
```bash
git add scout/state && git commit -m "Scout run <date>" && git push -u origin claude/brave-lovelace-7n0flp
```
- 暗号化されていない状態で個人情報をコミットしないこと。
  - `scout/data` と `scout/out` はgitignore済み。
  - Job Masterは `vault.enc` に暗号化して保存する。

## エラー時
- 収集に失敗した場合：`runs.jsonl` の `errors` に記録し、処理を続ける。
- Driveに接続できない場合：手順5をスキップして手順6まで進める。次回の実行で最新の状態を同期する。
