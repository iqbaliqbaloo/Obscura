"""
STEP 5 — Scene Planning (Obscura Edition)

Assigns visual_keywords (list of 3, ranked by specificity) to every scene.

TWO-LAYER keyword selection:
  Layer 1 — Semantic text analysis: scans the scene's actual script_text for
             emotional/narrative trigger words and prepends matching emotional
             visual keywords. This is what separates "stock search by topic"
             from "emotional visual storytelling."
  Layer 2 — Category+segment bank: static category-specific keywords as
             fallback and supplement.

Also sets focus_region and motion_emotion per scene to guide Ken Burns
motion presets in video_assembler.
"""

import json
import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_USED_KEYWORDS_PATH = _LOGS_DIR / "used_visual_keywords.json"
_KEYWORD_WINDOW = 10  # penalize keywords used in last 10 videos


def _load_used_keywords() -> set[str]:
    """Load the rolling 10-video keyword history."""
    try:
        if _USED_KEYWORDS_PATH.exists():
            data = json.loads(_USED_KEYWORDS_PATH.read_text())
            return set(data.get("keywords", []))
    except Exception:
        pass
    return set()


def _save_used_keywords(keywords: list[str]) -> None:
    """Append new keywords to the rolling window (cap at 10 videos * 15 keywords = 150)."""
    try:
        existing: list[str] = []
        if _USED_KEYWORDS_PATH.exists():
            existing = json.loads(_USED_KEYWORDS_PATH.read_text()).get("keywords", [])
        combined = (existing + keywords)[-150:]  # rolling 150 entries
        tmp = _USED_KEYWORDS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"keywords": combined}, indent=2))
        tmp.replace(_USED_KEYWORDS_PATH)
    except Exception:
        pass

# ── Layer 1: Semantic narrative → emotional visual keywords ───────────────────
# Pattern strings are pipe-separated regex alternatives.
# First matching pattern wins; its visuals are prepended to keyword list.

