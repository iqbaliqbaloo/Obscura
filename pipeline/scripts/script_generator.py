"""
STEP 2 — Script Generation (Obscura Edition)

Single Groq LLM call. Returns 5-segment retention-psychology script
plus YouTube metadata.

NARRATIVE VARIATION: four structural templates are rotated so every video
feels different to the viewer even when binge-watching the channel.
  classic     — HOOK mystery → TENSION build → CORE facts → PAYOFF reveal → CLOSE
  mystery     — open with unsolved mystery, delay answer until PAYOFF
  shock_first — lead with the single most impossible fact, then prove it
  reverse     — start at the incredible conclusion, work backward to cause

WOW MOMENTS: each CORE segment marks its most surprising sentence with [WOW]
so downstream modules can apply visual/audio intensity spikes.

CTR PSYCHOLOGY: title generation follows curiosity-gap rules — implies
information asymmetry without generic clickbait phrases.
"""

import json
import logging
import os
import random
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_KEYS = [
    os.getenv("GROQ_API_KEY_1", "").strip(),
    os.getenv("GROQ_API_KEY_2", "").strip(),
    os.getenv("GROQ_API_KEY_3", "").strip(),
    os.getenv("GROQ_API_KEY_4", "").strip(),
]
_MODEL         = "llama-3.3-70b-versatile"   # single-topic scripts
_MODEL_CLUSTER = "llama-3.1-8b-instant"       # cluster: 30,000 TPM vs 6,000 TPM on free tier

# ── Narrative templates ───────────────────────────────────────────────────────

_CLOSE_RULE = (
    "TWO parts: (1) Echo a specific word or phrase from the HOOK — this creates a loop "
    "that makes viewers rewatch. Hook: 'Your brain is lying' → Close starts: 'Your brain never stops lying.' "
    "Hook: 'Dead. Still moving.' → Close starts: 'It was never really dead.' "
    "(2) Then ONE subscribe CTA sentence. Vary wording: "
    "'Follow Obscura — a new fact drops every day.' "
    "/ 'Subscribe for more facts that shatter what you think you know.' "
    "NEVER say 'Like and subscribe' or 'Hit the bell'."
)

# Standard/long-form CLOSE — includes forward momentum to pull viewers into the next video
_CLOSE_RULE_STANDARD = (
    "THREE parts: "
    "(1) Echo ONE specific word or phrase from the HOOK — creates a mental rewatch loop. "
    "Example: HOOK started 'Black holes stop time' → CLOSE starts 'Time. It was always the answer.' "
    "(2) Natural subscribe CTA — vary the wording every video: "
    "'Follow Obscura — a new fact drops every day.' "
    "/ 'Subscribe to Obscura for facts that change how you see the world.' "
    "/ 'Obscura — subscribe if this changed how you see it.' "
    "NEVER say 'Like and subscribe' or 'Hit the bell'. "
    "(3) Forward momentum sentence — end the video with desire for the next one: "
    "'The next fact makes this one look ordinary.' "
    "/ 'Tomorrow's discovery breaks this completely.' "
    "/ 'What comes next is even harder to believe.' "
    "End on this forward momentum line — NEVER end with 'Goodbye', 'See you next time', or 'Thanks for watching'."
)

_NARRATIVE_VARIANTS: dict[str, dict] = {
    "classic": {
        "description": "Classic curiosity-gap structure: hook teases → tension builds mystery → core delivers facts → payoff resolves → close subscribe CTA",
        "hook_rule":    "ONE sentence, MAX 6 WORDS for Shorts. State something astonishing WITHOUT explaining it. Pure curiosity gap. No filler words.",
        "tension_rule": "2-3 sentences. Raise MORE questions. Make it feel like forbidden knowledge they were never taught.",
        "core_rule":    "4-6 short sentences. Most surprising fact FIRST. One fact per sentence. Vary rhythm: short. Longer context. Short again. Mark your single most shocking sentence with [WOW].",
        "payoff_rule":  "Max 2 sentences. Deliver the satisfying answer that resolves the hook.",
        "close_rule":   _CLOSE_RULE,
    },
    "mystery": {
        "description": "Unsolved mystery structure: open with an ancient or scientific mystery — answer is withheld until the very last moment",
        "hook_rule":    "ONE sentence, MAX 6 WORDS for Shorts. Open with a mysterious question that has no obvious answer. No explanation.",
        "tension_rule": "2-3 sentences. Deepen the mystery. Add conflicting evidence. Make it feel completely unsolvable.",
        "core_rule":    "4-6 sentences. Present evidence step by step — do NOT reveal the answer yet. Escalate the puzzle. Mark the most paradoxical fact with [WOW].",
        "payoff_rule":  "Max 2 sentences. Finally reveal the surprising answer. Make it feel worth the wait.",
        "close_rule":   _CLOSE_RULE,
    },
    "shock_first": {
        "description": "Lead with the most impossible-sounding fact as if it is obvious, then spend the rest of the video proving it",
        "hook_rule":    "ONE sentence, MAX 6 WORDS for Shorts. State the single most impossible-sounding fact as cold hard fact. No hedging.",
        "tension_rule": "2-3 sentences. Immediately challenge the viewer's disbelief. 'This sounds impossible. Here is exactly why it is real.'",
        "core_rule":    "4-6 sentences. Prove the shocking claim with layered evidence. Each sentence escalates the proof. Mark the most undeniable evidence with [WOW].",
        "payoff_rule":  "Max 2 sentences. Show the real-world implication — why this changes how we see everything.",
        "close_rule":   _CLOSE_RULE,
    },
    "reverse": {
        "description": "Reverse storytelling: start at the unbelievable outcome, work backward to reveal the hidden cause",
        "hook_rule":    "ONE sentence, MAX 6 WORDS for Shorts. Describe the unbelievable END RESULT as cold established fact.",
        "tension_rule": "2-3 sentences. Ask how this is even possible. Begin tracing backward through the chain of cause.",
        "core_rule":    "4-6 sentences. Unpack the hidden chain of causes in reverse order. Mark the most surprising cause with [WOW].",
        "payoff_rule":  "Max 2 sentences. Reveal the original tiny hidden cause that triggered the entire chain.",
        "close_rule":   _CLOSE_RULE,
    },
}

# ── Format profiles ──────────────────────────────────────────────────────────
# VIDEO_FORMAT env var controls target length.
# shorts   → 95-115 words  (~40s at fast pace)
# standard → 1650-1950 words  (~8-10 min at fast pace)
# long     → 1200-1500 words (~6-8 min at fast pace)

