# Scout 日次実行手順（毎朝05:00 JST）

日次スケジュール：05:00 Claude Scout・一次評価（Astra Queue）→ 06:00 Astra QA（Status Updates）→ 06:30 Claude 判定取込・応募準備（本書7）→ 07:30 ChatGPTがREADY_TO_APPLYを通知

Claude Codeのセッションが上から順に実行する。応募・契約・納品・課金・外部公開は行わない。

## 0. 準備
```bash
git fetch origin claude/brave-lovelace-7n0flp && git checkout claude/brave-lovelace-7n0flp && git pull
# SCOUT_VAULT_KEY は環境の設定で環境変数として渡される（値を表示・ログ出力・コミットしない）
```
- Driveのファイル・フォルダのIDは、`python3 scout/pipeline.py export` の後に `scout/out/sync_manifest.json` の `drive` で確認できる。

## 1. ステータス更新の取り込み（Sheets → Job Master）
Status Updatesは本人の入力欄ではない。Astra と Claude Code がステータスを受け渡すためのシートで、書き込むのはAstraだけ。本人はAstraに報告するだけで、このシートは編集しない。
- Astraの判定は PASS / REJECT / NEED_USER / SKIPPED の4種類。正式な判定として扱うのは、`updated_by` がAstra名義の行だけ。Astra名義でない行は取り込まず、`errors` に出す。
- 反映先：PASS → `ASTRA_PASS`（手順5.7で応募準備）、REJECT → `ASTRA_REJECT`（理由をJob Masterに保存）、NEED_USER → 本人確認待ち、SKIPPED → 今回見送り。
- Status Updatesに判定がない案件は `ASTRA_QA_PENDING` のままにする。Astra Queueを作った・6時を過ぎた、といった理由で判定済みにしない。Claude CodeがAstraの判定を推測・代行しない。
- `READY_TO_APPLY` 以降に進んだ案件は、PASSなどの判定が再送されてもステータスを戻さない（`errors` に記録）。
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

## 4.5 Astra Manual Review Queue（本人がAstraへCrowdWorks URLを手動指定した場合）
本人 → AstraへURLを貼る → Manual Review Queue（PENDING）→ Claude一次評価 → Astra二次評価 → PASSなら既存のApplication Queue、という流れにする。
Queueへの登録は評価依頼であり、応募指示ではない。応募・契約・メッセージ・条件提示・納品はしない。
1. 登録（どちらか一方でよい。同じjob_idは二重登録されない）
   - Astra：Status Updatesに `job_id`（またはnoteに案件URL）、`new_status=MANUAL_REVIEW`、`updated_by=Astra` の行を書く。`apply-updates` がQueueに `PENDING` で登録する。Astra名義でない行は受け付けない。
   - URLがClaudeに直接届いた場合：`python3 scout/pipeline.py manual-request <URL…> --by Astra`
   - 本人が「応募したい」などの意向を示している場合は `--intent "<本人の言葉>"` で記録する。Astra Queueの `source` に表示されるだけで、Astraの判定の代わりにはならない。
2. 取得：`python3 scout/pipeline.py manual-fetch`
   - `PENDING` の案件だけ、通常Scoutと同じ取得・解析で最新の募集要項を読む（全件探索はしない）。
   - 結果は `data/<date>/manual_pending.json` に保存される。取得できない場合は `ERROR` になる。
3. 一次評価：`manual_pending.json` の各案件を、EVAL_GUIDEの項目に次を加えて評価し、JSON配列に保存する。
   - 追加する項目：`lane`（Professional / Experience / Auto / Human Premium）、`triage`（応募候補 / PoC応募候補 / 見送り候補）、AI完結率、想定Human Minutes、`gross_jpy`（本文の単価。不明ならnull）、`template_potential`、`repeat_reduction`、`worker_automation`、`paid_tools`、`confirm_items`（本人確認が必要な事項）。
   - 確認できない項目は推測せず「UNKNOWN」とする。
   - 低単価だけを理由に見送らない。低単価でもAI完結率が高く、本人作業が数分で、繰り返せる案件はAutoレーンの応募候補として扱う。
   - 最終的な応募判断はしない（3分類で返すだけ）。
