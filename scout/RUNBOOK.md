# Scout 日次実行手順（毎朝05:00 JST ＋ 17:00 軽量Delta Scan）

日次スケジュール（2026-09-30改定）：05:00 Claude Scout・一次評価・**応募準備draftまで完了**（Astra Queueに応募文・設問回答を載せる）→ 06:00 Astra QA（案件の評価と応募文・設問回答をまとめて二次QA。必要なら応募文も修正）→ Astraが本人へ最終通知。

Astra→Claudeの判定の受け渡しは日次フローに無い。Astra PASS後にClaudeへ判定を戻して応募文を作る構造（旧06:30 / 12:30 Routine、Status Updates CSVのbase64復元）は廃止した（Drive connectorではSheets/CSVを無損失に取得できないため。7章）。Status Updatesは、監査・履歴用の正本として残す。応募文はAstra QA前のdraftで、Claudeは `READY_TO_APPLY` にしない。

**通知経路は一本化する**：本人への案件通知（READY_TO_APPLYの案件・応募文、納品準備完了など）は ChatGPT / Astra 側だけが行う。Claude の05:00 Routineはバックエンド処理（Scout、一次評価、応募文draft生成、Claude QA、Astra Queue生成、Vault / Drive同期）だけを行い、正常終了時は案件URL・案件名・応募文・納品リンクを報告に書かない（Routineの完了報告は本人のスマホにプッシュされるため、書くと二重通知になる）。Claude から本人に知らせてよいのは、本人対応が必要な異常だけ：Routineの失敗、Drive同期の失敗・未反映、データ不整合（push競合で未反映、Vault/Status Updatesの矛盾など）、SCOUT_VAULT_KEY未設定など。その場合は報告の1行目を【要対応】で始める。

Claude Codeのセッションが上から順に実行する。応募・契約・納品・課金・外部公開は行わない。

## 0. 準備
```bash
git fetch origin claude/brave-lovelace-7n0flp && git checkout claude/brave-lovelace-7n0flp && git pull
# SCOUT_VAULT_KEY は環境の設定で環境変数として渡される（値を表示・ログ出力・コミットしない）
```
- DriveのシートはIDを固定しない（同期のたびに新しいファイルへ差し替わり、古い版はゴミ箱へ移る）。どのシートを読む・消すかは、毎回フォルダの一覧から決める（5.0）。`sync_manifest.json` の `drive` は前回記録したIDで、「最新版」の根拠にはしない。

## 1. ステータス更新の取り込み（Sheets → Job Master）【日次フローから外した。休止中】
Status Updatesは監査・履歴用の正本（書き込むのはAstraだけ）。**Claudeの日次Routine（05:00）は、Status Updatesを取得・取り込みしない。** 「Astra QA後にClaudeへ判定を戻して応募文を作る」構造を廃止したため、判定の取り込みは不要になった。
- 次の処理は行わない：`download_file_content` でのCSV取得、base64の手動復元、`apply-updates`、`postqa astra/catchup`、`su-columns`。CSV/base64の手作業復元・`read_file_content` の抜粋・チャット本文からのAstra判定の取り込みは、フォールバックとしても使わない。
- Claudeは、Astraの判定を推測・代行・転記しない。判定がない案件は `ASTRA_QA_PENDING` のまま。
- コード（`apply-updates` など）は残してあるが、Astraが構造化された受け渡し方式（Sheets connectorやGit経由など）を用意するまで、日次では動かさない。それを前提にする工程（受注後のWorker工程5.8、応募済み・受注の記録、Client Masterへの応募・受注の反映）も、その方式が決まるまで休止する。
- 以下は旧フロー（休止中）の記録：

