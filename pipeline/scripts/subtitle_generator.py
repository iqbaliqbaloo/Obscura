"""
STEP 5 — Subtitle Generation

SRT files use RELATIVE timestamps (scene-start = 0) for per-scene subtitle
tracks burned into individual scene MP4s.

generate_ass_subtitles() produces a single full-video ASS file with karaoke
word-fill animation (\\kf).  Subtitle timing uses two improvements over a
plain character-count approach:

  1. TTS speech window correction — edge-tts SentenceBoundary events tell us
     exactly when speech starts (speech_offset_ms) and how long it lasts
     (speech_dur_ms) within each audio clip.  Subtitles are anchored to this
     real window, not estimated from the padded file duration.

  2. Syllable-proportional word distribution — for Roman Urdu/Hindi, syllable
     count is a better proxy for speaking time than character count.
     "jaata" (2 syl) gets more time than "ka" (1 syl) even though their
     character lengths are close.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _auto_font_adjust() -> int:
    try:
        p = Path(__file__).parent.parent / "logs" / "auto_fixes.json"
        if p.exists():
            return int(json.loads(p.read_text()).get("subtitle_font_adjust", 0))
    except Exception:
        pass
    return 0


def _ms_to_srt(ms: int) -> str:
    ms   = max(0, ms)
    h,  ms = divmod(ms, 3_600_000)
    m,  ms = divmod(ms,    60_000)
    s,  ms = divmod(ms,     1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ms_to_ass(ms: int) -> str:
    ms = max(0, ms)
    h,  ms = divmod(ms, 3_600_000)
    m,  ms = divmod(ms,    60_000)
    s,  ms = divmod(ms,     1_000)
    cs = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _syllables(word: str) -> int:
    """Estimate syllable count for a Roman Urdu / Hindi word.

    Counts vowel groups (consecutive vowels = one syllable).
    Minimum 1 so every word gets at least a token of time.
    """
    count = 0
    in_vowel = False
    for ch in word.lower():
        if ch in "aeiou":
            if not in_vowel:
                count += 1
            in_vowel = True
        else:
            in_vowel = False
    return max(1, count)


def generate_subtitles(timeline: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = timeline.get("profile", "standard")
    H       = timeline.get("height", 1080)

    if profile == "shorts":
        margin_v = int(H * 0.75)
    else:
        margin_v = max(60, int(H * 0.06))

    for sc in timeline["scenes"]:
        path = out_dir / f"sub_{sc['scene_id']}.srt"

        if sc["segment_label"] == "CLOSE" or not sc.get("subtitle_lines"):
            path.write_text("", encoding="utf-8")
            continue

        lines    = sc["subtitle_lines"]
        sc_start = sc["start_ms"]
        sc_dur   = sc["duration_ms"]
        srt: list[str] = []

        written = 0
        skipped = 0
        for ln in lines:
            rel_start = max(0, ln["start_ms"] - sc_start)
            rel_end   = min(ln["end_ms"] - sc_start, sc_dur)

            if rel_end <= rel_start:
                skipped += 1
                continue
            if rel_end - rel_start < 300:
                rel_end = rel_start + 300

            srt += [
                str(written + 1),
                f"{_ms_to_srt(rel_start)} --> {_ms_to_srt(rel_end)}",
                ln["text"],
                "",
            ]
            written += 1

        if skipped:
            log.warning("Scene %d: %d/%d subtitle line(s) fell outside scene window",
                        sc["scene_id"], skipped, len(lines))

        path.write_text("\n".join(srt), encoding="utf-8")
        log.debug("SRT scene %d: %d lines written, %d skipped (marginV=%d)",
                  sc["scene_id"], written, skipped, margin_v)

    log.info("SRT files written for %d scenes (profile=%s, marginV=%d)",
             len(timeline["scenes"]), profile, margin_v)
    timeline["_subtitle_margin_v"] = margin_v


def generate_ass_subtitles(timeline: dict, out_dir: Path) -> Path:
    """Generate a full-video ASS file with karaoke word-fill animation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    profile   = timeline.get("profile", "standard")
    W         = timeline.get("width",  1920)
    H         = timeline.get("height", 1080)
    is_shorts = profile == "shorts"

    font_size  = (84 if is_shorts else 42) + _auto_font_adjust()
    margin_v   = 0  if is_shorts else 80
    align_tag  = r"\an5" if is_shorts else r"\an2"
    fade_tag   = r"\fad(60,60)"  if is_shorts else r"\fad(150,150)"
    chunk_size = 2 if is_shorts else 4
    secondary  = "&H0000FFFF"   # yellow karaoke highlight

    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 1",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{font_size},&H00FFFFFF,{secondary},"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,20,20,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])

    events: list[str] = []

    for sc in timeline["scenes"]:
        if sc["segment_label"] == "CLOSE":
            continue

        pad_ms = sc.get("_voice_pad_ms", 300)

        # ── Determine the real speech window ─────────────────────────────────
        # _speech_offset_ms: initial silence in the TTS clip (from SentenceBoundary.offset)
        # _speech_dur_ms:    actual spoken duration (from SentenceBoundary.duration)
        # Together these give a frame-accurate speech window inside the padded audio.
        tts_offset_ms = sc.get("_speech_offset_ms", 0)
        tts_dur_ms    = sc.get("_speech_dur_ms")

        speech_start = sc["start_ms"] + tts_offset_ms

        if tts_dur_ms and tts_dur_ms > 100:
            speech_end = speech_start + tts_dur_ms
        else:
            # Fallback: subtract the appended silence from scene end
            speech_end = max(sc["start_ms"] + 500, sc["end_ms"] - pad_ms)

        # Sanity clamp — never run past the scene's actual end
        speech_end = min(speech_end, sc["end_ms"] - 50)
        speech_dur = max(200, speech_end - speech_start)

        # ── Collect words ─────────────────────────────────────────────────────
        all_text = sc.get("script_text", "").strip()
        if not all_text:
            continue
        words = all_text.split()
        if not words:
            continue

        # ── Chunk into display groups ─────────────────────────────────────────
        chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]

        # Distribute time proportional to syllable count — more accurate than
        # character count for Hindi/Urdu where word complexity varies widely.
        chunk_syls = [max(1, sum(_syllables(w) for w in c)) for c in chunks]
        total_syls = sum(chunk_syls)
        chunk_ms   = [int(speech_dur * s / total_syls) for s in chunk_syls]
        # Fix integer rounding so total == speech_dur exactly
        chunk_ms[-1] += speech_dur - sum(chunk_ms)

        cursor = speech_start
        for i, chunk in enumerate(chunks):
            c_start = cursor
            c_end   = cursor + chunk_ms[i]
            c_end   = max(c_end, c_start + 200)
            cursor  = c_end if i < len(chunks) - 1 else speech_end

            dur_cs  = max(len(chunk), (c_end - c_start) // 10)
            base_cs = dur_cs // len(chunk)
            extra   = dur_cs - base_cs * len(chunk)

            karaoke = ""
            for j, word in enumerate(chunk):
                display = word.upper() if is_shorts else word
                wcs = base_cs + (1 if j < extra else 0)
                karaoke += f"{{\\kf{wcs}}}{display} "

            events.append(
                f"Dialogue: 0,{_ms_to_ass(c_start)},{_ms_to_ass(c_end)},Default,,0,0,0,,"
                f"{{{align_tag}{fade_tag}}}{karaoke.strip()}"
            )

    ass_path = out_dir / "full_video.ass"
    ass_path.write_text(header + "\n" + "\n".join(events), encoding="utf-8")
    log.info("ASS subtitles: %d dialogue lines → %s", len(events), ass_path.name)
    return ass_path