_FORMAT_PROFILES: dict[str, dict] = {
    "shorts": {
        "word_target":   "95-115 words total",
        "duration_hint": "~40 seconds",
        "core_depth":    "3 short punchy sentences MAX. One fact per sentence. Cut every word that doesn't shock.",
        "max_tokens":    1200,
    },
    "standard": {
        "word_target":   "1650-1950 words total",
        "duration_hint": "8-10 minutes (MINIMUM 6 minutes — never shorter)",
        "core_depth":    (
            "5-6 NAMED CHAPTERS using [CHAPTER: Name] + [BRIDGE] markers (REQUIRED). "
            "Each chapter: surprising fact (1-2 short sentences) → mechanism (2 sentences) "
            "→ real-world scale (1 sentence) → counterintuitive implication (1-2 sentences) → [BRIDGE] teaser. "
            "Pattern interrupt at chapters 2 and 4: direct viewer re-engagement line. "
            "Chapter 1: historical context or origin. Chapters 2-4: escalating modern science. "
            "Chapter 5: the 'so what' that changes how the viewer sees the world. "
            "Vary rhythm: short. medium. LONG cinematic. short. Never three long sentences in a row. "
            "Mark the single most shocking sentence across all chapters with [WOW]."
        ),
        "max_tokens":    6000,
    },
    "long": {
        "word_target":   "1200-1500 words total",
        "duration_hint": "6-8 minutes (MINIMUM 5 minutes — never shorter)",
        "core_depth":    (
            "4-5 NAMED CHAPTERS using [CHAPTER: Name] + [BRIDGE] markers (REQUIRED). "
            "Each chapter: surprising fact → mechanism → real-world comparison → implication → [BRIDGE]. "
            "Pattern interrupt at chapter 2: direct viewer re-engagement line. "
            "Chapters escalate in impact: each one more surprising than the last. "
            "Include historical context in chapter 1, modern research in chapters 2-3, "
            "future implication in the final chapter. "
            "Mark the single most shocking sentence with [WOW]."
        ),
        "max_tokens":    6000,
    },
}

# ── Format-specific timing hints injected into both prompts ──────────────────
# These give the LLM concrete duration targets per segment so it writes
# enough text to actually fill the requested video length.

_FORMAT_TIMING: dict[str, dict] = {
    "shorts": {
        "video_label":   "YouTube Shorts (MUST be under 50 seconds total)",
        "hook_time":     "0-2s",
        "tension_time":  "3-12s",
        "core_time":     "12-34s",
        "payoff_time":   "34-44s",
        "close_time":    "N/A",
        "hook_dur":      3,
        "tension_dur":   9,
        "core_dur":      24,
        "payoff_dur":    12,
        "close_dur":     0,
        "total_est":     48,
    },
    "standard": {
        "video_label":   "YouTube educational video (target 8-10 minutes)",
        "hook_time":     "0-30s",
        "tension_time":  "30-110s",
        "core_time":     "110-530s",
        "payoff_time":   "530-565s",
        "close_time":    "565-592s",
        "hook_dur":      30,
        "tension_dur":   80,
        "core_dur":      420,
        "payoff_dur":    35,
        "close_dur":     27,
        "total_est":     592,
    },
    "long": {
        "video_label":   "YouTube educational video (target 6-8 minutes)",
        "hook_time":     "0-20s",
        "tension_time":  "20-80s",
        "core_time":     "80-390s",
        "payoff_time":   "390-430s",
        "close_time":    "430-450s",
        "hook_dur":      18,
        "tension_dur":   60,
        "core_dur":      300,
        "payoff_dur":    40,
        "close_dur":     25,
        "total_est":     443,
    },
}

# ── Fact-check prompt ────────────────────────────────────────────────────────
_FACTCHECK_PROMPT = (
    "You are a fact-checker for educational YouTube scripts about science, history, "
    "nature, space, animals, geography, ocean, and culture. "
    "Read the script and decide if it contains any clearly false, fabricated, or "
    "wildly exaggerated claims that would embarrass a credible education channel. "
    "Minor dramatic framing and rhetorical emphasis are fine. "
    'Respond ONLY with valid JSON: {"has_issues": true/false, "reason": "one sentence or null"}'
)

# ── Hook formula library ─────────────────────────────────────────────────────
# Rotated per video to prevent hook fatigue. Each formula creates a different
# psychological mechanism that captures attention in the first 1-2 seconds.

_HOOK_FORMULAS = [
    "IMPOSSIBILITY: State a fact about the topic that sounds physically impossible. 'X can Y.' No explanation. Let it hang.",
    "SPECIFIC NUMBER: Use an exact, surprising number about the topic. '[PRECISE NUMBER] [shocking fact about topic].' Specificity = credibility.",
    "CONTRADICTION: Show the topic defies common belief. '[TOPIC] is not what you think.' Instant curiosity gap.",
    "SCALE BREAK: Make the topic's scale incomprehensible. Compare it to something familiar but make the comparison impossible to process.",
    "TENSION GAP: State what the topic does without explaining why. '[TOPIC] exists. Nobody knows why.' Open loop psychology.",
    "IMPOSSIBLE CLAIM: Lead with a topic claim that sounds like a lie. 'Scientists just discovered [TOPIC] breaks physics.' Disbelief = engagement.",
    "TIMER: Create urgency with a specific time related to the topic. 'Every 24 hours, [TOPIC] does something impossible.' Makes it feel urgent.",
]

# Shorts-only hook formulas — topic-specific, tuned for the 2-second scroll window.
_SHORTS_HOOK_FORMULAS = [
    "SINGLE IMPOSSIBLE WORD OPENER: Start with the topic's most shocking attribute as one word. 'Dead. Yet still moving.' First word = the topic or a shocking fact about it.",
    "COUNTDOWN URGENCY: Attach a real time interval to the topic. 'Every 2 seconds [TOPIC] does something impossible.' Forces the viewer to count.",
    "WORLD LIE: Challenge one belief about the topic. '[TOPIC] is not what you think.' State it as fact, zero hedging. Topic must be in first 3 words.",
    "SCALE CRUSH: Make the topic's scale incomprehensible. 'A teaspoon of [TOPIC] weighs a billion tonnes.' Specificity = credibility.",
    "DIRECT STOP: Force the scroll to stop with the topic fact. '[TOPIC]. This is real.' Topic must be in the first 3 words.",
]

# Shorts-specific boost — injected only for Shorts format
_SHORTS_SYSTEM_BOOST = """
SHORTS RETENTION RULES — viewer swipes in 2 seconds if hook is off-topic:

HOOK RULES (most critical — a generic hook kills all views):
1. MAX 6 WORDS. The topic keyword MUST appear in the first 3 words.
   The viewer clicked the title — confirm you are delivering it IMMEDIATELY.
   CORRECT: "Black holes stop time." / "Sharks cannot sleep." / "Ocean glows at night."
   WRONG: "Your brain is lying." / "Wait for this." / "You won't believe this."
   Generic openers cause instant swipes because they don't match what the viewer clicked.
2. PRESENT TENSE. State the fact as happening now — not "scientists discovered" but "this exists."
3. ZERO EXPLANATION in hook. State the fact. Stop. Let curiosity pull them forward.
4. NO filler openers: Did / Have / There / This / In / A / The / Today / Welcome / Here / Wait / Stop.
   Start with the TOPIC NOUN or a shocking number related to it.

TENSION:
5. First sentence of TENSION names the topic again and deepens the mystery — do NOT switch subjects.

PAYOFF:
6. End PAYOFF with ONE engagement line: 'Like if this changed how you see it.' Natural, short.
   PAYOFF is the LAST segment — no CLOSE scene follows for Shorts.

NO CLOSE SEGMENT FOR SHORTS:
7. Shorts has EXACTLY 4 segments: HOOK, TENSION, CORE, PAYOFF.
   Do NOT write a CLOSE segment (id=5). End at PAYOFF. Omit the CLOSE entry from the JSON array.

EVERY SENTENCE:
8. No filler: never 'So', 'Basically', 'In other words', 'To summarize', 'Essentially'.
9. Every sentence = a fact or a stakes-raise. Nothing else.
"""