4. 統合：`python3 scout/pipeline.py manual-merge --evals <json>`
   - 新規・Claude担当中の案件は `ASTRA_QA_PENDING` になり、既存のAstra Queueに出る。列 `source=manual(Astra)`・`lane`・`claude_triage`・`net_per_human_min`・`confirm_items` が付く。
   - 以前にAstra REJECTになった案件も、本人がManual Reviewを依頼した場合は `ASTRA_QA_PENDING` に戻してAstraに再評価を求める。`confirm_items` の先頭に前回のREJECT理由が入る。
   - 応募期限が処理日当日の案件は `SAME_DAY_DEADLINE`（期限を過ぎていれば期限切れ）として応募対象外にし、Astraには回さない。
   - ASTRA_PASS以降の既存案件はステータスを変えず、最新情報だけを更新する。
   - Queueは `REVIEWED` になる。状態は `manual-status` で確認できる。
5. その後は通常どおり：Astraの判定（Status Updates）→ `ASTRA_PASS` → 5.7の応募準備 → `READY_TO_APPLY` → 既存の通知・本人応募。
   - Astraの判定が、Status Updatesではなく本人経由でチャットに届いた場合：伝えられた判定と理由をそのまま、`updated_by=Astra` の1行としてローカルCSV（`data/<date>/astra_relay_<id>.csv`）に書き、`note` に「本人がチャットで転記（Status Updatesシート未記入）」と残して `apply-updates` で取り込む。判定が明示されていない場合は作らない。
6. 05:00の実行では、手順1のStatus Updates取り込みの後、手順4（merge）と手順5（Driveへの同期）の間に、この4.5を行う。`PENDING` がなければ何もしない。

## 5. Google Sheetsへの同期（06:00のAstra QAより前に終える。応募準備より先に行う）
現在のDrive MCPは既存ファイルの中身を書き換えられないため、次の手順でシートを差し替える。
1. フォルダ `CW Scout (ai×cloud works)` に、次の2つを `text/csv` でアップロードし、Googleシートに変換する。タイトルは `scout/out/sync_manifest.json` の `titles` を使う（生成日時入り。例：`CW Scout - Astra Queue｜2026-09-27 06:05 JST`）。
   - Job Master
   - Astra Queue
2. 古いシート（manifestに記録された `job_master_sheet` と `astra_queue_sheet`）を `trash_file` でゴミ箱へ移す。
3. 新しいIDを記録する：`python3 scout/pipeline.py set-drive job_master_sheet <id>`（`astra_queue_sheet` も同様）
4. `CW Scout - Status Updates (記入用)` は差し替えない。例外は1.4の列追加だけ。
5. Drive反映は省略しない（2026-09-29に、Job MasterとApplication Queueのアップロードが省略されたまま「成功」と報告された）。
   - アップロードするCSVは、Drive用の軽量版（1ファイル約50KB以内）。
     - `job_master.csv`：Claude/ルール除外・CLOSED・前日より前のASTRA_REJECTを除く。全件はローカルの `job_master_full.csv` とVaultにある。
     - `application_queue.csv`：原文抜粋を除く。応募済み・見送りの行は応募文を省略する（`application_queue.json` とVaultに残る）。
   - `export` が `WARNING: ... exceeds the Drive upload budget` を出した場合も、アップロードは行い、報告に書く。
   - CSVはファイルの中身をそのまま `textContent` に渡す。要約・省略・行の削除はしない。
   - アップロード後に `download_file_content`（`text/csv`）で取得し直し、ローカルのCSVと行数・内容が一致することを確認してから、古いシートをゴミ箱へ移す。
   - 最後に `python3 scout/pipeline.py drive-status --keys <対象>` を実行する。`ok=false` なら、どのシートが未反映かを報告に必ず書く（成功として報告しない）。
   - `set-drive` はVaultを更新するので、IDの記録後に `git add scout/state && git commit -m "Scout: record Drive sheet IDs <date>" && git push` を行う。

