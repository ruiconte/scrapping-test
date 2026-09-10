"""Long-lived process holding ONE authenticated Instagram browser session
open, so a human can prepare, review, and manually send DMs one at a time
— without the browser closing between actions, and without needing to
click anything in the dashboard between messages.

Started via `python main.py outreach-worker` (or from the dashboard).
Stays alive, browser window open, until it receives SIGTERM (sent when
you stop it from the dashboard) or a security block is detected.

Auto-advance history: a first attempt polled whether the composer went
back to empty. Abandoned — in testing the composer emptied on its own
with no send at all (likely Instagram's own composer resetting when the
window loses OS focus), producing false "done" markings for prospects
never actually contacted. The current check (see
instagram.outreach.was_message_sent) additionally requires the message
text to actually appear in the conversation history, not just an empty
composer — verified in testing to correctly stay negative even after 60+
seconds with no real send, while still correctly detecting a real one.

HARD RULE: this process performs exactly two browser actions on its own —
instagram.outreach.open_dm_with_draft() (fills a composer) and
instagram.outreach.was_message_sent() (a read-only check, no click, no
keypress). It must never import or call
instagram.outreach.send_current_draft() / send_initial_outreach(), press
Enter in the composer, or click a send/submit control. Marking a prospect
"done" or "passed" happens entirely in storage/outreach_queue.py (a JSON
file) and never touches the browser.
"""
from __future__ import annotations

import json
import os
import signal
import time

from playwright.sync_api import Error as PlaywrightError

from browser.browser import InstagramSession, SecurityStopError
from config import DATA_DIR, OUTREACH_BROWSER_PROFILE_DIR
from instagram.outreach import open_dm_with_draft, was_message_sent
from storage import outreach_queue
from utils.logging import get_logger

log = get_logger()

COMMAND_PATH = DATA_DIR / "outreach_worker_command.json"
STATUS_PATH = DATA_DIR / "outreach_worker_status.json"

_stop_requested = False


def _handle_sigterm(_signum, _frame):
    global _stop_requested
    _stop_requested = True


def _write_status(status: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATUS_PATH)


def _read_command() -> str | None:
    if not COMMAND_PATH.exists():
        return None
    try:
        return json.loads(COMMAND_PATH.read_text(encoding="utf-8")).get("action")
    except (json.JSONDecodeError, OSError):
        return None


def _clear_command() -> None:
    if COMMAND_PATH.exists():
        try:
            COMMAND_PATH.unlink()
        except OSError:
            pass


def _prepare(session: InstagramSession, item: dict) -> str:
    """Attempts to prepare one item. Returns 'prepared', 'failed', or
    'fatal' (browser/context is dead — caller should stop the worker)."""
    if not item.get("message"):
        log.info(f"[OUTREACH-WORKER] @{item['username']} → message manquant, marqué 'failed'.")
        outreach_queue.update_status(item["username"], "failed")
        _write_status({"state": "idle", "pid": os.getpid(), "error": f"@{item['username']}: message manquant"})
        return "failed"

    try:
        ok = open_dm_with_draft(session.page, item["username"], item["message"])
    except PlaywrightError as exc:
        # The browser/context itself is dead (e.g. crashed from low system
        # memory) — retrying more items against it would just fail again
        # forever. Mark this one failed and let the caller stop cleanly.
        log.info(f"[ERROR] [OUTREACH-WORKER] Browser session appears dead ({exc}). Stopping.")
        outreach_queue.update_status(item["username"], "failed")
        _write_status({
            "state": "error", "pid": None,
            "error": f"Session navigateur perdue (probablement un manque de mémoire) : {exc}",
        })
        return "fatal"
    except Exception as exc:
        # Any other unexpected failure must never take the whole
        # persistent worker down — mark this one failed and keep going.
        log.info(f"[ERROR] [OUTREACH-WORKER] @{item['username']} → {exc}")
        outreach_queue.update_status(item["username"], "failed")
        _write_status({"state": "idle", "pid": os.getpid(), "error": f"@{item['username']}: {exc}"})
        return "failed"

    if ok:
        outreach_queue.update_status(item["username"], "prepared")
        _write_status({"state": "prepared", "pid": os.getpid(), "username": item["username"]})
        log.info(f"[OUTREACH-WORKER] @{item['username']} prêt — relis et envoie toi-même dans le navigateur.")
        return "prepared"

    outreach_queue.update_status(item["username"], "failed")
    _write_status({"state": "idle", "pid": os.getpid(), "error": f"@{item['username']}: échec de préparation du DM"})
    return "failed"


