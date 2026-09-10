"""Central configuration for the Fableya prospector.

All tunable knobs live here. Values can be overridden via environment
variables (loaded from .env) without touching code.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val else default


def _env_list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [x.strip() for x in val.split(",") if x.strip()]


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
DATA_DIR = BASE_DIR / "data"
EXPORTS_DIR = DATA_DIR / "exports"
LOGS_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "prospects.db"
BROWSER_PROFILE_DIR = BASE_DIR / "browser_profile"
PROSPECTS_EXPORT_DIR = BASE_DIR / "prospects"
PROSPECTS_EXPORT_STATE_FILE = DATA_DIR / "prospect_export_state.json"
PROSPECTS_EXPORT_BATCH_SIZE = _env_int("PROSPECTS_EXPORT_BATCH_SIZE", 50)

for _d in (DATA_DIR, EXPORTS_DIR, LOGS_DIR, BROWSER_PROFILE_DIR, PROSPECTS_EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --------------------------------------------------------------------------
# Business context (sent to Gemini as-is)
# --------------------------------------------------------------------------
BUSINESS_CONTEXT = {
    "product": "Fableya",
    "website": "fableya.com",
    "description": (
        "Fableya is a web platform that lets parents generate personalized, "
        "illustrated digital children's stories."
    ),
}

# --------------------------------------------------------------------------
# Qualification thresholds (all configurable, all overridable via env)
# --------------------------------------------------------------------------
MIN_SCORE = _env_int("MIN_SCORE", 50)  # below this -> effectively rejected
DEEP_ANALYSIS_THRESHOLD = _env_int("DEEP_ANALYSIS_THRESHOLD", 60)  # stage1 score to trigger stage2

# Follower range
FOLLOWERS_PREFERRED_MIN = _env_int("FOLLOWERS_PREFERRED_MIN", 300)
FOLLOWERS_PREFERRED_MAX = _env_int("FOLLOWERS_PREFERRED_MAX", 10_000)
FOLLOWERS_ACCEPTABLE_MIN = _env_int("FOLLOWERS_ACCEPTABLE_MIN", 100)
FOLLOWERS_ACCEPTABLE_MAX = _env_int("FOLLOWERS_ACCEPTABLE_MAX", 20_000)

# --------------------------------------------------------------------------
# Geography / language priority (never used to hard-discard, only to rank)
# --------------------------------------------------------------------------
PRIORITY_ENGLISH_COUNTRIES = [
    "United States", "United Kingdom", "Canada", "Australia",
    "New Zealand", "Ireland",
]
PRIORITY_FRENCH_COUNTRIES = ["France", "Belgium", "Switzerland", "Canada"]

# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
SEARCH_SEEDS_EN = [
    "parenting", "motherhood", "mom life", "mum life", "family life",
    "kids activities", "toddler activities", "preschool activities",
    "children's books", "kids books", "picture books", "raising readers",
    "reading with kids", "storytime", "bedtime stories",
    "learning through play", "montessori", "homeschool", "early learning",
    "kids crafts", "children's art", "screen free activities",
    "family activities", "creative kids", "children's literacy",
    "book recommendations for kids",
]

SEARCH_SEEDS_FR = [
    "parentalite", "maman", "vie de maman", "vie de famille",
    "activites enfants", "activites tout-petits", "activites maternelle",
    "livres pour enfants", "livres jeunesse", "albums jeunesse",
    "lecture enfant", "histoire du soir", "apprendre en jouant",
    "montessori famille", "ecole a la maison", "eveil enfant",
    "bricolage enfant", "art enfant", "activites sans ecran",
    "activites en famille", "creativite enfant", "litteratie enfant",
    "livres jeunesse recommandation",
]

SEARCH_SEEDS = _env_list("SEARCH_SEEDS", SEARCH_SEEDS_EN + SEARCH_SEEDS_FR)

MAX_PROFILES_PER_SESSION = _env_int("MAX_PROFILES_PER_SESSION", 50)
MAX_RELATED_PER_PROFILE = _env_int("MAX_RELATED_PER_PROFILE", 8)

# --------------------------------------------------------------------------
# Scraping limits (data minimization)
# --------------------------------------------------------------------------
MAX_POSTS_STAGE2 = _env_int("MAX_POSTS_STAGE2", 8)
MAX_COMMENTS_PER_POST = _env_int("MAX_COMMENTS_PER_POST", 8)

# --------------------------------------------------------------------------
# Analysis versioning (bump to force re-analysis of everything)
# --------------------------------------------------------------------------
ANALYSIS_VERSION = _env_int("ANALYSIS_VERSION", 1)

# --------------------------------------------------------------------------
# Playwright
# --------------------------------------------------------------------------
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
NAV_TIMEOUT_MS = _env_int("NAV_TIMEOUT_MS", 20_000)

# --------------------------------------------------------------------------
# Session control (dashboard START/PAUSE/STOP)
# --------------------------------------------------------------------------
CONTROL_FILE = DATA_DIR / "session_control.json"
PAUSE_FLAG = DATA_DIR / "pause.flag"