## 5.5 性能測定
- `python3 scout/pipeline.py metrics` で、累計の取得件数、Claude評価件数、Astra Queue件数、PASS・REJECTの件数、取りこぼし件数、Precision / Recallの目安を確認できる。
- `runs.jsonl` には実行ごとの値が記録される。

## 5.7 応募準備（Application Queue）※応募・フォーム入力・送信はしない
手順5の同期が終わってから行う。対象は、応募文がまだない `ASTRA_PASS` の案件（Status Updatesで取り込んだAstra PASS）。
1. `python3 scout/pipeline.py app-check` を実行し、`ASTRA_PASS` と `READY_TO_APPLY` の全件について、募集原文をその日のうちに再取得する。AIは使わない。
   - 確認する項目：募集中か、締切、募集枠、実際の報酬、AI利用条件、本文の条件行。
   - HOLDになるのは、応募判断に影響する実質的な変更だけ。
     - 報酬の減額。既知の実報酬、または評価時の想定より低い場合を含む。
     - 募集終了、募集枠の充足、期限切れ、期限の前倒し。
     - AI条件が厳しくなった場合。
     - 応募文の根拠にした報酬記載が本文から消えた場合。
     - 本文に条件行（連絡手段・応募資格・納期・報酬・AIなど）が追加された場合。
   - 取得できなかった値が取れるようになっただけの場合は、`notes` に記録するだけでHOLDにしない。例：見出しの「契約金額（目安）440円」が、既知の400円＋税と整合する場合。期限の延長や、条件行が増えない本文の修正も同様。
   - 1回だけ現れる変更（減額・前倒し・条件行の追加など）は、`recheck_flags` に残す。Astraが改めて判定するまで、HOLDのままにする。
   - 応募文がある案件は、この時点でREADY_TO_APPLYになるか、HOLDに戻るかが再判定される。APPLIED・SKIPPEDは変わらない。
2. `python3 scout/pipeline.py app-plan --cap 15` を実行し、`draft_now` の案件だけ応募文を作る。
   - 除外される案件：募集終了・枠充足・期限切れ・実質的な変更がある案件。上限の件数には数えない。
   - 並び順：応募期限の近い順、次に推定手取額 ÷ 推定本人作業時間の大きい順。
   - 上限（1回15件）を超えた分は `carry_over` に入り、次の実行（05:00または06:30）で処理する。
   - 報酬は一覧の表示額ではなく、募集本文の実額を使う（例：tokyoreve・supersameは本文の200円（税抜）＝税込220円）。
   - 募集終了・期限切れ・募集枠が埋まった案件は、応募文を作らない。
3. `scout/data/<date>/app_source/<id>.json` の原文と `show-profile` だけを使い、`scout/data/<date>/app_drafts.json` を作る。
   - 1件ごとの項目：`job_id`, `actual_reward`（税込）, `reward_evidence`（本文からそのまま引用）, `application_draft`, `application_questions`（本文の設問をそのまま）, `application_answers`, `facts_used`（`fact` と `profile_ref`、例：`professional.qualifications[2]`）, `unverified_facts`, `conflict_risk`（「低：」「中：」「高：」で始める）, `user_confirmation_required`, `review_minutes_est`, `next_action`
   - `application_draft` は、CrowdWorksで応募するときに送るメッセージ（挨拶・担当したい旨・どうまとめるか・結び）。記事本文や納品物は書かない。応募メッセージの形になっていない下書きは取り込まれず、READY_TO_APPLYにならない。
   - 本文に単価の記載がない場合は `actual_reward=null`、`reward_evidence` を「UNKNOWN（…）」とする。見出しの予算額を単価として使わない。
   - プロフィールにない経験・実績・好みは書かない。必要なら `unverified_facts` に入れ、回答欄は【本人記入】のままにして `user_confirmation_required=yes` にする。
   - 本Scout・AI運用を、AI案件の受注経験・コンサル経験・CrowdWorksでの実績として書かない。勤務先名は書かない。
   - 応募文ではAI利用に自分から触れない。募集本文の設問でAI利用を聞かれた場合だけ、回答欄で事実どおり答える（使わないと偽らない）。納品時は募集のAI条件（推敲して提出など）に従う。