def run() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    _write_status({"state": "starting", "pid": os.getpid()})

    # A SEPARATE Chrome profile from the discovery session's — Chrome
    # refuses to open the same user-data-dir twice at once, and this way
    # the outreach worker can stay open while a discovery session runs.
    # First use needs its own one-time manual login (same Instagram
    # account, independent session/cookies — perfectly normal, like being
    # logged in on two devices at once).
    session = InstagramSession(profile_dir=OUTREACH_BROWSER_PROFILE_DIR)
    session.start()
    try:
        if not session.ensure_logged_in():
            _write_status({"state": "error", "pid": None, "error": "not logged in"})
            return

        # A "prepared" item left over from a previous run of this process
        # (e.g. it crashed or was restarted) does NOT mean the composer is
        # actually open in THIS fresh browser session — it isn't. Reset it
        # to pending so it gets re-prepared for real on the next command.
        stale = outreach_queue.get_prepared()
        if stale:
            log.info(f"[OUTREACH-WORKER] @{stale['username']} était 'prepared' d'une session précédente — remis en attente pour être repréparé.")
            outreach_queue.update_status(stale["username"], "pending")

        _write_status({"state": "idle", "pid": os.getpid()})
        log.info("[OUTREACH-WORKER] Ready.")

        # Grace period before we even start checking, purely to avoid a
        # race right at typing time — the was_message_sent() check itself
        # is already strong (empty composer AND the text found in the
        # conversation history), verified not to false-positive even over
        # a full minute of waiting with no real send.
        GRACE_PERIOD_SECONDS = 5
        watching_username = None
        watch_started_at = 0.0

        while not _stop_requested:
            # An explicit command from the dashboard is just a manual
            # nudge — clear it, the loop below already auto-advances.
            if _read_command():
                _clear_command()

            prepared = outreach_queue.get_prepared()

            if prepared:
                if prepared["username"] != watching_username:
                    watching_username = prepared["username"]
                    watch_started_at = time.monotonic()

                if time.monotonic() - watch_started_at < GRACE_PERIOD_SECONDS:
                    time.sleep(1)
                    continue

                try:
                    sent = was_message_sent(session.page, prepared["message"])
                except PlaywrightError as exc:
                    log.info(f"[ERROR] [OUTREACH-WORKER] Browser session appears dead ({exc}). Stopping.")
                    _write_status({
                        "state": "error", "pid": None,
                        "error": f"Session navigateur perdue (probablement un manque de mémoire) : {exc}",
                    })
                    break
                except Exception:
                    sent = False

                if sent:
                    outreach_queue.update_status(prepared["username"], "done")
                    log.info(f"[OUTREACH-WORKER] @{prepared['username']} → envoi détecté, marqué 'done'.")
                    watching_username = None
                else:
                    time.sleep(2)
                    continue
            else:
                watching_username = None

            next_item = outreach_queue.get_next_pending()
            if not next_item:
                _write_status({"state": "empty", "pid": os.getpid()})
                time.sleep(2)
                continue

            outcome = _prepare(session, next_item)
            if outcome == "fatal":
                break

            time.sleep(1)

    finally:
        session.stop()
        _write_status({"state": "stopped", "pid": None})
        log.info("[OUTREACH-WORKER] Stopped, browser closed.")


if __name__ == "__main__":
    run()
