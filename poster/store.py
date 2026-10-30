"""SQLite state: every story the poster published or turned down, and a few counters."""

import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    status TEXT NOT NULL,          -- posted | rejected
    url TEXT NOT NULL,
    topic TEXT, headline TEXT, caption TEXT, media TEXT, reason TEXT
);
CREATE INDEX IF NOT EXISTS stories_url ON stories (url);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def add(self, status: str, url: str, **fields) -> None:
        cols = ["ts", "status", "url", *fields]
        self.db.execute(
            f"INSERT INTO stories ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (time.time(), status, url, *fields.values()),
        )
        self.db.commit()

    def seen(self, urls) -> bool:
        return any(
            self.db.execute("SELECT 1 FROM stories WHERE url=?", (u,)).fetchone() for u in urls
        )

    def posted_since(self, ts: float) -> int:
        return self.db.execute(
            "SELECT count(*) FROM stories WHERE status='posted' AND ts>=?", (ts,)
        ).fetchone()[0]

    def recent(self, since: float):
        return self.db.execute(
            "SELECT ts, status, url, topic, headline, reason FROM stories WHERE ts>=? ORDER BY ts",
            (since,),
        ).fetchall()
