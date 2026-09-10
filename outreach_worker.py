"""Long-lived process holding ONE authenticated Instagram browser session
open, so a human can prepare, review, and manually send DMs one at a time
from the dashboard — without the browser closing between actions.

Started via `python main.py outreach-worker` (or from the dashboard).
Stays alive, browser window open, until it receives SIGTERM (sent when
you stop it from the dashboard) or a security block is detected.

HARD RULE: this process performs exactly one browser action on its own —
instagram.outreach.open_dm_with_draft() ("prepare_next" command). It must
never import or call instagram.outreach.send_current_draft(), press
Enter in the composer, or click a send/submit control. Marking a prospect
"done" or "passed" happens entirely in storage/outreach_queue.py (a JSON
file) and never touches the browser.
"""
from __future__ import annotations

import json
import os
import signal
import time

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

        _write_status({"state": "idle", "pid": os.getpid()})
        log.info("[OUTREACH-WORKER] Ready. Waiting for commands from the dashboard...")

        while not _stop_requested:
            action = _read_command()

            if action == "prepare_next":
                _clear_command()
                item = outreach_queue.get_next_pending()

                if not item:
                    _write_status({"state": "empty", "pid": os.getpid()})

                elif not item.get("message"):
                    log.info(f"[OUTREACH-WORKER] @{item['username']} → message manquant, marqué 'failed'.")
                    outreach_queue.update_status(item["username"], "failed")
                    _write_status({
                        "state": "idle", "pid": os.getpid(),
                        "error": f"@{item['username']}: message manquant",
                    })

                else:
                    try:
                        ok = open_dm_with_draft(session.page, item["username"], item["message"])
                    except SecurityStopError as exc:
                        log.info(f"[OUTREACH-WORKER] STOPPED — security block detected: {exc}")
                        _write_status({"state": "security_stop", "pid": os.getpid(), "error": str(exc)})
                        break

                    if ok:
                        outreach_queue.update_status(item["username"], "prepared")
                        _write_status({"state": "prepared", "pid": os.getpid(), "username": item["username"]})
                        log.info(f"[OUTREACH-WORKER] @{item['username']} prêt — relis et envoie toi-même dans le navigateur.")
                    else:
                        outreach_queue.update_status(item["username"], "failed")
                        _write_status({
                            "state": "idle", "pid": os.getpid(),
                            "error": f"@{item['username']}: échec de préparation du DM",
                        })

            time.sleep(1)

    finally:
        session.stop()
        _write_status({"state": "stopped", "pid": None})
        log.info("[OUTREACH-WORKER] Stopped, browser closed.")


if __name__ == "__main__":
    run()
