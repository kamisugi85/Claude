# Scout 日次実行手順（毎朝06:00 JST）

Claude Codeのセッションが上から順に実行する。応募・契約・納品・課金・外部公開は行わない。

## 0. 準備
```bash
git fetch origin claude/brave-lovelace-7n0flp && git checkout claude/brave-lovelace-7n0flp && git pull
# SCOUT_VAULT_KEY は環境の設定で環境変数として渡される（値を表示・ログ出力・コミットしない）
```
- Driveのファイル・フォルダのIDは、`python3 scout/pipeline.py export` の後に `scout/out/sync_manifest.json` の `drive` で確認できる。

## 1. ステータス更新の取り込み（Sheets → Job Master）
Status Updatesは本人の入力欄ではない。Astra と Claude Code がステータスを受け渡すためのシートで、書き込むのはAstraだけ。本人はAstraに報告するだけで、このシートは編集しない。
1. Drive MCPの `download_file_content` で `status_updates_sheet` を `text/csv` として取得する。
2. 返ってきたbase64を `scout/data/status_updates.b64` に保存し、`base64 -d` でCSVに戻す。
3. `python3 scout/pipeline.py apply-updates --csv <csv>` を実行する。同じ行は二度反映されない。
   - 取り込む列：job_id, astra_verdict（PASS / REJECT / 要確認）, astra_reason, new_status, need_user, next_action, updated_at, updated_by, 実測値8項目, note
   - new_statusが空の場合は、astra_verdictとneed_userからステータスを決める。
   - `SCOUT_MISS`（ChatGPT Scoutなどが見つけ、Claudeが取りこぼした案件）は、取りこぼしとして記録する。
4. 実測用の列の同期（取り込みの後に毎回実行。何度実行しても結果は同じ）
   - `python3 scout/pipeline.py su-columns --csv <手順2のCSV>` を実行する。
   - `action=none` なら何もしない。既存の列または別名の列（例：`actual_human_minutes`＝`production_human_minutes`）があれば、足りているとみなす。
   - `action=replace` の場合だけ、次の順で進める。Drive MCPは既存シートに列を追加できないため、差し替えで対応する。
     1. `get_file_permissions` で今のシートの共有設定を確認する。フォルダから引き継いだもの以外の共有があれば中止し、報告する。
     2. `scout/out/status_updates_synced.csv` を、同じフォルダに同じタイトル `CW Scout - Status Updates (記入用)` でアップロードする。中身は既存の全行・全列をそのまま残し、不足列だけを末尾に空欄で足したもの。
     3. 新しいシートと今のシートの両方を `text/csv` で取得し直し、`su-columns --csv <手順2のCSV> --verify <新> --recheck <今>` を実行する。
     4. `ok=true` のときだけ、今のシートを `trash_file` でゴミ箱へ移し、`set-drive status_updates_sheet <新ID>` を実行する。
     5. `ok=false` のとき（既存セルの変化・取り込み後の追記など）は新しいシートをゴミ箱へ移し、今のシートはそのまま残す。次回の実行でやり直す。

## 2. 収集（Delta Scan）
```bash
python3 scout/collect.py --mode delta     # 日曜のみ --mode full（取りこぼしの回収）
python3 scout/pipeline.py prepare          # 既定：最大60件、評価入力の上限70,000字
```
- 評価の優先度：推定手取額 × 受注確率 ÷ 推定本人作業時間 に、次の係数を掛けて決める。
  - 緊急度（期限が2日以内・5日以内）
  - AI利用条件
  - プロフィール適合度
  - 分類（C・B・A）
  - 同じ発注者の案件数
  - 新着・条件変更を優先
- 評価の順序：新着・変更案件を先に評価し、枠が余ればバックログを優先度順に補充する。
- Claude評価の前に除外する案件：
  - 期限切れ
  - 募集枠が埋まっている
  - 低価値（推定で1分あたり8円未満）
  - 成果報酬型の営業
- 件数と字数の測定：評価件数と入力字数は `runs.jsonl` の `est_ai_usage` に記録される。PoC期間中はこれを見て `--cap` と `--budget-chars` を調整する。
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
1. フォルダ `CW Scout (ai×cloud works)` に、次の2つを `text/csv` でアップロードし、Googleシートに変換する。タイトルは `scout/out/sync_manifest.json` の `titles` を使う（生成日時入り。例：`CW Scout - Astra Queue｜2026-09-27 06:05 JST`）。
   - Job Master
   - Astra Queue
2. 古いシート（manifestに記録された `job_master_sheet` と `astra_queue_sheet`）を `trash_file` でゴミ箱へ移す。
3. 新しいIDを記録する：`python3 scout/pipeline.py set-drive job_master_sheet <id>`（`astra_queue_sheet` も同様）
4. `CW Scout - Status Updates (記入用)` は差し替えない。例外は1.4の列追加だけ。