Status Updatesは本人の入力欄ではない。Astra と Claude Code がステータスを受け渡すためのシートで、書き込むのはAstraだけ。本人はAstraに報告するだけで、このシートは編集しない。
- Astraの判定は PASS / REJECT / NEED_USER / SKIPPED の4種類。HOLD（保留）はNEED_USERとして扱い、応募準備しない。正式な判定として扱うのは、`updated_by` がAstra名義の行だけ。Astra名義でない行は取り込まず、`errors` に出す。
- 応募後の辞退は `new_status=WITHDRAWN`（APPLIED以降の状態として扱い、判定の再送で戻さない）。面談が理由なら理由欄に「面談」を書く（4.6）。
- 反映先：PASS → `ASTRA_PASS`（手順5.7で応募準備）、REJECT → `ASTRA_REJECT`（理由をJob Masterに保存）、NEED_USER → 本人確認待ち、SKIPPED → 今回見送り。
- Status Updatesに判定がない案件は `ASTRA_QA_PENDING` のままにする。Astra Queueを作った・6時を過ぎた、といった理由で判定済みにしない。Claude CodeがAstraの判定を推測・代行しない。
- `READY_TO_APPLY` 以降に進んだ案件は、PASSなどの判定が再送されてもステータスを戻さない（`errors` に記録）。
- 同じ案件に複数の行がある場合は、`updated_at` の新しい行が最後に反映される（シート上の並び順に関係なく、最新の判定が残る）。
1. 5.0の `drive-resolve` が返した `status_updates_sheet` のIDで、Drive MCPの `download_file_content` を使い `text/csv` として取得する。
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
- 検索入口（`collect.py` の `QUERIES`）：
  - `writing_all`（category_id=228）・`task_all`（payment_type=task）・専門キーワード（tier C）は従来どおり。
  - データ・調査系カテゴリ（tier D。2026-10-03にCrowdWorks公開ページの category id と名称、各カテゴリページのタイトル・検索結果の category_id で確認）：54 データ検索・データ収集／52 データ入力／282 スクレイピング・データ収集／146 資料作成・マニュアル作成／100 市場調査・マーケットリサーチ／86 調査・リサーチ／201 リスト作成／103 データ分類・カテゴリ分け／66 データ分析・統計解析。カテゴリIDは推測で足さない（追加するときは同じ方法で確認する）。
  - Auto系キーワード（tier D）：営業リスト・企業リスト・転記・データ整理・競合分析。
  - AI-BPOグループ（tier E。2026-10-03に確認：`/public/jobs/category/367` が「AI-BPO（AI活用の業務改善）」のグループページで、その検索結果の category_id は368〜372だけ。各IDのページタイトルで名称、各IDの検索結果がそのIDだけであることを確認）：368 AI営業・マーケティング支援／369 AIバックオフィス支援／370 AIシステム開発・導入・自動化支援／371 AIメディア・コンテンツ構築支援／372 その他業務AI化支援。13502286（AI画像生成＋Canvaの投稿画像制作）は371。
  - Autoはテキストに限らない：tier E（AI-BPO）では、生成AI・AI画像生成・Canva・AI動画・資料・投稿/記事/台本などのAI補助制作も Auto の手掛かりにする。AI-BPO以外では、AI生成が明示的に許可され（AI条件A）、成果物が画像・Canva・スライド・動画などのときだけ（AIライティングの扱いは従来どおり）。
  - tier D / E で Claude前に除外：有料ツールの追加契約が必須（Canva Pro・Adobe・Midjourney等の契約・用意が必須）、本人の撮影・出演・音声（顔出しでの掲載、ご自身で撮影した写真、音声収録など。「顔出し不要」やZoom面談での顔出しは対象外）、AI生成禁止・AI使用禁止、手作業指定、SNSのDM・フォーム送信作業。AI・自動処理で本人作業を減らせる手掛かりもプロフィール接点もないAI-BPO案件（営業・エンジニア常駐・相談役など）も除外。継続的な本人拘束（稼働条件が重い）は従来どおり除外、即レス等は減点。
  - tier D は、プロフィールのキーワードに一致しなくても Auto 処理の手掛かり（データ入力・収集・転記・Excel/スプレッドシート・PDF/Word・分類・要約・リライト・調査・資料作成など）があれば残す。手掛かりもプロフィール接点もない案件（アンケート・モニター・視聴・現地作業など）はClaude前に除外する。
  - Auto案件の推定本人作業時間（Claude前の順位付け用）は、手作業の時間ではなく自動処理後の確認時間（予算相当時間の15%、最低15分）。スタッフ・アシスタント・秘書など時間で働く募集には適用しない。
  - 検索母集団が増えても、Claudeに送る件数は従来の上限（`prepare` の `--cap 60`・`--budget-chars 70000`）のまま。順位は 推定手取額 × 受注確率 ÷ 推定本人作業時間 に係数を掛けたもの。`runs.jsonl` の `candidate_mix` に、Claudeへ送った案件の内訳（Professional / Auto / Other、手取り1,000円以上 / 未満、データ系カテゴリ由来）が残る。
- Claude評価の前に除外する案件：
  - 募集枠の充足（`FILLED_CAPACITY`）：検索結果の契約済み人数 ≥ 募集人数のとき。本文に追加募集・継続募集・募集人数を超えての採用が明記されていれば除外しない。人数が取得できない・意味が曖昧なときも除外しない。
  - 電話対応・架電、ツール使用禁止・手入力指定（tier D）、規約上許されない自動取得（会員ログイン後の取得・スクレイピング禁止）、SNSのDM・フォロー・問い合わせフォーム営業の送信作業（tier D）、営業パートナー（成果報酬型）。
  - 即レス・平日日中の常時対応・定例会議など拘束の大きい案件、「未経験・簡単」で高額なtier Dの募集は除外せず、順位を下げる。
  - 期限切れ
  - 低価値（推定で1分あたり8円未満）
  - 成果報酬型の営業
- 件数と字数の測定：評価件数と入力字数は `runs.jsonl` の `est_ai_usage` に記録される。PoC期間中はこれを見て `--cap` と `--budget-chars` を調整する。
- `prepare` の処理：
  - 重複の除外：変化のない既知の案件はスキップする
  - ルールによる除外
  - 差分の再評価対象の抽出：報酬、条件、AI条件、募集状態、発注者の変化
  - バックログからの補充
- 出力：`scout/data/<date>/pending_eval.json`