# Standard/long-form retention rules — injected for non-shorts formats
_STANDARD_SYSTEM_BOOST = """
STANDARD VIDEO RETENTION RULES — 8-10 minute educational YouTube video:

HOOK (0-30s — 70% of drop-off happens in the first 30 seconds):
1. 2-3 sentences ONLY. Sentence 1: the single most impossible-sounding fact — state it cold, no softening.
   Sentence 2: one number or scale comparison that proves it is real.
   Sentence 3 (optional): "And that's just where it starts."
2. End HOOK with forward momentum: "Here's what's actually happening — and nobody taught you this."
3. NEVER start with: "Today we'll learn" / "Welcome back" / "In this video" / "Have you ever wondered".
   Start directly with the FACT.

TENSION (30s-2 min — viewers decide whether to stay for the whole video):
4. Build in exactly TWO BEATS:
   Beat 1 (2-3 sentences): Deepen the mystery. "But it gets stranger." "Scientists spent decades trying to explain this."
   Beat 2 (2 sentences): Reveal an even bigger implication. "And the real explanation? Nobody saw it coming."
5. End TENSION with a bridge that creates urgency: "What you're about to find out changes how this looks forever."

CORE (2-9 min — main content, most critical for retention):
6. DIVIDE INTO 5-6 NAMED CHAPTERS using this exact format:
      [CHAPTER: Chapter Name]
      ... 5-7 sentences of content ...
      [BRIDGE] One teaser sentence pulling into the next chapter.
   Chapter names must be descriptive and intriguing: "The Impossible Number", "What Scientists Missed", "The Hidden Cause", etc.
7. Each chapter follows this 4-beat structure:
   - Surprising fact (1-2 short punchy sentences)
   - Mechanism explanation (2 sentences — HOW it actually works)
   - Real-world scale (1 sentence — make the scale incomprehensible)
   - Counterintuitive implication (1-2 sentences — the "so what" that reframes everything)
8. PATTERN INTERRUPT at chapters 2 and 4 — direct viewer re-engagement:
   "Here's where it gets unbelievable." / "Pay attention — this is the part they never tell you."
   / "Most people miss this entirely." / "Now here's the part that breaks everything you just learned."
9. Mark the single most shocking sentence across ALL chapters with [WOW].
10. Vary sentence rhythm dramatically throughout CORE:
    Short. Medium. LONGER cinematic sentence that builds atmosphere and detail. Short punch again.

PAYOFF:
11. 2-3 sentences. Directly resolve the HOOK's specific question — not a different question.
    Final sentence: "The real implication is this:" then the one sentence that reframes EVERYTHING.

CLOSE:
12. THREE parts as instructed above. End on forward momentum — NEVER on goodbye.

WRITING RULES FOR LONG-FORM:
13. Every paragraph ends with a FACT or a QUESTION — never a transition phrase.
14. ZERO filler connectors: "So," / "Basically," / "In other words," / "To summarize," / "Essentially,"
15. Write as if narrating a high-budget documentary — authoritative, urgent, surprising at every turn.
"""

# Category-specific seed tags. Injected into the tags instruction so the LLM
# generates relevant long-tail phrases instead of generic ones.
_CATEGORY_TAGS: dict[str, list[str]] = {
    "SPACE":       ["space facts", "nasa facts", "universe facts", "black hole facts", "astronomy facts", "space science", "galaxy facts", "planet facts", "cosmos facts", "space discovery 2025"],
    "SCIENCE":     ["science facts", "biology facts", "chemistry facts", "scientific discovery", "science explained", "real science facts", "science secrets", "physics facts"],
    "HISTORY":     ["history facts", "ancient history", "historical facts", "world history", "ancient civilizations", "forgotten history", "history mysteries", "historical secrets"],
    "ANIMALS":     ["animal facts", "wildlife facts", "amazing animals", "animal behavior", "rare animals", "strange animals", "animal science", "nature facts animals"],
    "NATURE":      ["nature facts", "earth facts", "natural phenomena", "environment facts", "plant facts", "nature science", "natural world facts", "ecology facts"],
    "GEOGRAPHY":   ["geography facts", "world geography", "country facts", "earth geography", "map facts", "countries of the world", "places on earth facts"],
    "OCEAN":       ["ocean facts", "deep sea facts", "marine biology facts", "deep ocean creatures", "sea facts", "underwater facts", "ocean science", "pacific ocean facts"],
    "CULTURE":     ["culture facts", "world culture facts", "ancient culture", "cultural history", "traditions facts", "civilization facts", "human culture"],
    "TECHNOLOGY":  ["technology facts", "tech facts", "computer science facts", "ai facts", "invention facts", "engineering facts", "science and technology"],
    "PSYCHOLOGY":  ["psychology facts", "human psychology", "brain facts", "mind facts", "mental science", "behavioral psychology", "cognitive science facts"],
    "MYTHOLOGY":   ["mythology facts", "greek mythology", "ancient myths", "mythology explained", "world mythology", "legend facts", "myths and legends"],
    "MEDICINE":    ["medical facts", "health facts", "human body facts", "anatomy facts", "biology human body", "medicine science", "doctor facts", "disease facts"],
    "MATHEMATICS": ["math facts", "mathematics facts", "number facts", "math history", "geometry facts", "mathematical discoveries", "math science"],
    "ECONOMICS":   ["economics facts", "money facts", "finance facts", "world economy facts", "economic history", "business facts", "wealth facts"],
    "PHYSICS":     ["physics facts", "quantum physics facts", "physics explained", "energy facts", "force facts", "physics science", "laws of physics"],
    "MYSTERY":        ["mystery facts", "unsolved mysteries", "unexplained events", "strange mysteries", "world mysteries", "conspiracy facts", "hidden secrets", "paranormal facts"],
    "ISLAMIC_SCIENCE": ["islamic science facts", "muslim scientists", "islamic golden age", "quran science", "islamic history facts", "muslim inventors", "arabic science", "islamic civilization"],
}


def _build_tags_for_prompt(intent: str) -> str:
    """Build a JSON array hint with category seeds + a placeholder the LLM will fill."""
    seeds = _CATEGORY_TAGS.get(intent.upper(), ["facts", "educational"])
    tags: list[str] = list(seeds[:8])
    tags.append(
        "REPLACE_WITH_5_TOPIC_SPECIFIC_TAGS: use exact long-tail phrases people search for this topic"
    )
    tags += ["Obscura", "educational", "facts", "did you know"]
    return json.dumps(tags)


def _load_viewer_note() -> str:
    try:
        from pathlib import Path as _P
        p = _P(__file__).parent.parent / "logs" / "script_feedback.json"
        if p.exists():
            note = json.loads(p.read_text()).get("viewer_note", "")
            if note:
                return f"\n\nVIEWER FEEDBACK (apply to this script):\n{note}"
    except Exception:
        pass
    return ""


