"""SQLite persistence. One file, no ORM: the schema is small and the demo should run anywhere."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS contractors (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    business_name TEXT NOT NULL,
    owner_name TEXT NOT NULL,
    trade TEXT NOT NULL DEFAULT 'general contractor',
    phone_number TEXT UNIQUE,              -- inbound number callers dial (E.164)
    notify_email TEXT NOT NULL,            -- where invites / urgent alerts go
    notify_phone TEXT,
    timezone TEXT NOT NULL DEFAULT 'America/Toronto',
    business_hours TEXT NOT NULL,          -- JSON {"mon": ["08:00","17:00"], ...}
    visit_duration_min INTEGER NOT NULL DEFAULT 60,
    calendar_mailbox TEXT,                 -- Outlook mailbox (UPN) for Graph; NULL = mock
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    filename TEXT NOT NULL,
    content TEXT NOT NULL,                 -- normalised text (markdown-style headings)
    token_estimate INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    contractor_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    uid TEXT UNIQUE NOT NULL,              -- record id in Pinecone: c{contractor}-d{doc}-{idx}
    section TEXT NOT NULL,                 -- heading path, e.g. 'Roof Repairs'
    text TEXT NOT NULL,                    -- includes the contextual header
    synced INTEGER NOT NULL DEFAULT 0      -- 1 once upserted to Pinecone
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    contractor_id INTEGER NOT NULL REFERENCES contractors(id),
    external_id TEXT UNIQUE NOT NULL,      -- Twilio CallSid or simulator id
    channel TEXT NOT NULL,                 -- 'phone' | 'simulator'
    caller_number TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- 'active' | 'completed'
    caller_name TEXT,
    caller_email TEXT,
    caller_phone TEXT,
    site_address TEXT,
    job_summary TEXT,
    outcome TEXT,                          -- short label the agent sets at wrap-up
    summary TEXT,
    messages_json TEXT NOT NULL DEFAULT '[]',  -- raw Messages API history (append-only)
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS transcript (
    id INTEGER PRIMARY KEY,
    call_id INTEGER NOT NULL REFERENCES calls(id),
    role TEXT NOT NULL,                    -- 'caller' | 'agent' | 'tool'
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    call_id INTEGER REFERENCES calls(id),
    contractor_id INTEGER NOT NULL,
    type TEXT NOT NULL,                    -- tool name
    payload TEXT NOT NULL,                 -- JSON input
    result TEXT NOT NULL,                  -- JSON output
    ok INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    contractor_id INTEGER NOT NULL,
    call_id INTEGER,
    title TEXT NOT NULL,
    start TEXT NOT NULL,                   -- ISO8601 with offset
    end TEXT NOT NULL,
    location TEXT,
    attendees TEXT NOT NULL,               -- JSON list of emails
    provider TEXT NOT NULL,                -- 'mock' | 'graph'
    external_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    contractor_id INTEGER NOT NULL,
    call_id INTEGER,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_html TEXT NOT NULL,
    ics TEXT,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_contractor ON chunks(contractor_id);
CREATE INDEX IF NOT EXISTS idx_events_contractor ON events(contractor_id, start);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = settings.database_path
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as db:
        db.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("business_hours", "attendees", "payload", "result"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d
