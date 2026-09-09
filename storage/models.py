"""SQLite schema definitions for the Fableya prospector."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    profile_url TEXT,
    bio TEXT,
    followers INTEGER,
    following INTEGER,
    post_count INTEGER,
    external_url TEXT,
    account_category TEXT,
    language TEXT,
    country TEXT,

    discovery_source TEXT,
    discovery_keyword TEXT,
    discovered_from_username TEXT,

    preliminary_score INTEGER,
    final_score INTEGER,

    prospect_types TEXT,          -- JSON array
    primary_category TEXT,
    likely_parent INTEGER,        -- 0/1/NULL
    likely_parent_audience INTEGER,
    children_age_relevance TEXT,

    positive_signals TEXT,        -- JSON array
    negative_signals TEXT,        -- JSON array
    fableya_fit TEXT,             -- JSON array
    analysis_reason TEXT,
    recommended_action TEXT,

    outreach_message TEXT,
    outreach_status TEXT DEFAULT 'NOT_DRAFTED',  -- NOT_DRAFTED, DRAFTED, SENT

    status TEXT DEFAULT 'DISCOVERED',

    date_discovered TEXT NOT NULL,
    date_last_checked TEXT,
    date_analyzed TEXT,

    analysis_version INTEGER,
    profile_data_hash TEXT,       -- hash of the data last sent to Gemini (avoid re-analysis)
    stage2_data_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_prospects_status ON prospects(status);
CREATE INDEX IF NOT EXISTS idx_prospects_final_score ON prospects(final_score);
CREATE INDEX IF NOT EXISTS idx_prospects_username ON prospects(username);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    post_url TEXT,
    caption TEXT,
    hashtags TEXT,       -- JSON array
    post_date TEXT,
    likes INTEGER,
    comments_count INTEGER,
    date_collected TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_posts_prospect ON posts(prospect_id);

CREATE TABLE IF NOT EXISTS comments_sample (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    comment_text TEXT,
    date_collected TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_post ON comments_sample(post_id);

CREATE TABLE IF NOT EXISTS discovery_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    priority REAL DEFAULT 0.0,
    discovery_source TEXT,
    discovery_keyword TEXT,
    discovered_from_username TEXT,
    status TEXT DEFAULT 'PENDING',  -- PENDING, PROCESSING, DONE, SKIPPED
    date_added TEXT NOT NULL,
    UNIQUE(username)
);

CREATE INDEX IF NOT EXISTS idx_queue_status_priority ON discovery_queue(status, priority DESC);

CREATE TABLE IF NOT EXISTS session_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_start TEXT NOT NULL,
    session_end TEXT,
    profiles_discovered INTEGER DEFAULT 0,
    profiles_analyzed_stage1 INTEGER DEFAULT 0,
    profiles_analyzed_stage2 INTEGER DEFAULT 0,
    gemini_calls INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0
);
"""

VALID_STATUSES = {
    "DISCOVERED",
    "PREQUALIFIED",
    "DEEP_ANALYSIS_PENDING",
    "HIGH_PRIORITY",
    "MEDIUM_PRIORITY",
    "LOW_PRIORITY",
    "REJECTED",
    "REVIEWED",
}