_SYSTEM_TMPL = """You are a world-class educational YouTube scriptwriter for the channel "Obscura".
Your scripts use retention psychology to make viewers feel they can't stop watching.
Content: real-world facts — mystery, psychology, science, technology, Islamic science, history.
ACCURACY RULE: Every fact, number, and claim must be real and verifiable. Never invent statistics or events. If verified facts are provided below, treat them as ground truth.

LANGUAGE RULE — ABSOLUTE. NO EXCEPTIONS. EVERY SEGMENT MUST BE ROMAN URDU.
Roman Urdu = Urdu language written in Latin/English alphabet, as spoken by Pakistani/Indian audiences.

CORRECT Roman Urdu (use this style for EVERY segment):
  HOOK:    "Ye raaz aapki duniya ka nazariya hamesha ke liye badal dega."
  TENSION: "Aksar log ye nahi jaante. Scientists saalon se is par research kar rahe hain."
  CORE:    "Black holes ke paas waqt ruk jaata hai — ye sirf theory nahi, proven science hai. [WOW] Iska matlab ye hai ke ek second wahan hazaron saal ke barabar ho sakta hai."
  PAYOFF:  "Ab aap samajh gaye hain ke ye duniya kitni ajeeb aur hairaan kar dene wali hai."
  CLOSE:   "Aur bhi aisi batein jaanne ke liye Obscura follow karein."

WRONG — NEVER write these:
  "Black holes stop time near their event horizon." ← English, REJECTED
  "Did you know that black holes..." ← English opener, REJECTED
  "بلیک ہولز وقت کو روکتے ہیں" ← Urdu script, REJECTED
  "Scientists have found that..." ← English, REJECTED

Title must also be in Roman Urdu (written in Latin letters, as Pakistani/Indian audience reads).
  CORRECT title: "Black Holes Ke Paas Waqt Ruk Jaata Hai | Space Facts 🌌"
  WRONG title:   "Black Holes Stop Time | Space Science Facts 🌌"  ← English, REJECTED
Description and tags stay in English for YouTube search indexing.
The voice actor speaks ONLY Roman Urdu. Every spoken word = Roman Urdu.

NARRATIVE STRUCTURE THIS VIDEO: {description}

SEGMENT RULES:
HOOK    ({hook_time})  : {hook_rule}
                  NEVER start with "Did you know", "Welcome back", "Today we discuss", "In today's video".
TENSION ({tension_time}) : {tension_rule}
CORE    ({core_time}): {core_rule}
                  DEPTH: {core_depth}
                  Mark the single most surprising sentence with [WOW].
PAYOFF  ({payoff_time}): {payoff_rule}
CLOSE   ({close_time}): {close_rule}
                  NEVER say "Like and subscribe".

TARGET: {word_target}. Duration hint: {duration_hint}. Pace = 3.3 words/second (fast voice — write complete sentences, never cut off mid-thought).

TITLE RULES — YouTube Shorts optimised (follow ALL rules every time):
  Rule 0: MUST match the exact topic. Never change the subject.
  Rule 1: FRONT-LOAD the keyword — first 40 characters must contain the main topic word(s).
          Shorts feed truncates at ~40 chars. The topic must be readable before the "…"
  Rule 2: Under 70 characters total. No excessive ALL CAPS (max 1 word in caps if any).
  Rule 3: End with exactly 1 emoji relevant to the topic. Never 0, never 2+.
  Rule 4: Include a specific number OR a strong qualifier (real, actual, hidden, why, how) when it fits naturally.
  Rule 5: BANNED overused words: shocking / unbelievable / amazing / mind-blowing / incredible / nobody told you / the truth nobody / what they don't.
  Rule 6: VARY THE FORMAT every video — pick ONE format from the pool below. Never repeat the same format twice in a row.

  FORMAT POOL — ALL IN ROMAN URDU (rotate — each video uses a different format):
  A. Sawal:         "[TOPIC] kyon [SURPRISING FACT] hota hai? 🤯"
  B. Asli wajah:    "[TOPIC]: Asli wajah kya hai? 🔬"
  C. Personal:      "[TOPIC] aapke saath ye karta hai 🧠"
  D. Scale:         "[TOPIC] [SCALE] hai — aur ye sab kuch badal deta hai 🌌"
  E. Discovery:     "Scientists ne [TOPIC] mein ye dhoond liya 🔭"
  F. Reversal:      "[TOPIC] asliyat mein aise kaam karta hai ⚡"
  G. Number-led:    "[NUMBER] [TOPIC] facts jo aapko hairaan kar denge 🫀"
  H. Identity:      "Aapka [BODY PART] [IMPOSSIBLE CLAIM] hai 👁️"
  I. Conflict:      "[TOPIC] vs [OPPOSING IDEA] — sirf ek sach ho sakta hai 🚨"
  J. Time urgency:  "Har [TIME UNIT] mein [TOPIC] ye karta hai ⏱️"
  K. Double keyword (| separator — HIGHEST PRIORITY, use often):
     "[Roman Urdu hook] | [Roman Urdu ya English category] 🔬"
     Before |: hook in Roman Urdu (what people CLICK). After |: category keywords (what people SEARCH).
     Example: "Black Holes Roshni Ko Khaa Jaate Hain | Space Facts 🌌"
     Example: "Dimag Rozana 35000 Faisle Karta Hai | Psychology Facts 🧠"
     Example: "Samundar Raat Ko Chamakta Hai | Marine Biology 🌊"

  GOOD: "Samundar Raat Ko Kyon Chamakta Hai? 🌊"                  ← A, sawal
  GOOD: "Black Holes: Roshni Kyon Nahi Bach Sakti? 🌌"             ← B, asli wajah
  GOOD: "Lava Insaani Haddi Ke Saath Ye Karta Hai 🔥"              ← C, personal
  GOOD: "Scientists Ne 11km Gehrai Mein Zindagi Dhoond Li 🐙"      ← E, discovery
  GOOD: "Aapka Dimag Neend Mein Yadein Delete Karta Hai 🧠"         ← F, reversal
  GOOD: "Samundar Chamakta Hai | Marine Biology Facts 🌊"           ← K, double keyword (BEST)
  GOOD: "Dimag Yadein Delete Karta Hai | Psychology Facts 🧠"       ← K, double keyword (BEST)
  BAD:  "Black Holes Stop Time | Space Science Facts 🌌"            ← English title, REJECTED
  BAD:  "The Truth Nobody Told You About Black Holes"               ← English + overused
  BAD:  "Hairaan Karne Wale DNA Facts"                              ← vague, no emoji

Writing style: authoritative, fast-paced, conversational.
Respond ONLY with valid JSON. No text outside the JSON."""