_NARRATIVE_TRIGGERS: list[tuple[str, list[str], str]] = [
    # (regex_pattern, visual_keywords, motion_emotion)

    # Destruction / extinction / catastrophe (expanded synonyms)
    (r"died|extinct|destroyed|collapse|impact|crash|doom|apocalypse|devastat"
     r"|vanish|wiped.?out|obliterat|annihilat|eradicat|perish|ruin",
     ["cinematic explosion destruction aftermath dark",
      "apocalyptic dramatic ruins devastation wide",
      "impact shock debris dramatic cinematic"],
     "dramatic"),

    # Discovery / secret / hidden (expanded)
    (r"discover|found|reveal|uncover|secret|hidden|unknown|first time|never seen"
     r"|uncov|exposed|unearthed|breakthrough|identified|confirmed|detected",
     ["discovery light emergence dramatic reveal",
      "scientist breakthrough discovery laboratory",
      "hidden reveal light dark contrast dramatic"],
     "mysterious"),

    # Scale / size comparison (expanded)
    (r"bigger|larger|massive|enormous|vast|huge|scale|trillion|billion|million times"
     r"|colossal|immense|gigantic|incomprehensible|dwarfs|overshadow",
     ["aerial vast scale comparison dramatic wide",
      "cosmic scale size comparison universe",
      "size contrast comparison dramatic aerial"],
     "dramatic"),

    # Speed / instant (expanded)
    (r"faster|speed|instant|second|millisecond|rapidly|lightning|immediate"
     r"|velocity|acceleration|simultaneous|nanosecond|blinding",
     ["speed blur motion fast dynamic",
      "lightning fast impact velocity dramatic",
      "fast motion dynamic energy speed"],
     "excited"),

    # Fear / danger / threat (expanded)
    (r"terrif|deadly|danger|threat|killer|fatal|lethal|predator|attack|horror"
     r"|catastroph|hazard|peril|venom|toxic|radiation|lethal",
     ["dark ominous threat dramatic cinematic",
      "danger predator dark atmospheric",
      "ominous cinematic thriller dark shadow"],
     "dramatic"),

    # Ancient / history / time (expanded)
    (r"ancient|prehistoric|million year|thousand year|oldest|century|civilisation|empire"
     r"|millennia|archaic|primordial|antiquity|paleolithic|neolithic|medieval",
     ["ancient ruins archaeological stone dramatic",
      "prehistoric landscape dramatic wide historical",
      "ancient civilisation monument stone aerial"],
     "mysterious"),

    # Underground / deep / hidden beneath (expanded)
    (r"underground|beneath|buried|ocean floor|deep sea|cave|trench|abyss"
     r"|subterranean|subsurface|below ground|hidden beneath|depths",
     ["cave underground dark depth mysterious",
      "deep ocean dark bioluminescence dramatic",
      "underground tunnel depth atmospheric dark"],
     "mysterious"),

    # Wonder / beauty / breathtaking (expanded)
    (r"beautiful|stunning|breathtaking|extraordinary|incredible|magnificent|wonder"
     r"|spectacular|awe.?inspiring|mesmerising|sublime|remarkable",
     ["stunning aerial beautiful cinematic wide",
      "breathtaking landscape golden dramatic light",
      "cinematic beautiful nature vast wide"],
     "excited"),

    # Impossible / mind-blowing / paradox (expanded)
    (r"impossible|paradox|bizarre|unbelievable|mind.?blow|defy|strange"
     r"|counterintuitive|defies|violates|contradicts|inexplicable|absurd",
     ["impossible surreal dramatic mind-blowing",
      "paradox strange dramatic contrast cinematic",
      "bizarre impossible dramatic wide surreal"],
     "mysterious"),

    # Life / survival / evolution (expanded)
    (r"evolv|survival|adapt|life form|organism|creature|species|born|alive"
     r"|mutate|reproduce|extinct|thrive|predator|prey|ecosystem",
     ["wildlife survival dramatic nature wide",
      "creature close detail dramatic nature",
      "life evolution dramatic nature wide"],
     "excited"),

    # Universe / cosmos / space (expanded)
    (r"universe|cosmos|galaxy|nebula|star|planet|black hole|solar|light year"
     r"|quasar|pulsar|supernova|dark matter|event horizon|interstellar",
     ["cosmos galaxy nebula dramatic wide",
      "deep space universe dramatic cinematic",
      "planet surface space dramatic atmospheric"],
     "mysterious"),

    # Water / ocean / flood (expanded)
    (r"ocean|sea|water|flood|wave|tsunami|underwater|marine|current"
     r"|aquatic|submerged|tidal|hydrothermal|bioluminescent|abyss",
     ["ocean dramatic wave cinematic wide",
      "underwater dramatic cinematic bioluminescent",
      "ocean surface dramatic aerial wide"],
     "dramatic"),
]

# Emotion-matched CLOSE scene visuals (replaces placeholder "CLOSE" string)
_CLOSE_VISUALS: dict[str, list[str]] = {
    "excited":    ["sunset cinematic fade wide aerial",
                   "golden hour horizon beautiful calm",
                   "nature wide shot peaceful golden"],
    "mysterious": ["dark fog atmospheric cinematic wide",
                   "night sky stars calm mysterious",
                   "silhouette horizon mysterious wide"],
    "dramatic":   ["dramatic sky clouds wide aerial",
                   "epic landscape cinematic wide aerial",
                   "sunset dramatic wide horizon"],
    "neutral":    ["calm aerial zoom out landscape",
                   "peaceful nature wide sunset",
                   "logo reveal soft glow dark blue"],
}

# ── Layer 2: Category + segment static keyword banks ─────────────────────────

_HOOK: dict[str, list[str]] = {
    "MYSTERY":        ["ancient ruins mystery dark atmospheric",       "forbidden hidden secret dark dramatic",        "paranormal phenomenon unexplained dramatic"],
    "PSYCHOLOGY":     ["human brain neuron close dramatic glow",       "face emotion expression close dramatic",       "mind psychology concept dark abstract"],
    "SCIENCE":        ["scientific discovery laboratory dramatic",      "nature creature wildlife dramatic close",      "human body cell microscope dramatic"],
    "TECHNOLOGY":     ["robot artificial intelligence futuristic dramatic", "circuit board chip neon glow dramatic",    "computer code digital dark dramatic"],
    "ISLAMIC_SCIENCE":["islamic architecture mosque interior dramatic", "ancient manuscript illuminated calligraphy",   "geometric pattern islamic art dramatic"],
    "HISTORY":        ["ancient ruins empire dramatic aerial",          "historical monument stone archaeological",     "ancient civilization dramatic wide"],
}

