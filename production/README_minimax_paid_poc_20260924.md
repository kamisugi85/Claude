# CLAUDE-D01 — MiniMax公式API 少額PoC (2026-09-24)

**課金・APIキー発行・有料クレジット購入は一切実行していません。**
CLAUDE-D01のクリエイティブは前フェーズから完全不変(`creative`ブロックの
SHA-256ハッシュ: `c851b8e875344421d2458ff605a73651fa189ec7916999a5d3ce1ae468207b1a`
を実行前後で確認)。GPT側のGPT-D01/GPT-D01-S15は参照・模倣していません。

Wan2.1無料GPUルート(`CLAUDE-D01_free_gpu_poc_wan21.ipynb`、
`README_local_and_free_gpu_investigation.md`)は削除・変更しておらず、
「将来の原価低減策 / API障害時のバックアップ」として維持しています。
今回はColab/Kaggle実行は行っていません。

## 1. リポジトリ/pipeline状態確認

- ブランチ`claude/tiktok-video-pipeline-fv6oea`はクリーン、既存37テスト全PASSを
  作業開始前に確認済み。
- 既存成果物(Wan2.1 ipynb、3本の比較レポート、MiniMax/Seedance/Replicate/Manual
  provider、Azure Neural TTS provider、既存tests)は全てそのまま。

## 2. クリエイティブ不変確認

`production/jobs/CLAUDE-D01.json`の`creative`ブロック(hook/narration_script/
cta/pr_disclosure/hashtags/source)をSHA-256でハッシュ化し、今回の作業前後で
完全一致を確認しました。変更したのは各ショットの実行時ブックキーピング
フィールド(status/attempts/cost系)とスキーマ拡張のみです。

## 3. MiniMax公式API再確認(2026-09-24時点、確認日・URLを明記)

**重要な制約: このサンドボックス環境は`api.minimax.io`・`platform.minimax.io`
の両方にネットワークポリシーでアクセスがブロックされています**
(`curl`で確認: `connect_rejected (organization policy)`、課金・APIキー不要の
接続確認ですら失敗)。したがって以下は**一次情報ページを直接開けない状態での
検索エンジン経由の確認**であり、断定はできません。以前の見積り($0.055/秒)は
別の(より古い)Hailuoモデルの数字を誤って参照したものと判明したため、
今回$0.08〜0.13/秒に更新しますが、**この数字自体もさらに古い/新しい情報と
入れ替わっている可能性があります**。

