# CLAUDE-D01 — 無料枠手動生成用 Shot Production Package

CLAUDE-D01のクリエイティブ(`production/jobs/CLAUDE-D01.json`)は一切変更していません。
このパッケージは、その4ショットを無料枠のWeb UIで**人間が手動生成**するための、
コピー&ペースト用プロンプト集です。

## 前提・注意事項(必ず先にお読みください)

このセッションで確認した限り、**主要な無料枠AI動画生成サービスは例外なく
「商用利用不可・透かし(watermark)入り・個人利用限定」をToSに明記しています**
(Invideo AI、Dreamina、Kling AI、Pika、Luma Dream Machineを確認、すべて同一パターン)。
- Invideo AI無料プラン: 商用利用不可、透かしあり、**API提供なし**(Web UI手動操作のみ)
- Dreamina無料枠: 個人利用限定ライセンス、透かしあり、公式無料APIなし(公式APIはSeedance/BytePlus ModelArkの有料版のみ)

したがって、ここで生成した動画は**技術検証・品質比較の目的専用**であり、
今回のCLAUDE-D01のように「PR表記付きの商用告知動画」としては、無料枠のままでは
公開できません(A8提携承認前につき、そもそも現時点で外部公開・投稿は禁止済みです)。
「無料枠だけでpublish候補品質に到達したか」を判定する際も、この商用利用制限が
解消されない限り`quality_tier`は`free_tier_noncommercial_preview`より上がりません。

ブラウザの自動操作・スクレイピングは行いません。以下はすべて人間が手動でWeb UIを
操作して生成してください。

## 手順

1. 下記の優先順位でサービスを開く(**Invideo AI優先**、次点Dreamina)。
2. 各ショットのプロンプトをそのままコピーし、text-to-videoで生成。
3. 縦動画(9:16)設定があれば選択。なければ生成後にトリミングで対応可能(パイプライン側で自動リサイズ・クロップされます)。
4. ダウンロードしたmp4ファイルを控えておく。
5. 4ショットすべて揃ったら、以下のコマンドで取り込み:
   ```bash
   python -m production.cli ingest-shot CLAUDE-D01 shot-00 /path/to/shot00.mp4 --service invideo --license-note "無料プラン、透かしあり、商用利用未確認"
   python -m production.cli ingest-shot CLAUDE-D01 shot-01 /path/to/shot01.mp4 --service invideo --license-note "同上"
   python -m production.cli ingest-shot CLAUDE-D01 shot-02 /path/to/shot02.mp4 --service invideo --license-note "同上"
   python -m production.cli ingest-shot CLAUDE-D01 shot-03 /path/to/shot03.mp4 --service invideo --license-note "同上"
   python -m production.cli render CLAUDE-D01
   ```
   (`--commercial-clear` フラグは、実際にアカウントのプラン/ダッシュボードで商用利用が
   許可されていると確認できた場合のみ付けてください。デフォルトは「未確認」扱いです。)

## ショット別プロンプト(そのままコピー可)

### shot-00 (6秒)
**日本語プロンプト:**
明るいカフェの窓際で、驚いた表情でスマホの画面を見つめる20代女性。自然光、縦構図9:16、手元にスマホ、清潔感のあるカジュアルな服装。

**English prompt (fallback if the tool works better in English):**
A woman in her 20s sitting by a bright cafe window, looking at her phone screen with a surprised expression. Natural light, vertical 9:16 composition, holding a smartphone, clean casual outfit. Eye-level, static shot, shallow depth of field.

**Negative / avoid:** blurry, distorted hands, extra fingers, low quality, on-screen text, watermark, logo (note: the tool's own watermark cannot be avoided on free tier - this refers to unwanted extra text/logos in the generated scene itself)

---

### shot-01 (7秒)
**日本語プロンプト:**
スマホアプリ画面を指でタップしてメモを整理する手元のクローズアップ。清潔感のあるデスク、縦構図9:16、柔らかい自然光。

**English prompt:**
Close-up of a hand tapping a smartphone app screen to organize notes, top-down shot. Clean desk background, vertical 9:16 composition, soft natural light.

**Negative / avoid:** blurry, distorted hands, extra fingers, low quality, on-screen text, watermark, logo

---

### shot-02 (7秒)
**日本語プロンプト:**
カフェを背景に笑顔でうなずく20代女性、リラックスした雰囲気、縦構図9:16、自然光。

**English prompt:**
A woman in her 20s smiling and nodding, cafe background, relaxed atmosphere, vertical 9:16 composition, natural light. Eye-level, slight handheld motion.

**Negative / avoid:** blurry, distorted hands, extra fingers, low quality, on-screen text, watermark, logo

---

### shot-03 (6秒)
**日本語プロンプト:**
スマホを持ち上げてカメラに見せるジェスチャーをする20代女性、明るい背景、縦構図9:16、指差しのポーズ。

**English prompt:**
A woman in her 20s raising her phone toward the camera, pointing gesture, bright background, vertical 9:16 composition. Eye-level, static shot.

**Negative / avoid:** blurry, distorted hands, extra fingers, low quality, on-screen text, watermark, logo

## 生成時に確認・記録してほしいこと(比較レポート用)

各ショットについて、可能であれば以下をメモしてください(厳密でなくて構いません):
- 使用サービス名・モデル名(表示されていれば)
- 消費したクレジット数
- 生成にかかった時間(送信〜ダウンロード可能まで)
- 再生成した回数(気に入らず何度も試した場合)
- 人物の一貫性(同一人物に見えるか、ショット間で顔や服装が大きく変わらないか)
- AIっぽさ(手の破綻、不自然な動き、違和感のある背景など)

これらは `production/README_free_poc_comparison.md` の比較表に転記します。
