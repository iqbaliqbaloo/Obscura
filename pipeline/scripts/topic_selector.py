"""
STEP 1 — Topic Selection (Obscura Edition)

Uses Groq to generate a specific, surprising world-fact topic from a curated seed bank.
Categories are rotated evenly across all 6 types using a frequency-inverse score so no
single category dominates. Deduplication: token_set_ratio > 0.80 against today's produced videos.

Algorithm 1 — Trending Topic Arbitrage (Google Trends via pytrends):
  Fetches rising related queries per category. Trending seeds are ranked ahead
  of static seeds so the most timely topics are attempted first.
  Opportunity score = trend_value (0-100 from Google) for ranking.
  Results cached for _TREND_CACHE_TTL_HOURS hours to avoid rate-limiting.

Algorithm 4 — Saturation Filter (YouTube Data API v3):
  After a title is generated, checks the YouTube search result count.
  Skips topics with > _SATURATION_MAX_RESULTS existing videos.
  Falls back gracefully (passes filter) when YOUTUBE_API_KEY is not set.

Fallback: random seed title if Groq is unavailable.
"""

import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    from rapidfuzz import fuzz as _fuzz
    def _sim(a: str, b: str) -> float:
        return _fuzz.token_set_ratio(a, b) / 100.0
except ImportError:
    def _sim(a: str, b: str) -> float:
        wa, wb = set(a.lower().split()), set(b.lower().split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

log = logging.getLogger(__name__)

CATEGORIES = ["MYSTERY", "PSYCHOLOGY", "SCIENCE", "TECHNOLOGY", "ISLAMIC_SCIENCE", "HISTORY"]

# ── Algorithm 4 config ────────────────────────────────────────────────────────
# Saturation is measured by view velocity of top 10 results, NOT total result count.
# totalResults is a corpus-size signal, not competition density.
# Median views of top 10:
#   < 100,000        → PASS (low competition)
#   100k – 500k      → PASS with -10 score penalty
#   > 500,000        → REJECT (market already dominated)
_SATURATION_MEDIAN_PASS    = 100_000
_SATURATION_MEDIAN_PENALTY = 500_000

# ── Algorithm 1 config ────────────────────────────────────────────────────────
# Trend data is cached to avoid hammering Google Trends on every pipeline run.
_TREND_CACHE_TTL_HOURS = 6

# Search terms used to query YouTube autocomplete per category.
# These are question-format queries that match how real viewers actually search —
# not optimised marketing phrases. Autocomplete returns what people COMPLETE after
# typing these, giving us the highest-demand titles in each category.
_CATEGORY_SEARCH_TERMS: dict[str, list[str]] = {
    "MYSTERY": [
        "ancient mysteries science cannot explain",
        "unexplained phenomena caught on camera",
        "bermuda triangle mystery explained",
        "ancient structures impossible to build today",
        "time travel proof real evidence",
        "mandela effect explained parallel universe",
        "nazca lines mystery who made them",
        "lost civilizations that disappeared without trace",
        "forbidden history they dont want you to know",
        "real paranormal events explained by science",
    ],
    "PSYCHOLOGY": [
        "dark psychology facts you didnt know",
        "how your subconscious mind controls you",
        "manipulation psychology tricks used on you",
        "why humans make terrible decisions science",
        "social media addiction brain chemistry explained",
        "why your memory is lying to you",
        "psychology of fear explained",
        "human behavior facts psychology",
        "why do we dream psychology explained",
        "cognitive biases ruining your decisions",
    ],
    "SCIENCE": [
        "human body facts that will shock you",
        "quantum physics explained simply",
        "animal superpowers that are scientifically real",
        "why do we age and can science stop it",
        "how dna actually works explained",
        "space facts nobody taught you",
        "science discoveries that changed everything",
        "biology facts that sound impossible but are true",
        "what happens inside a black hole",
        "most incredible scientific facts",
    ],
    "TECHNOLOGY": [
        "how artificial intelligence actually works",
        "future technology that already exists now",
        "elon musk neuralink brain chip explained",
        "how internet physically works explained",
        "robots replacing human jobs facts",
        "quantum computing explained simply",
        "social media algorithm secrets revealed",
        "ai will replace humans facts",
        "smartphone technology you dont understand",
        "cybersecurity threats you face right now",
    ],
    "ISLAMIC_SCIENCE": [
        "islamic golden age inventions that changed world",
        "muslim scientists who changed history",
        "ibn sina avicenna medical discoveries",
        "al khwarizmi invention of algebra",
        "science facts mentioned in the quran",
        "muslim contributions to mathematics astronomy",
        "house of wisdom baghdad history",
        "islamic civilization greatest achievements",
        "ibn al haytham father of optics",
        "al biruni scientific discoveries",
    ],
    "HISTORY": [
        "mughal empire facts nobody taught you",
        "ancient indus valley civilization secrets",
        "ottoman empire rise and fall explained",
        "silk road history facts",
        "history facts that sound impossible but are true",
        "ancient civilizations more advanced than we thought",
        "alexander the great real history facts",
        "genghis khan empire facts",
        "history mysteries never solved",
        "ancient egypt facts scientists discovered",
    ],
}

# Keywords for mapping Google's "trending now" searches to our categories.
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "MYSTERY": [
        "mystery", "unexplained", "paranormal", "ufo", "alien", "conspiracy", "secret",
        "hidden", "ancient", "bermuda", "atlantis", "time travel", "mandela", "supernatural",
        "ghost", "haunted", "forbidden", "unsolved", "strange", "bizarre", "phenomenon",
        "enigma", "cryptid", "nazca", "pyramid", "lost city", "voynich", "stonehenge",
        "disappear", "vanish", "anomaly", "impossible", "unexplored", "cursed",
    ],
    "PSYCHOLOGY": [
        "psychology", "brain", "mind", "behavior", "cognitive", "memory", "emotion",
        "subconscious", "unconscious", "dream", "sleep", "anxiety", "depression", "phobia",
        "trauma", "therapy", "neuroscience", "neuron", "dopamine", "serotonin", "cortisol",
        "addiction", "habit", "motivation", "decision", "bias", "manipulation", "dark psychology",
        "personality", "intelligence", "stress", "placebo", "hypnosis", "social media brain",
        "mental", "consciousness", "illusion", "hallucination", "perception", "focus",
    ],
    "SCIENCE": [
        "science", "biology", "dna", "gene", "quantum", "atom", "evolution", "discovery",
        "experiment", "molecule", "cell", "protein", "species", "genome", "mutation",
        "bacteria", "space", "nasa", "planet", "star", "galaxy", "universe", "black hole",
        "human body", "immune", "virus", "vaccine", "physics", "chemistry", "research",
        "animal", "creature", "organism", "fossil", "extinct", "octopus", "mantis shrimp",
        "tardigrade", "jellyfish", "deep sea", "bioluminescent", "photosynthesis",
    ],
    "TECHNOLOGY": [
        "technology", "ai", "robot", "computer", "internet", "software", "chip",
        "artificial intelligence", "chatgpt", "openai", "machine learning", "neural network",
        "algorithm", "quantum computer", "cryptocurrency", "bitcoin", "electric vehicle",
        "tesla", "drone", "cybersecurity", "hack", "smartphone", "neuralink", "elon musk",
        "automation", "5g", "satellite", "data", "deepfake", "algorithm", "tech", "startup",
        "innovation", "semiconductor", "gpu", "cloud", "metaverse", "vr", "ar",
    ],
    "ISLAMIC_SCIENCE": [
        "islamic", "islam", "muslim", "quran", "ibn sina", "avicenna", "al khwarizmi",
        "ibn al haytham", "al biruni", "al razi", "ibn battuta", "golden age", "baghdad",
        "house of wisdom", "islamic empire", "ottoman", "mughal", "algebra", "optics",
        "islamic invention", "islamic history", "muslim scientist", "islamic astronomy",
        "mosque", "caliphate", "hadith", "madrasa", "islamic medicine", "islamic math",
        "islamic architecture", "quran science", "prophet", "medieval islam",
    ],
    "HISTORY": [
        "history", "ancient", "civilization", "empire", "war", "kingdom", "dynasty",
        "mughal", "ottoman", "mongol", "silk road", "indus valley", "pyramid", "pharaoh",
        "roman", "greek", "persian", "alexander", "genghis", "tipu sultan", "partition",
        "colonial", "revolution", "battle", "conquest", "heritage", "artifact", "medieval",
        "archaeological", "sultan", "king", "queen", "historical", "century", "bc", "ad",
        "ancient pakistan", "ancient india", "islamic conquest", "trade route",
    ],
}

