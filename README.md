# A8.net 無人巡回・差分検知ツール

A8.net(pub.a8.net)のアフィリエイト管理画面を無人で定期巡回し、プログラムの新規追加や
報酬・成果条件などの変更を差分検知するためのツールです。Python + Playwright で実装しています。

## ★ 本番運用前に必ず確認・調整してください

このリポジトリは安全機構(許可リスト・異常検知・アラート・差分検知・ログ)を中心に実装したもので、
**実際のA8管理画面のURL構造やHTMLは私(Claude)からは確認できないため、以下はすべて推測のプレース
ホルダです。** 必ずご自身のアカウントで実際の画面を確認し、調整してから無人運転を開始してください。

| ファイル | 調整が必要な内容 |
|---|---|
| `config/allowlist.json` | `allowed_hosts` / `allowed_path_patterns`(閲覧ページのURLパターン)、`blocked_tracking_hosts`(計測ドメインの網羅性) |
| `config/targets.json` | 巡回対象ページの実URL、CSVダウンロードのトリガー(URL or クリックするセレクタ) |
| `config/csv_column_map.json` | ダウンロードしたCSVの実際のヘッダー名 |
| `a8_automation/scraper.py` の `extract_programs()` | 閲覧ページから項目を抜き出すCSSセレクタ(現状は `data-program-id` / `data-field` 属性を仮定したダミー実装) |

調整後は必ず `scripts/run.sh` を一度手動実行し、`data/logs/run-*.log` と
`data/diffs/diff-*.json` を見て意図通りに動いているか確認してください。

## 安全設計の要点

- **認証情報を一切保存しない**: ID/パスワードはコード・設定ファイル・ログのどこにも書き込みません。
  ログインは初回のみ手動で行い(`a8_automation.cli login`)、Playwrightの `storage_state()` で
  Cookie等のセッション状態だけを `browser_profile/storage_state.json`(gitignore済み)に書き出し、
  以降の無人実行ではそれを読み込んでセッションを再利用します(ブラウザプロファイル全体を使い回すと、
  ブラウザ終了時にセッションCookieが失われる場合があるため、この方式にしています)。
- **許可リスト方式(デフォルト拒否)**: `a8_automation/allowlist.py` の `decide()` が全リクエストを
  判定します。判定順序は次の通りで、計測ドメインと申請/解除/設定変更系のパスは許可リストの設定ミス
  があっても必ずコード側で先にブロックされます。
  1. 計測ドメイン(`px.a8.net`, `www08.a8.net` 等)へのアクセスは常にブロック
  2. `allowed_hosts` に無いホストは拒否
  3. 申請/解除/設定変更などのパスパターン(`blocked_path_patterns`)は常にブロック
  4. `GET`/`HEAD` 以外のメソッドは、CSVダウンロード対象パスの `POST` を除き拒否
  5. 上記を通過し、かつ `allowed_path_patterns` または CSVダウンロードパターンに一致する場合のみ許可
  6. それ以外はすべて拒否(デフォルト拒否)
- **異常検知で即停止・リトライなし**: 以下のいずれかを検知した場合、その場でリトライせず処理を中断し、
  `data/state/alert.json` に状態を書き込み、Slack Webhook が設定されていれば通知します。
  - ログイン切れ(ログインページへのリダイレクト)
  - CAPTCHA / 2段階認証(2FA)の要求(ページ内テキストのヒューリスティック検出)
  - 想定外の画面遷移(`targets.json` で指定した期待URLパターンに一致しない)
  - HTTPエラーの連続発生(既定は3回連続、`A8_MAX_CONSECUTIVE_HTTP_ERRORS` で変更可)
  - 上記以外の想定外の例外(念のためのフォールバック)
- **実行ログにアクセスURLと件数を記録**: 実行ごとに `data/logs/run-<timestamp>-access-summary.json`
  にアクセスを許可したURLごとの件数、および拒否したリクエストの一覧を残します。
- **差分検知が前提**: 新規プログラム、および報酬・成果条件・SNS条件・承認条件・否認条件・禁止事項の
  変更を検出し、`data/diffs/diff-<timestamp>.json` に出力します。取得成功時のみ
  `data/snapshots/latest.json` を更新します(異常検知で中断した場合は前回のスナップショットを保持)。

## セットアップ

```bash
cd /path/to/Claude
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # 本番運用のみなら requirements.txt でも可
playwright install chromium
cp .env.example .env
# .env に SLACK_WEBHOOK_URL を設定(任意。未設定でも alert.json への記録は行われる)
```