## 2.1 17:00 軽量Delta Scan（evening_delta）
05:00（morning_full）は従来どおり。17:00は、05:00以降に新しく掲載された案件・重要条件が変わった案件・05:00で取れなかった案件だけを見る軽量の実行。
```bash
python3 scout/collect.py --run evening            # 常にdelta。出力は scout/data/<date>/evening/
python3 scout/pipeline.py prepare --run evening   # 既定：最大15件・評価入力20,000字（RUN_LIMITS["evening"]）
# Claude一次評価（EVAL_GUIDE。pending_eval.json は scout/data/<date>/evening/）→ evals.json も同じ場所
python3 scout/pipeline.py merge --run evening --evals scout/data/<date>/evening/evals.json
# 応募準備（4.7：app-check → app-plan --cap 40 → app_drafts.json → app-merge）、Drive同期（5.0・5）、
python3 scout/pipeline.py daily-metrics           # 05:00だけの場合との比較を state/daily_metrics.jsonl へ
```
- 対象：
  - 05:00で処理済み（Claude評価済み、またはルール判定済み）で変化のない案件は、Claudeに再送しない。
  - 05:00以降の新着（indexにない案件）、ルール判定済みの案件で詳細が変わったもの（`条件変更`）、Claude評価済み・未キューの案件の重要な変更（報酬変更・募集条件変更・AI利用条件変更）。
  - Astra Queueに入っている案件（06:00のAstra QAが使った可能性がある）の変更は、17:00では再評価せず、案件の `change_log` と Astra Queue の `condition_change` 列に記録して、翌05:00の通常処理に回す。期限の延長など重要でない変更も05:00に回す。
- 検索・ルール処理は対象の新着全件に行う。Claudeに送るのはルール通過後の上位だけ。順位は05:00と同じ式（推定手取額×受注確率÷推定本人作業時間×AI条件・適合度・分類・同発注者の案件数・緊急度）に、17:00だけ応募速度（掲載12時間以内で募集枠に余裕：×1.2）と継続性（継続・長期・定期：×1.1）を掛ける。
- 17:00で枠に入らなかった案件（`deferred_to_morning`）は、index を17:00前の状態に戻す（新着はindexから外す）。翌05:00のDelta Scanが、17:00が無かった場合と同じく新着・変更として扱う（バックログに埋もれない）。
- Claude利用量の上限（2026-10-03の実データで決定。`RUN_LIMITS`）：
  - 05:00：最大60件・70,000字（変更なし）。
  - 17:00：最大15件・20,000字（05:00の約29%）。根拠：10/3の05:19〜17:00に掲載され、検索・ルールを通った新着は32件（処理133件）。1件あたり平均1,295字。優先度上位15件で17,416字で、上位には手取り1,000円以上の案件が集まる（上位6件はすべて手取り1,000円以上）。9/30〜10/3の候補（Astra Queue入り）のうち、前日05:15〜17:00の掲載は1日あたり約7件で、評価件数に対する候補率（約40%）から15件でほぼ拾える。
  - 実利用量は `runs.jsonl` の `est_ai_usage`（`run_type` ごと）と `daily_metrics.jsonl` の `claude_usage.evening_extra` に残る。17:00で枠外が毎回多い（`deferred_to_morning`）、または17:00の候補がほとんど出ない場合は、`prepare --run evening --cap/--budget-chars` または `RUN_LIMITS` を見直す。
- Astra Queue：17:00で一次評価を通過した案件は、同じ Astra Queue に追加する（job_id単位で1行。05:00分と重複しない）。`scout_run` 列が `<date> evening_delta`、`queued_at` がキュー投入時刻。05:00分は `<date> morning_full`（2026-10-03以前の行も同じ。当時は05:00だけ）。Astraの同日中の二次QAは `scout_run` = 当日の `evening_delta` の行だけを見ればよく、06:00に処理済みの05:00分を再通知しない（Astra側の設定は本Routineから変更しない）。`released_at` は掲載日時（CrowdWorksの公開日時。再掲載ではその時刻）。
- 状態遷移：05:00（新着・変更→ルール→上位60件をClaude→Astra Queue `morning_full`→応募draft→Drive）→ 06:00 Astra QA → 17:00（05:00以降の新着・重要変更→ルール→上位15件をClaude→Astra Queue `evening_delta`→応募draft→Drive。枠外とキュー済み案件の変更は翌05:00へ）→ 翌05:00（17:00の枠外は新着として、Astra Queue案件の変更は変更として通常処理。17:00で評価済みの案件は再評価しない）。

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

