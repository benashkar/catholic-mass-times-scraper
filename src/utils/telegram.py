"""
telegram.py — Send Telegram notifications for pipeline alerts.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables.
"""

import os

import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_telegram(message, parse_mode="HTML"):
    """Send a message to the configured Telegram chat.

    Returns True on success, False on failure.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [--] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping notification")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message[:4096],
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        print(f"  [WARN] Telegram API returned {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"  [WARN] Telegram send failed: {e}")
        return False
