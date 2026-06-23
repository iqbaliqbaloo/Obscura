"""
XTTS v2 Voice Cloning — Coqui TTS

Clones the voice from pipeline/assets/reference_voice.mp4 using XTTS v2.
Generates speech in Hindi (language='hi') so Roman Urdu words are
pronounced correctly (Hindi and Urdu share the same spoken sounds).

Improvements over the original:
  • Emotion-based speed control via FFmpeg atempo (matches edge-tts rates)
  • Long text chunked at sentence boundaries (XTTS v2 limit ~240 chars/call)
  • Reference audio: silence-trimmed, capped at 25 s, level-normalised
  • Output: 44100 Hz stereo MP3 (matches what the rest of the pipeline expects)
  • All temp files cleaned up even on exception

First run: downloads XTTS-v2 model (~1.9 GB) to ~/.local/share/tts/
Subsequent runs: loads from cache.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_REF_MP4    = Path(__file__).parent.parent / "assets" / "reference_voice.mp4"
_REF_WAV    = Path(__file__).parent.parent / "assets" / "reference_voice.wav"
_XTTS_LANG  = "hi"   # Hindi phonology matches Roman Urdu
_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
_MAX_CHARS  = 230    # safe per-chunk limit for XTTS v2

# Speed multipliers per emotion — mirror the edge-tts _EDGE_RATE values
_XTTS_SPEED: dict[str, float] = {
    "excited":    1.25,
    "mysterious": 1.10,
    "dramatic":   1.20,
    "neutral":    1.18,
}


# ── Transformers compat patch ─────────────────────────────────────────────────

def _patch_transformers_compat() -> None:
    """Restore BeamSearchScorer for transformers >= 4.46 where it moved namespaces."""
    try:
        import transformers
        if hasattr(transformers, "BeamSearchScorer"):
            return
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
        log.warning("XTTS compat: BeamSearchScorer not found — XTTS may fail")
    except Exception as exc:
        log.debug("XTTS compat patch: %s", exc)


# ── Availability ──────────────────────────────────────────────────────────────

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


# ── Reference audio ───────────────────────────────────────────────────────────

def extract_reference_wav() -> Path | None:
    """Extract + pre-process reference audio from reference_voice.mp4.

    Steps:
      1. Cap at 25 s — XTTS only needs a short sample; trimming speeds up clone loading
      2. Strip leading silence (threshold -50 dBFS) so voice starts immediately
      3. Normalize to -16 LUFS so the cloned voice has consistent loudness
      4. Convert to 22050 Hz mono PCM — XTTS v2 native format

    Re-extracts if the MP4 source is newer than the cached WAV.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("XTTS: ffmpeg not found — cannot extract reference audio")
        return None

    if not _REF_MP4.exists():
        log.warning("XTTS: reference_voice.mp4 not found at %s", _REF_MP4)
        return None

    # Use cached WAV if it exists and is newer than the source MP4
    if (_REF_WAV.exists()
            and _REF_WAV.stat().st_size > 10_000
            and _REF_WAV.stat().st_mtime >= _REF_MP4.stat().st_mtime):
        return _REF_WAV

    try:
        subprocess.run(
            [ffmpeg, "-y",
             "-i",  str(_REF_MP4),
             "-t",  "25",                          # max 25 s reference clip
             "-vn",                                # strip video
             "-ac", "1",                           # mono
             "-ar", "22050",                       # XTTS native sample rate
             "-af", (
                 "silenceremove=start_periods=1"
                 ":start_silence=0.1"
                 ":start_threshold=-50dB,"         # trim leading silence
                 "loudnorm=I=-16:LRA=7:TP=-1.5"   # EBU R128 level normalization
             ),
             "-c:a", "pcm_s16le",
             str(_REF_WAV)],
            capture_output=True, timeout=60,
        )
        if _REF_WAV.exists() and _REF_WAV.stat().st_size > 10_000:
            log.info("XTTS: reference audio ready → %.1f KB",
                     _REF_WAV.stat().st_size / 1024)
            return _REF_WAV
        log.warning("XTTS: reference audio extraction produced empty file")
    except Exception as exc:
        log.warning("XTTS: reference audio extraction failed: %s", exc)

    return None


