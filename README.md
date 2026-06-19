# Obscura — Automated Urdu Content Pipeline

Fully automated pipeline that produces and publishes Roman Urdu video content across **5 platforms daily** using only GitHub Actions — no server, no manual work.

Obscura covers 6 categories in conversational Pakistani/Indian Roman Urdu: **Mystery · Psychology · Science · Technology · Islamic Science · History**

---

## What it does (end to end)

| Step | Module | What it does |
|------|--------|--------------|
| 1 | `topic_selector` | Trend-aware topic selection: Google Trends rising queries + YouTube Autocomplete keyword mining + Groq viral angle hints; **velocity cluster queue** promotes follow-up seeds from viral videos; 30-day deduplication; YouTube saturation filter rejects titles where top-10 results have median views > 500k |
| 2 | `script_generator` | 5-segment Roman Urdu script in one of **4 rotating narrative structures** + one of **6 rotating hook formulas**; word count scales with `VIDEO_FORMAT` (130–180 / 680–840 / 900–1344 words); marks `[WOW]` moments; generates CTR-optimised Urdu title via Gemini 2.0 Flash |
| 3 | `timeline_builder` | Per-scene timeline with **global emotional arc** per narrative template; Shorts psychology (hook cap, tight CORE intervals); per-category audience persona dwell times; reads `adaptive_params.json` from previous retention signals |
| 4 | `voice_generator` | Per-scene MP3s — ElevenLabs (Standard/Long only) → edge-tts `en-US-ChristopherNeural` → gTTS → silence; 300 ms inter-scene silence (600 ms at CORE→PAYOFF); **locks timeline durations** |
| 5 | `scene_planner` | Semantic text analysis — 12 narrative trigger patterns map script text to emotional visual keywords per category |
| 5b | `cinematic_planner` | Director-level shot sequencing (WIDE / AERIAL / MEDIUM / CLOSE / EXTREME_CLOSE); pacing rhythm per scene; suspense arc peaks at WOW |
| 6 | `visual_fetcher` | Groq-optimised search query per scene; Pexels (primary) → Pixabay (fallback); content-level MD5 deduplication; portrait orientation enforced for Shorts |
| 7 | `video_assembler` | Renders each scene with watermark overlay (top-left); **16 emotion+shot-driven motion presets**; multi-image Ken-Burns slideshow for long scenes; branded close scene with CTA |
| 8 | `subtitle_generator` | Per-scene SRT + karaoke-style ASS files; Shorts captions positioned clear of YouTube UI |
| 9 | `audio_processor` | 3-stage pipeline: decode PCM → two-pass loudnorm −14 LUFS + afftdn → fades + alimiter → M4A; optional SFX mix; optional background music at −20 dB |
| 10 | `encoder` | Re-encodes with ASS subtitle burn-in (libx264 CRF 18); hard `-t locked_duration` cap; post-encode duration validation |
| 11 | `quality_gate` | **1 hard block + 10 scored checks** — quality score ≥ 75/100 required; up to 3 retry attempts |
| 12 | `thumbnail_generator` + `ctr_optimizer` | Pillow-based 1280×720 thumbnail (no channel name — content only); CTR optimizer scores 9 title/headline combinations |
| 13 | `uploader` | YouTube resumable upload (5 retries + token refresh); thumbnail; SRT captions; engagement comment; playlist management |
| 13b | `video_formatter` | Converts landscape output to 9:16 portrait for TikTok/Instagram/Telegram |
| 13c | `telegram_uploader` | Posts portrait video + Roman Urdu caption to Telegram channel |
| 13d | `tiktok_uploader` | Posts portrait video to TikTok via API |
| 13e | `makecom_uploader` | Fires 2 Make.com webhooks — Facebook Page + Instagram Business |
| 14 | `news_analytics` | Logs result + quality score; fetches retention curve; velocity clustering; applies **adaptive learning** — evolves pipeline parameters automatically |

---

## Video formats

| Format | Duration | Words | Resolution | Schedule |
|--------|----------|-------|------------|----------|
| `shorts` | ~60 s | 130–180 | 1080×1920 | 05:00, 14:00, 20:00 UTC daily |
| `standard` | 4–5 min | 680–840 | 1920×1080 | 00:00 UTC daily (Technology bonus) |
| `long` | 6–8 min | 900–1344 | 1920×1080 | 03:00 UTC every Friday (Islamic Science) |

