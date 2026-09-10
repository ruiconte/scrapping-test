"""Long-lived process holding ONE authenticated Instagram browser session
open, so a human can prepare, review, and manually send DMs one at a time
— without the browser closing between actions.

Started via `python main.py outreach-worker` (or from the dashboard).
Stays alive, browser window open, until it receives SIGTERM (sent when
you stop it from the dashboard) or a security block is detected.

NOTE on auto-advance: an earlier version tried to auto-detect a manual
send by polling whether the composer went back to empty. That was
abandoned — in testing, the composer went empty on its own after a
while with no send at all (most likely Instagram's own React composer
resetting when the window loses OS focus while another window is
active), which produced false "done" markings for prospects who were
never actually contacted. That risk (silently treating a real prospect
as handled when they weren't) was not acceptable, so this worker only
prepares on an explicit "prepare_next" command, and a human marks a
prospect done from the dashboard after actually sending it themselves.

HARD RULE: this process performs exactly one browser action on its own —
instagram.outreach.open_dm_with_draft() ("prepare_next" command). It must
never import or call instagram.outreach.send_current_draft() /
send_initial_outreach(), press Enter in the composer, or click a send/
submit control. Marking a prospect "done" or "passed" happens entirely in
storage/outreach_queue.py (a JSON file) and never touches the browser.
"""
from __future__ import annotations

import json
import os
import signal
import time

from playwright.sync_api import Error as PlaywrightError

from browser.browser import InstagramSession, SecurityStopError
from config import DATA_DIR, OUTREACH_BROWSER_PROFILE_DIR
from instagram.outreach import open_dm_with_draft
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
        log.info("[OUTREACH-WORKER] Ready. Waiting for commands from the dashboard...")

        while not _stop_requested:
            action = _read_command()

            if action == "prepare_next":
                _clear_command()

                if outreach_queue.get_prepared():
                    # Something is already prepared and awaiting a human
                    # decision — don't overwrite it with a new one.
                    time.sleep(1)
                    continue

                item = outreach_queue.get_next_pending()
                if not item:
                    _write_status({"state": "empty", "pid": os.getpid()})
                else:
                    outcome = _prepare(session, item)
                    if outcome == "fatal":
                        break

            time.sleep(1)

    finally:
        session.stop()
        _write_status({"state": "stopped", "pid": None})
        log.info("[OUTREACH-WORKER] Stopped, browser closed.")


if __name__ == "__main__":
    run()