## 初回ログイン(手動・1回のみ)

> **重要**: この手順は画面(ディスプレイ)のある自分のPC/Macで行ってください。ブラウザの
> ウィンドウが実際に開きます。サーバーやクラウド上の画面なしの環境では実行できません。

非エンジニアの方向けに、ターミナルにそのままコピペできるコマンドと、各段階で「これが表示され
れば成功」という目印をまとめます。上から順に1つずつコピペして実行してください。

**手順1: リポジトリのフォルダに移動する**

```bash
cd /path/to/Claude
```

`/path/to/Claude` は実際にこのリポジトリを置いた場所に置き換えてください(このリポジトリを
どこに置いたか分からない場合は、社内の担当者に確認してください)。

- ✅ 成功の目印: 何も表示されず、次の行の入力待ちに戻る。
- ❌ `No such file or directory` と出た場合: パスが間違っています。担当者に置き場所を確認してください。

**手順2: 一度だけの準備(仮想環境作成・ライブラリ・ブラウザ本体のインストール)**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

- ✅ 成功の目印: 最後に `pip install` や `playwright install` の行が止まらずに完了し、
  ターミナルのプロンプトの先頭に `(.venv)` という表示が付く。
- ❌ `command not found: python3` の場合: Pythonがインストールされていません。担当者に相談してください。
- この手順2は**最初の1回だけ**でよく、2回目以降のログイン(セッションが切れた時の再ログイン)
  では不要です。手順3から行ってください。

**手順3: 初回ログインスクリプトを実行する**

```bash
./scripts/login.sh
```

- ✅ 成功の目印: ターミナルに以下のような案内文が表示され、**Chromeのブラウザウィンドウが
  自動で開く**。
  ```
  ブラウザウィンドウで手動ログインを完了してください(2段階認証・CAPTCHA含む)。
  ログイン後、実際に閲覧したい管理画面のページまで進んでから、このターミナルに戻ってください。
  そこまで進めたら、Enter を押してください >
  ```

**手順4: 開いたブラウザでA8に普段通りログインし、いつも見ているページまで進む**

- 開いたブラウザ画面で、いつも通りA8.netのID・パスワードを入力してログインしてください。
- 2段階認証やCAPTCHA(画像認証)が出ても、この手順は人が操作しているので問題ありません
  (無人実行のときだけ、これらが出たら自動的に止まる仕組みになっています)。
- ログインしただけで終わらせず、**普段報酬額や成果条件を確認するページまで実際にクリックで
  進んでください**。これによって、そのページに必要なセッション情報もまとめて保存されます。
- ✅ 成功の目印: 見たいページが問題なく表示されている状態。

**手順5: ターミナルに戻ってEnterキーを押す**

- そこまで進めたら、ブラウザは閉じずに**ターミナルの画面に戻り**、何も入力せず Enter キーだけを
  押してください。

- ✅ 成功の目印: ブラウザウィンドウが自動的に閉じ、ターミナルに以下のように表示される。
  ```
  セッション情報を /path/to/Claude/browser_profile/storage_state.json に保存しました。
  ID/パスワードはどこにも保存していません(保存されるのはCookie等のセッション状態のみです)。
  ```
  この2行が出れば初回ログインは完了です。以降は `./scripts/run.sh`(または cron)による
  無人実行が、このセッションを使って動きます。ID/パスワードはこのファイルにも一切保存されません。

**うまくいかない場合**

- ブラウザが開かなかった/途中で固まった場合は、Ctrl+C でターミナルの処理を中断し、
  手順3からやり直してください。
- 無人実行(`./scripts/run.sh`)を続けているうちに `data/state/alert.json` に
  `"kind": "session_expired"` が記録されるようになったら、セッションが切れたサインです。
  この「初回ログイン」の手順3〜5をもう一度行えば再ログインできます(手順2は不要)。

## 実行

初回ログイン(上の手順)が完了していれば、実行時にブラウザ画面は開きません(裏で自動的に動きます)。

### 手動実行

**① フォルダに移動して仮想環境を有効にする**

```bash
cd /path/to/Claude
source .venv/bin/activate
```

- ✅ 目印: プロンプトの先頭に `(.venv)` が付く。
- 同じターミナルで手順2の準備直後にそのまま実行する場合、この手順①は不要です(既に有効なため)。

**② 実行する**

```bash
./scripts/run.sh
```

