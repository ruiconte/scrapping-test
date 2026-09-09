"""Start/pause/resume/stop control for a discovery session running as a
separate subprocess (so the Streamlit dashboard UI never blocks)."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

from config import BASE_DIR, CONTROL_FILE, PAUSE_FLAG


def _read_control() -> dict:
    if not CONTROL_FILE.exists():
        return {}
    try:
        return json.loads(CONTROL_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_control(data: dict) -> None:
    CONTROL_FILE.write_text(json.dumps(data))


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def get_status() -> dict:
    """Returns {'state': 'IDLE'|'RUNNING'|'PAUSED', 'pid': int|None}"""
    control = _read_control()
    pid = control.get("pid")
    if pid and _pid_running(pid):
        return {"state": "PAUSED" if PAUSE_FLAG.exists() else "RUNNING", "pid": pid}
    return {"state": "IDLE", "pid": None}


def start_session(keywords: list[str], max_profiles: int, env_overrides: Optional[dict] = None) -> int:
    if get_status()["state"] != "IDLE":
        raise RuntimeError("A session is already running.")

    if PAUSE_FLAG.exists():
        PAUSE_FLAG.unlink()

    env = os.environ.copy()
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items() if v is not None})

    cmd = [sys.executable, str(BASE_DIR / "main.py"), "discover", "--max-profiles", str(max_profiles)]
    if keywords:
        cmd += ["--keywords", ",".join(keywords)]

    log_path = BASE_DIR / "logs" / "prospector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log_file:
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR), env=env,
            stdout=log_file, stderr=subprocess.STDOUT,
        )
    _write_control({"pid": proc.pid})
    return proc.pid


def pause_session() -> None:
    PAUSE_FLAG.touch()


def resume_session() -> None:
    if PAUSE_FLAG.exists():
        PAUSE_FLAG.unlink()


def stop_session() -> None:
    control = _read_control()
    pid = control.get("pid")
    if pid and _pid_running(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if PAUSE_FLAG.exists():
        PAUSE_FLAG.unlink()
    _write_control({})