## 4.7 応募準備（Astra QAの前・05:00で完了する）※応募・フォーム入力・送信はしない
手順4（merge）で `ASTRA_QA_PENDING`（Astra Queue）になった**当日の全案件**について、Astra判定を待たずに応募文・設問回答のdraftまで作る。Astra判定の取り込みは不要。
流れ：案件取得 → Delta Scan → ルール除外 → Claude一次評価 → Client Master確認 → 本人プロフィール/確認済み事実照合 → 応募文生成 → 応募設問回答案生成 → Claude QA → Astra Queue生成 → Drive同期
1. `python3 scout/pipeline.py app-check`：当日のAstra Queue案件（`pre_draft_due`）の募集原文を再取得する（AIは使わない）。それ以前にAstra Queueへ送った案件は対象にしない（既存データは再評価しない）。
2. `python3 scout/pipeline.py app-plan --cap 40`：`draft_now` の案件だけ応募文を作る。`client` の関係（NONE / APPLIED / ORDERED / DELIVERED）と冒頭、`confirmed_facts` に必ず合わせる（5.7.1〜5.7.3・5.7.2）。上限を超えた分は `carry_over`（次の05:00で処理。Astra Queueには `NO_DRAFT` で載る）。
3. 応募文を `scout/data/<date>/app_drafts.json` に作る（項目・禁止事項は5.7の3と同じ）。
   - `client_history` はAstra Queueの列として自動で出る。
   - 既知プロフィールと確認済み事実（5.7.2）を最大限使い、既知の情報を【本人記入】にしない。未登録の経験・嗜好・実績は推測しない。
   - 氏名・ニックネームなど本人がCrowdWorks画面で直接入力する情報は、確認事項にしない・応募文を保留する理由にしない（`app-merge` が確認事項に入れた下書きを拒否する）。
   - 本人確認が本当に必要な事実（契約・支払い、外部送信、未登録の本人経験・資格、守秘義務・利益相反・勤務先、最終納品）だけ `unverified_facts` に入れ、`user_confirmation_required=yes` にする。なければ `no`。HOLDにしない。
   - 低単価で私的な嗜好・将来希望の確認だけが必要な案件は、確認コスト（手取り ÷（確認分＋本人確認5分）が100円/分未満）を含めて評価し、`confirm_cost=REJECT_CANDIDATE`（Astra REJECT候補）として送る。
4. `python3 scout/pipeline.py app-merge --drafts scout/data/<date>/app_drafts.json`
   - 原文（報酬・設問）・プロフィール参照・Client Master（冒頭）に合わない下書きは取り込まれない。
   - 取り込まれた下書きは、案件ステータスが `ASTRA_QA_PENDING` のまま。`READY_TO_APPLY` には**ならない**。`final_qa_status` は `CLAUDE_QA_PASSED`（確認事項なし）または `CLAUDE_QA_FLAGGED`（確認事項あり。`next_action` に内容）。`stage=PRE_ASTRA`。
   - 同じjob_idの下書きは作り直さない（`already_drafted`）。同じ入力を何度実行しても結果は変わらない。
5. Astra Queueに追加した応募準備情報：`application_draft` / `application_questions` / `application_answers` / `facts_used` / `unverified_facts` / `client_history` / `final_qa_status` / `user_confirmation_required` / `draft_next_action`。応募文がない案件は `final_qa_status=NO_DRAFT`。
   - 原文由来の列（2026-10-02〜。`source_facts.py`、AIは使わない。`app-check` が原文から作る）：
     - `application_questions_status`：`VERIFIED`（設問・応募時記載事項あり。`source_questions` に全文）／`NONE_VERIFIED`（原文を最後まで取得し、設問なしを確認）／`SOURCE_INCOMPLETE`（原文が途中まで。設問の有無は不明）／`FETCH_FAILED`（取得失敗）／`UNCHECKED`（app-check前）。取得できないものを「設問なし」にしない。
     - `reward_status`（CONFIRMED / AMBIGUOUS / UNKNOWN / FETCH_FAILED / SOURCE_INCOMPLETE）・`applicable_reward`（今回の応募に適用される報酬。初回テスト等があればその額）・`initial_reward`・`ongoing_reward`・`reward_basis`・`reward_source_excerpt`（原文の報酬行を全文）。`tier` と `applicable_net_per_human_min` は今回適用報酬で計算する（継続報酬を今回の期待利益にしない）。今回適用報酬が確定できなければ `UNKNOWN（今回適用報酬が不明）`。
     - `client_facts`：クライアントの数値を意味ごとに分けたもの（CW公開の募集実績・評価平均／Scout検知の他募集／当方の応募・受注・納品・支払・リピート）。ある項目の数を別の項目として扱わない。記録のない値は出さない（UNKNOWN）。
     - `provenance`：取得（fetch：状態・字数）→ 抽出（questions / reward の extraction_status）→ draft（app-merge：原文の設問N行→draft設問・回答数、原文報酬→draft報酬、facts_usedの参照先）→ Drive（省略した列・重要列が無傷か）→ repair の記録。どこで欠けたかをここで判別する。
     - `truncated`：Driveの容量（80KB）を超えたときだけ、低重要度の列（ai_steps・human_steps・hourly_est・repeatability・source_check・pay_detail・user_questions・profile_link・key_excerpt・claude_reason・requirements・provenance の順）を列ごと空にし、その列名を `truncated=true：…` で示す。設問・今回適用報酬・未確認本人事実・応募文は削らない（削られていないことを export が確認する）。`key_excerpt` の抜粋が上限で切れた場合も `truncated=true` を付ける。全文はVault（`job-detail`）。
   - `app-merge` が取り込まない（直して再merge）：原文に設問があるのに `application_questions` が空／継続報酬を今回の報酬にしている（初回報酬がある、または今回分が確定できない）／応募文・回答にクライアント記録と合わない実績数（例：「27件納品」）。
   - Claude QAの指摘（取り込んだうえで `CLAUDE_QA_FLAGGED`。案件はREJECTしない）：原文の設問のうちdraftにないもの、設問と回答の数の不一致、`SOURCE_INCOMPLETE` / `FETCH_FAILED`、今回適用報酬が不明、一次評価（client_risk・reason）の実績数の誤変換。
   - 既にAstra Queueにある案件の補完：`python3 scout/pipeline.py repair-source --ids <id,...>`。原文を取り直して設問・報酬構造・取得状態だけを補い、保存済みのdraftにClaude QAをかけ直す（draft・ステータス・Astraの判定は変えない。取得できなければ `FETCH_FAILED` のまま）。
   - `claude_qa_result`：Claude QAの結果を1セルで（`PASS` / `FLAGGED：<Astraに見てほしい点>` / `NO_DRAFT`）。
   - `tier`：主力＝手取り見込み1,000円以上／マイクロ＝1,000円未満でも、AI完結率80%以上かつ手取り÷本人作業分が30円/分以上／基準外＝それ以外／UNKNOWN＝報酬不明（本文に単価なし）。報酬は応募文作成時の本文の実額、なければ評価時の見込み。