_TENSION: dict[str, list[list[str]]] = {
    "MYSTERY":        [["dark fog abandoned ruins atmospheric",    "shadow silhouette dark mysterious",         "ancient mystery stone inscription"],
                       ["ocean storm aerial dramatic dark",         "mysterious sky phenomenon dramatic",        "unexplained light sky dramatic"],
                       ["ancient map route parchment close",        "secret document forbidden dark",            "undecoded inscription close"],
                       ["abandoned ghost town dark dramatic",        "underground chamber dark mysterious",       "ancient vault stone door"],
                       ["pyramid interior dark narrow chamber",      "cave darkness torch dramatic",              "subterranean ruins dark wide"]],
    "PSYCHOLOGY":     [["human eye iris close dramatic",            "face fear anxiety expression close",        "crowd mob behavior dramatic"],
                       ["brain synapse neural connection glow",      "thought concept mind abstract dark",        "neural pathway visualization"],
                       ["social media phone addiction dramatic",      "manipulation dark psychology concept",      "person controlled puppet concept"],
                       ["sleep REM dream dark night",                "subconscious mind dark abstract",           "memory distortion concept close"],
                       ["decision fatigue exhausted person",          "cognitive bias choice concept",             "fear adrenaline body dramatic"]],
    "SCIENCE":        [["dna helix molecule biology close",          "genetics laboratory dramatic research",     "microscope organism cell detail"],
                       ["quantum physics particle abstract glow",    "physics experiment energy light",           "atom nucleus visualization dramatic"],
                       ["animal predator hunting dramatic",           "mantis shrimp underwater close",           "deep sea creature bioluminescent"],
                       ["human body anatomy dramatic close",          "blood cell microscope dramatic",            "immune system virus battle"],
                       ["black hole accretion disk space",            "star formation nebula cosmic",              "supernova explosion galaxy"]],
    "TECHNOLOGY":     [["robot humanoid face dramatic close",        "AI machine learning data glow",             "futuristic computer interface"],
                       ["hacker dark screen code dramatic",           "cybersecurity data breach dramatic",        "digital surveillance camera"],
                       ["brain chip neuralink concept close",         "neural implant surgery dramatic",           "human computer interface"],
                       ["quantum computer glowing lab",               "semiconductor chip micro detail",           "processor circuit neon close"],
                       ["deepfake manipulation video concept",         "algorithm social media dramatic",           "data server dark room glow"]],
    "ISLAMIC_SCIENCE":[["ancient islamic library manuscripts dark",  "medieval scholar writing candlelight",      "illuminated arabic book close"],
                       ["mosque interior dome architecture",          "islamic geometric tile mosaic",             "arabesque ornamental pattern"],
                       ["medieval observatory astronomy tower",       "astrolabe instrument close dramatic",       "star map arabic ancient"],
                       ["arabic calligraphy ink manuscript",          "quran verse illuminated close",             "ancient medical text arabic"],
                       ["baghdad ancient city reconstruction",        "silk road caravan desert dramatic",         "islamic empire city wide"]],
    "HISTORY":        [["ancient pyramid stone aerial dramatic",      "mughal monument ruins dramatic",            "indus valley ruins excavation"],
                       ["historical battle painting dramatic",         "ancient warriors empire conquest",          "siege castle medieval dramatic"],
                       ["ancient map parchment route close",          "historical manuscript scroll detail",       "empire territory map old"],
                       ["archaeological dig discovery dramatic",       "ancient artifact close museum",             "excavation ruin dramatic wide"],
                       ["ottoman palace ruins dramatic",               "mughal fort walls aerial",                  "ancient civilization wide aerial"]],
}

