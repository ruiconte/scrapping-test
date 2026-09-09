"""SQLite persistence layer for the Fableya prospector.

Single-file module: connection handling + all read/write operations.
Kept intentionally simple (no ORM) since the schema is small and stable.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from config import DB_PATH
from storage.models import SCHEMA_SQL

_JSON_FIELDS = ("prospect_types", "positive_signals", "negative_signals", "fableya_fit", "hashtags")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in _JSON_FIELDS:
        if f in d:
            d[f] = _loads(d[f])
    return d


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)


# --------------------------------------------------------------------------
# Prospects
# --------------------------------------------------------------------------

def get_prospect(username: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM prospects WHERE username = ?", (username.lower(),)
        ).fetchone()
        return row_to_dict(row) if row else None


def upsert_discovered(
    username: str,
    profile_url: str,
    discovery_source: str,
    discovery_keyword: Optional[str] = None,
    discovered_from_username: Optional[str] = None,
) -> tuple[int, bool]:
    """Insert a newly discovered account if not already known.

    Returns (prospect_id, created). If the account already exists, it is
    left untouched (dedup by username) and created=False.
    """
    username = username.lower()
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM prospects WHERE username = ?", (username,)).fetchone()
        if row:
            conn.execute(
                "UPDATE prospects SET date_last_checked = ? WHERE id = ?",
                (_now(), row["id"]),
            )
            return row["id"], False

        cur = conn.execute(
            """
            INSERT INTO prospects (
                username, profile_url, discovery_source, discovery_keyword,
                discovered_from_username, status, date_discovered, date_last_checked
            ) VALUES (?, ?, ?, ?, ?, 'DISCOVERED', ?, ?)
            """,
            (username, profile_url, discovery_source, discovery_keyword,
             discovered_from_username, _now(), _now()),
        )
        return cur.lastrowid, True


def update_profile_fields(username: str, fields: dict) -> None:
    """Update raw profile fields collected by Stage 1 extraction."""
    if not fields:
        return
    username = username.lower()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [username]
    with get_connection() as conn:
        conn.execute(f"UPDATE prospects SET {cols} WHERE username = ?", values)


def save_stage1_result(
    username: str,
    preliminary_score: int,
    decision: str,
    profile_data_hash: str,
) -> None:
    """decision in {DEEP_ANALYZE, KEEP_LIGHT, REJECT}."""
    status_map = {
        "DEEP_ANALYZE": "DEEP_ANALYSIS_PENDING",
        "KEEP_LIGHT": "PREQUALIFIED",
        "REJECT": "REJECTED",
    }
    status = status_map.get(decision, "PREQUALIFIED")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE prospects
            SET preliminary_score = ?, status = ?, profile_data_hash = ?,
                date_analyzed = ?, analysis_version = analysis_version
            WHERE username = ?
            """,
            (preliminary_score, status, profile_data_hash, _now(), username.lower()),
        )


