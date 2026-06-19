"""Make.com webhook uploader — triggers Make.com scenarios for Facebook and Instagram upload."""
import logging
import os
import time
from pathlib import Path
import requests

log = logging.getLogger(__name__)


def _trigger_webhook(webhook_url: str, payload: dict, platform: str) -> bool:
    """Send webhook trigger to Make.com. Returns True on success."""
    if not webhook_url:
        log.warning("Make.com: %s webhook URL not set — skipping", platform)
        return False

    for attempt in range(1, 4):
        try:
            r = requests.post(webhook_url, json=payload, timeout=30)
            if r.ok:
                log.info("Make.com: %s webhook triggered successfully", platform)
                return True
            else:
                log.warning("Make.com: %s HTTP %d: %s", platform, r.status_code, r.text[:200])
        except requests.exceptions.Timeout:
            log.warning("Make.com: %s timeout (attempt %d/3)", platform, attempt)
        except Exception as exc:
            log.warning("Make.com: %s error (attempt %d/3): %s", platform, attempt, exc)

        if attempt < 3:
            time.sleep(5 * attempt)

    log.error("Make.com: %s all 3 webhook attempts failed", platform)
    return False


def upload_to_facebook(video_url: str, title: str, description: str, thumbnail_url: str = "") -> bool:
    """
    Trigger Make.com Facebook scenario.
    Uses MAKECOM_FACEBOOK_WEBHOOK from environment.
    video_url must be a publicly accessible URL (e.g., from a CDN or temporary storage).
    """
    webhook_url = os.getenv("MAKECOM_FACEBOOK_WEBHOOK", "").strip()
    payload = {
        "video_url":     video_url,
        "title":         title[:255],
        "description":   description[:5000],
        "thumbnail_url": thumbnail_url,
        "platform":      "facebook",
    }
    return _trigger_webhook(webhook_url, payload, "Facebook")


def upload_to_instagram(video_url: str, caption: str, thumbnail_url: str = "") -> bool:
    """
    Trigger Make.com Instagram Reels scenario.
    Uses MAKECOM_INSTAGRAM_WEBHOOK from environment.
    """
    webhook_url = os.getenv("MAKECOM_INSTAGRAM_WEBHOOK", "").strip()
    payload = {
        "video_url":     video_url,
        "caption":       caption[:2200],  # Instagram caption limit
        "thumbnail_url": thumbnail_url,
        "platform":      "instagram",
    }
    return _trigger_webhook(webhook_url, payload, "Instagram")