6. Application Queue・READY通知（ready-notice）にはPre-Astraのdraftは出ない（READY_TO_APPLY以降だけ）。応募文の修正・本人への最終通知はAstra側が行う。

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
   - Astra判定のSource of Truthは、Status UpdatesにAstra自身が記録した行だけ。Claudeは判定を推測しない・Astra名義で代理記録しない・本人から転記された判定をAstraの記録として扱わない。`apply-updates` は、noteや理由に「転記・代理・代行・本人経由・チャットで」などを含むAstra名義の行を取り込まず `errors` に出す。
   - Status UpdatesにAstraの判定がない案件は ASTRA_REVIEW_PENDING（ステータスは既存の `ASTRA_QA_PENDING` のまま）で停止し、応募準備に進めない。
   - 例外は、本人がそのプロンプトで対象job_idと判定を明示し「移行指示として利用してよい」とした場合だけ。そのときも記録者は `本人（移行指示）`、`astra.source=user_directive` とし、理由の先頭に【移行指示：本人伝達・Astra記録なし】と残す（Astra名義にしない）。
6. 05:00の実行では、手順4（merge）と手順4.7（応募準備draft）の間に、この4.5を行う（Status Updatesの取り込みは行わない。`MANUAL_REVIEW` 行での登録は、方式が決まるまで休止。URLの直接依頼 `manual-request` は使える）。`PENDING` がなければ何もしない。Manual Reviewで `ASTRA_QA_PENDING` になった案件も、4.7で応募文draftを作る。

## 4.6 Client Master（クライアント単位の履歴）
Scout → Job Master → Client Master参照 → Claude一次評価 → Astra → 応募文生成時にClient Masterを再参照、の順で使う。
- キーはCrowdWorksの発注者ID（`client.userId`）。表示名はキーにしない（表示名が変わっても同じクライアント）。
- 保存先はVaultの `meta.clients`。Vault保存のたびにJob Masterから作り直すので、Status UpdatesやWorkerで反映した応募・受注・納品・支払・修正回数・辞退は自動で入る。手で足すのは `client_notes` だけ（`python3 scout/pipeline.py client-note --id <client_id> --note "<内容>"`）。
- 項目：client_id / client_name / first_seen_at / last_seen_at / application_count / accepted_count / delivered_count / paid_count / repeat_order_count / past_job_ids / last_relationship_status / interview_required_history / interview_disclosed_in_posting / post_application_interview_request_count / revision_history / payment_history / client_notes / updated_at。記録から分からない値は `UNKNOWN`（推測しない）。
- 確認：`python3 scout/pipeline.py client-show --id <client_id>`、案件からは `client-show --job <job_id>`（関係・使う冒頭・履歴の要約）。ローカルに `scout/out/client_master.json` も出る（Driveには上げない）。
- 面談の扱い：
  - 募集文に面談必須が明記 → ルールで `面談必須（募集文に明記）` を付け、原則除外（RULE_REJECTED）。
  - 募集文に記載なし → 通常評価。
  - 応募後に初めて面談必須と判明して辞退 → AstraがStatus Updatesに `new_status=WITHDRAWN`（理由に「面談」を含める）を書く。取り込むと `post_application_interview_request_count` が増える。
  - 同じクライアントの次の案件では、Claude一次評価の入力（`pending_eval.json` の `client_history`）とAstra Queueの `client_history` 列に「⚠応募後に面談要求N回」と表示する。これだけで自動REJECTにはしない（Astraが条件と合わせて判断）。