_SEEDS: dict[str, list[str]] = {
    "MYSTERY": [
        "the Bermuda Triangle has swallowed hundreds of ships and planes without explanation",
        "ancient Egypt pyramid construction methods still baffle modern engineers",
        "Nazca lines are so large they can only be properly seen from the sky",
        "the Voynich Manuscript has never been decoded despite 600 years of trying",
        "Easter Island statues were moved across the island — no one knows how",
        "the Antikythera Mechanism was a working computer built 2000 years ago",
        "Gobekli Tepe is 6000 years older than Stonehenge and rewrites history",
        "ancient maps show Antarctica without ice before Europeans discovered it",
        "the Mandela effect suggests our memories are collectively wrong about reality",
        "the Baghdad Battery is an ancient electric cell found in Iraq",
        "Puma Punku stones are cut with laser precision but date to 500 AD",
        "ancient Indian texts describe flying machines called Vimanas",
        "the Yonaguni Monument is an underwater pyramid structure off Japan",
        "time slip experiences have been reported by credible witnesses worldwide",
    ],
    "PSYCHOLOGY": [
        "humans make 35000 unconscious decisions every single day",
        "your memories change and distort every single time you recall them",
        "the bystander effect means more witnesses means less chance of help",
        "dark psychology manipulation tricks are used on you every day",
        "social media is deliberately engineered like a slot machine for your brain",
        "your subconscious mind controls 95 percent of all your behavior",
        "sleep deprivation creates the exact same impairment as being drunk",
        "the placebo effect works even when patients know they are taking a fake pill",
        "fear and excitement produce the exact same chemical reaction in the body",
        "decision fatigue makes every choice worse as the day progresses",
        "human brains cannot reliably distinguish between real and imagined events",
        "loneliness is as physically damaging as smoking 15 cigarettes per day",
        "color affects your mood and behavior more than any other visual signal",
        "multitasking makes you 40 percent less productive than focusing on one thing",
    ],
    "SCIENCE": [
        "the human body completely replaces itself down to the cells every 7 to 10 years",
        "your gut contains more neurons than your entire spinal cord",
        "quantum particles can exist in two places simultaneously until observed",
        "time moves measurably slower near heavy objects — GPS satellites prove it",
        "everything solid you touch is 99.9 percent empty space",
        "the mantis shrimp punches at the speed of a bullet and sees 16 colors",
        "tardigrades survive outer space radiation vacuum and extreme temperature",
        "the immortal jellyfish can revert itself back to its juvenile form indefinitely",
        "crows can recognize individual human faces and remember grudges for years",
        "DNA encodes more data per gram than any hard drive ever manufactured",
        "the human immune system identifies and destroys over a billion cancer cells daily",
        "octopuses have three hearts two brains and copper-based blue blood",
        "black holes slow time so severely that one hour near one equals 7 years on Earth",
        "the universe is expanding faster than the speed of light at its edges",
    ],
    "TECHNOLOGY": [
        "AI already diagnoses cancer more accurately than human doctors",
        "a modern smartphone has more computing power than NASA computers in 1969",
        "quantum computers will render all current encryption instantly breakable",
        "Neuralink brain chips already allow paralyzed patients to type with thoughts",
        "robots are projected to replace 85 million jobs worldwide by 2030",
        "the internet physically weighs the same as a medium-sized strawberry",
        "your phone collects and sells more data about you than you consciously know",
        "deepfake technology can now create a convincing video of anyone saying anything",
        "the YouTube algorithm knows what you want to watch before you do",
        "brain-computer interfaces already exist and are implanted in living humans",
        "self-driving cars process more data per second than the human brain can handle",
        "fiber optic cables transmit data as pulses of light at near light-speed",
        "the first computer bug was a literal insect trapped in a relay in 1947",
        "3D printing can now print functional human organs layer by layer",
    ],
    "ISLAMIC_SCIENCE": [
        "Ibn Sina wrote the Canon of Medicine — the world's first complete medical encyclopedia",
        "Al-Khwarizmi invented algebra 800 years before European mathematicians",
        "Ibn al-Haytham proved light enters the eye — the first correct theory of vision",
        "the Islamic Golden Age produced 90 percent of the world's scientific knowledge",
        "Al-Biruni calculated Earth's circumference in 1000 AD with only 1 percent error",
        "Muslims built the world's first university — the University of al-Qarawiyyin in 859 AD",
        "the House of Wisdom in Baghdad translated and preserved all ancient knowledge",
        "Muslim astronomers named most of the stars we still use today including Aldebaran",
        "Ibn Battuta traveled 75000 miles — more than three times Marco Polo's total distance",
        "Al-Razi correctly described blood circulation 600 years before William Harvey",
        "Islamic architecture used mathematical fractals centuries before computers could model them",
        "the Quran described the expanding universe 1400 years before Edwin Hubble's discovery",
        "Jabir ibn Hayyan founded the science of chemistry in the 8th century",
        "the word algorithm comes from the name Al-Khwarizmi — a Muslim mathematician",
    ],
    "HISTORY": [
        "the Mughal Empire controlled 25 percent of global GDP — more than Europe combined",
        "Genghis Khan's empire covered one quarter of the entire surface of the planet",
        "the Indus Valley Civilization had flush toilets and sewage systems before Rome",
        "ancient Egyptians used honey as surgical antiseptic — it still works today",
        "the Ottoman Empire lasted 600 years and is one of the most misunderstood empires",
        "the Silk Road simultaneously spread plague Buddhism Islam and the Black Death",
        "the Library of Alexandria held half a million scrolls — the internet of its time",
        "ancient Pakistan's Mohenjo-daro had planned cities 4000 years ago",
        "Tipu Sultan of Mysore invented the world's first iron-cased war rockets",
        "the Persian Empire issued the first human rights charter in history",
        "Alexander the Great's army mutinied at the Jhelum River in modern Pakistan",
        "the Battle of Plassey in 1757 changed South Asia forever in three hours",
        "the Mongol sacking of Baghdad in 1258 ended the Islamic Golden Age overnight",
        "ancient Romans had concrete that is stronger after 2000 years than modern concrete",
    ],
}

_GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.3-70b-versatile"


# ── Public entry point ────────────────────────────────────────────────────────

def select_topic_cluster(logs_dir: Path) -> dict | None:
    """
    Collects live search seeds across all 6 Obscura categories via YouTube
    autocomplete (each category has 10 search queries → ~10 suggestions each).
    Merges with YouTube Trending top-50 for virality signals.

    Picks the category with the most total seeds (= highest current search demand),
    then feeds ALL its seeds to Groq which selects 10-12 that share one central
    theme. The result is a multi-topic cluster that fills an 8-10 minute script.

    Fallback chain: autocomplete mega-cluster → YT trending cluster → single topic.
    """
    full_history = _load_full_history(logs_dir)

    # ── Step 1: Pull seeds from all 6 Obscura categories via autocomplete ───────
    log.info("Fetching autocomplete seed pool across all 6 Obscura categories…")
    autocomplete = _fetch_autocomplete_seeds()  # {cat: [(phrase, score), ...]}

    # ── Step 2: Merge YouTube Trending top-50 (virality boost) ───────────────
    yt_trending = _fetch_youtube_trending()     # {cat: [(phrase, score), ...]}

    # Combined per-category seed list: autocomplete first (search demand),
    # trending appended (virality). Deduplicated by phrase.
    combined: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        seen: set[str] = set()
        seeds: list[str] = []
        # Autocomplete: sorted by score, already the strongest demand signals
        for phrase, _ in autocomplete.get(cat, []):
            p = phrase.strip()
            if p and p not in seen:
                seen.add(p)
                seeds.append(p)
        # Trending: real viral titles from YouTube right now
        for phrase, _ in yt_trending.get(cat, []):
            p = phrase.strip()
            if p and p not in seen:
                seen.add(p)
                seeds.append(p)
        combined[cat] = seeds

    total_seeds = sum(len(v) for v in combined.values())
    log.info("Total seed pool: %d topics across %d categories", total_seeds, len(CATEGORIES))

    # ── Step 3: Rank categories by seed count — most seeds = highest demand ──
    ranked = sorted(combined, key=lambda c: len(combined.get(c, [])), reverse=True)

    # ── Step 4: Try each category from richest downward ──────────────────────
    # Use 10-12 seeds per cluster → Groq picks the most coherent subset.
    # 10 topics × 50s each ≈ 8 minutes; 12 topics × 50s each ≈ 10 minutes.
    for cat in ranked:
        seeds = combined[cat]
        if len(seeds) < 5:
            continue

        # Take up to 8 seeds and ask Groq to pick the best 5 that connect.
        # Smaller cluster = smaller prompt = fits Groq 6000 TPM free tier.
        pool_size    = min(8, len(seeds))
        cluster_size = min(5, len(seeds))

        cluster = _groq_build_cluster(cat, seeds[:pool_size], cluster_size)
        if cluster and not _is_duplicate(cluster["title"], full_history):
            cluster["source"] = "AutocompleteCluster"
            log.info(
                "Mega-cluster [%s] '%s' — %d topics → 8-10 min video",
                cat, cluster["title"][:70], len(cluster.get("topics", [])),
            )
            return cluster

    # ── Step 5: Fallback — any category with 3+ seeds ─────────────────────────
    for cat in ranked:
        seeds = combined[cat]
        if len(seeds) >= 3:
            cluster = _groq_build_cluster(cat, seeds[:8], min(5, len(seeds)))
            if cluster and not _is_duplicate(cluster["title"], full_history):
                cluster["source"] = "SmallCluster"
                log.info("Small fallback cluster [%s] '%s' (%d topics)",
                         cat, cluster["title"][:60], len(cluster.get("topics", [])))
                return cluster

    log.warning("Cluster selection failed — falling back to single topic")
    return select_topic(logs_dir)