**Manual dispatch** — Actions → Obscura Urdu Channel Pipeline → Run workflow:
- `intent_override` — force a category (MYSTERY / PSYCHOLOGY / SCIENCE / TECHNOLOGY / ISLAMIC_SCIENCE / HISTORY)
- `video_format` — `shorts` | `standard` | `long`

---

## Topic categories

| Category | Roman Urdu Focus | Schedule priority |
|----------|-----------------|-------------------|
| MYSTERY | Raaz, paranormal, unsolved, Bermuda Triangle, Nazca Lines | Daily rotation |
| PSYCHOLOGY | Insani zehan, brain, behavior, subconscious | Daily rotation |
| SCIENCE | Science facts, space, quantum, DNA, black holes | Daily rotation |
| TECHNOLOGY | AI, robots, future tech, cyber, digital | Daily rotation |
| ISLAMIC_SCIENCE | Quran + science, Islamic golden age, Ibn Sina, Al-Biruni | **Friday priority — always `long` format** |
| HISTORY | Mughals, ancient civilizations, Pakistan/India history | Daily rotation |

---

## Viral intelligence system

### Narrative templates — 4 rotating structures

| Template | Structure |
|----------|-----------|
| `classic` | Hook teases → tension builds mystery → core delivers facts → payoff resolves → close CTA |
| `mystery` | Open with unsolved mystery → withhold answer until the final reveal |
| `shock_first` | Lead with the most impossible-sounding fact → spend the rest of the video proving it |
| `reverse` | Start at the incredible outcome → work backward to reveal the hidden cause |

### Hook formula engine — 6 rotating formulas

| Formula | Mechanism |
|---------|-----------|
| Impossibility | State a fact that sounds physically impossible. Let it hang. |
| Specific number | Use an exact, surprising number. Specificity = credibility. |
| Contradiction | Attack a widely-held belief. Instant curiosity gap. |
| Scale break | Make the comparison incomprehensible. Breaks mental models. |
| Tension gap | State something happened without explaining why. Open loop. |
| Forbidden knowledge | Frame the fact as something suppressed or never taught. |

4 templates × 6 hook formulas = **24 unique structural combinations** per rotation.

### Global emotional arc

| Template | HOOK | TENSION | CORE | PAYOFF | CLOSE |
|----------|------|---------|------|--------|-------|
| classic | excited | mysterious | neutral | dramatic | excited |
| mystery | mysterious | dramatic | mysterious | excited | neutral |
| shock_first | dramatic | excited | neutral | dramatic | excited |
| reverse | dramatic | mysterious | neutral | excited | mysterious |

---

## Voice quality

Engine priority per scene:

| Priority | Engine | Used when |
|----------|--------|-----------|
| 1 | **ElevenLabs** (`eleven_turbo_v2_5`) | Standard / Long videos (not Shorts) |
| 2 | **edge-tts** (`en-US-ChristopherNeural`) | Shorts, or ElevenLabs unavailable |
| 3 | **gTTS** | edge-tts fails |
| 4 | Silence fallback | All engines fail (hard-blocks upload via quality gate) |

ElevenLabs emotion settings:

| Emotion | Stability | Style | edge-tts rate |
|---------|-----------|-------|--------------|
| excited | 0.35 | 0.70 | +5% |
| mysterious | 0.85 | 0.10 | −10% |
| dramatic | 0.55 | 0.45 | −5% |
| neutral | 0.75 | 0.00 | −5% |

---

## Quality gate — 11 checks

1 hard block + 10 scored checks. Must reach **75/100** before upload. Failures trigger up to **3 retry attempts**.

| Check | Weight | Threshold |
|-------|--------|----------|
| `audio_failure` *(hard block)* | — | Silence fallback scenes block upload entirely |
| `file_integrity` | 15 pts | Container valid, moov at start, size > 100 KB |
| `resolution` | 10 pts | Exact match to profile spec + 30 fps |
| `duration` | 15 pts | Within ± 2.5 s of locked timeline total |
| `audio_sync` | 15 pts | A/V stream length diff within ± 0.3 s |
| `audio_level` | 10 pts | Integrated loudness −14 LUFS ± 2 |
| `subtitles` | 5 pts | No entry < 300 ms |
| `freeze_frame` | 10 pts | No freeze > 2000 ms |
| `voice_quality` | 5 pts | gTTS/silence scores 3/5 — warning only |
| `dropped_frames` | 5 pts | No more than 3 frames with irregular timing |
| `audio_gaps` | 10 pts | No silence gap > 2.0 s |