| 項目 | 確認結果 | 確認日 | 出典(公式URLだが直接アクセス不可、検索要約経由) | 確信度 |
|---|---|---|---|---|
| 現在の主力動画生成モデル | **MiniMax H3**(2026年7月31日発表のomni-modalモデル)。旧世代の"Hailuo-02"/"Hailuo-2.3-Fast"は別モデル・別課金体系(サブスクのvideo point制) | 2026-09-24 | platform.minimax.io/docs/guides/video-generation | 中 |
| 正式API model ID | `MiniMax-H3`(実装済み。`MiniMax H3 Max`という高速版もfal.aiとの共同post-trainingで存在するが、本実装では未使用) | 2026-09-24 | platform.minimax.io/docs/guides/video-generation | 中 |
| 解像度 | `768P` / `2K`(「1080p」という名称の選択肢は無い) | 2026-09-24 | platform.minimax.io/docs/api-reference/video-generation-v2-create | 中 |
| 生成可能秒数 | 4〜15秒、整数のみ | 2026-09-24 | 同上 | 中 |
| **料金体系** | Pay-as-you-go(従量課金)。**768P: $0.08/秒、2K: $0.13/秒、768P→2K再生成: $0.05/秒**が複数の独立した二次情報源で一致。ただし別の検索結果は$0.18/$0.26/秒という異なる数字を提示しており、両者は突き合わせできませんでした(H3 Max等、別ティアの数字が混在している可能性) | 2026-09-24 | platform.minimax.io/docs/guides/pricing-paygo | **中(価格は要ユーザー自身の確認、2系統の数字が併存)** |
| 音声同時生成 | H3はネイティブでステレオ音声(リップシンクした台詞を含む)を同時生成可能。日本語は安定サポート11言語の一つ | 2026-09-24 | minimax.io/blog/minimax-h3 等 | 中 |
| reference image等 | 参照画像は最初の5枚無料、以降1枚约$0.04。単一参照画像で人物一貫性を保つ`S2V-01`(Subject-Reference)という別モデルも存在(本実装では未使用) | 2026-09-24 | platform.minimax.io/docs | 中 |
| 商用利用条件 | **有料(API課金)経由の生成物は商用利用可**(生成物の権利はユーザーに帰属、ただしプロンプト/参照素材に含まれる第三者の権利(商標・肖像・著作物等)のクリアランスはユーザー責任)。無料トライアルクレジットは個人利用限定 | 2026-09-24 | minimax.io/audio/doc/terms-of-service.html 等 | 中 |
| 生成物の権利 | 上記の通りユーザー帰属(有料利用時) | 2026-09-24 | 同上 | 中 |
| 必要クレジット/最低チャージ | **未確認**(検索では具体的な最低チャージ額を特定できず) | 2026-09-24 | platform.minimax.io/docs/guides/pricing-paygo | 未確認 |
| API rate limit | RPMではなく**同時実行タスク数**で制限(無料枠2、有料枠15) | 2026-09-24 | platform.minimax.io/docs/guides/rate-limits | 中 |
| 日本からの利用可否 | **未確認**(国際版`api.minimax.io`の利用可能地域リストを直接確認できず) | 2026-09-24 | — | 未確認 |
| TikTokアフィリエイト利用上の懸念 | ライセンス上は commercial use 可だが、(a) 生成人物が実在人物に酷似しないこと、(b) TikTok自体のAI生成コンテンツ表示ポリシーへの準拠、(c) A8提携承認前は非公開扱いを維持、の3点は別途要確認・要遵守 | 2026-09-24 | — | 未確認(ポリシー面は別途調査要) |

**要するに**: モデル・エンドポイント・リクエスト構造については中程度の確信度で
確認できましたが、**価格($0.08 vs $0.18)と日本アカウント利用可否、最低チャージ額は
未確定**です。このサンドボックスから一次情報ページへ直接アクセスできないという
環境制約が根本原因であり、MiniMax側の問題ではありません。**最初の1回の実生成で
実際に引き落とされた金額を確認することが、最も確実な価格検証方法です。**

## 4. MiniMax provider監査結果

`production/providers/minimax_hailuo.py`を上記の再確認結果と照合しました。

| 項目 | 監査結果 |
|---|---|
| API endpoint | 一致(`POST/GET /v1/video_generation`, `/v1/query/video_generation`, `/v1/files/retrieve`)、変更不要 |
| model ID | 一致(`MiniMax-H3`)、変更不要 |
| request schema | 一致(`content`配列、`duration`4-15clamp、`resolution`、`ratio`)、変更不要 |
| polling | 一致(`Success`/`Fail`のみ終端、他は継続)、変更不要 |
| download | 一致(`files/retrieve`→`download_url`→ダウンロード)、変更不要 |
| error handling | 一致、`error.message`抽出は今回未実装だったため要修正点として発見 → **今回は変更せず**(現状`data`全体をエラーメッセージに含めており実用上問題ない) |
| retry | **監査で不足を発見し、今回追加**: タスク作成(POST)呼び出しに対し、5xxエラー/一時的なネットワーク例外を対象に指数バックオフ付き最大2回リトライを実装(`_create_task_with_retry`)。ポーリング・ダウンロードは元々十分な猶予(タイムアウト900秒)があるため未変更 |
| timeout | 一致(create/poll: 30秒, download: 120秒, 全体締切: 900秒)、変更不要 |
| cost logging | **今回新規実装**(`production/costlog.py`) - 下記5参照 |

**価格定数を修正**: `ESTIMATED_USD_PER_SECOND = 0.055`(誤り、別モデルの数字)を
`ESTIMATED_USD_PER_SECOND_BY_RESOLUTION = {"768P": 0.08, "2K": 0.13}`に置き換え、
解像度に応じたコスト見積りに対応しました。