def _build_topic_cluster(category: str, n: int, produced: list[dict]) -> dict | None:
    """Pick n related seeds from the category and ask Groq to build a cluster."""
    seeds = _SEEDS.get(category, [])
    if len(seeds) < n:
        return None

    # Sample n+2 seeds so Groq has extras to choose from
    candidates = random.sample(seeds, min(n + 2, len(seeds)))

    cluster = _groq_build_cluster(category, candidates, n)
    if not cluster:
        return None

    if _is_duplicate(cluster["title"], produced):
        log.debug("Cluster title duplicate — skipping: %s", cluster["title"][:60])
        return None

    return cluster


def _groq_build_cluster(category: str, seeds: list[str], n: int) -> dict | None:
    """
    Ask Groq to select n seeds that share ONE central theme, then generate
    an overarching title suitable for an 8-10 minute educational YouTube video.
    Returns a cluster dict ready for the script generator.
    """
    keys = [
        os.getenv("GROQ_API_KEY_1", "").strip(),
        os.getenv("GROQ_API_KEY_2", "").strip(),
        os.getenv("GROQ_API_KEY_3", "").strip(),
        os.getenv("GROQ_API_KEY_4", "").strip(),
    ]
    seeds_str = "\n".join(f"- {s}" for s in seeds)

    for key in [k for k in keys if k]:
        try:
            r = requests.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "model": _GROQ_MODEL,
                    "messages": [{
                        "role": "system",
                        "content": (
                            "You are a viral YouTube content strategist for an 8-10 minute "
                            "educational channel. Return ONLY valid JSON, no markdown, no explanation."
                        ),
                    }, {
                        "role": "user",
                        "content": (
                            f"Category: {category}\n"
                            f"Available topics ({len(seeds)} seeds):\n{seeds_str}\n\n"
                            f"Task: Select exactly {n} topics that connect to ONE central "
                            "angle — they must feel like chapters of the same deep-dive story, "
                            "not a random list. Each topic adds a surprising new layer.\n\n"
                            "Generate:\n"
                            "- title: 8-10 minute YouTube video title. "
                            "  Format options (pick the best fit):\n"
                            "  A) 'Why [Topic] Is More [Adjective] Than You Think'\n"
                            "  B) 'The [Number] Most [Category] Facts About [Topic]'\n"
                            "  C) 'What [Topic] Actually Does — The Science Explained'\n"
                            "  D) 'How [Topic] Really Works (Scientists Just Found Out)'\n"
                            "  E) '[Topic]: [Surprising Claim] | [Category] Explained'\n"
                            "  Rules: max 90 chars, front-load the topic keyword, "
                            "  no ALL CAPS, end with ONE emoji, "
                            "  BANNED words: shocking/amazing/unbelievable/mind-blowing\n"
                            "- description: 2 sentences covering the central connecting insight "
                            "  and why it matters. Include a specific number.\n"
                            "- central_angle: the unifying theme in 6-10 words\n\n"
                            "Return JSON:\n"
                            '{"title": "...", "description": "...", "central_angle": "...", '
                            f'"topics": [{{"seed": "...", "title": "...", "description": "..."}}]}} '
                            f"(exactly {n} items in topics array)"
                        ),
                    }],
                    "temperature": 0.75,
                    "max_tokens":  1800,
                },
                timeout=30,
            )
            if r.ok:
                raw = r.json()["choices"][0]["message"]["content"].strip()
                m   = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    data = json.loads(m.group())
                    if data.get("title") and isinstance(data.get("topics"), list) and data["topics"]:
                        curiosity = _curiosity_gap_score(data["title"])
                        return {
                            "title":             data["title"][:200],
                            "description":       data.get("description", "")[:500],
                            "intent":            category,
                            "source":            "Cluster",
                            "published_at":      datetime.utcnow().isoformat(),
                            "article_url":       "",
                            "seed":              data.get("central_angle", seeds[0]),
                            "trend_hint":        "",
                            "novelty_score":     50,
                            "curiosity_score":   curiosity,
                            "saturation":        "pass",
                            "viral_score":       0.0,
                            "performance_score": 50.0,
                            "competition_count": 0,
                            "central_angle":     data.get("central_angle", ""),
                            "topics":            data["topics"],
                        }
        except Exception as exc:
            log.debug("Groq cluster build: %s", exc)
    return None


def _check_news_trigger(logs_dir: Path) -> dict | None:
    """Pick up news trigger if available and not yet used today."""
    try:
        path = logs_dir / "news_trigger.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        today = datetime.utcnow().date().isoformat()
        if data.get("date") == today and not data.get("used", False):
            topic = data.get("topic")
            if topic:
                # Mark as used
                data["used"] = True
                path.write_text(json.dumps(data, indent=2))
                log.info("News trigger picked up: %s", topic["title"][:70])
                return topic
    except Exception as exc:
        log.debug("News trigger check: %s", exc)
    return None


# ── AI & Technology bonus video ───────────────────────────────────────────────
# Reliable AI/tech news RSS feeds — ordered by AI-specificity.
# The bonus video always covers current AI & Technology news (uploaded daily).
_AI_TECH_RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.technologyreview.com/feed/",
    "https://www.wired.com/feed/category/artificial-intelligence/latest/rss.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.theverge.com/rss/index.xml",
    "https://zdnet.com/topic/artificial-intelligence/rss.xml",
    "https://feeds.feedburner.com/TechCrunch",
    "https://www.engadget.com/rss.xml",
    "https://spectrum.ieee.org/feeds/feed.rss",
]

# Keywords that mark a story as AI/technology (for filtering general tech feeds)
_AI_KEYWORDS = {
    "ai", "artificial intelligence", "machine learning", "chatgpt", "gpt", "llm",
    "openai", "google gemini", "claude", "anthropic", "deepmind", "meta ai",
    "robot", "automation", "neural", "deep learning", "generative ai", "gen ai",
    "large language model", "computer vision", "self-driving", "autonomous",
    "chip", "semiconductor", "nvidia", "quantum", "cybersecurity", "drone",
    "tech", "software", "algorithm", "data center", "cloud computing", "apple",
    "microsoft", "google", "samsung", "intel", "amd", "spacex", "tesla",
}


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode basic entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    return " ".join(text.split())


def _fetch_ai_tech_news_items() -> list[tuple[str, str, str]]:
    """
    Fetch recent AI & Technology headlines from RSS feeds.
    Returns list of (title, description, link) — most recent first.
    Filters to items that are genuinely about AI or technology.
    """
    import xml.etree.ElementTree as ET

    items: list[tuple[str, str, str]] = []

    for feed_url in _AI_TECH_RSS_FEEDS:
        try:
            r = requests.get(
                feed_url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Obscura/1.0)"},
            )
            if not r.ok:
                continue
            root = ET.fromstring(r.content)

            # RSS 2.0 format
            channel = root.find("channel")
            if channel is not None:
                for item in channel.findall("item")[:8]:
                    title = _strip_html(item.findtext("title", "")).strip()
                    desc  = _strip_html(item.findtext("description", "")).strip()[:400]
                    link  = item.findtext("link", "").strip()
                    if title and len(title) > 15:
                        items.append((title, desc, link))
                continue

            # Atom format
            ns = "http://www.w3.org/2005/Atom"
            for entry in root.findall(f"{{{ns}}}entry")[:8]:
                title   = _strip_html(entry.findtext(f"{{{ns}}}title", "")).strip()
                summary = _strip_html(entry.findtext(f"{{{ns}}}summary", "")).strip()[:400]
                link_el = entry.find(f"{{{ns}}}link")
                link    = link_el.get("href", "") if link_el is not None else ""
                if title and len(title) > 15:
                    items.append((title, summary, link))

        except Exception as exc:
            log.debug("AI/tech RSS feed '%s': %s", feed_url[:50], exc)

    log.info("Bonus: fetched %d AI/tech news items from %d feeds",
             len(items), len(_AI_TECH_RSS_FEEDS))
    return items


def _is_real_ai_news(title: str, description: str, key: str) -> bool:
    """
    Verify this is real, factual AI/tech news (not opinion, satire, or rumour).
    Uses a fast Groq call with strict JSON output. Defaults to True if check fails.
    """
    title_lower = title.lower()

    # Instant heuristic rejections (no API call needed)
    skip_signals = [
        "opinion:", "editorial:", "satire", "parody", "rumor", "allegedly",
        "unverified", "report:", "exclusive:", "breaking:", "sponsored",
    ]
    if any(s in title_lower for s in skip_signals):
        return False

    # Keep items that mention at least one AI/tech keyword
    has_kw = any(kw in title_lower or kw in description.lower() for kw in _AI_KEYWORDS)
    if not has_kw:
        log.debug("Bonus: no AI/tech keyword in '%s' — skipping", title[:50])
        return False

    if not key:
        return True

    try:
        r = requests.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": _GROQ_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You verify if a tech news headline describes a real, factual "
                            "AI or technology development — not pure opinion, satire, or "
                            "unverified rumour. Respond ONLY with valid JSON: "
                            "{\"real\": true/false}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Title: {title}\nSummary: {description[:200]}",
                    },
                ],
                "temperature": 0,
                "max_tokens":  20,
            },
            timeout=10,
        )
        if r.ok:
            raw = r.json()["choices"][0]["message"]["content"].strip()
            m   = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                return json.loads(m.group()).get("real", True)
    except Exception as exc:
        log.debug("AI news verify: %s", exc)
    return True


