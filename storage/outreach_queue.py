"""Persistent local queue of qualified prospects awaiting manual DM outreach.

Each item flows: pending -> prepared -> done (or failed). The scraper/
qualification pipeline (pipeline.py) only ever appends new "pending"
items here — it never prepares or sends anything itself. Preparing a DM
(instagram.outreach.open_dm_with_draft, via outreach_worker.py) and
marking an item done or skipped are both actions a human explicitly
triggers from the dashboard.

Stored as a single JSON array, written atomically (temp file + os.replace)
so it survives a restart and is never left half-written.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from config import DATA_DIR
from utils.logging import get_logger
from utils.normalization import clean_username

log = get_logger()

QUEUE_PATH = DATA_DIR / "outreach_queue.json"
VALID_STATUSES = ("pending", "prepared", "done", "failed")


def _load() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    try:
        return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.info("[QUEUE] outreach_queue.json unreadable, starting from an empty queue.")
        return []


def _save(items: list[dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, QUEUE_PATH)


def add_to_queue(prospect: dict) -> bool:
    """Adds a qualified prospect as a new 'pending' item.

    Returns False without modifying anything if this username is already
    present in the queue (regardless of its current status) — a prospect
    is never added twice.
    """
    username = clean_username(prospect.get("username") or "")
    if not username:
        return False

    items = _load()
    if any(clean_username(i.get("username") or "") == username for i in items):
        return False

    score = prospect.get("final_score")
    if score is None:
        score = prospect.get("preliminary_score")

    items.append({
        "username": username,
        "profile_url": prospect.get("profile_url") or f"https://www.instagram.com/{username}/",
        "display_name": prospect.get("display_name") or "",
        "score": score,
        "reason": prospect.get("analysis_reason") or "",
        "message": prospect.get("outreach_message") or "",
        "status": "pending",
    })
    _save(items)
    pending = sum(1 for i in items if i["status"] == "pending")
    log.info(f"[QUEUE] @{username} added to outreach queue ({pending} pending)")
    return True


def get_counts() -> dict:
    items = _load()
    counts = {"total": len(items)}
    for s in VALID_STATUSES:
        counts[s] = sum(1 for i in items if i.get("status") == s)
    return counts


def list_items(status: Optional[str] = None) -> list[dict]:
    items = _load()
    return [i for i in items if status is None or i.get("status") == status]


def get_next_pending() -> Optional[dict]:
    for item in _load():
        if item.get("status") == "pending":
            return item
    return None


def get_prepared() -> Optional[dict]:
    """At most one item should be 'prepared' at a time — the one
    currently open in the browser awaiting a human decision."""
    for item in _load():
        if item.get("status") == "prepared":
            return item
    return None


def update_status(username: str, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    username = clean_username(username)
    items = _load()
    for item in items:
        if clean_username(item.get("username") or "") == username:
            item["status"] = status
            break
    _save(items)
    log.info(f"[QUEUE] @{username} → {status}")


def requeue_to_end(username: str) -> None:
    """Used by 'Passer': moves this item back to 'pending' at the END of
    the queue, so it's revisited later instead of being lost."""
    username = clean_username(username)
    items = _load()
    idx = next((i for i, item in enumerate(items)
                if clean_username(item.get("username") or "") == username), None)
    if idx is None:
        return
    item = items.pop(idx)
    item["status"] = "pending"
    items.append(item)
    _save(items)
    log.info(f"[QUEUE] @{username} → passé, remis en fin de file")