4. `python3 scout/pipeline.py app-merge --drafts scout/data/<date>/app_drafts.json` を実行する。
   - 引用・設問・プロフィール参照が原文と一致しない下書きは取り込まれない。
   - 次の条件をすべて満たす案件だけが、自動で `READY_TO_APPLY` になる：当日の原文再確認で変化なし、本人確認が必要な項目なし、実績を主張する表現なし、利益相反リスクが「低」。
   - 満たさない案件は `ASTRA_PASS` のまま残り、`next_action` に保留理由が入る。
5. `CW Scout - Application Queue｜<生成日時> JST` をJob Master等と同じ方法で差し替え、`set-drive application_queue_sheet <id>` で新しいIDを記録する。
6. `READY_TO_APPLY` 以降の案件を、応募準備の対象として再び提示しない。
7. 応募直前に原文を確認したいときは `app-check --ids <READY_TO_APPLYのid>` を実行する。`recheck_changes` に変化や募集終了が出たら、応募しない。
8. 応募の記録：本人はCrowdWorksで応募したあと、Astraに「N件応募した」と報告するだけ。その報告をもとに、AstraがStatus Updatesに `new_status=APPLIED`・`applied_at`・`human_review_minutes`・`updated_by=Astra` を書き、Claude Codeが次の実行で取り込む。
   - 報告が来るまで、案件は `READY_TO_APPLY` のまま変えない（Claude側で推測してAPPLIEDにしない）。
   - 応募日時・作業時間は推測しない。本人が伝えていなければ空欄のままにする。
   - 本人が複数件の合計時間だけを伝えた場合は、案件別に割り振らない。`app-batch --ids <id,...> --minutes <合計> --source <出典>` でバッチ実績として記録する。バッチに含まれる案件の `human_review_minutes` を案件別に書いた行は、取り込まずに `errors` に出す。
9. 実測PoCはStatus Updatesの次の列で記録する（書くのはAstra）。列名は別名でも同じ項目として取り込まれる。足りない列は1.4で自動的に追加される。
   - `application_preparation_ai_time`（別名 `gen_minutes`）
   - `human_review_minutes`
   - `applied_at` と、応募時の `new_status=APPLIED`
   - `result`（accepted / rejected。別名 `accept_result`。APPLIEDの案件ではステータスが ACCEPTED / NOT_SELECTED に変わる）
   - `production_ai_time`（＝`actual_ai_processing`）
   - `production_human_minutes`（＝`actual_human_minutes`）
   - `revision_count`
   - `actual_net_reward`
   - KPIは `metrics` の `kpi` で確認する。全体・auto（分類A/B）・professional（分類C＝Professional / Human Premium）・分類別に分かれ、`estimated`（推定値）と `actual`（実測値）を別々に出す。
   - `kpi` の項目：discovered, Claude候補率, Astra PASS率, 応募率, 受注率, 実際の手取り, 応募準備AI時間, 制作AI時間, 応募確認の分, 制作の分, 修正回数, Net ÷ Human Minutes（推定・実測）
   - 案件ごとの実測値は `actual_net_per_human_min` で確認する。バッチ実績は全体と、同じ区分の案件だけのバッチでその区分に入る。
   - 計算方法：実際の手取り ÷（応募確認の分＋制作の分）。見送り・不採用は0円として数える。

### 5.7.1 本人指定案件（手動取り込み）
Scoutが拾っていない案件を本人が指定した場合、その1件だけを処理する（既存案件の再取得・再分析はしない）。
1. 公開ページで条件を確認する（報酬・契約金額の指定・源泉徴収・期限・募集状態・AI利用条件・応募条件）。分からない項目は「未確認」とし、推測しない。
2. Claudeが評価JSON（EVAL_FIELDS、`gross_jpy`＝本文の単価）を作り、`python3 scout/pipeline.py manual-add --id <id> --eval <json>` を実行する。
   - 登録済みなら何もしない（重複登録・ステータス後退なし）。
   - 登録されると `designation`（本人指定）付きの `CLAUDE_CANDIDATE` になり、評価理由の先頭に【本人指定】が付く。Astra判定は作らない（Astra欄は空のまま）。
