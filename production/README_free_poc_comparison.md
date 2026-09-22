# Team Claude 無料PoC比較レポート — CLAUDE-D01

作成日: 2026-09-22。CLAUDE-D01のクリエイティブは変更していません
(`production/jobs/CLAUDE-D01.json` は前フェーズから不変)。
GPT-D01(Team GPT、Invideo無料PoC実施中)とは独立して作成・記録しています。
GPT-D01の内容は参照・模倣していません。

## 方針決定の経緯(ボトルネックの記録)

優先順位に従って調査した結果、**Invideo AI・Dreaminaのいずれも無料枠に
自動化可能な公式APIが存在しません**(Web UIの手動操作のみ)。また、無料枠
主要サービスを横断的に確認したところ、以下が共通のボトルネックとして
判明しました:

| サービス | 無料枠API | 商用利用 | 透かし |
|---|---|---|---|
| Invideo AI | なし(Web UIのみ) | 不可(ToSで明記) | あり(右下、常時) |
| Dreamina | なし(公式APIはSeedance/BytePlus有料版のみ) | 個人利用限定 | あり |
| Kling AI(参考調査) | あり(有料枠のみ) | 無料枠は不可 | あり |
| Pika(参考調査) | あり(有料枠のみ) | 無料枠(Basic以下)は不可 | あり |
| Luma Dream Machine(参考調査) | あり(有料枠のみ) | 無料/Liteは不可、Plus以上で解禁 | あり(無料枠は永続的に除去不可) |

→ **ボトルネック: 「追加費用0円」かつ「自動化(API)」かつ「商用利用可」の
3条件を同時に満たす動画生成手段は、調査した範囲では存在しません。**
無料枠は例外なく「個人利用限定・透かしあり・API非公開」という設計になっており、
これは特定サービスの制約ではなく無料枠モデル全般の構造的な制約です。

このため、優先順位1(Invideo自動生成)・2(Dreamina自動生成)は「自動化」の
時点で成立せず、実質的に優先順位3(人間による無料枠手動生成 + shot単位の
production package)が今回の唯一の実行可能パスです。パッケージは
`production/jobs/CLAUDE-D01_free_tier_shot_package.md` に用意済みで、
Invideo AIを優先候補として明記しています。有料Seedanceは指示通り一切呼び出していません。

## 比較表(記録用テンプレート)

人間による手動生成が完了し次第、この表を埋めます。現時点で判明している値のみ記入。

| 項目 | 値 | 備考 |
|---|---|---|
| 使用サービス／モデル | (未実施) | 優先: Invideo AI → 次点 Dreamina |
| 有料課金額 | **0円**(確定) | APIキー取得・課金設定は一切実行していません |
| 使用した無料クレジット | (未実施・人間の手動生成待ち) | |
| 生成shot数 | 0/4(未実施) | CLAUDE-D01は4ショット構成 |
| 再生成回数 | 0(未実施) | `ShotJob.attempts` で自動記録されます |
| AI生成に要した時間 | N/A(未実施) | 人間の手動操作のため計測はご本人にお願いします |
| 人間操作が必要だった工程と所要時間 | アカウント作成・ログイン・各ショットのプロンプト入力・生成・ダウンロード・`ingest-shot`への受け渡し(全工程) | 現状、無料枠にAPIがないため生成自体が全工程で人手 |
| 人物一貫性 | (未実施) | ショット間で同一人物に見えるか |
| AIらしさ | (未実施) | 手の破綻・不自然な動き等 |
| 音声品質 | espeak-ng(placeholder、変更なし) | 本フェーズでは音声は変更していません |
| 字幕品質 | 自動生成SRT(placeholder的、TTSタイミング依存) | ロジック自体は前フェーズと同一 |
| 最終MP4のquality tier | 未生成 | 全ショットがInvideo/Dreamina無料枠生成になれば `free_tier_noncommercial_preview`(商用利用未確認のため`final_candidate`にはなりません) |

## 実装済みの受け皿(コード側の対応)

- `production/pipeline.py: ingest_shot()` — 人間が無料枠で生成したmp4を
  取り込み、job JSONの該当ショットを `assigned_provider="human_free_tier:<service>"`,
  `license_commercial_clear=False`(デフォルト、明示的に確認できた場合のみTrueに)
  としてマークします。取り込み時に1080x1920/h264/30fpsへ自動正規化するため、
  Invideo/Dreaminaの出力解像度に関わらずそのまま合成パイプラインに載ります。
- `production/pipeline.py: _assess_quality()` — 全ショットがこの経路で
  生成された場合、`quality_tier="free_tier_noncommercial_preview"` を
  自動付与し、`placeholder_preview`/`final_candidate`と混同されないようにしています。
- CLI: `python -m production.cli ingest-shot CLAUDE-D01 shot-00 <file> --service invideo`
- Seedance有料Providerの実装はそのまま保持していますが、`ARK_API_KEY`は
  設定しておらず、本フェーズでは一度も呼び出していません。

## 次にお願いしたいこと(1ステップ)

**Invideo AIの無料アカウントを作成し、上記4ショットのプロンプトで
text-to-video生成を試していただけますか。**
生成できたmp4ファイル(4本)をこちらに渡していただければ、
`ingest-shot`で取り込み→自動合成→QA→比較表の記入まで進めます。
(Invideoで難しければ、次点のDreaminaで生成した場合も同様に対応できます。)

ログイン・アカウント作成以降の手動生成作業そのものは人間の操作が必須のため、
私からは代行できません。ここが今回の自動化の限界点です。