- ✅ 正常終了の目印: ターミナルに1行ずつログが流れ、最後に次のような行が出て入力待ちに戻る。
  ```
  ... run completed: new=0 changed=2 total_records=48
  ... access summary: allowed=12 blocked=0 (full URL list/counts in access-summary json)
  ```
  (`new=` `changed=` の数字がその日の新規・変更件数です)
- ❌ 異常検知で止まった場合の目印: 次のような行が出て終わる。
  ```
  ... ANOMALY DETECTED (session_expired): ... -- stopping immediately, no retry.
  ```
  この場合はリトライされず、そこで終了します。`data/state/alert.json` に検知内容が記録され、
  Slack Webhookを設定していれば通知も届きます。`session_expired`(ログイン切れ)であれば、
  上の「初回ログイン」手順3〜5を再度行えば復旧します。それ以外(CAPTCHA/2FA要求・想定外の
  画面遷移・HTTPエラー連続)の場合は、自動では対処せず内容を確認してください。

**③ 結果を確認する**

- その日の差分: `data/diffs/diff-<実行日時>.json`(新規プログラム・条件変更が入っています)
- 詳細ログ: `data/logs/run-<実行日時>.log`
- アクセスしたURLと件数: `data/logs/run-<実行日時>-access-summary.json`
- 直近の異常内容: `data/state/alert.json`(異常が起きていなければファイルは更新されません)

### 定期実行(cron、1日1回程度)

```bash
crontab -e
```

エディタが開くので、末尾に次の行を追加します(`/path/to/Claude` は実際の設置場所に置き換え)。
`scripts/crontab.example` にも同じ例があります。

```
0 7 * * * cd /path/to/Claude && ./scripts/run.sh >> data/logs/cron_stdout.log 2>&1
```

保存して閉じたら、登録されたか確認します。

```bash
crontab -l
```

- ✅ 目印: 先ほど追加した行がそのまま表示される。

失敗時に自動でリトライするような設定(`*/5 * * * *` のような細かい間隔やcron側のリトライ)は行わない
でください。異常検知時は原因を人間が確認してから再実行する設計です。翌日以降の実行結果は
`data/logs/cron_stdout.log` と上記③のファイル群で確認できます。

## GitHub Actions ではなく、お使いのWindows PCで実行してください

「GitHubで実行できないか」という質問について: **GitHub Actionsでの実行は推奨しません。**

- 初回ログイン(2段階認証・CAPTCHAを人が突破する手順)は実際のブラウザ画面が必要ですが、
  GitHub Actionsの標準実行環境には画面がなく、この手順ができません。
- GitHub Actionsの実行環境は毎回使い捨て(実行が終わると消える)のため、`browser_profile/`
  のセッション情報を保持できません。保持しようとするとGitHub側にセッション情報をアップロード
  することになり、「認証情報をコード・設定・ログに残さない」という要件と相性が悪く、万一
  ログ等から漏れた場合の影響も大きくなります。

そのため、**お使いのWindows PCなど、ご自身で管理している端末上で実行**し、Windowsの
「タスクスケジューラ」を cron の代わりに使う構成を想定しています。以下がWindowsでの手順です。

### Windowsでのセットアップ

