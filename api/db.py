"""SQLite persistence for user accounts and saved analyses.

Uses the Python standard-library ``sqlite3`` driver (no extra dependency).
The database file lives at the repo root as ``atlas.db`` and is gitignored.
Connections are per-request and stored on Flask's ``g`` so they are reused
within a request and closed afterwards.
"""

import os
import sqlite3

from flask import g

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "atlas.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    kind              TEXT    NOT NULL,           -- 'session' | 'comparison'
    created_at        TEXT    NOT NULL,
    original_filename TEXT,
    video_key         TEXT,
    shot_type         TEXT,
    comparison_pro    TEXT,
    summary_json      TEXT,
    results_json      TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses (user_id, id DESC);
"""


def get_db() -> sqlite3.Connection:
    """Return the request-scoped SQLite connection, opening one if needed."""
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    init_db()
