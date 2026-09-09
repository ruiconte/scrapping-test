"""Small helpers for cleaning/normalizing scraped data."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional


def parse_count(text: Optional[str]) -> Optional[int]:
    """Parse Instagram-style counts into an int.

    Handles both English ('12.4k', '1,204') and French ('13,9 k', '2 436',
    space as thousands separator, comma as decimal point) formatting.
    """
    if not text:
        return None
    t = text.strip().lower().replace("\xa0", " ")  # non-breaking space -> space
    if not t:
        return None

    match = re.match(r"^([\d.,\s]+)\s*(k|m|mrd)?$", t)
    if not match:
        digits = re.sub(r"[^\d]", "", t)
        return int(digits) if digits else None

    number_part, suffix = match.groups()
    number_part = number_part.strip().replace(" ", "")

    if suffix:
        if "," in number_part and "." in number_part:
            number_part = number_part.replace(",", "")  # comma = thousands sep here
        else:
            number_part = number_part.replace(",", ".")  # comma = decimal sep (French)
        try:
            value = float(number_part)
        except ValueError:
            return None
        multiplier = {"k": 1_000, "m": 1_000_000, "mrd": 1_000_000_000}[suffix]
        return int(value * multiplier)

    digits = re.sub(r"[^\d]", "", number_part)
    return int(digits) if digits else None


def extract_hashtags(text: Optional[str]) -> list[str]:
    if not text:
        return []
    return re.findall(r"#(\w+)", text)


def clean_username(raw: str) -> str:
    raw = raw.strip().lstrip("@")
    raw = raw.rstrip("/")
    if "instagram.com/" in raw:
        raw = raw.split("instagram.com/")[-1].split("/")[0].split("?")[0]
    return raw.lower()


def profile_url(username: str) -> str:
    return f"https://www.instagram.com/{clean_username(username)}/"


def stable_hash(data: dict) -> str:
    """Deterministic hash of a dict, used to detect changed profile data
    so we avoid re-sending identical information to Gemini."""
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