1. Python をインストールします。[python.org](https://www.python.org/downloads/) から
   Windows用インストーラをダウンロードし、インストール画面で **「Add python.exe to PATH」に
   必ずチェックを入れて**実行してください。
2. このリポジトリのフォルダを開き、その中で「PowerShell」を起動します
   (エクスプローラーでフォルダを開き、アドレスバーに `powershell` と入力してEnter、が簡単です)。
3. 以下をそのままコピペして実行します。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```

- ✅ 目印: プロンプトの先頭に `(.venv)` が付く。
- ❌ `login.ps1 は実行できません...` や `このシステムではスクリプトの実行が無効になっている
  ため...` と出た場合(実行ポリシーの制限): 次のコマンドを一度だけ実行してから、上のコマンドを
  やり直してください。
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  ```

### Windowsでの初回ログイン・実行

Linux/macOS向けの `scripts/login.sh` / `scripts/run.sh` に対応する PowerShell 版
(`scripts/login.ps1` / `scripts/run.ps1`)を用意しています。使い方は同じで、画面に出る
案内文や成功時の目印も上の「初回ログイン」「実行」の各セクションと同じです。

```powershell
# 初回ログイン(1回のみ)
.\scripts\login.ps1

# 手動実行
.\scripts\run.ps1
```

### Windowsでの定期実行(タスクスケジューラ)

cronの代わりに「タスクスケジューラ」を使います。

1. スタートメニューで「タスクスケジューラ」を検索して開く。
2. 右側の「基本タスクの作成」をクリック。
3. 名前を入力(例: `A8自動巡回`)して「次へ」。
4. トリガーで「毎日」を選び、実行したい時刻(例: 朝7:00)を指定して「次へ」。
5. 操作で「プログラムの開始」を選び「次へ」。
6. 「プログラム/スクリプト」に `powershell.exe` と入力。
7. 「引数の追加」に以下を入力(`C:\path\to\Claude` は実際の設置場所に置き換え):
   ```
   -ExecutionPolicy Bypass -File "C:\path\to\Claude\scripts\run.ps1"
   ```
8. 「完了」をクリックして登録。

- ✅ 目印: タスクスケジューラの一覧に作成したタスクが表示される。右クリックして「実行」を
  選べばすぐに1回テスト実行でき、`data/logs/` にログファイルが増えていれば成功です。

失敗時の自動リトライ設定(タスクのプロパティで再試行間隔を細かく設定する等)は行わないでください。
異常検知時は人が `data/state/alert.json` を確認してから再実行する設計です。

## ディレクトリ構成

```
a8_automation/       本体パッケージ
  allowlist.py          許可リスト判定(デフォルト拒否 + 計測ドメイン/危険操作のハードブロック)
  access_log.py         アクセスURL一覧・件数の記録
  http_guard.py         HTTPエラー連続検知
  page_checks.py        ログイン切れ / CAPTCHA・2FA / 想定外遷移の検知
  alert.py               alert.json 書き込み + Slack通知
  diff_store.py          差分検知(新規・条件変更)
  scraper.py              ページ抽出・CSVダウンロード/パース(★要検証)
  runner.py               全体のオーケストレーション(異常検知時はリトライせず即中断)
  cli.py                  `login` / `run` サブコマンド
config/
  allowlist.json         許可リスト設定(★要検証)
  targets.json            巡回対象ページ設定(★要検証)
  csv_column_map.json    CSVカラムマッピング(★要検証)
scripts/
  login.sh / login.ps1     初回手動ログイン(bash / PowerShell)
  run.sh / run.ps1         無人実行エントリポイント(bash / PowerShell)
  crontab.example          cron設定例(Linux/macOS用。Windowsはタスクスケジューラを使用)
data/                    実行時生成物(gitignore対象。ディレクトリのみ保持)
  snapshots/latest.json  最新スナップショット(取得成功時のみ更新)
  diffs/                  実行ごとの差分結果
  logs/                    実行ログ・アクセスサマリ
  downloads/              ダウンロードしたCSV原本
  state/alert.json       直近の異常検知内容
browser_profile/
  storage_state.json     ログインセッション(Cookie等)のスナップショット(gitignore対象。認証情報そのものではない)
```

## 他のAIエージェント(Codex等)・外部ツールとの連携

出力データはすべてプレーンなJSONで、Claude専用の形式ではない。ファイル一覧とスキーマは
[docs/data-schema.md](docs/data-schema.md) を参照。AIによるスクリーニングを行う場合は
カタログ全体(`data/snapshots/latest.json`)ではなく、その回の新規/変更分だけをまとめた
`data/state/needs_review.json` を入力にすることを想定している。

収集済みデータを手早く確認したい場合は `python -m a8_automation.cli inspect`
(`scripts/inspect.sh` / `scripts/inspect.ps1`)でサマリとサンプルレコードを表示できる。

## テスト

`allowlist.py` の判定ロジックと `diff_store.py` の差分ロジックはPlaywright不要の純粋関数のため、
ユニットテストがあります。

```bash
pip install -r requirements-dev.txt
pytest
```

## セキュリティ上の注意

- ID/パスワードはコード・設定ファイル・ログ・`.env` のいずれにも保存しません。
- `browser_profile/`(セッションCookie等)と `data/`(実行ログ・アラート・スナップショット)は
  `.gitignore` で除外されています。誤ってコミットしないよう注意してください。
- Slack Webhook URL は `.env` の `SLACK_WEBHOOK_URL` からのみ読み込み、ログには出力しません。
- 許可リストは「デフォルト拒否」が原則です。新しいページを巡回対象に追加する場合は
  `config/allowlist.json` の `allowed_path_patterns` に明示的に追加してください。
