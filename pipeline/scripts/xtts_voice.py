"""
XTTS v2 Voice Cloning — Coqui TTS

Clones the voice from pipeline/assets/reference_voice.mp4 using XTTS v2.
Generates speech in Hindi (language='hi') so Roman Urdu words are
pronounced correctly (Hindi and Urdu share the same spoken sounds).

First run: downloads XTTS-v2 model (~1.9 GB) to ~/.local/share/tts/
Subsequent runs: loads from cache — fast.

Returns:
  True  — audio written to out_path
  False — XTTS unavailable / failed (caller falls back to edge-tts)
"""

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_REF_MP4    = Path(__file__).parent.parent / "assets" / "reference_voice.mp4"
_REF_WAV    = Path(__file__).parent.parent / "assets" / "reference_voice.wav"
_XTTS_LANG  = "hi"   # Hindi — same phonology as Urdu; Roman Urdu text pronounced correctly
_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"


def _patch_transformers_compat() -> None:
    """
    Coqui TTS imports BeamSearchScorer from the top-level transformers namespace.
    In transformers>=4.46 it was moved to transformers.generation.beam_search.
    This patch restores it so XTTS loads without ImportError on any transformers version.
    """
    try:
        import transformers
        if hasattr(transformers, "BeamSearchScorer"):
            return  # already in the right place — nothing to do
        # Try the new location (transformers >= 4.46)
        import importlib
        for mod_path in ("transformers.generation.beam_search", "transformers.generation"):
            try:
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, "BeamSearchScorer", None)
                if cls is not None:
                    transformers.BeamSearchScorer = cls
                    log.debug("XTTS compat: patched BeamSearchScorer from %s", mod_path)
                    return
            except Exception:
                continue
        log.warning("XTTS compat: BeamSearchScorer not found in transformers — XTTS may fail")
    except Exception as exc:
        log.debug("XTTS compat patch error: %s", exc)


def xtts_available() -> bool:
    """Return True if TTS package is installed and reference voice exists."""
    if not _REF_MP4.exists():
        return False
    try:
        _patch_transformers_compat()
        import TTS  # noqa: F401
        os.environ.setdefault("COQUI_TOS_AGREED", "1")
        return True
    except ImportError:
        return False


def extract_reference_wav() -> Path | None:
    """Extract audio from reference_voice.mp4 → reference_voice.wav.
    Returns path on success, None on failure.
    """
    if _REF_WAV.exists() and _REF_WAV.stat().st_size > 10_000:
        return _REF_WAV

    if not _REF_MP4.exists():
        log.warning("XTTS: reference_voice.mp4 not found in pipeline/assets/")
        return None

    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("XTTS: ffmpeg not found — cannot extract reference audio")
        return None

    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(_REF_MP4),
             "-vn",                        # strip video
             "-ac", "1",                   # mono
             "-ar", "22050",               # XTTS needs 22050 Hz
             "-c:a", "pcm_s16le",
             str(_REF_WAV)],
            capture_output=True, timeout=60,
        )
        if _REF_WAV.exists() and _REF_WAV.stat().st_size > 10_000:
            log.info("XTTS: reference audio extracted → %s (%.1f KB)",
                     _REF_WAV.name, _REF_WAV.stat().st_size / 1024)
            return _REF_WAV
    except Exception as exc:
        log.warning("XTTS: reference audio extraction failed: %s", exc)

    return None


# Module-level cache so the model is only loaded once per process.
# _xtts_broken is set True after the first confirmed failure so subsequent
# scenes skip the model-load attempt entirely instead of retrying every time.
_tts_instance = None
_xtts_broken  = False


def _get_tts():
    global _tts_instance, _xtts_broken
    if _xtts_broken:
        return None
    if _tts_instance is not None:
        return _tts_instance

    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    _patch_transformers_compat()   # fix BeamSearchScorer for transformers>=4.46

    try:
        from TTS.api import TTS
        log.info("XTTS: loading model '%s' (first run downloads ~1.9 GB) …", _MODEL_NAME)
        use_gpu = os.getenv("XTTS_GPU", "0") == "1"
        tts = TTS(_MODEL_NAME, gpu=use_gpu)
        _tts_instance = tts
        log.info("XTTS: model loaded successfully")
        return tts
    except Exception as exc:
        log.warning("XTTS: model load failed: %s — skipping XTTS for remaining scenes", exc)
        _xtts_broken = True
        return None


def generate(text: str, out_path: Path) -> bool:
    """Generate speech cloning the reference voice. Returns True on success.

    XTTS always writes PCM WAV. We convert to MP3 afterwards using ffmpeg
    so the rest of the pipeline receives the expected format.
    """
    ref_wav = extract_reference_wav()
    if ref_wav is None:
        return False

    tts = _get_tts()
    if tts is None:
        return False

    # Generate to a temp WAV file, then convert to MP3
    tmp_wav = out_path.with_suffix(".xtts_tmp.wav")
    try:
        log.info("XTTS: synthesising %d chars in voice clone …", len(text))
        tts.tts_to_file(
            text=text,
            speaker_wav=str(ref_wav),
            language=_XTTS_LANG,
            file_path=str(tmp_wav),
        )
        if not (tmp_wav.exists() and tmp_wav.stat().st_size > 500):
            log.warning("XTTS: output WAV missing or empty")
            return False

        # Convert WAV → MP3
        import shutil
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            log.warning("XTTS: ffmpeg not found for WAV→MP3 conversion")
            return False

        subprocess.run(
            [ffmpeg, "-y", "-i", str(tmp_wav),
             "-c:a", "libmp3lame", "-q:a", "2",
             str(out_path)],
            capture_output=True, timeout=60,
        )
        if out_path.exists() and out_path.stat().st_size > 500:
            log.info("XTTS: ✓ generated %s (%.0f KB)",
                     out_path.name, out_path.stat().st_size / 1024)
            return True

    except Exception as exc:
        log.warning("XTTS: synthesis failed: %s", exc)
    finally:
        if tmp_wav.exists():
            tmp_wav.unlink(missing_ok=True)

    return False