def _ai_news_to_video_topic(news_title: str, news_desc: str, key: str) -> "dict | None":
    """
    Convert a real AI/tech news item into an Obscura YouTube video topic.
    Focuses on what this means for regular people (not just reporting the headline).
    """
    if not key:
        return None
    try:
        r = requests.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": _GROQ_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You convert real AI & technology news into educational YouTube "
                            "video topics for Obscura. The video explains what the "
                            "development MEANS for regular people — not just the headline. "
                            "Focus on: what is it, how does it actually work, why does it "
                            "matter to everyday life, what does it change.\n"
                            "RULES:\n"
                            "1. Title under 70 chars, ends with 1 emoji, no banned words "
                            "(shocking/amazing/unbelievable/nobody told you).\n"
                            "2. Title must be searchable — use the actual technology name.\n"
                            "3. Description: 2 sentences, what it does + real-world impact.\n"
                            "Return ONLY valid JSON (no markdown): "
                            "{\"title\": \"...\", \"description\": \"...\"}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"News headline: {news_title}\n"
                            f"News summary: {news_desc}\n\n"
                            "Create an Obscura YouTube video topic. Use the actual "
                            "technology/product name in the title. Explain the real impact."
                        ),
                    },
                ],
                "temperature": 0.70,
                "max_tokens":  200,
            },
            timeout=20,
        )
        if r.ok:
            raw = r.json()["choices"][0]["message"]["content"].strip()
            m   = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                data  = json.loads(m.group())
                title = data.get("title", "").strip()
                desc  = data.get("description", "").strip()
                if title and len(title) >= 10:
                    return {
                        "title":             title[:200],
                        "description":       desc[:500],
                        "intent":            "TECHNOLOGY",
                        "source":            "AITechRSS",
                        "published_at":      datetime.utcnow().isoformat(),
                        "article_url":       "",
                        "seed":              news_title[:100],
                        "trend_hint":        "",
                        "novelty_score":     70,
                        "curiosity_score":   65,
                        "saturation":        "pass",
                        "viral_score":       68.0,
                        "performance_score": 65.0,
                    }
    except Exception as exc:
        log.debug("_ai_news_to_video_topic: %s", exc)
    return None


def select_bonus_topic(logs_dir: Path) -> dict | None:
    """
    DAILY bonus video — always AI & Technology current news.
    Runs every day at 00:00 UTC (7 PM ET). No score threshold.
    Just verifies the news is real — no trending requirement.

    Priority:
      1. Existing news trigger (if already saved by news_monitor today)
      2. Latest AI/tech RSS headlines — verified real, turned into video topic
      3. Groq-generated fresh AI/tech topic (when RSS unavailable)
    """
    full_history = _load_full_history(logs_dir)
    groq_key     = next(
        (k for k in [os.getenv("GROQ_API_KEY_1", "").strip(),
                     os.getenv("GROQ_API_KEY_2", "").strip(),
                     os.getenv("GROQ_API_KEY_3", "").strip(),
                     os.getenv("GROQ_API_KEY_4", "").strip()] if k),
        None,
    )

    # Priority 1: News trigger saved by the hourly news_monitor (if present today)
    news_topic = _check_news_trigger(logs_dir)
    if news_topic:
        log.info("Bonus: existing trigger used → %s", news_topic["title"][:70])
        return news_topic

    # Priority 2: Fetch today's real AI & Technology news from RSS feeds
    news_items = _fetch_ai_tech_news_items()
    for title, description, link in news_items:
        if _is_duplicate(title, full_history):
            log.debug("Bonus: duplicate skip '%s'", title[:50])
            continue
        if not _is_real_ai_news(title, description, groq_key):
            log.debug("Bonus: fake/opinion filter '%s'", title[:50])
            continue
        topic = _ai_news_to_video_topic(title, description, groq_key)
        if topic:
            topic["article_url"] = link
            log.info("Bonus AI/tech topic from RSS: %s", topic["title"][:70])
            return topic

    # Priority 3: RSS unavailable — Groq generates fresh AI/tech topic
    log.warning("Bonus: RSS fetch failed — generating fresh AI/tech topic via Groq")
    ai_seeds = [
        "artificial intelligence", "large language model", "robotics",
        "computer vision", "autonomous vehicles", "quantum computing",
        "cybersecurity", "generative ai", "semiconductor technology",
        "neural network", "machine learning breakthrough",
    ]
    random.shuffle(ai_seeds)
    for seed in ai_seeds[:6]:
        topic = _build_topic("TECHNOLOGY", seed, full_history, "latest AI developments")
        if topic:
            topic["source"] = "AITechFallback"
            log.info("Bonus fallback topic: %s", topic["title"][:70])
            return topic

    log.error("Bonus: all topic sources failed")
    return None


def select_topic(logs_dir: Path) -> dict | None:
    produced_today  = _load_produced_today(logs_dir)
    full_history    = _load_full_history(logs_dir)
    used_categories = {v.get("intent", "") for v in produced_today}
    perf_weights    = _load_performance_weights(logs_dir)

    # Priority 0: INTENT_OVERRIDE env var — forces a specific category
    override = os.getenv("INTENT_OVERRIDE", "").strip().upper()
    if override and override in CATEGORIES:
        log.info("INTENT_OVERRIDE=%s — forcing category", override)
        seeds = _SEEDS.get(override, [])
        random.shuffle(seeds)
        for seed in seeds[:6]:
            topic = _build_topic(override, seed, full_history, "")
            if topic:
                topic["source"] = "IntentOverride"
                return topic
        # Fallback: bypass filters for forced category
        seed = random.choice(_SEEDS.get(override, ["facts"]))
        title, description = _groq_expand(override, seed, "")
        if not title:
            title = f"The Incredible Truth About {seed.title()}"
            description = f"Fascinating facts about {seed}."
        return {
            "title": title[:200], "description": description[:500],
            "intent": override, "source": "IntentOverride-Fallback",
            "published_at": datetime.utcnow().isoformat(),
            "article_url": "", "seed": seed, "trend_hint": "",
            "novelty_score": 50, "curiosity_score": 0,
            "saturation": "pass", "viral_score": 0.0,
            "performance_score": 50.0,
        }

    # Priority 1: Velocity cluster queue — follow-up topics from viral videos
    velocity_queue = _load_velocity_queue(logs_dir)
    for item in velocity_queue:
        cat  = item.get("category", "")
        seed = item.get("seed", "")
        if not cat or not seed or cat in used_categories:
            continue
        topic = _build_topic(cat, seed, full_history, "")
        if topic:
            _consume_velocity_item(logs_dir, item)
            topic["source"] = "VelocityCluster"
            log.info("Velocity cluster [%s]: %s", cat, topic["title"][:80])
            return topic

    # Priority 1b: Topic cluster sequences — follow-up to viral video chains
    cluster_topic = _next_cluster_topic(logs_dir, used_categories)
    if cluster_topic:
        return cluster_topic

    # Priority 2: YouTube Autocomplete — real-time search demand.
    # These are EXACTLY what people are typing into YouTube right now.
    # Use the search phrase directly as the topic seed so the title matches
    # what people search for — highest chance of appearing in search results.
    autocomplete_seeds = _fetch_autocomplete_seeds()
    # Flatten and sort all autocomplete results by score
    all_ac: list[tuple[str, str, float]] = []
    for cat, items in autocomplete_seeds.items():
        for phrase, score in items:
            all_ac.append((cat, phrase, score))
    all_ac.sort(key=lambda x: x[2], reverse=True)

    for cat, phrase, _ in all_ac[:30]:
        # is_search_phrase=True → _build_topic uses _groq_title_from_search
        # which KEEPS the autocomplete keywords in the title so the video
        # ranks for the exact search that generated this phrase.
        topic = _build_topic(cat, phrase, full_history, "", is_search_phrase=True)
        if topic:
            topic["source"]       = "YouTubeSearch"
            topic["search_query"] = phrase
            log.info("Search demand [%s] phrase='%s' → '%s'",
                     cat, phrase[:50], topic["title"][:60])
            return topic

    # Priority 3: YouTube trending tells us WHICH CATEGORY is hot right now,
    # but we pick the actual topic from curated broad _SEEDS (not the trending
    # video title itself — those are niche/competitive and already covered by
    # big channels). Broad evergreen seeds get wide reach; trending category
    # gives us the timing signal.
    yt_trending = _fetch_youtube_trending()
    all_yt: list[tuple[str, str, float]] = []   # (cat, seed, score)
    for cat, items in yt_trending.items():
        for seed, score in items:
            all_yt.append((cat, seed, score))
    all_yt.sort(key=lambda x: x[2], reverse=True)  # rank 1 first

    # Dedupe to top category order (highest total trending score per category)
    seen_cats: list[str] = []
    for cat, _, _ in all_yt:
        if cat not in seen_cats:
            seen_cats.append(cat)

    for cat in seen_cats[:8]:
        # Use broad curated seeds — not the trending niche title
        broad_seeds = list(_SEEDS.get(cat, []))
        random.shuffle(broad_seeds)
        for seed in broad_seeds[:10]:
            topic = _build_topic(cat, seed, full_history, "")
            if topic:
                topic["source"] = "YouTubeTrending+BroadSeed"
                log.info("Broad topic [%s]: %s", cat, topic["title"][:80])
                return topic

    # All categories exhausted — last resort, bypass all filters
    log.info("All categories exhausted — generating fresh angle (filters bypassed)")
    cat = all_yt[0][0] if all_yt else random.choice(CATEGORIES)
    seed = random.choice(_SEEDS[cat])

    # Try Groq first
    title, description = _groq_expand(cat, seed, "")
    if not title:
        title       = f"The Incredible Truth About {seed.title()}"
        description = f"Fascinating and little-known facts about {seed}."

    # Return directly — no duplicate check, no saturation, no curiosity filter
    # This is the pipeline's safety net and must always produce a topic
    return {
        "title":             title[:200],
        "description":       description[:500],
        "intent":            cat,
        "source":            "Obscura-Fallback",
        "published_at":      datetime.utcnow().isoformat(),
        "article_url":       "",
        "seed":              seed,
        "trend_hint":        "",
        "novelty_score":     50,
        "curiosity_score":   0,
        "saturation":        "pass",
        "viral_score":       0.0,
        "performance_score": 50.0,
    }


