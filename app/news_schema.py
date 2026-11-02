"""Additive storage for reviewed reporting, separate from official recordings."""

NEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS wyoming_news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    publisher TEXT NOT NULL,
    title TEXT,
    published_date TEXT,
    content_text TEXT,
    content_hash TEXT,
    bill_links_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    discovered_at TEXT NOT NULL,
    fetched_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wyoming_news_queue
ON wyoming_news_articles(status, next_attempt_at, discovered_at);

CREATE TABLE IF NOT EXISTS wyoming_news_reasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    article_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'wy' CHECK(state = 'wy'),
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    bill_num TEXT NOT NULL,
    roll_call_key TEXT NOT NULL,
    member_key TEXT NOT NULL,
    lawmaker_name TEXT NOT NULL,
    vote_position TEXT NOT NULL CHECK(vote_position IN ('yes', 'no')),
    reason_summary TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    attribution_text TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('reported_direct_quote', 'reported_paraphrase')),
    review_status TEXT NOT NULL DEFAULT 'review_hold',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(article_id, article_hash, year, special_session_key, bill_num, roll_call_key, member_key)
);
CREATE INDEX IF NOT EXISTS idx_wyoming_news_reasons_bill
ON wyoming_news_reasons(state, year, bill_num, special_session_key, review_status);
CREATE INDEX IF NOT EXISTS idx_wyoming_news_reasons_member
ON wyoming_news_reasons(state, member_key, year, review_status);
"""

# Keep official evidence untouched. Changed articles require a fresh review.
VOTE_EVIDENCE_CTE = """
WITH vote_evidence AS (
    SELECT id, state, year, special_session_key, special_session_value, bill_num,
           roll_call_key, member_key, lawmaker_name, vote_position, reason_summary,
           evidence_text, source_media_id, source_url, source_title,
           source_start_seconds, source_end_seconds, statement_date, source_kind,
           review_status, source_synced_at, created_at, updated_at,
           NULL AS source_publisher
    FROM bill_vote_explanations
    UNION ALL
    SELECT r.id, r.state, r.year, r.special_session_key, r.special_session_value, r.bill_num,
           r.roll_call_key, r.member_key, r.lawmaker_name, r.vote_position, r.reason_summary,
           r.evidence_text, NULL AS source_media_id, a.url AS source_url, a.title AS source_title,
           NULL AS source_start_seconds, NULL AS source_end_seconds,
           a.published_date AS statement_date, r.source_kind, r.review_status,
           a.fetched_at AS source_synced_at, r.created_at, r.updated_at,
           a.publisher AS source_publisher
    FROM wyoming_news_reasons r
    JOIN wyoming_news_articles a ON a.id = r.article_id AND a.content_hash = r.article_hash
    WHERE r.review_status = 'curated'
      AND EXISTS (SELECT 1 FROM bill_roll_call_votes v WHERE v.state=r.state AND v.year=r.year
        AND v.special_session_key=r.special_session_key AND v.bill_num=r.bill_num
        AND v.roll_call_key=r.roll_call_key AND v.member_key=r.member_key AND v.vote_position=r.vote_position)
)
"""