_USER_TMPL = """Write a {video_label} "Obscura" script for this EXACT topic:

TOPIC    : {title}
DETAILS  : {description}
CATEGORY : {intent}
TEMPLATE : {template_name}{wiki_facts}

CRITICAL CONTENT RULES — SWIPE PREVENTION (most important rules in this entire prompt):
1. The HOOK must contain the EXACT topic keyword from "{title}" — within the first 3 words.
   BANNED hooks: anything generic like "Your brain is lying", "Wait for this", "You won't believe",
   "This will shock you", "Nobody knows this" — these are off-topic and cause immediate swipes.
   REQUIRED: Hook names the topic directly. Examples:
     Title "Black Holes Eat Time" → Hook: "Black holes literally stop time."
     Title "Ocean Glows At Night" → Hook: "The ocean glows. Here's why."
     Title "Sharks Never Sleep" → Hook: "Sharks cannot sleep. Ever."
   Viewer clicked for THIS topic — confirm it in word 1-3 or they swipe.
2. TENSION must stay on the same topic — deepen it, do NOT switch subjects.
3. CORE must answer exactly what the title and hook promised — no detours.
4. PAYOFF must resolve the specific question raised by the hook — not a different fact.
5. If VERIFIED FACTS are provided above, build the script around those exact facts.
6. NEVER write a generic script. The viewer clicked "{title}" — every sentence must be about "{title}".

Return EXACTLY this JSON (no extra keys, no markdown fences):
{{
  "narrative_template": "{template_name}",
  "segments": [
    {{"id": 1, "label": "HOOK",    "text": "...", "estimated_duration_seconds": {hook_dur},    "emotion": "excited",    "complexity": "simple"}},
    {{"id": 2, "label": "TENSION", "text": "...", "estimated_duration_seconds": {tension_dur}, "emotion": "mysterious", "complexity": "moderate"}},
    {{"id": 3, "label": "CORE",    "text": "...", "estimated_duration_seconds": {core_dur},    "emotion": "neutral",    "complexity": "complex"}},
    {{"id": 4, "label": "PAYOFF",  "text": "...", "estimated_duration_seconds": {payoff_dur},  "emotion": "dramatic",   "complexity": "simple"}},
    {{"id": 5, "label": "CLOSE",   "text": "...", "estimated_duration_seconds": {close_dur},   "emotion": "neutral",    "complexity": "simple"}}
  ],
  "total_estimated_seconds": {total_est},
  "full_script": "all segments combined into one paragraph",
  "metadata": {{
    "title": "Write a ROMAN URDU YouTube title for '{title}'. Roman Urdu = Urdu language in Latin letters. The entire title must be in Roman Urdu — no English sentences. RULES: (1) STRONGLY PREFER format K — use the | separator: 'Roman Urdu hook | Category 🔬'. Example: 'Black Holes Roshni Ko Khaa Jaate Hain | Space Facts 🌌'. (2) If K doesn't fit, use any format from the pool. (3) First 40 chars must contain the topic in Roman Urdu. (4) Under 70 chars total. (5) End with exactly 1 relevant emoji. (6) Must describe '{title}' — no subject changes. (7) NEVER use: shocking / amazing / hairaan karne wala (overused).",
    "description": "SEO-CRITICAL structure — follow exactly:\nLine 1 (max 140 chars): open with the EXACT 2-3 word phrase people search for this topic, then a compelling sentence. Front-load the keyword — YouTube indexes first words most heavily. Example: 'Black holes are regions...' / 'Octopuses have three hearts...' / 'The real reason Rome collapsed...'\nLine 2: The single most shocking specific fact from the script — include a real number or a scale comparison.\nLine 3: Subscribe to Obscura for daily mind-blowing facts in Roman Urdu.\nLine 4-5: 2 natural sentences weaving in long-tail keywords people actually search (e.g. 'Scientists recently discovered...', 'Most people never learn that...', 'The truth about X is...').\nFinal line: 10-12 hashtags — mix specific topic hashtags with broad ones: #Facts #DidYouKnow #Educational #Science #Obscura",
    "tags": {tags_instruction},
    "engagement_question": "One question about '{title}' that sparks debate or invites personal stories from viewers"
  }}
}}"""


_CLUSTER_USER_TMPL = """Write an 8-10 minute Obscura YouTube script on: {title}
Topics: {topics_list}
Central angle: {central_angle} | Category: {intent}

WORD REQUIREMENTS (non-negotiable):
- HOOK: 30-40 words
- TENSION: 80-100 words
- CORE: minimum 900 words (each of the {n_topics} topics gets 180+ words)
- PAYOFF: 60-80 words
- CLOSE: 40-50 words
- TOTAL: minimum 1200 words

Structure:
HOOK (30s): 1 shocking sentence + "Here's what connects all of this."
TENSION (90s): 3-4 sentences building anticipation about {intent_lower}.
CORE (7min): Each topic = [CHAPTER: Name] then 180+ words covering fact/mechanism/scale/implication + [BRIDGE] teaser. Mark best sentence [WOW].
PAYOFF (30s): "The real implication is this:" + 2 sentences.
CLOSE (30s): Like/subscribe + teaser for next video.

Return ONLY this JSON:
{{
  "narrative_template": "{template_name}",
  "segments": [
    {{"id": 1, "label": "HOOK",    "text": "...", "estimated_duration_seconds": {hook_dur},    "emotion": "excited",    "complexity": "simple"}},
    {{"id": 2, "label": "TENSION", "text": "...", "estimated_duration_seconds": {tension_dur}, "emotion": "mysterious", "complexity": "moderate"}},
    {{"id": 3, "label": "CORE",    "text": "...", "estimated_duration_seconds": {core_dur},    "emotion": "neutral",    "complexity": "complex"}},
    {{"id": 4, "label": "PAYOFF",  "text": "...", "estimated_duration_seconds": {payoff_dur},  "emotion": "dramatic",   "complexity": "simple"}},
    {{"id": 5, "label": "CLOSE",   "text": "...", "estimated_duration_seconds": {close_dur},   "emotion": "neutral",    "complexity": "simple"}}
  ],
  "total_estimated_seconds": {total_est},
  "full_script": "all segments combined",
  "metadata": {{
    "title": "YouTube title under 90 chars with 1 emoji for: {title}",
    "description": "SEO description with hashtags #Facts #DidYouKnow #Educational #Obscura",
    "tags": {tags_instruction},
    "engagement_question": "debate question about {title}"
  }}
}}"""