def save_stage2_result(username: str, analysis: dict, stage2_data_hash: str, analysis_version: int) -> None:
    status = analysis.get("recommended_action", "LOW_PRIORITY")
    if status not in {"HIGH_PRIORITY", "MEDIUM_PRIORITY", "LOW_PRIORITY", "REJECT"}:
        status = "LOW_PRIORITY"
    if status == "REJECT":
        status = "REJECTED"

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE prospects
            SET final_score = ?, prospect_types = ?, primary_category = ?,
                likely_parent = ?, likely_parent_audience = ?, children_age_relevance = ?,
                language = COALESCE(?, language), country = COALESCE(?, country),
                positive_signals = ?, negative_signals = ?, fableya_fit = ?,
                analysis_reason = ?, recommended_action = ?, status = ?,
                stage2_data_hash = ?, analysis_version = ?, date_analyzed = ?
            WHERE username = ?
            """,
            (
                analysis.get("relevance_score"),
                _dumps(analysis.get("prospect_types")),
                analysis.get("primary_category"),
                _bool_to_int(analysis.get("likely_parent")),
                _bool_to_int(analysis.get("likely_parent_audience")),
                analysis.get("children_age_relevance"),
                analysis.get("language"),
                analysis.get("country"),
                _dumps(analysis.get("positive_signals")),
                _dumps(analysis.get("negative_signals")),
                _dumps(analysis.get("fableya_fit")),
                analysis.get("reason"),
                analysis.get("recommended_action"),
                status,
                stage2_data_hash,
                analysis_version,
                _now(),
                username.lower(),
            ),
        )


def _bool_to_int(v: Optional[bool]) -> Optional[int]:
    return None if v is None else int(bool(v))


def save_outreach_message(username: str, message: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE prospects SET outreach_message = ?, outreach_status = 'DRAFTED' WHERE username = ?",
            (message, username.lower()),
        )


def mark_outreach_sent(username: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE prospects SET outreach_status = 'SENT' WHERE username = ?",
            (username.lower(),),
        )


def list_prospects(filters: Optional[dict] = None, order_by: str = "final_score DESC") -> list[dict]:
    filters = filters or {}
    clauses, params = [], []

    if filters.get("min_score") is not None:
        clauses.append("(final_score >= ? OR final_score IS NULL)")
        params.append(filters["min_score"])
    if filters.get("min_followers") is not None:
        clauses.append("(followers >= ? OR followers IS NULL)")
        params.append(filters["min_followers"])
    if filters.get("max_followers") is not None:
        clauses.append("(followers <= ? OR followers IS NULL)")
        params.append(filters["max_followers"])
    if filters.get("language"):
        clauses.append("language = ?")
        params.append(filters["language"])
    if filters.get("country"):
        clauses.append("country = ?")
        params.append(filters["country"])
    if filters.get("status"):
        clauses.append("status = ?")
        params.append(filters["status"])
    if filters.get("discovery_source"):
        clauses.append("discovery_source = ?")
        params.append(filters["discovery_source"])
    if filters.get("prospect_type"):
        clauses.append("prospect_types LIKE ?")
        params.append(f'%"{filters["prospect_type"]}"%')
    if filters.get("category"):
        clauses.append("(primary_category = ? OR account_category = ?)")
        params.extend([filters["category"], filters["category"]])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM prospects {where} ORDER BY {order_by}"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [row_to_dict(r) for r in rows]


# --------------------------------------------------------------------------
# Posts / comments
# --------------------------------------------------------------------------

def save_posts(prospect_id: int, posts: Iterable[dict]) -> list[int]:
    ids = []
    with get_connection() as conn:
        for p in posts:
            cur = conn.execute(
                """
                INSERT INTO posts (prospect_id, post_url, caption, hashtags, post_date,
                                    likes, comments_count, date_collected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prospect_id, p.get("post_url"), p.get("caption"),
                    _dumps(p.get("hashtags")), p.get("date"),
                    p.get("likes"), p.get("comments_count"), _now(),
                ),
            )
            ids.append(cur.lastrowid)
    return ids


def save_comments(post_id: int, comments: Iterable[str]) -> None:
    with get_connection() as conn:
        for c in comments:
            conn.execute(
                "INSERT INTO comments_sample (post_id, comment_text, date_collected) VALUES (?, ?, ?)",
                (post_id, c, _now()),
            )


def get_posts_with_comments(prospect_id: int) -> list[dict]:
    with get_connection() as conn:
        posts = conn.execute(
            "SELECT * FROM posts WHERE prospect_id = ? ORDER BY id", (prospect_id,)
        ).fetchall()
        result = []
        for p in posts:
            pd = row_to_dict(p)
            comments = conn.execute(
                "SELECT comment_text FROM comments_sample WHERE post_id = ?", (p["id"],)
            ).fetchall()
            pd["sample_comments"] = [c["comment_text"] for c in comments]
            result.append(pd)
        return result


# --------------------------------------------------------------------------
# Discovery queue
# --------------------------------------------------------------------------

def enqueue(
    username: str,
    priority: float = 0.0,
    discovery_source: str = "",
    discovery_keyword: Optional[str] = None,
    discovered_from_username: Optional[str] = None,
) -> bool:
    """Add to the discovery queue. Returns True if newly added."""
    username = username.lower()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id, priority FROM discovery_queue WHERE username = ?", (username,)
        ).fetchone()
        already_known = conn.execute(
            "SELECT 1 FROM prospects WHERE username = ?", (username,)
        ).fetchone()
        if already_known:
            return False
        if existing:
            if priority > existing["priority"]:
                conn.execute(
                    "UPDATE discovery_queue SET priority = ? WHERE id = ?",
                    (priority, existing["id"]),
                )
            return False
        conn.execute(
            """
            INSERT INTO discovery_queue (username, priority, discovery_source,
                discovery_keyword, discovered_from_username, status, date_added)
            VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (username, priority, discovery_source, discovery_keyword,
             discovered_from_username, _now()),
        )
        return True


def pop_next_from_queue() -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM discovery_queue WHERE status = 'PENDING' ORDER BY priority DESC, id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE discovery_queue SET status = 'PROCESSING' WHERE id = ?", (row["id"],))
        return row_to_dict(row)


def mark_queue_status(username: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE discovery_queue SET status = ? WHERE username = ?",
            (status, username.lower()),
        )


def recover_stale_processing() -> int:
    """Reset any queue items stuck in PROCESSING (e.g. from a crash or an
    unclean shutdown) back to PENDING so they're retried on the next run."""
    with get_connection() as conn:
        cur = conn.execute("UPDATE discovery_queue SET status = 'PENDING' WHERE status = 'PROCESSING'")
        return cur.rowcount


def queue_size(status: str = "PENDING") -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM discovery_queue WHERE status = ?", (status,)
        ).fetchone()
        return row["c"]


