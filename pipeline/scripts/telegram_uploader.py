"""Telegram channel uploader — sends video file to Telegram channel via Bot API."""
import logging
import os
import time
from pathlib import Path
import requests

log = logging.getLogger(__name__)

def upload_to_telegram(video_path: Path, caption: str, thumbnail_path: Path | None = None) -> bool:
    """
    Upload video to Telegram channel.
    Returns True on success, False on failure.
    Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID from environment.
    """
    bot_token  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

    if not bot_token or not channel_id:
        log.warning("Telegram: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set — skipping")
        return False

    if not video_path.exists():
        log.error("Telegram: video file not found: %s", video_path)
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    for attempt in range(1, 4):
        try:
            with open(video_path, "rb") as vf:
                files = {"video": (video_path.name, vf, "video/mp4")}
                data  = {
                    "chat_id":           channel_id,
                    "caption":           caption[:1024],  # Telegram caption limit
                    "parse_mode":        "HTML",
                    "supports_streaming": "true",
                }
                r = requests.post(url, data=data, files=files, timeout=120)

            if r.ok:
                result = r.json()
                if result.get("ok"):
                    msg_id = result.get("result", {}).get("message_id", "?")
                    log.info("Telegram: uploaded successfully (message_id=%s)", msg_id)
                    return True
                else:
                    log.warning("Telegram API error: %s", result.get("description", "unknown"))
            else:
                log.warning("Telegram HTTP %d: %s", r.status_code, r.text[:200])

        except requests.exceptions.Timeout:
            log.warning("Telegram: upload timeout (attempt %d/3)", attempt)
        except Exception as exc:
            log.warning("Telegram: upload error (attempt %d/3): %s", attempt, exc)

        if attempt < 3:
            time.sleep(10 * attempt)

    log.error("Telegram: all 3 upload attempts failed")
    return False