3. 5.7の手順1〜4と同じく `app-check --ids <id>` → 応募文 → `app-merge`。条件を満たせば `READY_TO_APPLY` になる。条件を外れた場合は `CLAUDE_CANDIDATE` に戻る。
4. Job Master・Application Queueは、手順5の方法でDriveに反映する。

## 5.8 受注後（Worker工程）※標準フロー。外部送信・納品はしない
ACCEPTED → 仮払い確認 → クライアント最新指示確認 → Claude Worker制作 → 自己QA → 内部管理用成果物を保存
→ READY_FOR_QA（Worker state = ASTRA_QA_PENDING）→ Astra第二段階QA →（FIXなら修正して再提出）→ Astra QA PASS
→ クライアント納品用成果物を生成 → 直接URLを取得・検証 → READY_TO_DELIVER → 本人へ納品準備完了通知
→ 本人がリンクを開いて最終確認 → 本人がCrowdWorksで納品。
`python3 scout/pipeline.py worker-status` で、受注案件ごとの次の工程が分かる。

### 成果物は2種類。混ぜない
- **内部管理用成果物**（AIチーム内だけ。クライアントには渡さない）
  - Vaultの `master[<id>].worker`：job_id・案件名・クライアント指示・制作条件・使用した本人事実・切り口・下書き・最終稿・文字数・自己QA・修正履歴・Astra QA結果・ステータス。
  - Astra確認用に、Driveに `CW Worker <id>｜<テーマ>｜…` のGoogleドキュメントを置いてよい（内部用。納品には使わない）。
- **クライアント納品用成果物**（Astra QA PASSの後にだけ作る）
  - 中身は、求められた成果物と最低限のタイトルだけ。
  - Claude・Astra・AIチーム・QA・プロンプト・修正履歴・内部ステータス・Job Master・Status Updates・Vault・処理ログ・内部メモ・プロフィール管理情報・求められていない説明は入れない。
  - ファイル名はテーマや内容から自然に付ける（例：`投資信託について`）。`CW Worker`・job_id・`ASTRA_QA_PENDING`・`Final`・`Draft`・`QA PASS` などは使わない。

### 手順
1. 受注の確認：Status UpdatesのAstra名義の `ACCEPTED` 行とVaultの案件が同じjob_idであること。不整合があれば制作しない。
2. 仮払いの確認：Astra名義の行に「仮払い完了」または `new_status=IN_PROGRESS` があること（`apply-updates` が `worker.escrow_confirmed` に記録）。なければ制作しない。
3. 制作：クライアントの最新指示を制作要件にする。切り口を3つ以上検討して1つ選ぶ。一次成果物 → 文字数確認 → 自己QA（PASS/FIX）→ FIXを修正 → 改善は1回だけ。本人経験は登録済みの事実と、Astra/本人が確認した事実だけを使う。
4. 保存：内部記録をJSONにして `python3 scout/pipeline.py worker-save --id <id> --record <json>` を実行する。
   - 必須：`client_instructions`・`angle`・`profile_facts_used`・`draft_v1`・`self_qa_final`（全PASS）・`final`。
   - 結果は `READY_FOR_QA`、Worker stateは `ASTRA_QA_PENDING`。案件ステータスを `ASTRA_QA_PENDING` にはしない（応募前のAstra Queueに戻ってしまうため）。
5. Astra QA：Status UpdatesのAstra名義の行を `apply-updates` で取り込む。
   - `PASS`（または `new_status=READY_TO_DELIVER`）：そのとき提出済みの最終稿に対するPASSとして記録する。ステータスはClaudeが納品物を検証するまで `READY_FOR_QA` のまま。
   - `FIX` / `REVISE` / `修正`：`IN_PROGRESS` に戻り、Worker stateは `REVISE`。修正して手順4からやり直す。最終稿が変わるとPASSは無効になり、Astra QAがもう一度必要になる。
   - `HOLD` / `保留`：停止する。
