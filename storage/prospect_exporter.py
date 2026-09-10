"""Batched CSV export of qualified prospects, BATCH_SIZE at a time.

The filesystem is the source of truth for both the next batch number and
the set of already-exported usernames: on every call we (re)derive them by
reading whatever prospects_NNN.csv files already exist in
config.PROSPECTS_EXPORT_DIR, rather than trusting a counter that could
drift out of sync. An in-progress (incomplete) batch is additionally
persisted to a small JSON state file so it survives a restart.

Usage: call add_prospect(prospect_dict) exactly once per profile, right
after it has been decided to be a genuine (non-rejected) qualification
outcome. Safe to call more than once for the same account (deduplicated
by normalized username) and safe across process restarts.
"""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Optional

from config import BASE_DIR, PROSPECTS_EXPORT_BATCH_SIZE, PROSPECTS_EXPORT_DIR, PROSPECTS_EXPORT_STATE_FILE
from utils.logging import get_logger
from utils.normalization import clean_username

log = get_logger()

_CSV_COLUMNS = [
    "Username", "Profile URL", "Display Name", "Followers", "Bio",
    "Score", "Reason", "Message",
]
_FILENAME_RE = re.compile(r"^prospects_(\d+)\.csv$")

_buffer: list[dict] = []
_exported_usernames: set[str] = set()
_next_batch_number: Optional[int] = None
_initialized = False


def _scan_existing_batches() -> tuple[int, set[str]]:
    """Reads every prospects_NNN.csv already on disk to determine the next
    batch number and the full set of already-exported usernames."""
    PROSPECTS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    max_n = 0
    usernames: set[str] = set()
    for f in PROSPECTS_EXPORT_DIR.glob("prospects_*.csv"):
        m = _FILENAME_RE.match(f.name)
        if not m:
            continue
        max_n = max(max_n, int(m.group(1)))
        try:
            with open(f, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    u = row.get("Username")
                    if u:
                        usernames.add(clean_username(u))
        except OSError:
            continue
    return max_n, usernames


def _load_state() -> None:
    global _buffer, _exported_usernames, _next_batch_number, _initialized
    if _initialized:
        return

    disk_max_n, disk_usernames = _scan_existing_batches()

    buffer: list[dict] = []
    state_usernames: set[str] = set()
    if PROSPECTS_EXPORT_STATE_FILE.exists():
        try:
            data = json.loads(PROSPECTS_EXPORT_STATE_FILE.read_text(encoding="utf-8"))
            buffer = data.get("buffer", [])
            state_usernames = set(data.get("exported_usernames", []))
        except (json.JSONDecodeError, OSError):
            log.info("[PROSPECT] Export state file unreadable, rebuilding from disk only.")

    _buffer = buffer
    _exported_usernames = disk_usernames | state_usernames
    _next_batch_number = disk_max_n + 1
    _initialized = True
    if _buffer:
        log.info(f"[PROSPECT] Resumed an in-progress batch with {len(_buffer)}/{PROSPECTS_EXPORT_BATCH_SIZE} prospect(s).")


def _save_state() -> None:
    PROSPECTS_EXPORT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PROSPECTS_EXPORT_STATE_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps({"buffer": _buffer, "exported_usernames": sorted(_exported_usernames)}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, PROSPECTS_EXPORT_STATE_FILE)  # atomic on the same filesystem


def _prospect_to_row(p: dict) -> list:
    score = p.get("final_score")
    if score is None:
        score = p.get("preliminary_score")
    return [
        p.get("username", ""),
        p.get("profile_url", ""),
        p.get("display_name") or "",
        p.get("followers") if p.get("followers") is not None else "",
        p.get("bio") or "",
        score if score is not None else "",
        p.get("analysis_reason") or "",
        p.get("outreach_message") or "",
    ]


def _flush_batch() -> None:
    global _buffer, _next_batch_number
    PROSPECTS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"prospects_{_next_batch_number:03d}.csv"
    final_path = PROSPECTS_EXPORT_DIR / filename
    tmp_path = PROSPECTS_EXPORT_DIR / f".{filename}.tmp"

    # Write to a temp file, then atomically rename — a crash mid-write can
    # never leave a partially-written prospects_NNN.csv on disk.
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_COLUMNS)
        for p in _buffer:
            writer.writerow(_prospect_to_row(p))
    os.replace(tmp_path, final_path)

    for p in _buffer:
        _exported_usernames.add(clean_username(p.get("username", "")))
    flushed_count = len(_buffer)
    _buffer = []
    _next_batch_number += 1
    _save_state()  # only clear the buffer in persisted state once the CSV is safely on disk

    try:
        rel = final_path.relative_to(BASE_DIR)
    except ValueError:
        rel = final_path
    log.info(f"[PROSPECT] Batch complete — exported {rel} ({flushed_count} profiles)")

    try:
        from storage import google_drive
        google_drive.upload_file(final_path)
    except Exception as exc:
        # Drive is a nice-to-have mirror of a batch that is already safely
        # on disk — an upload problem must never affect the local export.
        log.info(f"[ERROR] [DRIVE] Unexpected error during upload trigger: {exc}")


def add_prospect(prospect: dict) -> None:
    """Adds a qualified (non-rejected) prospect to the pending export
    batch, flushing a full CSV batch automatically when it fills up.

    `prospect` should carry whatever of these keys are available:
    username, profile_url, display_name, followers, bio, final_score,
    preliminary_score, analysis_reason, outreach_message. Missing fields
    are left blank in the CSV — never invented.
    """
    _load_state()

    username = clean_username(prospect.get("username") or "")
    if not username:
        return
    if username in _exported_usernames:
        return
    if any(clean_username(b.get("username") or "") == username for b in _buffer):
        return

    row = dict(prospect)
    row["username"] = username
    _buffer.append(row)
    _save_state()
    log.info(f"[PROSPECT] Added @{username} — batch {len(_buffer)}/{PROSPECTS_EXPORT_BATCH_SIZE}")

    if len(_buffer) >= PROSPECTS_EXPORT_BATCH_SIZE:
        _flush_batch()