## 5.5 性能測定
- `python3 scout/pipeline.py metrics` で、累計の取得件数、Claude評価件数、Astra Queue件数、PASS・REJECTの件数、取りこぼし件数、Precision / Recallの目安を確認できる。
- `runs.jsonl` には実行ごとの値が記録される。

## 5.7 応募準備（Application Queue）※応募・フォーム入力・送信はしない
対象は `ASTRA_PASS` の案件だけ。少数を選んで実行する（初回PoCは4〜5件）。
1. `python3 scout/pipeline.py app-check --ids <id,...>` で、案件ページから報酬・応募期限・契約状況をもう一度取得する。
   - 報酬は一覧の表示額ではなく、募集本文の実額を使う（例：tokyoreve・supersameは本文の200円（税抜）＝税込220円）。
   - 募集終了・期限切れ・契約が募集人数に達した案件は選ばない。
2. `scout/data/<date>/app_source/<id>.json` の原文と `show-profile` だけを使い、`scout/data/<date>/app_drafts.json` を作る。
   - 1件ごとの項目：`job_id`, `actual_reward`（税込）, `reward_evidence`（本文からそのまま引用）, `application_draft`, `application_questions`（本文の設問をそのまま）, `application_answers`, `facts_used`（`fact` と `profile_ref`、例：`professional.qualifications[2]`）, `unverified_facts`, `conflict_risk`, `user_confirmation_required`, `human_review_minutes`, `next_action`
   - プロフィールにない経験・実績・好みは書かない。必要なら `unverified_facts` に入れ、回答欄は【本人記入】のままにして `user_confirmation_required=yes` にする。
   - 本Scout・AI運用を、AI案件の受注経験・コンサル経験・CrowdWorksでの実績として書かない。勤務先名は書かない。
3. `python3 scout/pipeline.py app-merge --drafts scout/data/<date>/app_drafts.json` を実行する。引用・設問・プロフィール参照が原文と一致しない下書きは取り込まれない。
4. `CW Scout - Application Queue｜<生成日時> JST` をJob Master等と同じ方法で差し替え、`set-drive application_queue_sheet <id>` で新しいIDを記録する。
5. Astraの最終QA結果はStatus Updatesから取り込む（`astra_verdict` は空欄にして、ステータスが戻らないようにする）。
   - 通過：`new_status=READY_TO_APPLY`, `final_qa_status=PASS`
   - 今回見送り：`new_status=SKIPPED`, `final_qa_status=SKIP`、理由は `note` に書く。条件不一致によるREJECTとは区別する。
6. 応募直前に `app-check --ids <READY_TO_APPLYのid>` を再実行する。
   - `recheck_changes` に報酬・期限・募集枠・AI条件・本文の変化、または募集終了が出たら、応募しない。
7. 応募の記録：本人はCrowdWorksで応募したあと、Astraに「N件応募した」と報告するだけ。その報告をもとに、AstraがStatus Updatesに `new_status=APPLIED`・`applied_at`・`human_review_minutes`・`updated_by=Astra` を書き、Claude Codeが次の実行で取り込む。報告が来るまで、案件は `READY_TO_APPLY` のまま変えない（Claude側で推測してAPPLIEDにしない）。本人が作業時間を伝えていなければ、`human_review_minutes` は空欄のままにする（推定値は書かない）。
8. 実測PoCはStatus Updatesの次の列で記録する（書くのはAstra）。列名は別名でも同じ項目として取り込まれる。足りない列は1.4で自動的に追加される。
   - `application_preparation_ai_time`（別名 `gen_minutes`）
   - `human_review_minutes`
   - `applied_at` と、応募時の `new_status=APPLIED`
   - `result`（accepted / rejected。別名 `accept_result`。APPLIEDの案件ではステータスが ACCEPTED / NOT_SELECTED に変わる）
   - `production_ai_time`（＝`actual_ai_processing`）
   - `production_human_minutes`（＝`actual_human_minutes`）
   - `revision_count`
   - `actual_net_reward`
   - 実績の Net ÷ Human Minutes は `metrics` の `poc_actual` と、各案件の `actual_net_per_human_min` で確認する。
   - 計算方法：実際の手取り ÷（応募確認の分＋制作の分）。見送り・不採用は0円として数える。

## 6. 保存
```bash
python3 scout/tests/test_application.py   # 応募準備の検証（本物のVaultは変更しない）
git add scout/state && git commit -m "Scout run <date>" && git push -u origin claude/brave-lovelace-7n0flp
```
- 暗号化されていない状態で個人情報をコミットしないこと。
  - `scout/data` と `scout/out` はgitignore済み。
  - Job Masterは `vault.enc` に暗号化して保存する。

## エラー時
- 収集に失敗した場合：`runs.jsonl` の `errors` に記録し、処理を続ける。
- Driveに接続できない場合：手順5をスキップして手順6まで進める。次回の実行で最新の状態を同期する。
