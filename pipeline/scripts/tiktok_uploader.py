"""TikTok Content Posting API uploader."""
import logging
import os
import time
from pathlib import Path
import requests

log = logging.getLogger(__name__)

_TIKTOK_UPLOAD_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"


def _get_access_token() -> str | None:
    """Get TikTok access token from environment."""
    return os.getenv("TIKTOK_ACCESS_TOKEN", "").strip() or None


def upload_to_tiktok(video_path: Path, title: str, description: str = "") -> bool:
    """
    Upload video to TikTok using Content Posting API.
    Returns True on success, False on failure.
    Requires TIKTOK_ACCESS_TOKEN in environment.
    """
    access_token = _get_access_token()
    if not access_token:
        log.warning("TikTok: TIKTOK_ACCESS_TOKEN not set — skipping")
        return False

    if not video_path.exists():
        log.error("TikTok: video file not found: %s", video_path)
        return False

    file_size = video_path.stat().st_size

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

    # Step 1: Initialize upload
    try:
        init_payload = {
            "post_info": {
                "title":        title[:150],
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet":  False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source":     "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": min(file_size, 10_000_000),
                "total_chunk_count": max(1, (file_size + 9_999_999) // 10_000_000),
            },
        }
        r = requests.post(_TIKTOK_UPLOAD_URL, headers=headers, json=init_payload, timeout=30)
        if not r.ok:
            log.error("TikTok: init failed HTTP %d: %s", r.status_code, r.text[:300])
            return False

        data       = r.json().get("data", {})
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")

        if not publish_id or not upload_url:
            log.error("TikTok: init missing publish_id or upload_url: %s", data)
            return False

        log.info("TikTok: init OK (publish_id=%s)", publish_id)

    except Exception as exc:
        log.error("TikTok: init exception: %s", exc)
        return False

    # Step 2: Upload video file in chunks
    try:
        chunk_size = min(file_size, 10_000_000)
        offset = 0
        chunk_index = 0

        with open(video_path, "rb") as f:
            while offset < file_size:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                end_offset = offset + len(chunk) - 1
                upload_headers = {
                    "Content-Range":  f"bytes {offset}-{end_offset}/{file_size}",
                    "Content-Type":   "video/mp4",
                    "Content-Length": str(len(chunk)),
                }
                ur = requests.put(upload_url, headers=upload_headers, data=chunk, timeout=120)
                if ur.status_code not in (200, 201, 206):
                    log.error("TikTok: chunk %d upload failed HTTP %d", chunk_index, ur.status_code)
                    return False
                offset += len(chunk)
                chunk_index += 1

        log.info("TikTok: video uploaded (%d chunks, %d bytes)", chunk_index, file_size)

    except Exception as exc:
        log.error("TikTok: upload exception: %s", exc)
        return False

    # Step 3: Poll status
    for poll in range(1, 11):
        try:
            time.sleep(15)
            sr = requests.post(
                _TIKTOK_STATUS_URL,
                headers=headers,
                json={"publish_id": publish_id},
                timeout=20,
            )
            if sr.ok:
                status_data = sr.json().get("data", {})
                status      = status_data.get("status", "")
                if status == "PUBLISH_COMPLETE":
                    log.info("TikTok: publish complete (publish_id=%s)", publish_id)
                    return True
                elif status in ("FAILED", "PROCESSING_FAILED"):
                    log.error("TikTok: publish failed: %s", status_data)
                    return False
                else:
                    log.info("TikTok: status=%s (poll %d/10)", status, poll)
            else:
                log.warning("TikTok: status poll HTTP %d (poll %d/10)", sr.status_code, poll)
        except Exception as exc:
            log.warning("TikTok: status poll error (poll %d/10): %s", poll, exc)

    log.error("TikTok: publish timed out after 10 polls")
    return False