# --------------------------------------------------------------------------
# Stats (for dashboard)
# --------------------------------------------------------------------------

def start_session_stats() -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO session_stats (session_start) VALUES (?)", (_now(),)
        )
        return cur.lastrowid


def finish_session_stats(session_id: int, stats: dict, gemini_calls: int = 0) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE session_stats
            SET session_end = ?, profiles_discovered = ?, profiles_analyzed_stage1 = ?,
                profiles_analyzed_stage2 = ?, gemini_calls = ?, errors = ?
            WHERE id = ?
            """,
            (
                _now(),
                stats.get("processed", 0),
                stats.get("processed", 0),
                stats.get("stage2_processed", 0),
                gemini_calls,
                stats.get("errors", 0),
                session_id,
            ),
        )


def recent_sessions(limit: int = 10) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM session_stats ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_summary_stats() -> dict:
    with get_connection() as conn:
        def scalar(sql, params=()):
            return conn.execute(sql, params).fetchone()[0]

        today = datetime.now(timezone.utc).date().isoformat()
        return {
            "total_discovered": scalar("SELECT COUNT(*) FROM prospects"),
            "total_analyzed": scalar("SELECT COUNT(*) FROM prospects WHERE final_score IS NOT NULL"),
            "high_priority": scalar("SELECT COUNT(*) FROM prospects WHERE status = 'HIGH_PRIORITY'"),
            "medium_priority": scalar("SELECT COUNT(*) FROM prospects WHERE status = 'MEDIUM_PRIORITY'"),
            "low_priority": scalar("SELECT COUNT(*) FROM prospects WHERE status = 'LOW_PRIORITY'"),
            "rejected": scalar("SELECT COUNT(*) FROM prospects WHERE status = 'REJECTED'"),
            "discovered_today": scalar(
                "SELECT COUNT(*) FROM prospects WHERE date(date_discovered) = ?", (today,)
            ),
            "analyzed_today": scalar(
                "SELECT COUNT(*) FROM prospects WHERE date(date_analyzed) = ?", (today,)
            ),
            "queue_pending": queue_size("PENDING"),
        }


_ALLOWED_DISTRIBUTION_FIELDS = {
    "primary_category", "language", "country", "discovery_source", "account_category",
}


def distribution(field: str, limit: int = 10) -> list[tuple[str, int]]:
    if field not in _ALLOWED_DISTRIBUTION_FIELDS:
        raise ValueError(f"Unsupported distribution field: {field}")
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {field} as val, COUNT(*) as c FROM prospects
            WHERE {field} IS NOT NULL AND {field} != ''
            GROUP BY {field} ORDER BY c DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(r["val"], r["c"]) for r in rows]


def score_distribution() -> list[tuple[str, int]]:
    """Buckets final_score into 0-20, 20-40, ... 80-100 ranges."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT final_score FROM prospects WHERE final_score IS NOT NULL"
        ).fetchall()
    buckets = {f"{i}-{i+20}": 0 for i in range(0, 100, 20)}
    for r in rows:
        score = r["final_score"]
        bucket_idx = min(score // 20, 4) * 20
        buckets[f"{bucket_idx}-{bucket_idx+20}"] += 1
    return list(buckets.items())


_CSV_COLUMNS = [
    "Score", "Username", "Display Name", "Followers", "Category", "Language",
    "Country", "Prospect Type", "Reason", "Instagram URL", "Discovery Source",
    "Date Discovered", "Status",
]


def export_csv(path: Path, filters: Optional[dict] = None) -> int:
    rows = list_prospects(filters)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_COLUMNS)
        for r in rows:
            writer.writerow([
                r.get("final_score") if r.get("final_score") is not None else r.get("preliminary_score"),
                r.get("username"),
                r.get("display_name"),
                r.get("followers"),
                r.get("primary_category") or r.get("account_category"),
                r.get("language"),
                r.get("country"),
                ", ".join(r.get("prospect_types") or []),
                r.get("analysis_reason"),
                r.get("profile_url"),
                r.get("discovery_source"),
                r.get("date_discovered"),
                r.get("status"),
            ])
    return len(rows)


def top_discovery_keywords(limit: int = 10) -> list[tuple[str, int]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT discovery_keyword, COUNT(*) as c FROM prospects
            WHERE discovery_keyword IS NOT NULL
            GROUP BY discovery_keyword ORDER BY c DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(r["discovery_keyword"], r["c"]) for r in rows]