# ── Algorithm 1 — Trending Topic Arbitrage ────────────────────────────────────

def _fetch_trending_seeds(logs_dir: Path) -> dict[str, list[tuple[str, float]]]:
    """
    Returns {category: [(fact_topic, score), ...]} from current science/facts RSS feeds.
    Replaces Google Trends (celebrity/sports noise) with real educational fact sources:
    NASA, ScienceDaily, LiveScience, Space.com, BBC Science, PhysOrg, etc.
    Results cached for _TREND_CACHE_TTL_HOURS to avoid repeat fetches.
    """
    cached = _load_trend_cache(logs_dir)
    if cached:
        log.info("Trend cache hit — skipping facts RSS fetch")
        return {cat: [tuple(x) for x in v] for cat, v in cached.items()}

    results: dict[str, list[tuple[str, float]]] = {cat: [] for cat in CATEGORIES}

    # Current science/facts RSS feeds — free, no auth, always educational content
    _FACTS_RSS = [
        ("NASA",        "https://www.nasa.gov/rss/dyn/breaking_news.rss"),
        ("ScienceDaily","https://www.sciencedaily.com/rss/all.xml"),
        ("LiveScience", "https://www.livescience.com/feeds/all"),
        ("Space",       "https://www.space.com/feeds/all"),
        ("PhysOrg",     "https://phys.org/rss-feed/"),
        ("BBCSci",      "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
        ("NewSci",      "https://www.newscientist.com/feed/home/"),
        ("EarthSky",    "https://earthsky.org/feed"),
        ("NatGeo",      "https://www.nationalgeographic.com/pages/topic/rss"),
        ("Smithsonian", "https://www.smithsonianmag.com/rss/latest_articles/"),
    ]

    import xml.etree.ElementTree as ET
    seen_titles: dict[str, int]   = {}   # title → feed count (cross-feed = stronger)
    best_position: dict[str, int] = {}   # title → lowest (best) position seen

    for feed_name, url in _FACTS_RSS:
        try:
            r = requests.get(url, timeout=12,
                             headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                log.debug("Facts RSS [%s]: HTTP %d", feed_name, r.status_code)
                continue
            root  = ET.fromstring(r.content)
            items = root.findall(".//item")[:20]
            for pos, item in enumerate(items):
                title = (item.findtext("title") or "").strip().lower()
                desc  = (item.findtext("description") or "")[:200].lower()
                text  = f"{title} {desc}"
                if not title or len(title) < 10:
                    continue
                seen_titles[text] = seen_titles.get(text, 0) + 1
                if text not in best_position or pos < best_position[text]:
                    best_position[text] = pos
            log.debug("Facts RSS [%s]: %d items", feed_name, len(items))
        except Exception as exc:
            log.debug("Facts RSS [%s]: %s", feed_name, exc)

    # Score: 95 base + 45 per extra feed + rank bonus (50 → 0 over 20 positions)
    for text, count in seen_titles.items():
        pos        = best_position.get(text, 19)
        rank_bonus = max(0.0, 50.0 * (1.0 - pos / 19.0))
        score      = 95.0 + 45.0 * (count - 1) + rank_bonus
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                results[cat].append((text[:120], score))
                break

    cross_feed = sum(1 for c in seen_titles.values() if c > 1)
    total_mapped = sum(len(v) for v in results.values())
    log.info("Facts RSS: %d topics (%d cross-feed) mapped across categories",
             total_mapped, cross_feed)

    _save_trend_cache(logs_dir, {cat: list(v) for cat, v in results.items()})
    log.info("Current facts RSS: %d topics fetched across %d categories",
             total_mapped, len(CATEGORIES))

    return results


def _rank_seeds(cat: str, trending: list[tuple[str, float]],
                autocomplete: list[tuple[str, float]] | None = None) -> list[str]:
    """Trending + autocomplete seeds (score-ranked) prepended to shuffled static seeds."""
    all_dynamic = trending + (autocomplete or [])
    ranked      = [t for t, _ in sorted(all_dynamic, key=lambda x: x[1], reverse=True)]
    static      = _SEEDS[cat].copy()
    random.shuffle(static)
    seen        = {t.lower() for t in ranked}
    static      = [s for s in static if s.lower() not in seen]
    return ranked + static


def _load_trend_cache(logs_dir: Path) -> dict:
    try:
        path = logs_dir / "trend_cache.json"
        if not path.exists():
            return {}
        data       = json.loads(path.read_text())
        cached_at  = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        age_hours  = (datetime.utcnow() - cached_at).total_seconds() / 3600
        if age_hours > _TREND_CACHE_TTL_HOURS:
            return {}
        return data.get("trends", {})
    except Exception:
        return {}


def _save_trend_cache(logs_dir: Path, trends: dict) -> None:
    try:
        path = logs_dir / "trend_cache.json"
        path.write_text(json.dumps({
            "cached_at": datetime.utcnow().isoformat(),
            "trends":    trends,
        }))
    except Exception:
        pass


# ── Algorithm: YouTube Autocomplete Keyword Mining ────────────────────────────

_AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"

def _fetch_autocomplete_seeds() -> dict[str, list[tuple[str, float]]]:
    """
    Queries YouTube's public autocomplete API for each category using multiple
    search terms per category. Returns real-time search demand — exactly what
    people are typing into YouTube right now.

    Scoring: position 0 = 95 pts, position 1 = 85, ..., decaying by 10 per slot.
    Multiple queries per category are merged; duplicates deduplicated by keeping
    the highest score. Results sorted by score descending.
    """
    results: dict[str, list[tuple[str, float]]] = {cat: [] for cat in CATEGORIES}

    for cat in CATEGORIES:
        queries   = _CATEGORY_SEARCH_TERMS.get(cat, [])
        seen: dict[str, float] = {}

        for query in queries:
            try:
                r = requests.get(
                    _AUTOCOMPLETE_URL,
                    params={"ds": "yt", "client": "firefox", "q": query, "hl": "en"},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=8,
                )
                if r.ok:
                    data        = r.json()
                    suggestions = data[1] if len(data) > 1 else []
                    for i, suggestion in enumerate(suggestions[:9]):
                        phrase = str(suggestion).strip()
                        score  = max(95.0 - i * 10.0, 5.0)
                        if phrase not in seen or seen[phrase] < score:
                            seen[phrase] = score
            except Exception as exc:
                log.debug("Autocomplete [%s] '%s': %s", cat, query[:30], exc)
            time.sleep(0.2)

        results[cat] = sorted(seen.items(), key=lambda x: x[1], reverse=True)
        log.debug("Autocomplete [%s]: %d unique suggestions", cat, len(results[cat]))

    total = sum(len(v) for v in results.values())
    log.info("Autocomplete: %d live search suggestions across all categories", total)
    return results


# ── Algorithm: YouTube Trending Top 50 ───────────────────────────────────────

def _fetch_youtube_trending() -> dict[str, list[tuple[str, float]]]:
    """
    Fetches the top 50 currently trending YouTube videos (mostPopular chart).
    Uses YOUTUBE_API_KEY — 1 quota unit, very cheap.

    Scoring: rank 1 = 100 pts down to rank 50 ≈ 8 pts (linear).
    Topics mapped to categories via _CATEGORY_KEYWORDS.
    Returns {category: [(topic_phrase, score), ...]}
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return {}

    results: dict[str, list[tuple[str, float]]] = {cat: [] for cat in CATEGORIES}
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "key":        api_key,
                "chart":      "mostPopular",
                "regionCode": "US",
                "maxResults": 50,
                "part":       "snippet",
            },
            timeout=12,
        )
        if not r.ok:
            log.debug("YouTube trending: HTTP %d", r.status_code)
            return {}

        items = r.json().get("items", [])
        for pos, item in enumerate(items):
            snippet = item.get("snippet", {})
            title   = snippet.get("title", "").strip().lower()
            desc    = snippet.get("description", "")[:200].lower()
            text    = f"{title} {desc}"

            # rank 1 = 100, rank 50 ≈ 8, linear decay
            score = max(8.0, 100.0 * (1.0 - pos / max(len(items) - 1, 1)))

            for cat, keywords in _CATEGORY_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    # Use title as seed — it's already a proved viral phrase
                    results[cat].append((title, score))
                    break

        mapped = sum(len(v) for v in results.values())
        log.info("YouTube Trending top-%d: %d videos mapped to categories",
                 len(items), mapped)
    except Exception as exc:
        log.debug("YouTube trending fetch: %s", exc)

    return results


# ── Topic Velocity Clustering — queue helpers ─────────────────────────────────

def _next_cluster_topic(logs_dir: Path, used_categories: set) -> dict | None:
    """
    Returns the next topic in an active cluster chain if one exists.
    Updates current_idx in topic_clusters.json after consuming a topic.
    """
    try:
        clusters_path = logs_dir / "topic_clusters.json"
        if not clusters_path.exists():
            return None
        clusters = json.loads(clusters_path.read_text())
        changed  = False

        for cluster in clusters:
            cat = cluster.get("category", "")
            if cat in used_categories:
                continue
            idx   = cluster.get("current_idx", 0)
            chain = cluster.get("chain", [])
            if idx >= len(chain):
                continue  # cluster exhausted

            seed = chain[idx]
            full_history = []  # cluster topics bypass deduplication for simplicity

            topic = _build_topic(cat, seed, full_history, "")
            if topic:
                cluster["current_idx"] = idx + 1
                changed = True
                topic["source"] = "TopicCluster"
                log.info("Topic cluster [%s] step %d/%d: %s",
                         cat, idx + 1, len(chain), topic["title"][:60])
                if changed:
                    clusters_path.write_text(json.dumps(clusters, indent=2))
                return topic

    except Exception as exc:
        log.debug("Cluster topic check: %s", exc)
    return None


_VELOCITY_TTL_HOURS = 72

def _load_velocity_queue(logs_dir: Path) -> list[dict]:
    """Returns pending high-priority seeds younger than 72 hours from velocity_queue.json."""
    try:
        path = logs_dir / "velocity_queue.json"
        if not path.exists():
            return []
        entries = json.loads(path.read_text())
        cutoff  = (datetime.utcnow().timestamp()) - (_VELOCITY_TTL_HOURS * 3600)
        fresh   = []
        for e in entries:
            # Support both Unix ts field (new) and ISO queued_at field (legacy)
            ts = e.get("ts")
            if ts is None:
                try:
                    ts = datetime.fromisoformat(e["queued_at"]).timestamp()
                except Exception:
                    ts = cutoff + 1  # unknown age — treat as fresh
            if ts >= cutoff:
                fresh.append(e)
        if len(fresh) < len(entries):
            log.info("Velocity queue: evicted %d stale entries (>72h old)",
                     len(entries) - len(fresh))
        return fresh
    except Exception:
        return []


def _consume_velocity_item(logs_dir: Path, item: dict) -> None:
    """Remove a consumed item from velocity_queue.json."""
    try:
        path  = logs_dir / "velocity_queue.json"
        queue = json.loads(path.read_text()) if path.exists() else []
        queue = [q for q in queue
                 if not (q.get("seed") == item.get("seed")
                         and q.get("queued_at") == item.get("queued_at"))]
        path.write_text(json.dumps(queue, indent=2))
    except Exception:
        pass

# ── Existing helpers (unchanged) ──────────────────────────────────────────────

def _load_comment_boost() -> dict[str, int]:
    try:
        p = Path(__file__).parent.parent / "logs" / "auto_fixes.json"
        if p.exists():
            return json.loads(p.read_text()).get("category_boost", {})
    except Exception:
        pass
    return {}


def _recent_category_counts(logs_dir: Path, n: int = 30) -> dict[str, int]:
    """Count how many times each category appears in the last n uploaded videos."""
    try:
        path = logs_dir / "video_results.json"
        if not path.exists():
            return {}
        results = json.loads(path.read_text())
        counts: dict[str, int] = {}
        for r in results[-n:]:
            cat = r.get("intent", "")
            if cat:
                counts[cat] = counts.get(cat, 0) + 1
        return counts
    except Exception:
        return {}


def _prioritise_categories(
    cats: list[str],
    used_today: set[str],
    weights: dict[str, float],
    logs_dir: Path | None = None,
    trending_seeds: dict | None = None,
) -> list[str]:
    available = [c for c in cats if c not in used_today]
    if not available:
        available = list(cats)

    boost         = _load_comment_boost()
    recent_counts = _recent_category_counts(logs_dir, n=30) if logs_dir else {}

    # Trending bonus: categories with active Google Trends hits get a +0 to +65
    # score boost so they rise above frequency-penalised categories.
    # Raw trend scores are 95-185 (95 base + 45/extra market); normalise to 0-65.
    trend_bonus: dict[str, float] = {}
    if trending_seeds:
        for cat, seeds in trending_seeds.items():
            if seeds:
                top = max(s for _, s in seeds[:3])      # best of top 3
                trend_bonus[cat] = min((top - 95) / 2, 65)  # normalised 0-65

    def _score(c: str) -> float:
        perf     = weights.get(c, 50.0)                      # 0-100 retention %
        cb       = boost.get(c, 0) * 5                       # viewer request boost
        freq_pen = min(recent_counts.get(c, 0), 5) * 11      # 0-55 penalty
        freq_bon = 30 if recent_counts.get(c, 0) == 0 else 0 # never-used bonus
        trend    = trend_bonus.get(c, 0)                     # 0-65 trending bonus
        return perf + cb - freq_pen + freq_bon + trend

    # Shuffle BEFORE sorting so equal-score categories get random order rather
    # than always putting SPACE first (SPACE is first in the CATEGORIES list).
    random.shuffle(available)
    available.sort(key=_score, reverse=True)
    return available


def _fetch_trending_hints() -> dict[str, str]:
    """Use Groq to fetch one AI-generated trending angle hint per category."""
    keys = [os.getenv("GROQ_API_KEY_1", "").strip(), os.getenv("GROQ_API_KEY_2", "").strip(), os.getenv("GROQ_API_KEY_3", "").strip(), os.getenv("GROQ_API_KEY_4", "").strip()]
    for key in keys:
        if not key:
            continue
        try:
            cats_str = ", ".join(CATEGORIES)
            r = requests.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": _GROQ_MODEL,
                    "messages": [{
                        "role": "system",
                        "content": (
                            "You are a viral content strategist. "
                            "Return ONLY valid JSON, no markdown. "
                            'Format: {"CATEGORY": "hint"} for each category.'
                        ),
                    }, {
                        "role": "user",
                        "content": (
                            f"For each category: {cats_str}\n"
                            "Give ONE trending angle in 8-12 words — fresh, specific, "
                            "currently viral on YouTube in 2025. Focus on recent discoveries, "
                            "counterintuitive facts, or newly revealed historical truths."
                        ),
                    }],
                    "temperature": 0.7,
                    "max_tokens": 400,
                },
                timeout=15,
            )
            if r.ok:
                raw = r.json()["choices"][0]["message"]["content"].strip()
                m   = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    data = json.loads(m.group())
                    if isinstance(data, dict):
                        return data
        except Exception as exc:
            log.debug("Trend hints: %s", exc)
    return {}


def _wikipedia_novelty_score(topic: str) -> int:
    """
    Two-signal novelty score (0-100):
      30% — lexical signal: presence of discovery/novelty keywords in Wikipedia extract
      70% — recency signal: days since last Wikipedia page edit (100=today, 0=90+ days)
    Free, no API key required.
    Returns 50 (neutral) on any error so it never blocks the pipeline.
    """
    try:
        # Recency signal: check last edit date via recentchanges API
        rc = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "recentchanges",
                    "rctitle": topic, "rclimit": "1", "format": "json"},
            timeout=8,
        )
        recency_score = 0
        if rc.ok:
            changes = rc.json().get("query", {}).get("recentchanges", [])
            if changes:
                from datetime import datetime, timezone
                ts = changes[0].get("timestamp", "")
                if ts:
                    edited = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    days_ago = (datetime.now(timezone.utc) - edited).days
                    recency_score = max(0, int(100 - (days_ago / 90) * 100))

        # Lexical signal: check extract for discovery keywords
        ex = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "titles": topic,
                    "prop": "extracts", "exintro": True,
                    "explaintext": True, "format": "json"},
            timeout=8,
        )
        lexical_score = 0
        if ex.ok:
            pages = ex.json().get("query", {}).get("pages", {})
            extract = " ".join(
                p.get("extract", "") for p in pages.values()
            ).lower()
            novelty_words = [
                "discovered", "revealed", "hidden", "secret", "ancient",
                "impossible", "mystery", "unknown", "lost", "forbidden",
                "breakthrough", "first", "new", "recent", "confirmed",
            ]
            hits = sum(1 for w in novelty_words if w in extract)
            lexical_score = min(100, hits * 15)

        score = int(lexical_score * 0.30 + recency_score * 0.70)
        log.debug("Novelty score for '%s': %d (lexical=%d recency=%d)",
                  topic[:40], score, lexical_score, recency_score)
        return score

    except Exception as exc:
        log.debug("Wikipedia novelty check: %s", exc)
        return 50  # neutral fallback


_CURIOSITY_GAP_PATTERNS = [
    # Curiosity gap: implies hidden/forbidden knowledge
    (r"scientists? found|discovered|revealed|hidden|secret|nobody told|"
     r"never taught|forbidden|they don.?t want|suppressed|covered up", 30),
    # Surprise: violates expectation
    (r"impossible|defies|shouldn.?t|can.?t exist|shouldn.?t be possible|"
     r"breaks (the )?rules|shouldn.?t work|against (all )?odds", 25),
    # Contradiction: attacks widely-held belief
    (r"everything.*(wrong|false|lie)|wrong about|myth|actually|"
     r"contrary to|opposite of|turns out|in fact", 20),
    # Mystery: open question
    (r"why|how (is it possible|does|could)|what (really |actually )?happen|"
     r"mystery|no.?one knows|still unknown|unexplained|unsolved", 15),
    # Specificity: exact numbers / real places / real science
    (r"\d[\d,]*(\.\d+)?\s*(km|miles?|ton|year|second|billion|million|"
     r"percent|degree|meter|kg|lb)", 10),
]


def _curiosity_gap_score(title: str) -> int:
    """
    Score a title 0-100 on curiosity-gap psychology.
    Titles below 30 are likely generic ("Amazing Facts") and should be rejected.
    Titles above 70 are strong candidates.
    """
    title_lower = title.lower()
    score = 0
    for pattern, pts in _CURIOSITY_GAP_PATTERNS:
        if re.search(pattern, title_lower):
            score += pts
    return min(100, score)


def _check_saturation(title: str) -> str:
    """
    Returns 'pass', 'penalty', or 'reject' based on view velocity of top 10 results.
    Uses YOUTUBE_API_KEY (research project) — does NOT count against upload quota.
    Falls back to 'pass' when API key is missing.
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return "pass"
    try:
        # Step 1: search top 10 results for this title
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": api_key, "q": title,
                "type": "video", "order": "relevance",
                "maxResults": "10", "part": "id",
            },
            timeout=10,
        )
        if not r.ok:
            return "pass"
        items = r.json().get("items", [])
        if not items:
            return "pass"

        video_ids = ",".join(i["id"]["videoId"] for i in items if "videoId" in i.get("id", {}))
        if not video_ids:
            return "pass"

        # Step 2: fetch view counts (videos.list = 1 unit — very cheap)
        s = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api_key, "id": video_ids,
                    "part": "statistics", "maxResults": "10"},
            timeout=10,
        )
        if not s.ok:
            return "pass"

        view_counts = []
        for item in s.json().get("items", []):
            vc = item.get("statistics", {}).get("viewCount")
            if vc:
                view_counts.append(int(vc))

        if not view_counts:
            return "pass"

        view_counts.sort()
        median = view_counts[len(view_counts) // 2]

        if median > _SATURATION_MEDIAN_PENALTY:
            log.debug("Saturation: median views=%d — REJECT (>500k)", median)
            return "reject"
        if median > _SATURATION_MEDIAN_PASS:
            log.debug("Saturation: median views=%d — penalty (100k-500k)", median)
            return "penalty"
        log.debug("Saturation: median views=%d — pass (<100k)", median)
        return "pass"

    except Exception as exc:
        log.debug("Saturation check error: %s", exc)
        return "pass"


def _count_youtube_results(query: str, api_key: str) -> int:
    """Count how many YouTube videos exist for a search query."""
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": api_key, "q": query,
                "type": "video", "part": "id",
                "maxResults": "50",
            },
            timeout=10,
        )
        if r.ok:
            return r.json().get("pageInfo", {}).get("totalResults", 999999)
    except Exception:
        pass
    return 999999