---

## Adaptive learning

After each upload, retention signals automatically update `logs/adaptive_params.json`. `timeline_builder` reads these on the next run.

| Signal | Automatic adjustment |
|--------|---------------------|
| `early_drop` (leave before 25%) | Hook cap reduced 150 ms; tension interval tightened 0.3 s |
| `mid_drop` (leave in CORE) | CORE scene interval reduced 0.25 s |
| `late_drop` (leave after PAYOFF) | PAYOFF/CLOSE flagged for script review |
| Retention > 70% | Parameters locked — system is working |

---

## Self-learning comment system

`comment_analyzer` uses Groq to classify every new YouTube comment into production faults (speech speed, audio level, image quality, font size) and content faults (facts too basic/complex, boring middle, want more drama). Fault counts above threshold trigger automatic pipeline parameter adjustments written to `logs/auto_fixes.json`. `comment_responder` auto-replies in Roman Urdu using Groq within the hour.

---

## Circuit breaker

After **3 consecutive pipeline failures**, the circuit opens and subsequent runs skip with `SystemExit`. Resets automatically after 24 hours or by deleting `logs/circuit_state.json`.

---

## Schedule

| UTC | PKT | Job | Format | Category |
|-----|-----|-----|--------|----------|
| 05:00 daily | 10:00 AM | `news-video` | shorts | Rotating |
| 14:00 daily | 07:00 PM | `news-video` | shorts | Rotating |
| 20:00 daily | 01:00 AM | `news-video` | shorts | Rotating |
| 00:00 daily | 05:00 AM | `bonus-video` | standard | TECHNOLOGY |
| 03:00 every Friday | 08:00 AM | `friday-islamic-bonus` | long | ISLAMIC_SCIENCE |
| Every hour | — | `news-monitor` | — | News trigger check (Groq) |
| Every hour | — | `comment-responder` | — | Auto-reply + fault analysis (Groq) |

---

## Repository secrets

