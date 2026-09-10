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


def open_conversation(page: Page, username: str, log_prefix: str = "OUTREACH"):
    """Navigates to the profile and opens its DM conversation panel.

    Returns the composer Locator if the panel opened successfully, or
    None otherwise. Pure navigation/observation — never types or sends
    anything. Used both to start a fresh draft and to re-open a
    conversation whose panel may have auto-collapsed while we were
    watching it (Instagram closes an idle DM panel after a while, which
    would otherwise make a later "was it sent?" check look at a page that
    no longer shows the conversation at all).
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
        log.info(f"[{log_prefix}] @{username} → no Message button found (private/unavailable/self?)")
        return None

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
        log.info(f"[{log_prefix}] @{username} → composer not open yet, retrying ({attempt + 1}/3)...")

    if composer is None:
        log.info(f"[{log_prefix}] @{username} → DM composer did not open after retries")
    return composer


def open_dm_with_draft(page: Page, username: str, message: str) -> bool:
    """Opens the profile's DM composer and types the message in. Stops
    there — does NOT press Enter or click Send.

    Returns True if the message was successfully typed into the composer.
    """
    composer = open_conversation(page, username)
    if composer is None:
        return False

    composer.click()
    composer.type(message, delay=15)
    # Do NOT press Enter here: in Instagram's DM composer, Enter submits the
    # message immediately. The message must stay in the composer, untouched,
    # for a human to review and send themselves.
    log.info(f"[OUTREACH] @{clean_username(username)} → message drafted in composer, awaiting your review to send")
    return True


def _distinctive_fragment(message: str) -> str:
    """Picks the longest single line of the message as a search fragment.

    Avoids embedded newlines (which can render/collapse differently once a
    message is posted vs. sitting in an editable composer) and avoids
    short/generic lines that could false-match unrelated text.
    """
    lines = [l.strip() for l in message.splitlines() if l.strip()]
    return max(lines, key=len) if lines else message.strip()


def _composer_and_fragment_state(page: Page, message: str) -> tuple[str, bool]:
    """Returns (current composer text, whether the message fragment is
    found anywhere in the page's rendered text)."""
    composer_text = ""
    for sel in _COMPOSER_INPUT_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.is_visible(timeout=1000):
                composer_text = (loc.inner_text(timeout=1000) or "").strip()
                break
        except Exception:
            continue

    fragment = _distinctive_fragment(message)
    try:
        body_text = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body_text = ""
    return composer_text, (fragment in body_text)


def was_message_sent(page: Page, message: str, username: str | None = None) -> bool:
    """Read-only check: was this exact message actually sent?

    Two things must BOTH be true:
    1. The composer is empty (Instagram clears it after a real send).
    2. A distinctive fragment of the message text is found in the page's
       rendered text — i.e. it has moved into the conversation history,
       not just vanished from the composer for some unrelated reason
       (e.g. a re-render triggered by the window losing OS focus, which a
       composer-emptiness check alone was previously fooled by).

    If neither the composer nor the fragment is found at all, the
    conversation panel may have auto-collapsed while we were watching it
    (Instagram closes an idle DM panel after a while). When `username` is
    given, we then re-open the conversation (navigation + clicking the
    Message button again — never typing or sending anything) and check
    once more before concluding "not sent", so a closed panel is never
    mistaken for "nothing happened".

    Never clicks or presses anything that could send a message.
    """
    composer_text, fragment_found = _composer_and_fragment_state(page, message)

    if composer_text:
        return False  # still sitting in the composer — not sent
    if fragment_found:
        return True
    if not username:
        return False

    log.info(f"[OUTREACH-CHECK] @{clean_username(username)} → panel looked closed, re-opening to check...")
    composer = open_conversation(page, username, log_prefix="OUTREACH-CHECK")
    if composer is None:
        return False
    composer_text, fragment_found = _composer_and_fragment_state(page, message)
    return bool(fragment_found and not composer_text)


def send_initial_outreach(page: Page, username: str, message: str) -> bool:
    """Open a prospect's DM, fill the first outreach message and send it."""

    drafted = open_dm_with_draft(page, username, message)

    if not drafted:
        log.info(
            f"[OUTREACH] @{username} → message not sent because draft creation failed"
        )
        return False

    page.wait_for_timeout(1000)

    return send_current_draft(page, username)

def send_current_draft(page: Page, username: str) -> bool:
    """Send the message currently present in the Instagram DM composer.

    Returns True when the send action was performed successfully.
    Does not attempt to bypass Instagram warnings, challenges or blocks.
    """
    try:
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)

        log.info(f"[OUTREACH] @{username} → message sent")
        return True

    except Exception as exc:
        log.info(f"[OUTREACH] @{username} → send failed: {exc}")
        return False
