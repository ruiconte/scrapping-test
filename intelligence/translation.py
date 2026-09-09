"""Translates the outreach message template into a prospect's detected
language, with a small on-disk cache so we never re-translate the same
template into the same language twice (cost/token optimization)."""
from __future__ import annotations

import json
from pathlib import Path

from config import DATA_DIR
from intelligence.gemini_client import generate_text
from utils.logging import get_logger
from utils.normalization import stable_hash

log = get_logger()

_CACHE_PATH = DATA_DIR / "translation_cache.json"

_TRANSLATE_INSTRUCTION = (
    "You translate short outreach message templates for Fableya, a platform "
    "for personalized illustrated children's stories. Translate the message "
    "into the requested target language, keeping the same friendly, casual "
    "tone. Preserve any {placeholder} tokens (curly braces and all) exactly "
    "as-is, untranslated — they are filled in afterward with a name. "
    "Preserve the URL fableya.com exactly as-is. Return ONLY the translated "
    "message text, nothing else — no quotes, no explanation."
)


def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def translate_template(template: str, target_language: str) -> str:
    """Returns `template` translated into `target_language`, using a cached
    result when this exact template has already been translated into this
    language before."""
    cache_key = f"{target_language.lower()}:{stable_hash({'t': template})}"
    cache = _load_cache()
    if cache_key in cache:
        return cache[cache_key]

    log.info(f"[GEMINI] Translating outreach template into {target_language}...")
    translated = generate_text(
        _TRANSLATE_INSTRUCTION,
        f"Target language: {target_language}\n\nMessage:\n{template}",
    )
    cache[cache_key] = translated
    _save_cache(cache)
    return translated