| Secret | Purpose |
|--------|---------|
| `GROQ_API_KEY_1` / `_2` / `_3` / `_4` | Topic selection, news facts angle, comment classification (Groq llama-3.3-70b) |
| `GEMINI_API_KEY` | Script writing (Gemini 2.0 Flash, 8192 output tokens) |
| `ELEVENLABS_API_KEY` / `_2` / `_3` / `_4` | Premium TTS voice (optional — falls back to edge-tts) |
| `PEXELS_API_KEY` | Primary stock video source |
| `PIXABAY_API_KEY` | Fallback stock video source |
| `YOUTUBE_API_KEY` | YouTube Data API — saturation filter |
| `YOUTUBE_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | YouTube upload OAuth |
| `YOUTUBE_CLIENT_ID_READ` / `_SECRET_READ` / `_REFRESH_TOKEN_READ` | YouTube Analytics read-only OAuth |
| `TELEGRAM_BOT_TOKEN` | Telegram bot for channel posting |
| `TELEGRAM_CHANNEL_ID` | Target Telegram channel |
| `TIKTOK_ACCESS_TOKEN` | TikTok Content Posting API |
| `MAKECOM_FACEBOOK_WEBHOOK` | Make.com webhook → Facebook Page post |
| `MAKECOM_INSTAGRAM_WEBHOOK` | Make.com webhook → Instagram Business post |
| `GMAIL_SENDER` / `GMAIL_APP_PASSWORD` | Failure alert emails |

---

## Brand assets

| Path | Purpose |
|------|---------|
| `pipeline/assets/watermark.png` | 300×300 RGBA transparent eye icon — top-left overlay on all videos |
| `pipeline/assets/obscura_logo.png` | 1000×1000 channel logo — social media profiles |
| `pipeline/assets/obscura_banner.png` | 2560×1440 YouTube channel banner |
| `pipeline/assets/music/*.mp3` | Background music by emotion — drop royalty-free files here |
| `pipeline/assets/sfx/*.mp3` | Sound effects — placed at timed scene events |

---

## Local setup

```bash
# System deps (Ubuntu / Debian)
sudo apt-get install ffmpeg fonts-dejavu fonts-liberation libass-dev

# Python deps
pip install requests gtts edge-tts rapidfuzz Pillow pytrends

# Shorts (~60 s) — default
python pipeline/main.py

# Standard (4–5 min)
VIDEO_FORMAT=standard python pipeline/main.py

# Long (6–8 min) — Islamic Science
VIDEO_FORMAT=long INTENT_OVERRIDE=ISLAMIC_SCIENCE python pipeline/main.py

# Force a category
VIDEO_FORMAT=shorts INTENT_OVERRIDE=MYSTERY python pipeline/main.py
```

Outputs land in `pipeline/output/`. Logs go to `pipeline/logs/`.

---

## Project structure

```
pipeline/
  main.py                        # Orchestrator — 14 steps, 3-attempt quality retry
  assets/
    watermark.png                # Transparent eye icon — video overlay (top-left)
    obscura_logo.png             # Channel logo 1000×1000
    obscura_banner.png           # YouTube banner 2560×1440
    music/                       # Background music — named by emotion
    sfx/                         # Sound effects — hook, wow, payoff
  scripts/
    topic_selector.py            # Step 1   — topic selection + velocity queue + 30-day dedup (Groq)
    script_generator.py          # Step 2   — 4 templates × 6 hooks + Roman Urdu CTR titles (Gemini)
    timeline_builder.py          # Step 3   — emotional arc + Shorts psychology + adaptive params
    voice_generator.py           # Step 4   — ElevenLabs → edge-tts → gTTS → silence
    scene_planner.py             # Step 5   — semantic text → emotional visual keywords
    cinematic_planner.py         # Step 5b  — shot sequencing + pacing + suspense arc
    visual_fetcher.py            # Step 6   — Groq query + Pexels/Pixabay + MD5 dedup
    video_assembler.py           # Step 7   — 16 motion presets + watermark overlay + branded close
    subtitle_generator.py        # Step 8   — SRT + karaoke ASS with Shorts positioning
    audio_processor.py           # Step 9   — loudnorm + SFX + background music
    encoder.py                   # Step 10  — ASS burn-in + hard duration cap
    quality_gate.py              # Step 11  — 1 hard block + 10 scored checks, 3-attempt retry
    thumbnail_generator.py       # Step 12  — Pillow 1280×720 thumbnail (no channel name)
    ctr_optimizer.py             # Step 12b — title + headline CTR scoring and synergy
    uploader.py                  # Step 13  — YouTube upload + captions + comment + playlists
    video_formatter.py           # Step 13b — landscape → 9:16 portrait conversion
    telegram_uploader.py         # Step 13c — Telegram channel post
    tiktok_uploader.py           # Step 13d — TikTok Content Posting API
    makecom_uploader.py          # Step 13e — Make.com webhooks (Facebook + Instagram)
    news_analytics.py            # Step 14  — retention + velocity clustering + adaptive learning
    news_monitor.py              # Hourly    — Google News RSS → bonus video trigger (Groq)
    comment_analyzer.py          # Hourly    — fault classification from comments (Groq)
    comment_responder.py         # Hourly    — Roman Urdu auto-replies via Groq
  temp/                          # Runtime scratch (voice, visuals, scenes, subtitles)
  output/                        # Final MP4s + thumbnails
  logs/
    topic_bank.json              # Seed topics per category (6 categories)
    video_results.json           # Per-video upload log with quality score (last 200)
    quality_failures.json        # Gate failures with attempt detail
    analytics_data.json          # YouTube Analytics + retention curve data
    adaptive_params.json         # Auto-evolved pipeline parameters
    auto_fixes.json              # Comment-driven parameter adjustments
    velocity_queue.json          # High-priority follow-up seeds (72h TTL)
    circuit_state.json           # Consecutive failure counter (circuit breaker)
    playlist_ids.json            # Cached YouTube playlist IDs
.github/workflows/
  news_video.yml                 # All jobs — scheduled + manual dispatch
```

---

## Stack

Python 3.11 · Groq llama-3.3-70b (topic selection, news, comments) · Gemini 2.0 Flash (script writing) · ElevenLabs → edge-tts → gTTS (voice) · FFmpeg · Pexels API · Pixabay API · YouTube Data API v3 · TikTok Content Posting API · Telegram Bot API · Make.com webhooks · GitHub Actions