6. 納品物の生成：クライアント指定の形式で作る。
   - 「WordまたはGoogleドキュメント」ならGoogleドキュメント。Word指定ならWord、Excel指定ならExcel。
   - 複数の形式を選べる場合は、追加費用ゼロ・本人作業が最少・クライアントが確認しやすいものを選ぶ。判断できないときだけAstraに確認する。
   - Googleドキュメントは、Drive MCPの `create_file`（`text/plain`、タイトル＝ファイル名、本文＝タイトル行＋最終稿）で作る。
7. 検証：作ったファイルを `download_file_content` で取得し直して保存する。そのうえで次を実行する。
   `python3 scout/pipeline.py worker-deliver --id <id> --title <ファイル名> --type gdoc --file-id <ID> --url https://docs.google.com/document/d/<ID>/edit --exported <取得した本文> --deadline <納期> --deadline-basis <根拠>`
   - 次のすべてを満たしたときだけ `READY_TO_DELIVER` になる：受注済み、仮払い確認済み、自己QA完了、Astra QA PASS（その最終稿に対するもの）、クライアント指定の形式、内部情報なし、本文がPASS版と一致、ファイルそのものを開く直接URL（フォルダURL不可・内部管理用ドキュメント不可・検証したファイルIDと一致）。
   - どれかが欠ければ、理由を出してステータスは変えない。
8. 引き継ぎ：Job Masterの `worker_status`・`worker_astra_qa`・`delivery_artifact_url`・`delivery_artifact_type`・`delivery_deadline`・`ready_to_deliver_at` に出る（報酬は `gross_jpy`／`net_est_jpy`）。Driveへの反映は手順5と同じにする。Status Updatesの列は変えない。
9. 通知：`python3 scout/pipeline.py worker-notice --id <id>` の文面だけを本人に出す（【納品準備完了】、案件名、job_id、報酬、納品期限、納品物の直接URL、形式、Astra QA、ステータス、「上記リンクを開いて最終確認 → 問題なければCrowdWorksで納品」）。内部管理用ドキュメントのリンクやQAの詳細は、本人から求められたときだけ出す。
10. 納品後：本人の報告を受けたAstraが、Status Updatesに `DELIVERED` / `PAID` などを書く。
- 禁止：CrowdWorksへの納品・メッセージ送信、Chatworkなどクライアントへの送信。自己QAだけで納品可能とすること。Astra QA PASS前に納品物を最終版として確定すること。

## 6. 保存
```bash
python3 scout/tests/test_application.py   # 応募準備の検証（本物のVaultは変更しない）
python3 scout/tests/test_recheck.py
python3 scout/tests/test_worker.py
python3 scout/tests/test_manual.py
python3 scout/tests/test_manual_review.py
git add scout/state && git commit -m "Scout run <date>" && git push -u origin claude/brave-lovelace-7n0flp
```
- 暗号化されていない状態で個人情報をコミットしないこと。
  - `scout/data` と `scout/out` はgitignore済み。
  - Job Masterは `vault.enc` に暗号化して保存する。
- pushが拒否された場合は、手順7の8と同じにする（force pushしない。先に入ったコミットが `scout/state/` を変更していなければrebaseして1回だけpushし直す。変更していれば、Driveも更新せずに終了する）。

## 7. 第2Routine：Astra判定の取り込みと応募準備（毎朝06:30 JST、06:00のAstra QAの後）
Scoutの収集・ルール処理・Claude一次評価は行わない。Astra Queueは作らない。Status Updatesの列追加（1.4）もしない。
1. `git pull`（手順0と同じ）
2. `python3 scout/pipeline.py postqa guard`
   - `ok=false`（当日の05:00 Scoutの記録がまだない＝実行中・未実行・失敗）なら、何もせずに終了する。
