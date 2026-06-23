"""
STEP 4 — Voice Generation (Obscura — Roman Urdu)

Generates one audio file per scene (voice_{scene_id}.mp3).
Measures ACTUAL duration with ffprobe, then updates the master timeline.

Engine priority:
  1. XTTS v2 voice clone  — uses pipeline/assets/reference_voice.mp4 as the speaker
                             voice (free, open-source Coqui TTS, Hindi language)
  2. edge-tts Madhur      — hi-IN-MadhurNeural (Indian male, high quality)
  3. edge-tts Salman      — ur-IN-SalmanNeural (Indian Urdu male)
  4. gTTS Hindi           — Google TTS fallback
  5. silence              — last resort

SentenceBoundary events from edge-tts are collected during streaming:
  _speech_offset_ms  — silence before speech starts in the audio (typically 50-200ms)
  _speech_dur_ms     — actual spoken duration reported by the TTS engine
These values let the subtitle generator lock subtitles to the REAL speech window.

300 ms of silence is appended ONCE (only when the file is freshly generated).
Cached voice files are reused as-is so silence does not accumulate on reruns.
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# High-quality Indian male voices (Microsoft Azure / edge-tts)
_EDGE_VOICE        = "hi-IN-MadhurNeural"   # Indian Hindi male — warm, authoritative
_EDGE_VOICE_BACKUP = "ur-IN-SalmanNeural"   # Indian Urdu male — perfect for Roman Urdu

# Rate tuned for Hindi/Roman-Urdu narration rhythm
_EDGE_RATE: dict[str, str] = {
    "excited":    "+25%",   # High energy hook — fast, punchy
    "mysterious": "+12%",   # Deliberate but not dragging
    "dramatic":   "+20%",   # Payoff reveal — confident
    "neutral":    "+20%",   # Core facts — clear and steady
}

# Pitch for natural Indian male expressiveness
_EDGE_PITCH: dict[str, str] = {
    "excited":    "+10Hz",
    "mysterious": "-2Hz",
    "dramatic":   "+6Hz",
    "neutral":    "+3Hz",
}


def _auto_tts_rate_adjust() -> int:
    try:
        p = Path(__file__).parent.parent / "logs" / "auto_fixes.json"
        if p.exists():
            return int(json.loads(p.read_text()).get("tts_rate_adjust", 0))
    except Exception:
        pass
    return 0


_SCENE_PAD_MS            = 300
_SECTION_BOUNDARY_PAD_MS = 600   # CORE → PAYOFF transition


def generate_voices(timeline: dict, voice_dir: Path) -> dict:
    voice_dir.mkdir(parents=True, exist_ok=True)
    scenes = timeline["scenes"]

    for i, sc in enumerate(scenes):
        path    = voice_dir / f"voice_{sc['scene_id']}.mp3"
        emotion = sc.get("emotion", "neutral")

        is_core_payoff = (
            sc["segment_label"] == "CORE" and
            i + 1 < len(scenes) and
            scenes[i + 1]["segment_label"] == "PAYOFF"
        )
        is_last_scene = (i == len(scenes) - 1)
        if is_last_scene:
            pad_s = 1.05
        elif is_core_payoff:
            pad_s = _SECTION_BOUNDARY_PAD_MS / 1000
        else:
            pad_s = _SCENE_PAD_MS / 1000

        sc["_voice_pad_ms"] = int(pad_s * 1000)

        is_fresh = not (path.exists() and path.stat().st_size > 500)
        if is_fresh:
            engine, meta = _generate(
                sc["script_text"], path, emotion,
                fallback_duration_s=sc["duration_ms"] / 1000,
            )
            sc["tts_engine"] = engine
            # Store TTS speech window for subtitle sync
            if meta.get("speech_offset_ms") is not None:
                sc["_speech_offset_ms"] = meta["speech_offset_ms"]
            if meta.get("speech_dur_ms") is not None:
                sc["_speech_dur_ms"] = meta["speech_dur_ms"]
            if engine == "silence":
                sc["audio_failure"] = True
            _append_silence(path, pad_s)
        else:
            sc.setdefault("tts_engine", "cached")

        actual_ms = _duration_ms(path)
        if actual_ms > 0:
            drift = abs(actual_ms - sc["duration_ms"])
            if drift > 500:
                log.info("  Scene %d: drift %dms — adjusting scene to %dms",
                         sc["scene_id"], drift, actual_ms)
                sc["duration_ms"] = actual_ms
                sc["end_ms"]      = sc["start_ms"] + actual_ms

    # Re-anchor all scene timestamps sequentially
    t = 0
    for sc in scenes:
        sc["start_ms"]       = t
        sc["end_ms"]         = t + sc["duration_ms"]
        sc["voice_start_ms"] = t
        sc["voice_end_ms"]   = t + sc["duration_ms"]
        t += sc["duration_ms"]

    timeline["total_duration_ms"]      = t
    timeline["total_duration_seconds"] = round(t / 1000, 2)

    _vf = os.getenv("VIDEO_FORMAT", "").lower()
    if _vf == "shorts":
        timeline["profile"], timeline["width"], timeline["height"] = "shorts",   1080, 1920
    elif _vf in ("standard", "long"):
        timeline["profile"], timeline["width"], timeline["height"] = "standard", 1920, 1080
    elif timeline["total_duration_seconds"] <= 70:
        timeline["profile"], timeline["width"], timeline["height"] = "shorts",   1080, 1920
    else:
        timeline["profile"], timeline["width"], timeline["height"] = "standard", 1920, 1080

    for sc in scenes:
        pad_ms = sc.get("_voice_pad_ms", _SCENE_PAD_MS)
        voice_end_ms = max(sc["start_ms"] + 500, sc["end_ms"] - pad_ms)
        sc["subtitle_lines"] = _rescale_subs(
            sc["subtitle_lines"], sc["start_ms"], voice_end_ms
        )

    engines_used = [sc.get("tts_engine", "") for sc in scenes]
    degraded = [e for e in engines_used if e in ("gtts", "silence")]
    if degraded:
        log.warning("  TTS quality degraded for %d scene(s) — engines: %s",
                    len(degraded), set(degraded))

    return timeline


# ── TTS engines ───────────────────────────────────────────────────────────────

def _generate(
    text: str,
    out: Path,
    emotion: str,
    fallback_duration_s: float = 3.0,
) -> tuple[str, dict]:
    """Returns (engine_name, meta_dict).
    meta_dict keys:
      speech_offset_ms — initial silence before speech starts in the audio
      speech_dur_ms    — actual spoken duration from TTS engine
    """
    # ── Engine 1: XTTS v2 voice clone (reference_voice.mp4) ──────────────────
    try:
        from xtts_voice import xtts_available, generate as xtts_generate
        if xtts_available():
            if xtts_generate(text, out, emotion):
                log.info("  TTS: XTTS voice clone ✓ (emotion=%s)", emotion)
                return "xtts", {}
    except Exception as exc:
        log.debug("XTTS unavailable: %s", exc)

    # ── Engine 2: edge-tts primary Indian voice ────────────────────────────
    ok, meta = _edge_tts(text, out, emotion, voice=_EDGE_VOICE)
    if ok:
        return "edge-tts", meta

    # ── Engine 3: edge-tts backup Indian Urdu voice ────────────────────────
    ok, meta = _edge_tts(text, out, emotion, voice=_EDGE_VOICE_BACKUP)
    if ok:
        log.info("  TTS: fell back to backup Indian Urdu voice (Salman)")
        return "edge-tts-backup", meta

    # ── Engine 4: gTTS ─────────────────────────────────────────────────────
    if _gtts(text, out):
        return "gtts", {}

    # ── Engine 5: silence ──────────────────────────────────────────────────
    _silence_file(out, max(1.0, fallback_duration_s))
    log.error("All TTS engines failed — silence for: %s", text[:50])
    return "silence", {}


def _edge_tts(
    text: str,
    out: Path,
    emotion: str,
    voice: str = _EDGE_VOICE,
) -> tuple[bool, dict]:
    """Stream edge-tts, collect audio + SentenceBoundary events.

    Returns (success, meta) where meta contains:
      speech_offset_ms — silence before speech (from SentenceBoundary.offset)
      speech_dur_ms    — actual speech duration (from SentenceBoundary.duration)
    Both values are in milliseconds, relative to the start of the audio clip.
    """
    base = int(_EDGE_RATE.get(emotion, "+0%").replace("%", ""))
    adjusted = max(-30, min(30, base + _auto_tts_rate_adjust()))
    rate  = f"{adjusted:+d}%"
    pitch = _EDGE_PITCH.get(emotion, "+0Hz")

    for attempt in range(2):
        try:
            async def _run() -> dict:
                import edge_tts
                comm   = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
                chunks = []
                meta   = {}
                async for event in comm.stream():
                    t = event.get("type", "")
                    if t == "audio":
                        chunks.append(event["data"])
                    elif t == "SentenceBoundary":
                        # offset/duration in 100-nanosecond ticks → ms
                        meta["speech_offset_ms"] = event["offset"]   // 10_000
                        meta["speech_dur_ms"]    = event["duration"] // 10_000
                out.write_bytes(b"".join(chunks))
                return meta

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        meta = ex.submit(asyncio.run, _run()).result(timeout=30)
                else:
                    meta = loop.run_until_complete(_run())
            except RuntimeError:
                meta = asyncio.run(_run())

            if out.exists() and out.stat().st_size > 500:
                log.debug("edge-tts [%s]: offset=%sms spoken=%sms",
                          voice,
                          meta.get("speech_offset_ms", "?"),
                          meta.get("speech_dur_ms", "?"))
                return True, meta

        except Exception as exc:
            log.debug("edge-tts [%s] attempt %d: %s", voice, attempt + 1, exc)
            if attempt == 0:
                import time as _t; _t.sleep(1)

    return False, {}


def _gtts(text: str, out: Path) -> bool:
    for lang in ("hi", "ur"):
        try:
            from gtts import gTTS
            gTTS(text=text, lang=lang, slow=False).save(str(out))
            if out.exists() and out.stat().st_size > 500:
                log.debug("gTTS: success with lang=%s", lang)
                return True
        except Exception as exc:
            log.debug("gTTS lang=%s: %s", lang, exc)
    return False


def _silence_file(out: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=stereo",
         "-t", str(duration_s), str(out)],
        capture_output=True,
    )


def _append_silence(path: Path, duration_s: float) -> None:
    """Append exactly duration_s seconds of silence to the voice file."""
    if not path.exists():
        return
    tmp = path.with_suffix(".tmp.mp3")
    try:
        subprocess.run(
            ["ffmpeg", "-y",
             "-i", str(path),
             "-f", "lavfi",
             "-i", f"anullsrc=r=44100:cl=stereo:d={duration_s:.3f}",
             "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[outa]",
             "-map", "[outa]",
             "-c:a", "libmp3lame", "-q:a", "2",
             str(tmp)],
            capture_output=True, timeout=30,
        )
        if tmp.exists() and tmp.stat().st_size > 500:
            tmp.replace(path)
    except Exception as exc:
        log.debug("append_silence: %s", exc)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _duration_ms(path: Path) -> int:
    return int(_duration_s(path) * 1000)


def _duration_s(path: Path) -> float:
    """Return accurate duration for any audio file including VBR MP3."""
    if not path.exists():
        return 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format",
             "-count_packets",
             str(path)],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(r.stdout)
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur and float(dur) > 0:
                return float(dur)
        fmt_dur = data.get("format", {}).get("duration")
        if fmt_dur and float(fmt_dur) > 0:
            return float(fmt_dur)
    except Exception:
        pass

    try:
        r2 = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        streams = json.loads(r2.stdout).get("streams", [])
        for s in streams:
            nb = s.get("nb_frames") or s.get("nb_read_frames")
            sr = s.get("sample_rate")
            if nb and sr:
                estimated = int(nb) / int(sr)
                if estimated > 0:
                    log.debug("Duration fallback via nb_frames: %.3fs", estimated)
                    return estimated
    except Exception:
        pass

    log.warning("Could not determine duration for %s — returning 0.0", path.name)
    return 0.0


def _rescale_subs(lines: list, new_start: int, new_end: int) -> list:
    if not lines:
        return lines
    orig_start = lines[0]["start_ms"]
    orig_dur   = max(lines[-1]["end_ms"] - orig_start, 1)
    new_dur    = new_end - new_start
    scale      = new_dur / orig_dur
    result     = []
    for ln in lines:
        ns = new_start + round((ln["start_ms"] - orig_start) * scale / 50) * 50
        ne = new_start + round((ln["end_ms"]   - orig_start) * scale / 50) * 50
        result.append({"text": ln["text"], "start_ms": ns, "end_ms": max(ne, ns + 500)})
    return result
