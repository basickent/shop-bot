import sqlite3
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS categories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   TEXT,
    sort_order  INTEGER DEFAULT 0,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category_id TEXT,
    price       REAL NOT NULL,
    image_url   TEXT,
    description TEXT,
    available   INTEGER DEFAULT 1,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    username      TEXT,
    first_name    TEXT,
    items_json    TEXT NOT NULL,
    total         REAL NOT NULL,
    customer_name TEXT,
    phone         TEXT,
    address       TEXT,
    comment       TEXT,
    status        TEXT DEFAULT 'new',
    created_at    TEXT NOT NULL
);
"""

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        conn.commit()

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")