# ── Model cache ───────────────────────────────────────────────────────────────

_tts_instance = None
_xtts_broken  = False


def _get_tts():
    global _tts_instance, _xtts_broken
    if _xtts_broken:
        return None
    if _tts_instance is not None:
        return _tts_instance
    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    _patch_transformers_compat()
    try:
        from TTS.api import TTS
        log.info("XTTS: loading model (first run downloads ~1.9 GB) …")
        use_gpu = os.getenv("XTTS_GPU", "0") == "1"
        _tts_instance = TTS(_MODEL_NAME, gpu=use_gpu)
        log.info("XTTS: model loaded")
        return _tts_instance
    except Exception as exc:
        log.warning("XTTS: model load failed: %s — disabling for this run", exc)
        _xtts_broken = True
        return None


# ── Text utilities ────────────────────────────────────────────────────────────

def _split_text(text: str) -> list[str]:
    """Split text into chunks ≤ _MAX_CHARS at sentence boundaries.

    Splits on . ! ? । (Devanagari danda) then falls back to commas,
    then to word boundaries if a single sentence exceeds the limit.
    """
    # Split at sentence-ending punctuation followed by whitespace
    raw_sentences = re.split(r'(?<=[.!?।])\s+', text.strip())
    chunks: list[str] = []
    current = ""

    for sent in raw_sentences:
        sent = sent.strip()
        if not sent:
            continue
        # Fits into the current chunk
        candidate = (current + " " + sent).strip() if current else sent
        if len(candidate) <= _MAX_CHARS:
            current = candidate
            continue
        # Flush current
        if current:
            chunks.append(current)
        # Sentence itself exceeds limit — split at commas then words
        if len(sent) > _MAX_CHARS:
            parts = re.split(r',\s*', sent)
            part_buf = ""
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                trial = (part_buf + ", " + part).strip(", ") if part_buf else part
                if len(trial) <= _MAX_CHARS:
                    part_buf = trial
                else:
                    if part_buf:
                        chunks.append(part_buf)
                    # Individual comma-clause still too long — split at words
                    words = part.split()
                    word_buf = ""
                    for w in words:
                        trial_w = (word_buf + " " + w).strip() if word_buf else w
                        if len(trial_w) <= _MAX_CHARS:
                            word_buf = trial_w
                        else:
                            if word_buf:
                                chunks.append(word_buf)
                            word_buf = w
                    if word_buf:
                        chunks.append(word_buf)
                    part_buf = ""
            if part_buf:
                current = part_buf
            else:
                current = ""
        else:
            current = sent

    if current:
        chunks.append(current)

    return [c.strip() for c in chunks if c.strip()]


# ── FFmpeg helpers ────────────────────────────────────────────────────────────

def _concat_wavs(wav_files: list[Path], out_wav: Path) -> bool:
    """Concatenate WAV files via FFmpeg concat demuxer (stream copy)."""
    if not wav_files:
        return False
    if len(wav_files) == 1:
        shutil.copy(wav_files[0], out_wav)
        return True
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    lst = out_wav.parent / f"_xtts_lst_{out_wav.stem}.txt"
    try:
        lst.write_text(
            "\n".join(f"file '{str(p).replace(chr(92), '/')}'" for p in wav_files),
            encoding="utf-8",
        )
        subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0",
             "-i", str(lst), "-c", "copy", str(out_wav)],
            capture_output=True, timeout=120,
        )
        return out_wav.exists() and out_wav.stat().st_size > 500
    finally:
        lst.unlink(missing_ok=True)