def _find_best_seo_title(base_title: str, seed: str, category: str) -> tuple[str, int]:
    """
    Generate title variants with low-competition modifiers.
    Returns (best_title, result_count) — lowest competition wins.

    Low competition = few existing YouTube videos = easy to rank #1.
    """
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return base_title, 999999

    year = datetime.utcnow().year
    cat  = category.title()

    # Generate variants with low-competition modifiers
    variants = [
        base_title,
        f"{base_title} {year}",
        f"{seed.title()} Facts {year}",
        f"{seed.title()} Facts Nobody Knows",
        f"The Truth About {seed.title()} {year}",
        f"{cat} Facts {year} That Will Shock You",
        f"Why {seed.title()} Will Surprise You {year}",
    ]

    best_title  = base_title
    best_count  = 999999

    for variant in variants[:5]:  # check max 5 variants
        count = _count_youtube_results(variant, api_key)
        log.debug("SEO check '%s': %d results", variant[:50], count)
        if count < best_count:
            best_count = count
            best_title = variant
        time.sleep(0.3)  # gentle rate limiting

    log.info("SEO: best title '%s' (%d competing videos)",
             best_title[:60], best_count)
    return best_title[:200], best_count


def _groq_title_from_search(phrase: str, category: str, wiki_summary: str = "") -> tuple[str, str]:
    """
    Take an actual YouTube search phrase a real person typed and turn it into
    a video title that KEEPS the exact keywords so the video ranks for that search.
    This is different from _groq_expand which invents a fresh angle — here we
    honour the search intent and reformat only for CTR.
    """
    keys = [os.getenv("GROQ_API_KEY_1", "").strip(), os.getenv("GROQ_API_KEY_2", "").strip(), os.getenv("GROQ_API_KEY_3", "").strip(), os.getenv("GROQ_API_KEY_4", "").strip()]
    for key in keys:
        if not key:
            continue
        try:
            r = requests.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": _GROQ_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a YouTube SEO title specialist. A real person typed a search "
                                "phrase into YouTube. Your job is to turn that exact phrase into a "
                                "compelling video title that ranks for that search.\n\n"
                                "STRICT RULES:\n"
                                "1. KEEP the core keywords from the search phrase — do NOT swap them "
                                "for synonyms or change the main subject. The keywords are what make "
                                "the video appear in that person's search results.\n"
                                "2. Reformat for click-through: rephrase the structure if needed, "
                                "add a specific number or power word ONLY if it fits naturally.\n"
                                "3. Under 70 characters total. End with exactly 1 relevant emoji.\n"
                                "4. If phrase is a question (starts with why/how/what/where), keep it "
                                "as a question — questions get 40% more clicks on YouTube.\n"
                                "5. No ALL CAPS. No banned phrases: shocking/amazing/mind-blowing/"
                                "unbelievable/nobody told you/they don't want you to know.\n"
                                "6. The title must describe something the video can SPECIFICALLY answer.\n"
                                "7. Front-load the topic keyword in the first 40 characters.\n\n"
                                "Return ONLY valid JSON (no markdown): "
                                "{\"title\": \"...\", \"description\": \"one sentence with the "
                                "most surprising specific fact about this exact topic\"}"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Search phrase (what a real viewer typed): \"{phrase}\"\n"
                                f"Category: {category}\n"
                                + (f"Verified facts to use: {wiki_summary[:280]}\n" if wiki_summary else "")
                                + "Generate a YouTube title. KEEP the core keywords. Make it specific "
                                "and clickable. The title must rank for this exact search phrase."
                            ),
                        },
                    ],
                    "temperature": 0.60,
                    "max_tokens":  160,
                },
                timeout=20,
            )
            if r.ok:
                raw = r.json()["choices"][0]["message"]["content"].strip()
                m   = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    data  = json.loads(m.group())
                    title = data.get("title", "").strip()
                    desc  = data.get("description", "").strip()
                    if title and len(title) >= 10:
                        log.debug("Search-matched title for '%s': %s", phrase[:40], title[:60])
                        return title, desc
        except Exception as exc:
            log.debug("_groq_title_from_search: %s", exc)
    return "", ""


