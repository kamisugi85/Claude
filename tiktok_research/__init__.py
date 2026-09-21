"""TikTok Competitive Intelligence基盤(既存のa8_automation/A8 Collectorとは
完全に独立したモジュール)。

役割: A8案件データを受け取り、TikTok上の市場・競合調査の「ジョブ」を生成し、
(TikTokへの実アクセスは行うクラウドブラウザ環境=ChatGPT Work等の外部主体が担当)
その結果を取り込んで、Team ChatGPT/Team Claude共通の「観測事実」データへ変換する。

このモジュール自体はTikTokへの直接アクセス・ログイン・スクレイピングを一切行わない。
"""
