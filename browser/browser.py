"""Persistent Playwright browser session for Instagram.

Uses a persistent context (a real Chrome user-data-dir equivalent) so the
login session survives across runs. We never touch or store the user's
Instagram password: the very first run opens a visible browser and waits
for the human to log in manually.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from browser import selectors
from config import BROWSER_PROFILE_DIR, NAV_TIMEOUT_MS, PLAYWRIGHT_HEADLESS
from utils.logging import get_logger

log = get_logger()

INSTAGRAM_HOME = "https://www.instagram.com/"


class SecurityStopError(Exception):
    """Raised when Instagram presents a challenge/CAPTCHA/checkpoint.

    We deliberately do NOT try to work around this. The caller must stop
    the automation and let the human resolve it in the visible browser.
    """


class InstagramSession:
    def __init__(self, profile_dir: Optional[Path] = None):
        """`profile_dir` lets a caller use a SEPARATE persistent Chrome
        profile (its own login/cookies) instead of the default shared one
        — needed so the outreach worker can hold a browser open at the
        same time as a discovery session, since Chrome refuses to open
        the same profile directory twice concurrently. Requires its own
        one-time manual login the first time it's used.
        """
        self._pw = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._profile_dir = profile_dir or BROWSER_PROFILE_DIR

    def start(self) -> Page:
        self._pw = sync_playwright().start()
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self.context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            channel="chrome",
            headless=PLAYWRIGHT_HEADLESS,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        self.context.set_default_timeout(NAV_TIMEOUT_MS)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        return self.page

    def stop(self) -> None:
        if self.context:
            self.context.close()
        if self._pw:
            self._pw.stop()

    def ensure_logged_in(self) -> bool:
        """Navigate to Instagram and verify (or wait for) an authenticated session.

        Returns True once logged in. Blocks (with periodic prompts) on the
        first run until the human completes login manually.
        """
        page = self.page
        page.goto(INSTAGRAM_HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        if self._is_logged_in():
            log.info("[SESSION] Already authenticated (persistent session reused).")
            return True

        self._check_for_security_block()

        log.info("[SESSION] Not logged in. Please log in manually in the open browser window.")
        log.info("[SESSION] Waiting for you to complete login (up to 5 minutes)...")

        for _ in range(60):  # ~5 minutes, polling every 5s
            page.wait_for_timeout(5000)
            self._check_for_security_block()
            if self._is_logged_in():
                log.info("[SESSION] Login detected. Session will be reused on future runs.")
                return True

        log.info("[ERROR] Timed out waiting for manual login.")
        return False

    def _is_logged_in(self) -> bool:
        page = self.page
        for sel in selectors.LOGGED_IN_INDICATORS:
            try:
                if page.locator(sel).first.is_visible(timeout=1000):
                    return True
            except Exception:
                continue
        # Absence of a login form is a weaker but useful signal
        for sel in selectors.LOGIN_PAGE_INDICATORS:
            try:
                if page.locator(sel).first.is_visible(timeout=500):
                    return False
            except Exception:
                continue
        return False

    def _check_for_security_block(self) -> None:
        page = self.page
        content = ""
        try:
            # Only visible rendered text, NOT raw HTML source: Instagram embeds
            # large hidden JSON blobs (e.g. containing strings like
            # "checkpoint_url") that caused false positives when we scanned
            # page.content() instead of what a human actually sees on screen.
            content = page.locator("body").inner_text(timeout=2000).lower()
        except Exception:
            return

        for phrase in selectors.CHALLENGE_INDICATORS_TEXT:
            if phrase in content:
                log.info(f"[SESSION] Instagram is presenting a challenge/checkpoint ('{phrase}').")
                log.info("[SESSION] STOPPING automation. Please resolve this manually in the browser.")
                raise SecurityStopError(f"Challenge/checkpoint detected: {phrase}")

        for sel in selectors.CAPTCHA_INDICATORS:
            try:
                if page.locator(sel).first.is_visible(timeout=500):
                    log.info("[SESSION] CAPTCHA detected. STOPPING automation.")
                    raise SecurityStopError("CAPTCHA detected")
            except SecurityStopError:
                raise
            except Exception:
                continue