## 5.0 Driveの最新版の特定（Driveを読む・消す前に毎回行う）
1. Drive MCPの `search_files` で、フォルダ内のシートを一覧する：`parentId = '<folder>' and title contains 'CW Scout' and mimeType = 'application/vnd.google-apps.spreadsheet'`（`excludeContentSnippets=true`。`next_page_token` があれば全ページ）。folderは `sync_manifest.json` の `drive.folder`。
2. 返ってきたJSONを `scout/data/drive_listing.json` に保存し、`python3 scout/pipeline.py drive-resolve --listing scout/data/drive_listing.json` を実行する。
   - 種類ごと（Job Master / Astra Queue / Application Queue）の現在の版＝タイトルの生成日時（`｜YYYY-MM-DD HH:MM JST`）が最も新しいもの、同時刻なら作成日時の新しいもの。Status Updatesは、記録済みのシートがフォルダにあればそれ（列追加の差し替えは検証後に正式になるため）、なければ作成日時の最も新しいもの。
   - フォルダ外・ゴミ箱のファイルは対象外。`recorded_is_current=false` は、記録済みIDが最新版ではないことを示す（読むのは `id` の方）。`others` は同じ種類の古い版（過去の失敗で残ったものを含む）。
3. アップロードして検証した直後は `drive-resolve --listing <新しい一覧> --keep <key>=<新ID>` を実行し、`trash` に出たIDをすべて `trash_file` する（前回記録したIDだけでなく、残っていた古い版も片付ける）。

## 5. Google Sheetsへの同期（06:00のAstra QAより前に終える。手順4.7の応募準備draftの後に行う）
現在のDrive MCPは既存ファイルの中身を書き換えられないため、次の手順でシートを差し替える。Driveは表示用で、Job MasterのSource of TruthはVault。
1. フォルダ `CW Scout (ai×cloud works)` に、次の2つを `text/csv` でアップロードし、Googleシートに変換する。タイトルは `scout/out/sync_manifest.json` の `titles` を使う（生成日時入り。例：`CW Scout - Astra Queue｜2026-09-27 06:05 JST`）。
   - Job Master
   - Astra Queue（応募準備draft入り。容量は80KBまで）
2. 古いシートを `trash_file` でゴミ箱へ移す。対象は5.0の3（`drive-resolve --keep`）が返す `trash`。
3. 新しいIDを記録する：`python3 scout/pipeline.py set-drive job_master_sheet <id>`（`astra_queue_sheet` も同様）
4. `CW Scout - Status Updates (記入用)` は差し替えない・読まない・列追加もしない（監査・履歴用。日次フローの対象外）。
5. Drive反映は省略しない（2026-09-29に、Job MasterとApplication Queueのアップロードが省略されたまま「成功」と報告された）。
   - アップロードするCSVは、Drive用の軽量版（1ファイル約50KB以内）。
     - `job_master.csv`：Claude/ルール除外・CLOSED・前日より前のASTRA_REJECTを除く。全件はローカルの `job_master_full.csv` とVaultにある。
     - Drive版Job Masterは日常確認用の索引（26列）。status・lane・報酬・手取り見込み・期限・Claude/Astra判定・need_user・next_action・AI完結率・想定Human Minutes・手取り/分・応募状態・Worker/納品リンクだけを載せる。理由文・発注者リスク・実測ログなどの長文は載せない（Vaultと `job_master_full.csv` に全列が残る）。
     - Source of TruthはVault。Drive版の行から詳細を見るときは `python3 scout/pipeline.py job-detail --ids <job_id,...>`。内部処理はDriveのCSVを読まない。
     - 容量は `drive-status` の `bytes`（今回）・`delta_bytes`（前回exportからの増減）・`near_budget`（予算の80%超）・`over_budget`（50KB超）で確認する。
     - `application_queue.csv`：原文抜粋を除く。応募済み・見送りの行は応募文を省略する（`application_queue.json` とVaultに残る）。
   - `export` が `WARNING: ... exceeds the Drive upload budget` を出した場合も、アップロードは行い、報告に書く。
   - CSVはファイルの中身をそのまま `textContent` に渡す。要約・省略・行の削除はしない。
   - アップロード後に `download_file_content`（`text/csv`）で取得し直し、ローカルのCSVと行数・内容が一致することを確認してから、古いシートをゴミ箱へ移す。
   - 最後に `python3 scout/pipeline.py drive-status --keys <対象>` を実行する。`ok=false` なら、どのシートが未反映かを報告に必ず書く（成功として報告しない）。
   - `set-drive` はVaultを更新するので、IDの記録後に `git add scout/state && git commit -m "Scout: record Drive sheet IDs <date>" && git push` を行う。

## 5.5 性能測定
- `python3 scout/pipeline.py metrics` で、累計の取得件数、Claude評価件数、Astra Queue件数、PASS・REJECTの件数、取りこぼし件数、Precision / Recallの目安を確認できる。
- `runs.jsonl` には実行ごとの値が記録される（`run_type`：`morning_full` / `evening_delta`）。
  - 検索母集団 `listed`、新規 `new`、ルール除外 `rule_rejected`、Claude評価件数・入力字数 `est_ai_usage`、Claude前の内訳 `candidate_mix`、`limits`（その実行の上限）、`deferred_to_morning`。
  - Claude一次評価の lane 内訳 `lane_mix`（Professional / Experience / Auto / Human Premium、未記入）、Astra Queueに新しく入った案件の `tier_mix`（主力 / マイクロ / 基準外 / UNKNOWN）と `astra_queue_added`（同じ実行で再評価したキュー済み案件は `astra_queue_requeued`）。
  - 公開→Scout検知 `detect_delay_h`（その実行で初めて見た案件。CrowdWorksの公開日時から）、公開→Astra Queue投入 `queue_delay_h`。
  - 新しい検索入口（データ・調査系 tier D、AI-BPO tier E）だけで見つかった件数 `new_entry`（検索母集団・新規・Claudeへ送付・AI-BPO）、`astra_queue_added_new_entry`。