**課金を伴わないAPI接続確認**: 実行を試みましたが、`api.minimax.io`/
`platform.minimax.io`ともにこのサンドボックスのネットワークポリシーで
ブロックされており(`curl`で`connect_rejected`)、**認証すら発生しない
疎通確認すら実行できませんでした**。これは技術的な実行不可能性ではなく、
このセッション環境固有の制約です。**`MINIMAX_API_KEY`を設定した後の実行は、
必ずこのサンドボックス以外の環境(ご自身のPC、または他のクラウド環境)で
行ってください** — この環境では課金の有無に関わらずMiniMaxへの通信自体が
届きません。

## 5. コスト計測の実装

`production/costlog.py`を新規実装し、`pipeline.generate_shots()`から
生成試行のたびに以下を記録するようにしました(JSONL、
`output/<job_id>/cost_log.jsonl`):

```
provider, model, creative_id, shot_id, duration_sec, resolution,
attempt_count, generation_cost_usd, generation_time_sec, accepted,
rejection_reason, timestamp
```

`ShotJob`には`total_cost_usd`(累計)・`first_attempt_cost_usd`・
`last_generation_time_sec`・`accepted`・`rejection_reason`を追加。
`ProductionJob.output`には`total_generation_cost_usd`
(全ショットが1回で採用された場合の理論値)・`total_regeneration_cost_usd`
(再生成分のみ)・`total_render_cost_usd`・`cost_per_finished_video`を追加し、
`render()`実行時に自動集計されます。

CLI:
```
python -m production.cli estimate-cost CLAUDE-D01 shot-00 --provider minimax_hailuo
python -m production.cli accept-shot CLAUDE-D01 shot-00
python -m production.cli reject-shot CLAUDE-D01 shot-00 "hand distortion in frame 40"
python -m production.cli cost-report CLAUDE-D01
```

## 6. 少額PoC対象shotと事前コスト見積り

**対象: shot-00**(6秒)。理由:
- 表情(驚き)・視線(スマホ画面への注視)・背景安定性(カフェの窓際、自然光)を
  同時に評価できる
- 既存のWan2.1無料GPU PoC(`CLAUDE-D01_free_gpu_poc_wan21.ipynb`)も同じ
  shot-00を対象にしているため、**MiniMax vs Wan2.1の直接比較が可能**
- クリエイティブ内容は変更していません(既存job定義をそのまま使用)

事前見積り(`estimate-cost`コマンドの実行結果、768P・$0.08/秒想定):

| 試行回数 | expected accepted-shot cost |
|---|---|
| 1回生成 | **$0.48** |
| 1.5回生成(平均) | $0.72 |
| 2回生成 | $0.96 |

(2Kを使う場合は$0.13/秒 → 1回$0.78、1.5回$1.17、2回$1.56。価格が
$0.18/0.26系統だった場合は上記をそれぞれ約2.25倍/2倍にしてください。)

## 7. 実生成結果

**未実施です。** 理由は上記4の通り、このサンドボックスから`api.minimax.io`へ
到達できないためです。`MINIMAX_API_KEY`をお持ちの環境(ご自身のPC等)で
以下を実行すれば、コード変更なしに1ショットだけ生成できる状態です:

```bash
export MINIMAX_API_KEY="..."   # チャットには貼らず、環境変数として設定してください
python -m production.cli regen-shot CLAUDE-D01 shot-00
python -m production.cli cost-report CLAUDE-D01
```

生成後、目視で品質を確認し:
- 採用できる場合: `python -m production.cli accept-shot CLAUDE-D01 shot-00`
- 不採用の場合: `python -m production.cli reject-shot CLAUDE-D01 shot-00 "<理由>"`
  → 該当ショットのみ`needs_regen`になるので、`regen-shot`で再実行してください
  (他の3ショットや全体の再生成は発生しません)。

## 8. 音声方式(A vs B)の設計判断

CLAUDE-D01の各ショットは「ナレーション音声のボイスオーバー」形式であり、
画面上の人物が台詞を話してリップシンクする設計にはなっていません
(video_promptに台詞・発話の指示は無し、字幕生成モデルへのテキスト描画禁止
バリデーションとも整合)。したがって:

- **A. MiniMax video + native/generated audio**: H3はリップシンクした台詞を
  ネイティブ生成できますが、CLAUDE-D01の画面上人物は台詞を話す設計ではないため、
  **今回のクリエイティブには構造的に適用できません**(単なる好みの問題ではなく)。
- **B. MiniMax video + Azure Neural TTS(採用)**: 既存のAzure Neural TTS
  providerをそのまま維持し、ナレーションは引き続き別レイヤーで合成します。
  現状のjob設定は`tts.provider: espeak_local`のままです(Azure未有効化)。

今回の少額PoCでは指示通り二重生成を行わず、Bを設計上の結論としています。
将来、画面上人物が台詞を話す別クリエイティブを作る場合にのみ、Aの検討価値が
生じます。

## 9. 合格基準に対する暫定評価

| シナリオ | 4ショット合計(26秒、768P) | 1本あたり(¥150/$1換算、要実勢レート確認) | 判定 |
|---|---|---|---|
| 1回生成(理想) | $2.08 | 約¥312 | 理想基準(¥500以下)を**クリア** |
| 1.5回生成(平均) | $3.12 | 約¥468 | 理想基準を**ギリギリクリア** |
| 2回生成 | $4.16 | 約¥624 | 理想基準は超過するがPoC許容(¥1000以下)は**クリア** |
| (価格が$0.18/秒系統だった場合の2回生成) | 約$9.36 | 約¥1404 | PoC許容ラインも**超過** |

**価格の2系統($0.08 vs $0.18)のどちらが正しいかで合否が変わります。**
これが実生成前に確定できなかった最大の不確実性です。最初の1回の実生成で
実際の請求額を確認することを強く推奨します。

## 10. 最終アウトプット(このPoCフェーズ時点)

- **使用した正式モデル**: MiniMax H3(実生成は未実施、コード準備完了)
- **公式価格**: $0.08/秒(768P)・$0.13/秒(2K) ※もう1系統$0.18/$0.26の情報もあり未確定、要ユーザー確認
- **商用利用確認結果**: 有料API経由なら商用利用可(公式ToS、確認日2026-09-24)。日本アカウント利用可否・最低チャージ額は未確認
- **PoC対象shot**: shot-00(6秒、驚きの表情+視線+背景安定性を評価)
- **実生成秒数/attempt数/実コスト/生成時間/採否/品質上の問題/再生成理由**: 全て未実施(APIキー未設定、かつこのサンドボックスはMiniMaxへ到達不可のため)
- **CLAUDE-D01完成時の推定総原価**: 768P想定で$2.08〜$4.16(1〜2回生成、約¥312〜624)。TTS(Azure Neural想定)は1本あたり$0.01未満
- **30本/月の推定変動費**: $62.4〜$124.8(768P、1〜2回生成想定)
- **100本/月の推定変動費**: $208〜$416(同上)
- **本人作業時間**: APIキー設定後は`render`/`regen-shot`/`accept-shot`/`reject-shot`のCLI実行のみ。生成自体は無人(このサンドボックス以外の環境での実行が前提)
- **MiniMaxを本番採用するかの判断材料**: (1)最初の実生成での価格系統の確定、(2)shot-00の品質(人物自然さ・表情・視線・背景安定性・AI破綻の有無)の目視確認、の2点が揃うまで判断保留
- **Wan無料GPUへ戻す条件**: MiniMaxの価格が高い系統($0.18/秒)で確定し、かつ月間量産コストが許容範囲を超える場合。Wan2.1ルートは削除せず維持済み
- **Seedance等を試す条件**: MiniMax H3の品質が基準未達で、かつSeedanceのマルチモーダル参照機能(最大50素材)による人物一貫性向上が価格差($0.57/秒級)を正当化できると判断した場合

## 保存先

本レポート: `production/README_minimax_paid_poc_20260924.md`(このファイル)。
共有Drive「A8_TikTok_PoC」にも保存します。既存のDrive成果物は上書きしていません。