_CORE: dict[str, list[list[str]]] = {
    "MYSTERY":        [["nazca lines aerial wide Peru",             "ancient stone carving impossible detail",   "easter island moai statue close"],
                       ["bermuda triangle ocean aerial storm",       "ship disappearance ocean dramatic",         "pilot cockpit storm dramatic"],
                       ["voynich manuscript page close detail",      "ancient undecoded text parchment",          "mysterious symbol carving stone"],
                       ["underwater ruins submarine ancient",        "lost city underwater archaeology",          "submerged structure ancient ocean"],
                       ["puma punku stone precision cut",            "antikythera mechanism ancient gears",       "ancient technology impossible close"],
                       ["mandela effect memory concept glow",        "parallel universe concept dramatic",         "reality distortion abstract"]],
    "PSYCHOLOGY":     [["brain mri scan activity glow",             "neuron synapse fire dramatic close",        "brain hemisphere visualization"],
                       ["behavioral experiment person lab",           "social conformity crowd dramatic",          "cognitive test brain concept"],
                       ["mob psychology crowd dramatic wide",         "peer pressure person dramatic",             "bystander effect crowd dramatic"],
                       ["fear response cortisol body dramatic",       "anxiety nervous system close",              "fight flight response dramatic"],
                       ["false memory reconstruction concept",        "amnesia psychology dramatic close",         "memory palace concept glow"],
                       ["dopamine reward cycle brain glow",           "addiction loop brain dramatic",             "placebo medicine pill dramatic"]],
    "SCIENCE":        [["microscope cell organism detail close",     "biology discovery dramatic close",          "scientific experiment lab"],
                       ["mantis shrimp punch speed underwater",       "tardigrade microscope extreme close",       "octopus color change close"],
                       ["quantum wave particle duality abstract",     "atom model electron orbit",                 "molecular bond visualization"],
                       ["dna helix strand close detail",              "gene sequence laboratory close",            "protein molecule structure"],
                       ["black hole visualization space dramatic",    "neutron star pulsar beam space",            "cosmic nebula wide dramatic"],
                       ["immune cell attacking virus close",          "cancer cell microscope dramatic",           "blood vessel anatomy close"]],
    "TECHNOLOGY":     [["circuit board close neon glow dramatic",   "microchip processor extreme close",         "electronic component detail"],
                       ["robot factory automation arm dramatic",       "AI data processing visualization",          "machine learning concept glow"],
                       ["satellite dish array technology",             "fiber optic light speed cable",             "data center server dark room"],
                       ["VR headset person immersive close",          "augmented reality digital overlay",         "hologram projection dramatic"],
                       ["autonomous vehicle road technology",          "electric battery technology close",          "drone swarm formation aerial"],
                       ["neural network nodes glow visualization",    "3D printing layer by layer close",          "quantum computer lab dramatic"]],
    "ISLAMIC_SCIENCE":[["ancient medical manuscript illuminated",    "medieval pharmacy herb medicine close",     "islamic medicine surgical tool"],
                       ["arabic algebra manuscript equation",          "mathematical proof ancient text",           "number theory islamic manuscript"],
                       ["astrolabe islamic astronomy instrument",      "star chart arabic calligraphy",             "observatory dome medieval"],
                       ["islamic geometric art architecture",          "muqarnas ceiling detail close",             "arabesque tile pattern extreme close"],
                       ["illuminated quran page calligraphy",          "arabic script ink manuscript",              "ancient bookbinding leather close"],
                       ["silk road trade goods market",                "spice market ancient commerce",             "medieval islamic city aerial wide"]],
    "HISTORY":        [["ancient ruins wide aerial dramatic",         "archaeological site excavation dramatic",   "historical ruins monument close"],
                       ["mughal architecture marble detail",           "taj mahal reflection pool dramatic",        "red fort architecture wide aerial"],
                       ["mohenjo-daro ruins ancient grid",             "indus valley brick structure close",        "ancient city plan archaeological"],
                       ["ottoman janissary army historical",           "historical battle reconstruction wide",      "empire conquest territory map"],
                       ["silk road caravan desert route",              "ancient caravanserai ruins close",          "trade goods ancient market"],
                       ["ancient coin gold treasure hoard",            "archaeological artifact museum close",       "shipwreck underwater artifact"]],
}

_PAYOFF: dict[str, list[str]] = {
    "MYSTERY":        ["ancient truth revealed dramatic light",   "mystery solved concept dramatic glow",    "ancient wonder heritage revealed"],
    "PSYCHOLOGY":     ["person calm realization peaceful warm",   "human mind clarity concept warm glow",    "emotional breakthrough person warm"],
    "SCIENCE":        ["scientific discovery breakthrough glow",  "nature beauty close golden warm",         "science wonder universe beautiful"],
    "TECHNOLOGY":     ["technology future hope sunrise wide",     "innovation digital world beautiful glow", "AI human harmony concept warm"],
    "ISLAMIC_SCIENCE":["islamic art beauty calligraphy golden",   "mosque golden hour light peaceful",       "islamic heritage monument beautiful"],
    "HISTORY":        ["ancient heritage monument golden hour",   "historical site sunrise golden wide",     "ancient civilization beauty triumph"],
}