- `python3 scout/pipeline.py daily-metrics --date <date>`（17:00の最後に実行）：`state/daily_metrics.jsonl` に、その日のAstra Queue投入分について、公開→キュー投入の実際の遅延（05:00＋17:00）と、05:00だけだった場合の推定遅延（17:00分を翌05:00の投入とみなす）の平均・中央値、17:00でだけ見つかった案件数（主力・マイクロ）、05:00と17:00のClaude利用量を残す。

## 5.7 応募準備（Application Queue・旧フロー：Astra PASS後）※応募・フォーム入力・送信はしない
【現行は4.7。以下は、Astra判定をStatus Updatesから取り込む旧フロー（休止中）の記録。5.7.1〜5.7.3の規則（Client Master・確認済み事実・確認コスト）は4.7でも同じ。】
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
   - `client` に各案件のクライアントとの関係（NONE / APPLIED / ORDERED / DELIVERED）と、使う冒頭が出る。応募文は必ずこれに合わせる。`history` に「⚠応募後に面談要求N回」などが出たクライアントは、応募文には書かず、Astraの判断材料として扱う（それだけで除外しない）。
   - `confirmed_facts` は本人が一度確認した事実（5.7.2）。設問がこれで答えられる場合はそのまま回答し（`facts_used` の `profile_ref` は `confirmed_facts[i].fact`）、本人確認にしない。
     - NONE（初回）：「はじめまして。」
     - APPLIED（応募・やり取りのみ）：「はじめまして」を使わない。受注していないので過去の依頼へのお礼も書かない（例：「先日は別のご募集にも応募させていただきました。」）。
     - ORDERED / DELIVERED（受注・納品あり）：「以前はお仕事をご依頼いただき、ありがとうございました。」など。実際の履歴以上の関係は書かない。
   - `app-merge` と READY_TO_APPLY の判定は、この関係と冒頭が合わない応募文をFAIL（取り込まない／READYにしない）にする。
   - 除外される案件：募集終了・枠充足・期限切れ・実質的な変更がある案件。上限の件数には数えない。
   - 並び順：応募期限の近い順、次に推定手取額 ÷ 推定本人作業時間の大きい順。
   - 上限（1回15件）を超えた分は `carry_over` に入り、次の05:00の実行で処理する。
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

### 5.7.2 本人確認済み事実の再利用と、本人確認の最小化
- 本人が一度確認した事実は `profile.confirmed_facts` に `reuse=true` で保存し、以後は同じ質問を本人に確認しない。追加は `python3 scout/pipeline.py profile-fact --fact <事実> --source <確認日・経路> --question-re <その事実で答えられる質問の正規表現> [--job-re <対象案件>] [--ref <プロフィール上の位置>]`（同じ事実は上書き）。
- 登録済み（2026-09-30 本人指示）：NISAの利用経験 / NISAで投資信託を運用 / 投資用区分マンションの経験 / FP2級 / 簿記2級 / TOEIC 835 / 本業での文字起こし経験 / 文字起こし（1ファイル3,000字程度）の納期は1ファイル3日程度（文字起こし案件の標準回答）。
- これらから別の経験を推測・創作しない（例：NISA経験から「投資記事の執筆経験」を作らない）。
- `app-merge` は、確認済み事実で答えられる項目を `unverified_facts` や【本人記入】に回した下書きをFAILにする。
- 本人確認の対象にするのは、応募・制作に本当に必要なものだけ：契約・支払い・報酬、重要な外部送信・連絡手段、プロフィールにない本人経験・実績・資格、守秘義務・利益相反・勤務先、最終納品、署名に使う名前。
- 嗜好・将来の希望・私生活などだけが未確認で、手取り ÷（確認分＋本人確認5分）が100円/分未満の案件は、本人に確認しない。`app-merge` が `confirm_cost=REJECT_CANDIDATE`・`user_confirmation_required=no` とし、`next_action` に「Astra REJECT候補」と出す（READYにはしない。判定はAstraがStatus Updatesで行う）。

