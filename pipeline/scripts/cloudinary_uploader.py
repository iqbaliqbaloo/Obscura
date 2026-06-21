"""Cloudinary video uploader — uploads portrait MP4 and returns a public URL for Facebook/Instagram Reels."""
import logging
import os
from pathlib import Path

import cloudinary
import cloudinary.uploader

log = logging.getLogger(__name__)


def upload_video(video_path: Path) -> str:
    """Upload video to Cloudinary. Returns public HTTPS URL, or empty string on failure."""
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key    = os.getenv("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "").strip()

    if not all([cloud_name, api_key, api_secret]):
        log.warning("Cloudinary: credentials not set (CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET) — skipping")
        return ""

    cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)

    try:
        log.info("Cloudinary: uploading %s (%.1f MB) …",
                 video_path.name, video_path.stat().st_size / 1_048_576)
        result = cloudinary.uploader.upload(
            str(video_path),
            resource_type="video",
            folder="obscura",
            use_filename=True,
            unique_filename=True,
            overwrite=False,
        )
        url = result.get("secure_url", "")
        if url:
            log.info("Cloudinary: ready → %s", url)
        else:
            log.error("Cloudinary: upload returned no URL — response: %s", result)
        return url
    except Exception as exc:
        log.error("Cloudinary: upload failed: %s", exc)
        return ""