def _apply_atempo(src: Path, dst: Path, speed: float) -> bool:
    """Speed up audio using FFmpeg atempo filter (range 0.5–2.0)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or speed == 1.0:
        return False
    # Chain two atempo filters if speed > 2.0 (not expected here but safe)
    if speed <= 2.0:
        af = f"atempo={speed:.4f}"
    else:
        af = f"atempo=2.0,atempo={speed/2.0:.4f}"
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(src),
             "-af", af, "-c:a", "pcm_s16le", str(dst)],
            capture_output=True, timeout=60,
        )
        return dst.exists() and dst.stat().st_size > 500
    except Exception as exc:
        log.debug("atempo failed: %s", exc)
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def generate(text: str, out_path: Path, emotion: str = "neutral") -> bool:
    """Generate speech cloning the reference voice.

    Flow:
      1. Split text into ≤230-char chunks at sentence boundaries
      2. Generate each chunk as a WAV using XTTS v2
      3. Concatenate chunks → raw WAV
      4. Apply atempo speed factor based on emotion (mirrors edge-tts rates)
      5. Convert to 44100 Hz stereo MP3 (pipeline-expected format)

    Returns True on success, False on any failure (caller falls back to edge-tts).
    """
    ref_wav = extract_reference_wav()
    if ref_wav is None:
        return False

    tts = _get_tts()
    if tts is None:
        return False

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("XTTS: ffmpeg not found — cannot produce MP3")
        return False

    chunks = _split_text(text)
    if not chunks:
        log.warning("XTTS: text split produced no chunks: %s", text[:60])
        return False

    work   = out_path.parent
    stem   = out_path.stem
    temps: list[Path] = []

    try:
        # ── 1-2. Generate each chunk ──────────────────────────────────────────
        chunk_wavs: list[Path] = []
        for idx, chunk in enumerate(chunks):
            cpath = work / f"_xtts_{stem}_c{idx}.wav"
            temps.append(cpath)
            try:
                log.debug("XTTS chunk %d/%d: %d chars", idx + 1, len(chunks), len(chunk))
                tts.tts_to_file(
                    text=chunk,
                    speaker_wav=str(ref_wav),
                    language=_XTTS_LANG,
                    file_path=str(cpath),
                )
                if cpath.exists() and cpath.stat().st_size > 500:
                    chunk_wavs.append(cpath)
                else:
                    log.warning("XTTS: chunk %d returned empty audio", idx + 1)
            except Exception as exc:
                log.warning("XTTS: chunk %d failed: %s", idx + 1, exc)

        if not chunk_wavs:
            log.warning("XTTS: all chunks failed for: %s", text[:60])
            return False

        # ── 3. Concatenate ────────────────────────────────────────────────────
        raw_wav = work / f"_xtts_{stem}_raw.wav"
        temps.append(raw_wav)
        if not _concat_wavs(chunk_wavs, raw_wav):
            log.warning("XTTS: WAV concat failed")
            return False

        # ── 4. Speed adjustment ───────────────────────────────────────────────
        speed    = _XTTS_SPEED.get(emotion, _XTTS_SPEED["neutral"])
        fast_wav = work / f"_xtts_{stem}_fast.wav"
        temps.append(fast_wav)
        use_wav  = fast_wav if _apply_atempo(raw_wav, fast_wav, speed) else raw_wav

        # ── 5. Convert to 44100 Hz stereo MP3 ────────────────────────────────
        subprocess.run(
            [ffmpeg, "-y", "-i", str(use_wav),
             "-ac", "2",            # stereo — matches pipeline silence/audio files
             "-ar", "44100",        # standard pipeline sample rate
             "-c:a", "libmp3lame",
             "-q:a", "2",           # VBR ~190-250 kbps
             str(out_path)],
            capture_output=True, timeout=60,
        )
        if out_path.exists() and out_path.stat().st_size > 500:
            log.info("XTTS: ✓ %s  %.0f KB  speed=%.2f×  %d chunk(s)",
                     out_path.name, out_path.stat().st_size / 1024,
                     speed, len(chunk_wavs))
            return True

        log.warning("XTTS: final MP3 missing or empty")

    except Exception as exc:
        log.warning("XTTS: synthesis error: %s", exc)

    finally:
        for p in temps:
            p.unlink(missing_ok=True)

    return False