def _build_topic(category: str, seed: str, produced: list[dict],
                 trend_hint: str = "", is_search_phrase: bool = False) -> dict | None:
    # Wikipedia verification first — skip topic if no article found
    wiki_summary = _wikipedia_verify(seed)
    if not wiki_summary:
        log.debug("Wikipedia: no article for '%s' — skipping", seed[:40])
        return None
    log.debug("Wikipedia verified seed '%s'", seed[:40])

    # If seed IS a real human search phrase (≥4 words or explicitly flagged),
    # keep its keywords in the title so the video ranks for that exact search.
    # _groq_title_from_search preserves the search phrase keywords.
    # _groq_expand invents a fresh angle — good for seeds, bad for search phrases.
    if is_search_phrase or len(seed.split()) >= 4:
        title, description = _groq_title_from_search(seed, category, wiki_summary)
        if not title:
            title, description = _groq_expand(category, seed, trend_hint)
    else:
        title, description = _groq_expand(category, seed, trend_hint)

    if not title:
        title       = f"Incredible Facts About {seed.title()}"
        description = f"Fascinating and little-known facts about {seed}."

    if _is_duplicate(title, produced):
        log.debug("Duplicate — skipping: %s", title[:60])
        return None

    saturation = _check_saturation(title)
    if saturation == "reject":
        log.debug("Saturation reject (high competition): %s", title[:60])
        return None

    # SEO upgrade — find lowest competition title variant
    title, competition_count = _find_best_seo_title(title, seed, category)

    # Curiosity gap validation — reject generic titles
    curiosity = _curiosity_gap_score(title)
    if curiosity < 30:
        log.debug("Curiosity gap reject (score=%d): %s", curiosity, title[:60])
        return None

    # Wikipedia novelty score — warn if stale but don't hard-block
    novelty = _wikipedia_novelty_score(category + " " + seed)
    if novelty < 20:
        log.debug("Low novelty score (%d) for: %s — proceeding with warning", novelty, title[:60])

    # Sub-topic performance signal (from analytics history)
    performance_score = _subtopic_performance_score(seed, category)

    # Combined viral opportunity score (0-100)
    # Weights: trend 30%, search 20%, novelty 15%, curiosity 15%, performance 10%, saturation 10%
    saturation_bonus = 10 if saturation == "pass" else (5 if saturation == "penalty" else 0)
    trend_score      = min(100, float(trend_hint[:3].strip()) if trend_hint and trend_hint[:3].isdigit() else 50)
    viral_score      = (
        0.30 * trend_score
      + 0.20 * 50             # search score placeholder (autocomplete already used for ranking)
      + 0.15 * novelty
      + 0.15 * curiosity
      + 0.10 * performance_score
      + 0.10 * saturation_bonus * 10
    )
    log.debug("Viral score for '%s': %.1f (novelty=%d curiosity=%d perf=%.0f)",
              title[:50], viral_score, novelty, curiosity, performance_score)

    return {
        "title":            title[:200],
        "description":      description[:500],
        "intent":           category,
        "source":           "Obscura",
        "published_at":     datetime.utcnow().isoformat(),
        "article_url":      "",
        "seed":             seed,
        "trend_hint":       trend_hint[:100] if trend_hint else "",
        "novelty_score":    novelty,
        "curiosity_score":  curiosity,
        "saturation":       saturation,
        "viral_score":        round(viral_score, 1),
        "performance_score":  performance_score,
        "wiki_summary":       wiki_summary,
        "competition_count":  competition_count,
    }


