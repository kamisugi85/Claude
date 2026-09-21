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
  ログインは初回のみ手動で行い(`a8_automation.cli login`)、以降は Playwright の永続ブラウザプロ
  ファイル(`browser_profile/`, gitignore 済み)に保存されたセッション状態を使い回します。
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

```bash
./scripts/login.sh
```

ブラウザウィンドウが開くので、通常通りA8にログインしてください(2段階認証やCAPTCHAが出ても
問題ありません。これは人間が操作する対話的セッションであり、無人実行時のみ許可リストや異常検知
が働きます)。ログイン完了後、ターミナルに戻って Enter を押すとブラウザが閉じ、セッション情報が
`browser_profile/`(gitignore対象)に保存されます。ID/パスワードは保存されません。

## 実行

手動実行:

```bash
./scripts/run.sh
```

`data/logs/run-<timestamp>.log` に詳細ログ、`data/logs/run-<timestamp>-access-summary.json`
にアクセスURL一覧と件数、`data/diffs/diff-<timestamp>.json` に差分結果が出力されます。異常を
検知した場合は終了コード1で終わり、`data/state/alert.json` に検知内容が記録されます。

### 定期実行(cron)

```bash
crontab -e
# scripts/crontab.example の内容を参考に1日1回程度の行を追加する
```

失敗時に自動でリトライするような設定(`*/5 * * * *` の細かい間隔やcron側のリトライ)は行わない
でください。異常検知時は原因を人間が確認してから再実行する設計です。

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
  login.sh                初回手動ログイン
  run.sh                   無人実行エントリポイント
  crontab.example          cron設定例
data/                    実行時生成物(gitignore対象。ディレクトリのみ保持)
  snapshots/latest.json  最新スナップショット(取得成功時のみ更新)
  diffs/                  実行ごとの差分結果
  logs/                    実行ログ・アクセスサマリ
  downloads/              ダウンロードしたCSV原本
  state/alert.json       直近の異常検知内容
browser_profile/         永続ブラウザプロファイル(gitignore対象。認証情報そのものではなくセッション状態)
```

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
