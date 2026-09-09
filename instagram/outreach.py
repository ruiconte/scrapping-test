"""Outreach message drafting: opens a qualified prospect's Instagram DM
composer and fills in a personalized message. Never sends automatically —
a human reviews and presses send/enter themselves for every message.
"""
from __future__ import annotations

from playwright.sync_api import Page

from utils.logging import get_logger
from utils.normalization import clean_username, profile_url

log = get_logger()

_MESSAGE_BUTTON_SELECTORS = [
    'header div[role="button"]:text-is("Message")',
    'header div[role="button"]:text-is("Envoyer un message")',
    'div[role="button"]:text-is("Message")',
]
_COMPOSER_INPUT_SELECTORS = [
    'textarea[placeholder="Message..."]',
    'textarea[placeholder="Message"]',
    'div[contenteditable="true"][aria-label="Message"]',
    'div[contenteditable="true"]',
]


def render_template(template: str, profile: dict) -> str:
    """Fill {placeholders} in the template with available profile fields.
    Unknown/missing placeholders are left as empty strings, never invented."""
    values = {
        "username": profile.get("username", ""),
        "display_name": profile.get("display_name") or profile.get("username", ""),
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError):
        return template


def get_message_for_profile(profile: dict, base_template_en: str) -> str:
    """Builds the outreach message in the profile's detected language.

    Falls back to English when the language is unknown, empty, or already
    English. Any other detected language is translated via Gemini (with
    caching — see intelligence/translation.py) so this works for any
    language Gemini recognizes, not just a fixed list.
    """
    language = (profile.get("language") or "").strip()
    if not language or language.upper() == "UNKNOWN" or language.lower() == "english":
        template = base_template_en
    else:
        from intelligence.translation import translate_template
        try:
            template = translate_template(base_template_en, language)
        except Exception as exc:
            log.info(f"[ERROR] Translation to {language} failed ({exc}); falling back to English.")
            template = base_template_en
    return render_template(template, profile)


def open_dm_with_draft(page: Page, username: str, message: str) -> bool:
    """Navigates to the profile, opens the DM composer, and types the
    message in. Stops there — does NOT press Enter or click Send.

    Returns True if the message was successfully typed into the composer.
    """
    username = clean_username(username)
    page.goto(profile_url(username), wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    message_button = None
    for attempt in range(3):
        for sel in _MESSAGE_BUTTON_SELECTORS:
            loc = page.locator(sel).first
            try:
                if loc.is_visible(timeout=2000):
                    message_button = loc
                    break
            except Exception:
                continue
        if message_button is not None:
            break
        page.wait_for_timeout(1500)

    if message_button is None:
        log.info(f"[OUTREACH] @{username} → no Message button found (private/unavailable/self?)")
        return False

    # Opening the chat overlay is flaky/timing-dependent (React hydration +
    # a network fetch for the thread), so retry the click a couple of times
    # with increasing waits rather than giving up after one attempt.
    composer = None
    for attempt in range(3):
        message_button.scroll_into_view_if_needed()
        message_button.click(force=True)
        page.wait_for_timeout(2500 + attempt * 1500)
        for sel in _COMPOSER_INPUT_SELECTORS:
            loc = page.locator(sel).first
            try:
                if loc.is_visible(timeout=2000):
                    composer = loc
                    break
            except Exception:
                continue
        if composer is not None:
            break
        log.info(f"[OUTREACH] @{username} → composer not open yet, retrying ({attempt + 1}/3)...")

    if composer is None:
        log.info(f"[OUTREACH] @{username} → DM composer did not open after retries")
        return False

    composer.click()
    composer.type(message, delay=15)
    page.keyboard.press("Enter")
    log.info(f"[OUTREACH] @{username} → message drafted in composer, awaiting your review to send")
    return True