# Focus region per segment — guides Ken Burns direction
_FOCUS: dict[str, str] = {
    "HOOK":    "center",
    "TENSION": "center",
    "CORE":    "center",
    "PAYOFF":  "center",
    "CLOSE":   "center",
}

# Emotion → motion_emotion tag for video_assembler preset selection
_EMOTION_MOTION: dict[str, str] = {
    "excited":    "excited",
    "mysterious": "mysterious",
    "dramatic":   "dramatic",
    "neutral":    "neutral",
}

_DEFAULT = "SCIENCE"


# ── Semantic text analysis ────────────────────────────────────────────────────

def _text_visual_hints(text: str) -> tuple[list[str], str | None]:
    """
    Scan script_text for narrative/emotional trigger words.
    Returns (visual_keyword_overrides, motion_emotion_override).
    Overrides are prepended to the category keyword list.
    """
    text_lower = text.lower()
    for pattern, visuals, motion in _NARRATIVE_TRIGGERS:
        if re.search(pattern, text_lower):
            return visuals[:2], motion
    return [], None


_EMOTION_KEYWORDS: dict[str, list[str]] = {
    "dramatic":   ["dramatic", "cinematic", "dark", "impact", "destruction", "ruins"],
    "mysterious": ["mysterious", "dark", "hidden", "deep", "unknown", "atmospheric"],
    "excited":    ["stunning", "beautiful", "dynamic", "aerial", "wide", "golden"],
    "neutral":    ["wide", "landscape", "aerial", "calm", "nature"],
}


def _score_keywords(candidates: list[str], emotion: str,
                    semantic_hints: list[str],
                    used_keywords: set[str] | None = None) -> list[str]:
    """
    Score candidate keywords and return sorted list (highest score first).
    Scoring:
      +20 if keyword came from semantic text analysis (Layer 1)
      +15 if keyword contains an emotion-aligned word
      +20 if keyword NOT in recent 10-video history (novelty bonus)
      -10 if keyword IS in recent history (freshness penalty)
    """
    emotion_words   = _EMOTION_KEYWORDS.get(emotion, [])
    recently_used   = used_keywords or set()
    scores: list[tuple[int, str]] = []
    seen: set[str] = set()
    for kw in candidates:
        if kw in seen:
            continue
        seen.add(kw)
        score = 0
        if kw in semantic_hints:
            score += 20
        kw_lower = kw.lower()
        if any(ew in kw_lower for ew in emotion_words):
            score += 15
        if recently_used:
            if kw not in recently_used:
                score += 20  # novelty bonus
            else:
                score -= 10  # freshness penalty
        scores.append((score, kw))
    scores.sort(key=lambda x: x[0], reverse=True)
    return [kw for _, kw in scores]


# ── Groq: batch topic-specific visual keywords ───────────────────────────────

_GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.1-8b-instant"