def _generate_cluster_script(topic: dict, video_format: str) -> dict:
    """Generate a multi-topic cluster script for standard/long videos."""
    fmt_profile  = _FORMAT_PROFILES.get(video_format, _FORMAT_PROFILES["standard"])
    fmt_timing   = _FORMAT_TIMING.get(video_format, _FORMAT_TIMING["standard"])
    template_name = random.choice(list(_NARRATIVE_VARIANTS.keys()))
    hook_formula  = random.choice(_HOOK_FORMULAS)
    variant       = _NARRATIVE_VARIANTS[template_name]

    topics_list = "\n".join(
        f"{i+1}. {t.get('title', t.get('seed', ''))} — {t.get('description', '')}"
        for i, t in enumerate(topic["topics"])
    )

    # Use a minimal system prompt for cluster — the full _SYSTEM_TMPL + boosts
    # exceeded Groq's payload size limit (413). Content quality comes from the
    # structured user template instead.
    system_prompt = (
        "You are an educational YouTube scriptwriter for Obscura. "
        "EVERY spoken segment (HOOK, TENSION, CORE, PAYOFF, CLOSE) MUST be 100% Roman Urdu — "
        "Urdu language written in Latin/English letters as Pakistani audiences speak it. "
        "CORRECT: 'Ye raaz aapko hairaan kar dega. Black holes ke paas waqt ruk jaata hai.' "
        "WRONG: 'This fact will blow your mind. Black holes stop time.' "
        "NOT A SINGLE English sentence is allowed in any spoken segment. "
        "YouTube title, description, tags stay in English for SEO only. "
        "Return only valid JSON, no markdown."
    )

    filled_prompt = _CLUSTER_USER_TMPL.format(
        video_label       = fmt_timing["video_label"],
        title             = topic["title"],
        central_angle     = topic.get("central_angle", topic["description"][:60]),
        intent            = topic["intent"],
        intent_lower      = topic["intent"].lower(),
        template_name     = template_name,
        topics_list       = topics_list,
        n_topics          = len(topic["topics"]),
        hook_formula      = hook_formula,
        core_depth        = fmt_profile["core_depth"],
        close_rule        = _CLOSE_RULE_STANDARD,
        word_target       = fmt_profile["word_target"],
        duration_hint     = fmt_profile["duration_hint"],
        hook_dur          = fmt_timing["hook_dur"],
        tension_dur       = fmt_timing["tension_dur"],
        core_dur          = fmt_timing["core_dur"],
        payoff_dur        = fmt_timing["payoff_dur"],
        close_dur         = fmt_timing["close_dur"],
        total_est         = fmt_timing["total_est"],
        tags_instruction  = _build_tags_for_prompt(topic["intent"]),
    )

    def _try_script(raw):
        script = _parse(raw)
        if not script:
            return None
        words = len(script["full_script"].split())
        if words < 400:
            log.warning("Cluster script too short (%d words) — skipping", words)
            return None
        log.info("Cluster script OK — %d words [%s/%s/%d topics]",
                 words, video_format, template_name, len(topic["topics"]))
        script["video_format"]    = video_format
        script["is_cluster"]      = True
        script["cluster_topics"]  = [t.get("title", t.get("seed", ""))
                                      for t in topic["topics"]]
        return script

    # ── 1. Gemini (free, 8192 output tokens → 8-10 min video) ───────────────
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            full_prompt = system_prompt + "\n\n" + filled_prompt
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                json={
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.75},
                },
                timeout=120,
            )
            if r.ok:
                raw = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                script = _try_script(raw)
                if script:
                    log.info("Cluster script via Gemini")
                    return script
            else:
                log.warning("Gemini cluster HTTP %d — falling back to Groq", r.status_code)
        except Exception as exc:
            log.warning("Gemini cluster error: %s — falling back to Groq", exc)

    # ── 2. Groq fallback (free, ~2000 tokens → 4 min video) ─────────────────
    for key in _GROQ_KEYS:
        if not key:
            continue
        for attempt in range(3):
            try:
                r = requests.post(
                    _GROQ_URL,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type":  "application/json"},
                    json={
                        "model":       _MODEL_CLUSTER,
                        "messages":    [{"role": "system", "content": system_prompt},
                                        {"role": "user",   "content": filled_prompt}],
                        "temperature": 0.75,
                        "max_tokens":  3000,
                    },
                    timeout=90,
                )
                if r.status_code == 429:
                    log.warning("Rate limit on key …%s (cluster)", key[-4:])
                    break
                r.raise_for_status()
                raw    = r.json()["choices"][0]["message"]["content"].strip()
                script = _try_script(raw)
                if script:
                    check = _fact_check(script["full_script"], key)
                    if check.get("has_issues"):
                        log.warning("Fact-check flagged cluster (attempt %d): %s",
                                    attempt + 1, check.get("reason"))
                        if attempt < 2:
                            continue
                        log.warning("Fact-check still flagged — using best available")
                    else:
                        log.info("Cluster fact-check passed")
                    return script
            except Exception as exc:
                log.warning("Groq cluster attempt %d: %s", attempt + 1, exc)

    log.warning("Cluster LLM unavailable — falling back to single-topic script")
    return _fallback(topic)