### 5.7.3 READY通知（07:30。通知するのはChatGPT / Astraだけ）
- 本人への通知の対象は `READY_TO_APPLY` の案件だけで、ChatGPT / AstraがDriveのApplication Queueから取得して通知する（唯一の通知経路）。Claude のRoutineは通知しない（冒頭の「通知経路は一本化する」）。
- `python3 scout/pipeline.py ready-notice`（`scout/out/ready_notice.txt` にも出る）は、通知と同じ形（1件ごとに `案件URL | 完成した応募文`、設問があれば【応募時の回答】）をローカルで確認するためのもの。Routineの報告には載せない。
- READY_TO_APPLYになるのは、【本人記入】が残っていない完成形の応募文だけ（5.7の4）。

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
9. 通知：本人への通知は ChatGPT / Astra が行う（Job Masterの `worker_status=READY_TO_DELIVER`・`delivery_artifact_url` を参照）。Claude のRoutineは通知文を報告に載せない。`python3 scout/pipeline.py worker-notice --id <id>` は通知文の確認用（構成：【納品準備完了】、案件名、job_id、報酬、納品期限、納品物の直接URL、形式、Astra QA、ステータス、「上記リンクを開いて最終確認 → 問題なければCrowdWorksで納品」）。内部管理用ドキュメントのリンクやQAの詳細は、本人から求められたときだけ出す。
10. 納品後：本人の報告を受けたAstraが、Status Updatesに `DELIVERED` / `PAID` などを書く。
- 禁止：CrowdWorksへの納品・メッセージ送信、Chatworkなどクライアントへの送信。自己QAだけで納品可能とすること。Astra QA PASS前に納品物を最終版として確定すること。

## 6. 保存
```bash
python3 scout/tests/test_application.py   # 応募準備の検証（本物のVaultは変更しない）
python3 scout/tests/test_recheck.py
python3 scout/tests/test_worker.py
python3 scout/tests/test_manual.py
python3 scout/tests/test_manual_review.py
python3 scout/tests/test_drive_view.py
python3 scout/tests/test_client_master.py
python3 scout/tests/test_predraft.py       # 05:00のAstra QA前の応募準備draft（新クライアント/過去応募/過去納品・確認済み事実・冪等性・READYにしない）
python3 scout/tests/test_queue_tier.py      # Astra Queueのtier（主力・マイクロ）とclaude_qa_result
python3 scout/tests/test_scout_scope.py     # 検索入口（データ・調査系カテゴリ）とClaude前のルール（Auto・拘束・FILLED_CAPACITY）
python3 scout/tests/test_scout_evening.py   # AI-BPO入口（13502286相当）・AI補助制作のAuto/除外・05:00→17:00→翌05:00・17:00上限・Queue重複なし
python3 scout/tests/test_source_facts.py    # 10/1の実障害の再現（設問欠落・初回/継続報酬・実績数の誤変換）とDrive軽量化
python3 scout/tests/test_daily_ops.py      # 旧フロー（休止中）の通し（PASS/REJECT/HOLD/未判定・冒頭QA・冪等性・catch-up・最新シート）
git add scout/state && git commit -m "Scout run <date>" && git push -u origin claude/brave-lovelace-7n0flp
```
- 暗号化されていない状態で個人情報をコミットしないこと。
  - `scout/data` と `scout/out` はgitignore済み。
  - Job Masterは `vault.enc` に暗号化して保存する。
- pushが拒否された場合は、手順7の8と同じにする（force pushしない。先に入ったコミットが `scout/state/` を変更していなければrebaseして1回だけpushし直す。変更していれば、Driveも更新せずに終了する）。

## 7. 第2Routine（06:30）・catch-up（12:30）：廃止（判定取込の目的では不要）
Astra QA後にClaudeへ判定を戻して応募文を作る構造を廃止したため、06:30の本処理（Astra判定取込→応募文生成）と12:30のcatch-upは、日次では何もしない。
- 無効化の方法：`scout/routine.json` の `postqa_import=retired`。`python3 scout/pipeline.py postqa guard` が `ok=false, mode=retired` を返すので、06:30 / 12:30 のRoutineは、何も取得・変更・commit・pushせずに終了する（手順2の「ok=falseなら何もせず終了」）。Routine自体の停止・削除はスケジュール設定側の操作で、コードからは行わない。
- 廃止前に確認した、他の依存（この工程が、Astra判定の取り込み以外に持っていた処理）：
  - 受注後のWorker工程（5.8）：`ACCEPTED` / 仮払い / Astra QA PASS を Status Updates から取り込んで進める。Status Updatesの取り込みがない間は動かせない。
  - 応募済み・受注・辞退・納品の記録（`APPLIED` / `ACCEPTED` / `WITHDRAWN` など）と、それに依存するClient Master・KPIの実績値。
  - `MANUAL_REVIEW` 行によるManual Review Queueへの登録（URLをClaudeへ直接渡す `manual-request` は使える）。
  これらは、Astraが構造化された受け渡し方式を用意するまで休止する（Routineを無効化しても、取得できない以上、これらは動かせない）。方式が決まったら、この章に再設計を書く。
- 旧手順（06:30 / 12:30）の記録：`postqa guard/astra/catchup/targets`、`apply-updates`、5.7の手順は、`routine.json` を外せばコードとして動く（テスト `test_daily_ops.py` で検証。日次では使わない）。

## エラー時
- 収集に失敗した場合：`runs.jsonl` の `errors` に記録し、処理を続ける。
- Driveに接続できない場合：手順5をスキップして手順6まで進める（Astra QAに間に合わない場合は、その旨を【要対応】で報告する）。次回の実行で最新の状態を同期する。報告には「Drive未反映」と明記し、成功として報告しない。
