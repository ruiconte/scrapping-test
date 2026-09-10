"""Start/stop control and command dispatch for the long-lived outreach
worker (outreach_worker.py), which holds one Instagram browser session
open for manual DM preparation.

Mirrors the pattern already used by session_control.py for the discovery
session, but the outreach worker is a persistent daemon (stays alive
across many "prepare next" actions) rather than a one-shot subprocess.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

from config import BASE_DIR, DATA_DIR

COMMAND_PATH = DATA_DIR / "outreach_worker_command.json"
STATUS_PATH = DATA_DIR / "outreach_worker_status.json"


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def get_status() -> dict:
    if not STATUS_PATH.exists():
        return {"state": "not_started"}
    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"state": "unknown"}
    pid = status.get("pid")
    if pid and not _pid_running(pid):
        return {"state": "stopped"}
    return status


def start_worker() -> int:
    status = get_status()
    if status.get("state") not in ("not_started", "stopped", "unknown", "error", "security_stop"):
        raise RuntimeError("Le worker outreach tourne déjà.")

    if COMMAND_PATH.exists():
        COMMAND_PATH.unlink()

    log_path = BASE_DIR / "logs" / "prospector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "outreach_worker.py")],
            cwd=str(BASE_DIR), stdout=log_file, stderr=subprocess.STDOUT,
        )
    return proc.pid


def stop_worker() -> None:
    status = get_status()
    pid = status.get("pid")
    if pid and _pid_running(pid):
        os.kill(pid, signal.SIGTERM)


def request_prepare_next() -> None:
    COMMAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMAND_PATH.write_text(json.dumps({"action": "prepare_next"}), encoding="utf-8")
