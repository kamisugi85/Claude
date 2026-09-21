from __future__ import annotations

import json
import logging
import os
import urllib.request

from .utils import iso_now, write_json

logger = logging.getLogger("a8_automation")


def write_alert(alert_json_path: str, kind: str, message: str, context: dict) -> dict:
    payload = {
        "timestamp": iso_now(),
        "kind": kind,
        "message": message,
        "context": context,
    }
    write_json(alert_json_path, payload)
    _notify_slack(payload)
    return payload


def _notify_slack(payload: dict) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL is not set; skipping Slack notification (alert.json was still written).")
        return

    text = (
        ":rotating_light: *A8自動巡回: 異常検知により停止しました*\n"
        f"種別: {payload['kind']}\n"
        f"内容: {payload['message']}\n"
        f"時刻: {payload['timestamp']}\n"
        f"直前URL: {payload['context'].get('last_url', 'N/A')}\n"
        f"run_id: {payload['context'].get('run_id', 'N/A')}"
    )
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        logger.exception("Slack notification failed (webhook URL is not logged).")