def _fact_check(text: str, key: str) -> dict:
    try:
        r = requests.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model":    _MODEL,
                "messages": [
                    {"role": "system", "content": _FACTCHECK_PROMPT},
                    {"role": "user",   "content": text[:2000]},
                ],
                "temperature": 0,
                "max_tokens":  120,
            },
            timeout=20,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```\s*$",        "", raw, flags=re.MULTILINE)
        return json.loads(raw)
    except Exception as exc:
        log.debug("Fact-check skipped: %s", exc)
        return {"has_issues": False, "reason": None}


def _load_banned_phrases(logs_dir: Path | None, n: int = 18) -> list[str]:
    """Return hook + key sentences from the last n videos to prevent repetition."""
    if logs_dir is None:
        return []
    try:
        path = Path(logs_dir) / "video_results.json"
        if not path.exists():
            return []
        results = json.loads(path.read_text())
        phrases: list[str] = []
        for r in results[-n:]:
            hook = r.get("hook_text", "").strip()
            if hook:
                phrases.append(hook[:140])
            for sent in r.get("script_key_sents", []):
                if sent and len(sent) > 15:
                    phrases.append(sent[:140])
        return phrases
    except Exception:
        return []


def _sim_score(a: str, b: str) -> float:
    """Rough token-overlap similarity between two strings (0-1)."""
    ta = set(a.lower().split())
    tb = set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _is_script_repeat(script: dict, banned: list[str], threshold: float = 0.60) -> bool:
    """Return True if the new script's hook is too similar to any recent hook."""
    segs = script.get("segments", [])
    hook_seg = next((s for s in segs if s.get("label") == "HOOK"), None)
    if not hook_seg:
        return False
    hook_text = hook_seg.get("text", "")
    return any(_sim_score(hook_text, b) > threshold for b in banned)


def generate_script(topic: dict, logs_dir: Path | None = None) -> dict:
    import os
    video_format = os.getenv("VIDEO_FORMAT", "shorts").lower()
    if video_format not in _FORMAT_PROFILES:
        video_format = "shorts"
    # For standard (non-shorts) runs randomly alternate 4-5 min vs 6-8 min
    if video_format == "standard":
        video_format = random.choice(["standard", "long"])

    # Cluster topics (standard/long only): use the multi-topic prompt
    if topic.get("topics") and video_format != "shorts":
        return _generate_cluster_script(topic, video_format)
    fmt_profile = _FORMAT_PROFILES[video_format]

    # Rotate narrative template + hook formula — double variety prevents formula fatigue
    template_name = random.choice(list(_NARRATIVE_VARIANTS.keys()))
    # Shorts uses a more extreme formula pool — tuned for 2-second scroll psychology
    formula_pool  = _SHORTS_HOOK_FORMULAS if video_format == "shorts" else _HOOK_FORMULAS
    hook_formula  = random.choice(formula_pool)
    variant       = _NARRATIVE_VARIANTS[template_name]

    # Inject hook formula into the hook rule
    augmented_hook_rule = f"{variant['hook_rule']} HOOK FORMULA TO USE: {hook_formula}"

    fmt_timing = _FORMAT_TIMING.get(video_format, _FORMAT_TIMING["shorts"])

    is_longform = video_format != "shorts"
    close_rule  = _CLOSE_RULE_STANDARD if is_longform else variant["close_rule"]

    system_prompt = _SYSTEM_TMPL.format(
        description   = variant["description"],
        hook_rule     = augmented_hook_rule,
        tension_rule  = variant["tension_rule"],
        core_rule     = variant["core_rule"],
        payoff_rule   = variant["payoff_rule"],
        close_rule    = close_rule,
        word_target   = fmt_profile["word_target"],
        duration_hint = fmt_profile["duration_hint"],
        core_depth    = fmt_profile["core_depth"],
        hook_time     = fmt_timing["hook_time"],
        tension_time  = fmt_timing["tension_time"],
        core_time     = fmt_timing["core_time"],
        payoff_time   = fmt_timing["payoff_time"],
        close_time    = fmt_timing["close_time"],
    ) + _load_viewer_note() + (_SHORTS_SYSTEM_BOOST if not is_longform else _STANDARD_SYSTEM_BOOST)

    # ── Inject banned phrases from recent videos to prevent repetition ────────
    banned = _load_banned_phrases(logs_dir, n=18)
    if banned:
        banned_block = (
            "\n\nSCRIPT UNIQUENESS — NON-NEGOTIABLE:\n"
            "The following sentences were ALREADY USED in recent videos on this channel.\n"
            "Do NOT reuse, paraphrase, or echo ANY of these openings or fact statements.\n"
            "Write completely original sentences that this channel has NEVER said:\n"
            + "\n".join(f'- "{p}"' for p in banned[:15])
            + "\nEvery sentence must feel like a NEW discovery viewers have never heard."
        )
        system_prompt += banned_block

    wiki_summary = topic.get("wiki_summary", "")
    wiki_facts = (
        f"\nVERIFIED FACTS (Wikipedia — use as ground truth, reflect accurately):\n{wiki_summary}"
        if wiki_summary else ""
    )

    # If topic came from real YouTube search demand, inject the search phrase
    # so Groq generates a title that matches what people actually type.
    search_query = topic.get("search_query", "")
    if search_query:
        wiki_facts += (
            f"\nSEARCH DEMAND: People are actively searching '{search_query}' on YouTube right now. "
            f"The title MUST closely match this search phrase so the video appears in results. "
            f"Answer EXACTLY what this search query is asking — nothing else."
        )

    log.info("Generating [%s] script, template=%s wiki=%s",
             video_format, template_name, "yes" if wiki_summary else "no")

    # Build the filled user prompt once (reused across all LLM attempts)
    title_str = topic["title"]
    question_directive = (
        f"\nTHIS VIDEO MUST ANSWER EXACTLY: \"{title_str}\"\n"
        "The CORE segment must contain the direct, specific answer to this title's "
        "implied question. Do not answer a different or broader question. "
        "If a viewer watches and the title is not answered by the end, the script fails."
    )
    filled_prompt = _USER_TMPL.format(
        video_label      = fmt_timing["video_label"],
        title            = topic["title"],
        description      = topic["description"][:400],
        intent           = topic["intent"],
        template_name    = template_name,
        wiki_facts       = wiki_facts + question_directive,
        hook_dur         = fmt_timing["hook_dur"],
        tension_dur      = fmt_timing["tension_dur"],
        core_dur         = fmt_timing["core_dur"],
        payoff_dur       = fmt_timing["payoff_dur"],
        close_dur        = fmt_timing["close_dur"],
        total_est        = fmt_timing["total_est"],
        tags_instruction = _build_tags_for_prompt(topic["intent"]),
    )

    # Common Roman Urdu function words — if fewer than 5 appear, script is English
    _URDU_MARKERS = {
        "hai", "hain", "aur", "ka", "ki", "ke", "nahi", "nahin",
        "toh", "yeh", "woh", "kya", "se", "bhi", "ne", "ko",
        "mein", "par", "ek", "lekin", "phir", "jab", "tab", "ab",
        "jo", "koi", "sab", "bahut", "bohot", "sirf", "baat", "log",
        "duniya", "raaz", "dimag", "hoga", "thi",
        "jaata", "jaate", "jaati", "aata", "aate", "karte", "karta",
        "hota", "hoti", "hote", "karein", "samajh", "accha", "bilkul", "tha",
        "zaroor", "aapko", "aapka", "aapki", "humein", "unka", "iska",
    }

    def _validate(script: dict, source: str, attempt: int = 0) -> dict | None:
        """Common validation: repeat-check, word-count check, language check. Returns script or None."""
        if not script:
            return None
        if banned and _is_script_repeat(script, banned):
            log.warning("Script repeat detected (%s attempt %d) — will retry", source, attempt + 1)
            return None
        words_list = script["full_script"].lower().split()
        words = len(words_list)
        if is_longform and words < 800:
            log.warning("%s script too short (%d words, need ≥800) — will retry", source, words)
            return None

        # ── Roman Urdu gate — two checks ────────────────────────────────────
        # 1. Full-script percentage: ≥8% of all words must be Urdu markers.
        #    Pure English ≈ 0%. Mixed ≈ 3-7%. Good Roman Urdu ≈ 15-40%.
        urdu_hits = sum(1 for w in words_list if w.strip(".,!?[]()") in _URDU_MARKERS)
        urdu_pct  = urdu_hits / max(words, 1)
        if urdu_pct < 0.08:
            log.warning(
                "%s script is English/mixed — Urdu markers=%.0f%% (need ≥8%%). "
                "Rejecting (attempt %d).",
                source, urdu_pct * 100, attempt + 1,
            )
            return None

        # 2. Per-segment check: every segment longer than 12 words must have ≥2 Urdu markers.
        #    Catches the case where HOOK or CLOSE is entirely English while CORE is Urdu.
        for seg in script.get("segments", []):
            seg_text  = seg.get("text", "")
            seg_words = seg_text.lower().split()
            if len(seg_words) < 12:
                continue  # short segments (transitional phrases) get a pass
            seg_hits = sum(1 for w in seg_words if w.strip(".,!?[]()") in _URDU_MARKERS)
            if seg_hits < 2:
                log.warning(
                    "%s segment %s is English (%d Urdu markers in %d words) — "
                    "rejecting whole script (attempt %d).",
                    source, seg.get("label", "?"), seg_hits, len(seg_words), attempt + 1,
                )
                return None

        # ── Hook must reference the video topic ──────────────────────────────
        hook_seg = next((s for s in script.get("segments", []) if s.get("label") == "HOOK"), None)
        if hook_seg:
            hook_lower = hook_seg.get("text", "").lower()
            _stops = {"aur", "hai", "hain", "ka", "ki", "ke", "ne", "ko", "se",
                      "bhi", "the", "and", "for", "with", "this", "that"}
            topic_kws = [w.strip(".,!?[]()#|").lower()
                         for w in title_str.split() if len(w) > 3 and w.lower() not in _stops][:4]
            if topic_kws and not any(kw in hook_lower for kw in topic_kws):
                log.warning(
                    "%s HOOK missing topic keyword (attempt %d) — "
                    "title='%s' hook='%s'",
                    source, attempt + 1, title_str[:50], hook_seg["text"][:60],
                )
                return None

        # ── Title must be in Roman Urdu (not all-English) ────────────────────
        title_in_meta = script.get("metadata", {}).get("title", "")
        if title_in_meta:
            t_words = title_in_meta.lower().split()
            if len(t_words) > 3 and not any(
                w.strip(".,!?[]()") in _URDU_MARKERS for w in t_words
            ):
                log.warning(
                    "%s title appears to be English (0 Urdu markers) — "
                    "rejecting (attempt %d): '%s'",
                    source, attempt + 1, title_in_meta[:60],
                )
                return None

        log.info("Script OK — %d words, %.0f%% Urdu, via %s [%s/%s]",
                 words, urdu_pct * 100, source, video_format, template_name)
        return script

    # ── 1. Gemini (free, 8192 output tokens, high rate limits) ───────────────
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        for attempt in range(2):
            try:
                full_prompt = system_prompt + "\n\n" + filled_prompt
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"gemini-2.0-flash:generateContent?key={gemini_key}",
                    json={
                        "contents": [{"parts": [{"text": full_prompt}]}],
                        "generationConfig": {
                            "maxOutputTokens": fmt_profile["max_tokens"],
                            "temperature": 0.85 if attempt > 0 else 0.75,
                        },
                    },
                    timeout=90,
                )
                if r.ok:
                    raw    = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    script = _validate(_parse(raw), "Gemini", attempt)
                    if script:
                        active_key = [k for k in _GROQ_KEYS if k]
                        fc_key = active_key[0] if active_key else ""
                        if fc_key:
                            check = _fact_check(script["full_script"], fc_key)
                            if check.get("has_issues"):
                                log.warning("Gemini fact-check flagged (attempt %d): %s",
                                            attempt + 1, check.get("reason"))
                                if attempt < 1:
                                    continue
                                log.warning("Using Gemini script despite fact-check flag")
                        script["video_format"] = video_format
                        if video_format == "shorts":
                            script["segments"] = [
                                s for s in script["segments"] if s.get("label") != "CLOSE"
                            ]
                        return script
                elif r.status_code == 429:
                    log.warning("Gemini rate limit (attempt %d) — waiting 15s", attempt + 1)
                    import time; time.sleep(15)
                else:
                    log.warning("Gemini HTTP %d — falling back to Groq", r.status_code)
                    break
            except Exception as exc:
                log.warning("Gemini attempt %d: %s", attempt + 1, exc)

    # ── 2. Groq — rotate keys, wait on 429, retry up to 3× per key ──────────
    import time as _time
    active_keys = [k for k in _GROQ_KEYS if k]
    if not active_keys:
        log.error("No GROQ API keys set — check GROQ_API_KEY_1..4 in GitHub Secrets")

    for key in active_keys:
        for attempt in range(3):
            try:
                temperature = 0.85 if attempt > 0 else 0.75
                r = requests.post(
                    _GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type":  "application/json",
                    },
                    json={
                        "model":    _MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": filled_prompt},
                        ],
                        "temperature": temperature,
                        "max_tokens":  fmt_profile["max_tokens"],
                    },
                    timeout=60,
                )
                if r.status_code == 429:
                    retry_after = int(r.headers.get("retry-after", "30"))
                    wait_s = min(retry_after, 60)
                    log.warning("Rate limit on key …%s — waiting %ds then trying next key",
                                key[-4:], wait_s)
                    _time.sleep(wait_s)
                    break   # move to next key after waiting

                r.raise_for_status()
                raw    = r.json()["choices"][0]["message"]["content"].strip()
                script = _validate(_parse(raw), "Groq", attempt)
                if script is None:
                    if attempt < 2:
                        continue  # retry with higher temperature
                    break

                check = _fact_check(script["full_script"], key)
                if check.get("has_issues"):
                    log.warning("Fact-check flagged (attempt %d): %s",
                                attempt + 1, check.get("reason"))
                    if attempt < 2:
                        continue
                    log.warning("Fact-check still flagged — using best available")
                else:
                    log.info("Fact-check passed")

                script["video_format"] = video_format
                if video_format == "shorts":
                    script["segments"] = [
                        s for s in script["segments"] if s.get("label") != "CLOSE"
                    ]
                return script

            except Exception as exc:
                log.warning("Groq key …%s attempt %d: %s", key[-4:], attempt + 1, exc)
                if attempt < 2:
                    _time.sleep(5)

    log.warning("LLM unavailable — using fallback script")
    fb = _fallback(topic)
    if video_format == "shorts":
        fb["segments"] = [s for s in fb["segments"] if s.get("label") != "CLOSE"]
    return fb


def _parse(raw: str) -> dict | None:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$",        "", raw.strip(), flags=re.MULTILINE)
    for src in [raw, re.search(r'\{.*\}', raw, re.DOTALL)]:
        if src is None:
            continue
        text = src if isinstance(src, str) else src.group()
        try:
            data = json.loads(text)
            segs = data.get("segments", [])
            if len(segs) >= 4 and data.get("full_script"):
                _defaults = [
                    ("HOOK",    "excited",    "simple"),
                    ("TENSION", "mysterious", "moderate"),
                    ("CORE",    "neutral",    "complex"),
                    ("PAYOFF",  "dramatic",   "simple"),
                    ("CLOSE",   "neutral",    "simple"),
                ]
                for seg, (_, emo, cplx) in zip(segs, _defaults):
                    seg.setdefault("emotion",    emo)
                    seg.setdefault("complexity", cplx)
                data.setdefault("metadata", {})
                data["metadata"].setdefault(
                    "engagement_question",
                    "What fact surprised you the most? Drop it below",
                )
                data.setdefault("narrative_template", "classic")
                return data
        except json.JSONDecodeError:
            pass
    return None


def _fallback(topic: dict) -> dict:
    t   = topic["title"]
    cat = topic.get("intent", "SCIENCE")

    _HOOK_BY_CAT = {
        "MYSTERY":         "Ye anjaana raaz aapko bilkul hairaan kar dega.",
        "PSYCHOLOGY":      "Aapka apna dimag ye sach aapse chupaata tha.",
        "SCIENCE":         "Science ka ye amazing fact aapki duniya badal dega.",
        "TECHNOLOGY":      "Technology ka ye raaz aapko bilkul shock kar dega.",
        "ISLAMIC_SCIENCE": "Islam aur science ka ye rishta aap nahi jaante the.",
        "HISTORY":         "Taareekh ka ye chupaaya hua raaz aaj sab ke saamne aayega.",
    }
    hook    = _HOOK_BY_CAT.get(cat, "Ye baat aapki duniya ka nazariya hamesha ke liye badal degi.")
    tension = ("Aksar log ye nahi jaante. Lekin saalon ki research ke baad sach samne aaya. "
               "Aur ab ye raaz chupaaya nahi ja sakta.")
    core    = (f"{t}. [WOW] Is baat ki gehrai samajhna almost na-mumkin lagta hai. "
               "Researchers ne decades se is par kaam kiya hai. "
               "Aur ab saboot nakar-nahi ho sakta.")
    payoff  = "Ab aap jaante hain duniya ke is sabse important aur anjaane raaz ki sachai."
    close   = "Aur bhi aisi batein jaanne ke liye Obscura follow karein — har roz naya raaz."
    full    = " ".join([hook, tension, core, payoff, close])
    return {
        "narrative_template": "classic",
        "segments": [
            {"id": 1, "label": "HOOK",    "text": hook,    "estimated_duration_seconds": 3,
             "emotion": "excited",    "complexity": "simple"},
            {"id": 2, "label": "TENSION", "text": tension, "estimated_duration_seconds": 12,
             "emotion": "mysterious", "complexity": "moderate"},
            {"id": 3, "label": "CORE",    "text": core,    "estimated_duration_seconds": 30,
             "emotion": "neutral",    "complexity": "complex"},
            {"id": 4, "label": "PAYOFF",  "text": payoff,  "estimated_duration_seconds": 10,
             "emotion": "dramatic",   "complexity": "simple"},
            {"id": 5, "label": "CLOSE",   "text": close,   "estimated_duration_seconds": 5,
             "emotion": "neutral",    "complexity": "simple"},
        ],
        "total_estimated_seconds": 60,
        "full_script": full,
        "metadata": {
            "title": t[:90],
            "description": (
                f"{t}\n\n"
                f"Category: {cat}\n\n"
                "#Obscura #Facts #DidYouKnow #RomanUrdu #Educational"
            ),
            "tags": ["real world facts", "facts", "did you know", "world facts",
                     "educational", cat.lower()],
            "engagement_question": f"Did you already know this about {t[:40]}? Tell us below!",
        },
    }