def _groq_batch_keywords(scenes: list[dict], intent: str,
                         wiki_summary: str = "") -> dict[int, list[str]]:
    """
    Single Groq call for all scenes. Returns {scene_id: [kw1, kw2, kw3]}.
    Keywords are topic-specific (derived from actual script text) rather than
    generic category banks. Falls back to empty dict on any error.
    """
    keys = [os.getenv("GROQ_API_KEY_1", "").strip(),
            os.getenv("GROQ_API_KEY_2", "").strip()]
    keys = [k for k in keys if k]
    if not keys:
        return {}

    scene_lines = [
        f"Scene {sc['scene_id']} ({sc['segment_label']}): {sc.get('script_text', '')[:150]}"
        for sc in scenes
        if sc.get("clip_type") != "close"
    ]
    if not scene_lines:
        return {}

    wiki_ctx = f"\nVerified facts: {wiki_summary[:300]}" if wiki_summary else ""

    system_prompt = (
        "You generate stock-photo search queries for educational YouTube video scenes.\n"
        "For each scene output 3 queries (4-6 words each) a photographer would actually shoot.\n"
        "Translate abstract/scientific concepts into visible physical subjects.\n"
        "BAD: 'neutron star radiation'  GOOD: 'bright cosmic explosion nebula glow'\n"
        "BAD: 'Vikings crossed Atlantic' GOOD: 'ancient wooden ship ocean storm'\n"
        'Return ONLY valid JSON: {"1": ["kw1","kw2","kw3"], "2": [...]}'
    )

    user_msg = f"Category: {intent}{wiki_ctx}\n\nScenes:\n" + "\n".join(scene_lines)

    for key in keys:
        try:
            r = requests.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "model":    _GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_msg},
                    ],
                    "temperature": 0.4,
                    "max_tokens":  800,
                },
                timeout=30,
            )
            if not r.ok:
                continue
            raw = r.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```\s*$",        "", raw, flags=re.MULTILINE)
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if not m:
                continue
            data   = json.loads(m.group())
            result = {}
            for k, v in data.items():
                try:
                    sid = int(k)
                    if isinstance(v, list) and v:
                        result[sid] = [str(kw)[:60] for kw in v[:3]]
                except (ValueError, TypeError):
                    pass
            if result:
                log.info("Groq batch keywords: %d scenes", len(result))
                return result
        except Exception as exc:
            log.debug("Groq batch keywords: %s", exc)

    return {}


# ── Main ─────────────────────────────────────────────────────────────────────

def plan_scenes(timeline: dict, intent: str, wiki_summary: str = "") -> dict:
    intent = intent.upper()
    if intent not in _HOOK:
        intent = _DEFAULT

    t_pool = [kws[:] for kws in _TENSION[intent]]
    c_pool = [kws[:] for kws in _CORE[intent]]
    t_idx = c_idx = 0

    used_keywords = _load_used_keywords()
    new_keywords:  list[str] = []

    # Batch Groq call — topic-specific keywords from actual script text
    groq_keywords = _groq_batch_keywords(timeline["scenes"], intent, wiki_summary)

    for sc in timeline["scenes"]:
        label   = sc["segment_label"]
        emotion = sc.get("emotion", "neutral")

        if label == "HOOK":
            kws = _HOOK[intent]

        elif label == "TENSION":
            kws = t_pool[t_idx % len(t_pool)]
            t_idx += 1

        elif label == "CORE":
            kws = c_pool[c_idx % len(c_pool)]
            c_idx += 1

        elif label == "PAYOFF":
            kws = _PAYOFF[intent]

        elif label == "CLOSE":
            # Use emotion-matched visual keywords instead of the placeholder "CLOSE"
            close_emotion = sc.get("emotion", "neutral")
            close_kws = _CLOSE_VISUALS.get(close_emotion, _CLOSE_VISUALS["neutral"])
            sc["visual_keyword"]  = close_kws[0]
            sc["visual_keywords"] = close_kws[:3]
            sc["clip_type"]       = "close"
            sc["focus_region"]    = "center"
            sc["motion_emotion"]  = "neutral"
            continue

        else:
            kws = _PAYOFF.get(intent, ["nature landscape wide"])

        # Layer 0: Groq topic-specific keywords (from actual script text) — highest priority
        groq_kws = groq_keywords.get(sc["scene_id"], [])

        # Layer 1: semantic text analysis — emotional visual hints
        text_hints, motion_override = _text_visual_hints(sc.get("script_text", ""))

        # Merge: Groq-specific first, then emotional hints, then static bank
        candidate_pool = groq_kws + text_hints + list(kws)

        # Score: semantic hint +20, emotion alignment +15, novelty +20, penalty -10
        scored   = _score_keywords(candidate_pool, emotion, text_hints, used_keywords)
        combined = scored[:3] if scored else candidate_pool[:3]

        sc["visual_keyword"]  = combined[0] if combined else "cinematic dramatic wide"
        sc["visual_keywords"] = combined
        sc["focus_region"]    = _FOCUS.get(label, "center")
        sc["motion_emotion"]  = motion_override or _EMOTION_MOTION.get(emotion, "neutral")
        new_keywords.extend(combined)

    # Persist used keywords for novelty rotation across next 10 videos
    if new_keywords:
        _save_used_keywords(new_keywords)

    log.info("Scene keywords assigned (%d scenes, category=%s)",
             len(timeline["scenes"]), intent)
    return timeline
