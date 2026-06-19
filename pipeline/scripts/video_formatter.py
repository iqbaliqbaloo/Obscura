"""
Video Formatter — converts 16:9 landscape video to 9:16 portrait for Shorts/Reels.
Uses FFmpeg: crops center, adds blurred background sides, repositions subtitles.
"""
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def make_portrait(
    input_path: Path,
    output_path: Path,
    target_width:  int = 1080,
    target_height: int = 1920,
) -> Path | None:
    """
    Convert landscape (16:9) video to portrait (9:16) for Shorts/Reels/TikTok.
    Method: Blurred background + centered crop.
    Returns output_path on success, None on failure.
    """
    if not input_path.exists():
        log.error("VideoFormatter: input not found: %s", input_path)
        return None

    # Calculate the crop area from center of landscape video
    # At 1080p landscape (1920x1080): crop 608x1080 from center → scale to 1080x1920
    # FFmpeg filter: scale to fill height, then crop center column

    # For better quality: blurred background approach
    # Layer 1: Blurred full-frame background
    # Layer 2: Original video centered with proper scale
    vf_complex = (
        f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},boxblur=20:5[bg];"
        f"[0:v]scale=-2:{int(target_height * 0.75)}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-filter_complex", vf_complex,
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error("VideoFormatter: FFmpeg failed:\n%s", result.stderr[-1000:])
            return None
        log.info("VideoFormatter: portrait version created: %s (%.1f MB)",
                 output_path.name, output_path.stat().st_size / 1_048_576)
        return output_path
    except subprocess.TimeoutExpired:
        log.error("VideoFormatter: FFmpeg timeout")
        return None
    except Exception as exc:
        log.error("VideoFormatter: exception: %s", exc)
        return None