3. Status UpdatesをCSVで取得し、`python3 scout/pipeline.py postqa astra --csv <csv>` を実行する。時刻だけでは処理を始めない。
   - `ok=false`：当日のScout実行より後に書かれたAstra名義の判定がない（06:00のAstra QAが未実行・失敗・遅延）。取り込みも応募準備もせずに終了する。判定は翌朝05:00の実行で取り込まれる。
   - `ok=true, complete=false`：Astraがまだ書き込み中の可能性がある。テストの実行など他の作業を先に済ませてから、CSVを取得し直してもう一度確認する（1回だけ。sleepで待たない）。それでも未完了なら、判定済みの案件だけを取り込む。未判定の案件は `ASTRA_QA_PENDING` のまま残し、報告に件数を書く。
   - `ok=true, complete=true`：次へ進む。
4. 同じCSVで `apply-updates --csv <csv>` を実行する。
   - Astra名義の新しい行だけが反映される（PASS / REJECT / NEED_USER / SKIPPED / APPLIEDなど）。
   - 05:00の実行で取り込み済みの行は、行ごとの署名で除外され、二重に反映されない。
   - Driveが使えない、またはAstra QAが未実行・失敗で新しい行がない場合は、ここで何も生成せずに終了する。
4.5 受注案件：`python3 scout/pipeline.py worker-status` を実行する（手順5の終了判定より先に行う）。
   - `worker_status=ASTRA_QA_PASS` の案件：5.8の手順6〜9（納品物の生成 → 検証 → READY_TO_DELIVER → 通知文）を行う。
   - `REVISE` の案件：Astraの指摘を反映して修正し、5.8の手順4で再提出する。
   - それ以外は何もしない（仮払い確認待ち・Astra QA待ち・HOLD）。
5. `python3 scout/pipeline.py postqa targets` を実行する。
   - `action=none` なら、応募準備をしない。
     - ステータスの変化（`new_status_changes`）が1件以上あれば、手順7へ進む。
     - 0件なら、何もせずに終了する。
   - `targets` は、今回の取り込みでASTRA_PASSになり、まだ応募文がない案件だけ。
6. 5.7の手順1〜4（app-check → app-plan → 応募文 → app-merge）を行う。条件を満たした案件だけが `READY_TO_APPLY` になる。`targets` が空でも、既存の応募文の再判定のために app-check は行う。
7. テストを実行する：`python3 scout/tests/test_application.py`・`python3 scout/tests/test_recheck.py`・`python3 scout/tests/test_worker.py`・`python3 scout/tests/test_manual.py`・`python3 scout/tests/test_manual_review.py`
8. 保存する：`git add scout/state && git commit -m "Scout post-QA <date>" && git push -u origin claude/brave-lovelace-7n0flp`
   - pushが拒否された場合は、force pushしない。
     1. `git fetch` を行う。
     2. 先に入ったコミットが `scout/state/` を変更していなければ（コードやRUNBOOKだけの変更）、`git rebase` して1回だけpushし直す。
     3. `scout/state/` が変更されていれば、Driveも更新せずに終了する。未反映の行は、次の実行で取り込まれる。
9. pushできた場合だけ、Driveを更新する。07:30のREADY_TO_APPLY通知より前に終える。この手順は省略しない。
   - Application QueueとJob Masterを、手順5と同じ方法で差し替える（手順5の5を含む）。
   - Astra Queueは差し替えない（Astra QAの対象は05:00の実行が決める）。
   - `python3 scout/pipeline.py drive-status --keys job_master,application_queue` が `ok=true` になるまでが完了。
   - IDを記録したVaultをcommit・pushする（手順8と同じ規則）。
10. 報告の最後に、Drive反映の結果（シートごとに「更新済み／未反映と理由」）を必ず書く。

## エラー時
- 収集に失敗した場合：`runs.jsonl` の `errors` に記録し、処理を続ける。
- Driveに接続できない場合：手順5をスキップして手順6まで進める。次回の実行で最新の状態を同期する。報告には「Drive未反映」と明記し、成功として報告しない。