def _wikipedia_verify(seed: str) -> str:
    """
    Searches Wikipedia for the seed topic and returns a verified 1-3 sentence
    summary. Tries 5 fallback levels so trending seeds like "nasa asteroid 2025"
    still resolve. Returns empty string only if all attempts fail.
    """
    words = [w for w in seed.strip().split() if w]
    _stopwords = {"the", "a", "an", "of", "in", "on", "at", "is", "are", "was",
                  "were", "and", "or", "to", "for", "with", "by", "from", "that"}
    content_words = [w for w in words if w.lower() not in _stopwords]

    attempts: list[str] = [seed]                       # level 1: full seed
    if len(words) > 4:
        attempts.append(" ".join(words[:4]))           # level 2: first 4 words
    if len(words) > 3:
        attempts.append(" ".join(words[:3]))           # level 3: first 3 words
    if len(words) > 2:
        attempts.append(" ".join(words[:2]))           # level 4: first 2 words
    # level 5: most significant single word (longest content word, skip pure numbers)
    sig = max(content_words, key=lambda w: len(w) if not w.isdigit() else 0, default="")
    if sig and sig not in attempts:
        attempts.append(sig)

    # Deduplicate while preserving order
    seen: list[str] = []
    for a in attempts:
        if a and a not in seen:
            seen.append(a)
    attempts = seen

    for query in attempts:
        try:
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": query,
                        "srlimit": "1", "format": "json"},
                timeout=8,
            )
            if not r.ok:
                continue
            results = r.json().get("query", {}).get("search", [])
            if not results:
                continue
            page_title = results[0]["title"]
            ex = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "titles": page_title,
                        "prop": "extracts", "exintro": True,
                        "explaintext": True, "exsentences": 3, "format": "json"},
                timeout=8,
            )
            if not ex.ok:
                continue
            pages = ex.json().get("query", {}).get("pages", {})
            for page in pages.values():
                extract = page.get("extract", "").strip()
                if extract and len(extract) > 50:
                    if query != seed:
                        log.debug("Wikipedia: simplified retry succeeded for '%s'", seed[:40])
                    return extract[:400]
        except Exception as exc:
            log.debug("Wikipedia verify [%s]: %s", query[:40], exc)

    return ""


def _groq_expand(category: str, seed: str, trend_hint: str = "") -> tuple[str, str]:
    keys = [os.getenv("GROQ_API_KEY_1", "").strip(), os.getenv("GROQ_API_KEY_2", "").strip(), os.getenv("GROQ_API_KEY_3", "").strip(), os.getenv("GROQ_API_KEY_4", "").strip()]
    for key in keys:
        if not key:
            continue
        try:
            r = requests.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "model": _GROQ_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You generate specific, mind-blowing world-fact video topics. "
                                "IMPORTANT: Only generate topics based on real, verifiable facts. "
                                "Never invent statistics, events, or claims. "
                                "Return ONLY valid JSON with no markdown: "
                                "{\"title\": \"...\", \"description\": \"...\"} "
                                "Title: max 80 chars. Use curiosity-gap psychology — imply "
                                "hidden or forbidden knowledge. "
                                "Good: 'The Impossible Thing Scientists Found in Deep Ocean' "
                                "Bad:  'Amazing Ocean Facts' "
                                "Description: 1-2 sentences with the most surprising specific detail. "
                                "Focus on FRESH ANGLES: recent discoveries, counterintuitive facts, "
                                "or surprising connections to modern life. Make it feel like "
                                "something people would share right now."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Category: {category}\n"
                                f"Seed topic: {seed}\n"
                                + (f"Trending angle hint: {trend_hint}\n" if trend_hint else "") +
                                "Generate a fresh, surprising angle on this topic for a viral "
                                "YouTube Shorts video. Prioritize recent discoveries or "
                                "counterintuitive facts. Use the trending hint if provided."
                            ),
                        },
                    ],
                    "temperature": 0.90,
                    "max_tokens":  200,
                },
                timeout=20,
            )
            if r.ok:
                raw = r.json()["choices"][0]["message"]["content"].strip()
                m   = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    data = json.loads(m.group())
                    if data.get("title"):
                        return data["title"], data.get("description", "")
        except Exception as exc:
            log.debug("Groq topic expand: %s", exc)
    return "", ""


def _is_duplicate(title: str, produced: list[dict]) -> bool:
    return any(_sim(title.lower(), p.get("title", "").lower()) > 0.80
               for p in produced)


def _load_produced_today(logs_dir: Path) -> list[dict]:
    try:
        path  = logs_dir / "video_results.json"
        if not path.exists():
            return []
        today = datetime.utcnow().date().isoformat()
        return [r for r in json.loads(path.read_text())
                if r.get("uploaded_at", "").startswith(today)]
    except Exception:
        return []


def _load_full_history(logs_dir: Path) -> list[dict]:
    try:
        path = logs_dir / "video_results.json"
        if not path.exists():
            return []
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(days=45)).isoformat()
        return [r for r in json.loads(path.read_text())
                if r.get("uploaded_at", "") >= cutoff]
    except Exception:
        return []


def _load_performance_weights(logs_dir: Path) -> dict[str, float]:
    try:
        path = logs_dir / "performance_history.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        # Support both flat {cat: pct} and nested {cat: {avg_retention_pct: pct}}
        weights = {}
        for cat, info in data.items():
            if isinstance(info, dict):
                weights[cat] = info.get("avg_retention_pct", 50.0)
            elif isinstance(info, (int, float)):
                weights[cat] = float(info)
        return weights
    except Exception:
        return {}


def _subtopic_performance_score(seed: str, category: str) -> float:
    """
    Return sub-topic level performance score (0-100) from analytics history.
    Normalizes the seed to a key and looks it up in subtopic_history.json.
    Falls back to category average, then 50 (neutral) if no data.
    """
    try:
        logs_dir = Path(__file__).parent.parent / "logs"
        path     = logs_dir / "subtopic_history.json"
        if path.exists():
            data = json.loads(path.read_text())
            # Normalize seed to a simple key
            key = re.sub(r"[^a-z0-9]", "_", seed.lower().strip())[:40]
            if key in data:
                return float(data[key].get("avg_retention_pct", 50.0))
            # Try partial match
            for k, v in data.items():
                if k in key or key in k:
                    return float(v.get("avg_retention_pct", 50.0))
        # Fall back to category average
        perf_path = logs_dir / "performance_history.json"
        if perf_path.exists():
            perf = json.loads(perf_path.read_text())
            cat_data = perf.get(category, {})
            if isinstance(cat_data, dict):
                return float(cat_data.get("avg_retention_pct", 50.0))
            elif isinstance(cat_data, (int, float)):
                return float(cat_data)
    except Exception:
        pass
    return 50.0
