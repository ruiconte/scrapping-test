"""Extract lightweight public profile data from an Instagram profile page.

Stage-1 data collection: cheap, no post-by-post scraping.

Rather than chase Instagram's obfuscated, constantly-churning CSS class
names, we read the profile header's rendered TEXT (accessible to any
screen reader too) and parse it positionally. This is more robust to UI
changes than deep class-name selectors: as of the current Instagram web
UI, the header text renders as a fixed sequence of lines, e.g.:

    theletsplaymom
    2 436
    followers
    931
    suivi(e)s
    Easy Baby & Toddler Activities
    Relatable mom life + humor ...
    plus
    www.facebook.com/share/...
    Mom Humor          <- story highlight names (ignored)
    ...

"""
from __future__ import annotations

import re
from typing import Optional

from playwright.sync_api import Page

from utils.logging import get_logger
from utils.normalization import clean_username, parse_count, profile_url

log = get_logger()

SKIP_WORDS = {"plus", "voir moins", "show less", "show more", "more"}
STOP_WORDS = {
    "suivre", "follow", "following", "suivi(e)", "abonné(e)",
    "modifier le profil", "edit profile", "message", "contacter",
    "contact", "s'abonner", "message instagram",
}
_URL_LINE_RE = re.compile(r"^(https?://\S+|www\.\S+|[a-z0-9-]+\.[a-z]{2,}(/\S*)?)$", re.IGNORECASE)

# Instagram's account-category badge is always one of a small set of exact
# phrases (unlike bio/link text, which can loosely contain similar words —
# e.g. a linked "Amazon Storefront" chip previously false-matched a loose
# "store" substring check). Matching the full line against this whitelist
# avoids mislabeling bio or link text as a category.
_KNOWN_CATEGORIES = {
    "digital creator", "créateur de contenu numérique", "personal blog",
    "blog personnel", "public figure", "personnage public", "author",
    "auteur", "autrice", "blogger", "blogueur", "blogueuse", "artist",
    "artiste", "educator", "éducateur", "éducatrice", "small business",
    "petite entreprise", "product/service", "produit/service",
    "education website", "site web éducatif", "writer", "écrivain",
    "écrivaine", "entrepreneur", "community", "communauté",
    "photographer", "photographe", "video creator", "créateur vidéo",
}


def _is_known_category(line: str) -> bool:
    return line.strip().lower() in _KNOWN_CATEGORIES

# A stat line looks like "175 posts", "2,436 followers", "931 following",
# or the French equivalents "175 publications", "2 436 abonné(e)s", "931 abonnements".
_STAT_LINE_RE = re.compile(r"^([\d][\d.,\s ]*)\s+([A-Za-zÀ-ÿ()'’ ]+)$")


def _classify_stat_label(label: str) -> Optional[str]:
    low = label.lower()
    if "abonnement" in low or "suivi" in low or low.strip() == "following":
        return "following"
    if "abonné" in low or "follower" in low:
        return "followers"
    if "publicat" in low or "post" in low:
        return "posts"
    return None


def _parse_header_lines(lines: list[str], username: str) -> dict:
    stats: dict[str, Optional[int]] = {"posts": None, "followers": None, "following": None}
    stat_indices: list[int] = []

    for i, line in enumerate(lines):
        m = _STAT_LINE_RE.match(line)
        if not m:
            continue
        kind = _classify_stat_label(m.group(2))
        if kind:
            stats[kind] = parse_count(m.group(1))
            stat_indices.append(i)

    # Fallback: some layouts put the number and label on separate lines.
    if not stat_indices:
        for i in range(len(lines) - 1):
            if re.fullmatch(r"[\d.,\s ]+", lines[i]):
                kind = _classify_stat_label(lines[i + 1])
                if kind:
                    stats[kind] = parse_count(lines[i])
                    stat_indices.extend([i, i + 1])

    display_name = None
    if stat_indices:
        first_stat_idx = min(stat_indices)
        for i in range(1, first_stat_idx):
            if i not in stat_indices and lines[i].strip():
                display_name = lines[i]
                break
        bio_start = max(stat_indices) + 1
    else:
        bio_start = 1

    bio_lines: list[str] = []
    external_url = None
    category = None

    for line in lines[bio_start:]:
        low = line.lower().strip()
        if low in SKIP_WORDS:
            continue
        if low in STOP_WORDS:
            break
        if _URL_LINE_RE.match(line.strip()):
            external_url = line if line.startswith("http") else f"https://{line}"
            break
        if _is_known_category(line):
            category = line.strip()
            continue
        if display_name is None and line.lower() != username.lower():
            display_name = line
            continue
        bio_lines.append(line)
        if len(bio_lines) >= 6:
            break

    if display_name and display_name.lower() == username.lower():
        display_name = None

    return {
        "followers": stats["followers"],
        "following": stats["following"],
        "post_count": stats["posts"],
        "display_name": display_name,
        "bio": "\n".join(bio_lines) if bio_lines else None,
        "external_url": external_url,
        "account_category": category,
    }


def extract_profile(page: Page, username: str) -> dict:
    """Navigate to the profile and return a dict of publicly visible fields.

    Returns {"username": ..., "status": "OK"|"PRIVATE"|"UNAVAILABLE", ...}
    """
    username = clean_username(username)
    url = profile_url(username)
    log.info(f"[PROFILE] Visiting @{username}")
    page.goto(url, wait_until="domcontentloaded")

    header = page.locator("header")
    header_text = ""
    for _ in range(6):  # poll up to ~6s for client-side hydration to fill in real data
        page.wait_for_timeout(1000)
        try:
            header_text = header.inner_text(timeout=2000)
        except Exception:
            header_text = ""
        if header_text and any(ch.isdigit() for ch in header_text):
            break

    try:
        body_text = page.locator("body").inner_text(timeout=2000).lower()
    except Exception:
        body_text = ""

    if any(p in body_text for p in ["sorry, this page isn't available", "page introuvable", "page not found"]):
        log.info(f"[SKIP] @{username} → unavailable (deleted or invalid)")
        return {"username": username, "profile_url": url, "status": "UNAVAILABLE"}

    is_private = any(p in body_text for p in ["this account is private", "ce compte est priv"])

    lines = [l.strip() for l in header_text.split("\n") if l.strip()]
    parsed = _parse_header_lines(lines, username)

    result = {
        "username": username,
        "profile_url": url,
        "display_name": parsed["display_name"],
        "bio": parsed["bio"],
        "followers": parsed["followers"],
        "following": parsed["following"],
        "post_count": parsed["post_count"],
        "external_url": parsed["external_url"],
        "account_category": parsed["account_category"],
        "status": "PRIVATE" if is_private else "OK",
    }
    log.info(
        f"[PROFILE] @{username} → followers={result['followers']} "
        f"following={result['following']} private={is_private}"
    )
    return result
