"""Discovery surfaces: turning a seed keyword or a good profile into a list
of candidate usernames.

Stage 1 of the discovery graph: search results + "related accounts" from a
profile page. Both return only lightweight candidates (username + how we
found them) — no profile data is collected here, that happens later in
profile_extractor.py so we don't pay the cost of visiting every candidate.
"""
from __future__ import annotations

import re
from typing import Optional

from playwright.sync_api import Page

from utils.logging import get_logger
from utils.normalization import clean_username, profile_url

log = get_logger()

_NON_PROFILE_PATHS = {
    "explore", "accounts", "direct", "reels", "stories", "p", "tv", "legal",
    "about", "developer", "privacy", "terms", "web", "challenge", "popular",
    "notifications", "settings", "inbox", "lite", "download", "language",
    "topics", "api", "help", "",
}

_USERNAME_HREF_RE = re.compile(r"^/([A-Za-z0-9._]{1,30})/?$")


def _extract_usernames_from_links(hrefs: list[str]) -> list[str]:
    seen = []
    for href in hrefs:
        if not href:
            continue
        m = _USERNAME_HREF_RE.match(href)
        if not m:
            continue
        candidate = m.group(1)
        if candidate.lower() in _NON_PROFILE_PATHS:
            continue
        if candidate not in seen:
            seen.append(candidate)
    return seen


def search_accounts(page: Page, query: str, max_results: int = 15) -> list[str]:
    """Use Instagram's own search UI to find accounts matching a keyword.

    Returns a list of usernames (not yet validated as real/public/relevant —
    that's the job of profile_extractor + Gemini stage 1).
    """
    log.info(f"[DISCOVERY] Searching for keyword: '{query}'")

    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    search_icon = None
    for sel in ['svg[aria-label="Search"]', 'svg[aria-label="Recherche"]', 'a[href="/explore/search/"]']:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=1500):
                search_icon = loc
                break
        except Exception:
            continue

    if search_icon is None:
        log.info("[ERROR] Could not find the search icon in the nav bar.")
        return []

    search_icon.click()
    page.wait_for_timeout(1500)  # let the search panel's open animation finish

    search_input = None
    for sel in ['input[placeholder="Search"]', 'input[placeholder="Recherche"]', 'input[aria-label="Search input"]']:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=1500):
                search_input = loc
                break
        except Exception:
            continue

    if search_input is None:
        log.info("[ERROR] Could not find the search input field.")
        return []

    # .fill() focuses + sets the value directly instead of dispatching a
    # pointer click, which avoids Instagram's own search-icon button (still
    # animating into place) intercepting the click.
    search_input.fill(query)
    page.wait_for_timeout(2000)  # let results populate

    hrefs = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => e.getAttribute('href'))",
    )
    usernames = _extract_usernames_from_links(hrefs)[:max_results]

    log.info(f"[DISCOVERY] Found {len(usernames)} candidate account(s) for '{query}'")
    for u in usernames:
        log.info(f"[DISCOVERY] Found @{u}")

    # Close the search panel to leave the page in a clean state
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    return usernames


def find_related_accounts(page: Page, username: str, max_results: int = 8) -> list[str]:
    """Visit a profile and look for Instagram's own 'Suggested for you' /
    related-accounts surface, when present. Used to grow the discovery
    graph outward from an already-good profile."""
    url = profile_url(username)
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    hrefs = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => e.getAttribute('href'))",
    )
    candidates = _extract_usernames_from_links(hrefs)
    candidates = [c for c in candidates if clean_username(c) != clean_username(username)]
    result = candidates[:max_results]
    if result:
        log.info(f"[RELATED] @{username} → discovered {len(result)} new candidates")
    return result
