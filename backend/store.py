"""The UnderCover store: SQLite, seeded from data/ on first use."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent.parent / "data"
DEFAULT_DB = Path(__file__).parent / "undercover.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    data TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    payout REAL,
    reason TEXT,
    policy_section TEXT,
    paid_amount REAL,
    signed_off_by TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS policies (policy_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customers (customer_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cap INTEGER NOT NULL,
    todos TEXT NOT NULL DEFAULT '[]',
    summary TEXT
);
CREATE TABLE IF NOT EXISTS run_claims (
    run_id INTEGER NOT NULL,
    claim_id TEXT NOT NULL,
    PRIMARY KEY (run_id, claim_id)
);
"""


def load(name: str) -> Any:
    """Read one JSON file from data/."""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def seed(conn: sqlite3.Connection) -> None:
    """Copy the queue, the policies and the customers from data/."""
    claims = load("claims.json")
    conn.executemany(
        "INSERT INTO claims (claim_id, position, data, status) VALUES (?, ?, ?, ?)",
        [(c["claim_id"], i, json.dumps(c), "waiting") for i, c in enumerate(claims)],
    )
    conn.executemany(
        "INSERT INTO policies VALUES (?, ?)",
        [(p["policy_id"], json.dumps(p)) for p in load("policies.json")],
    )
    conn.executemany(
        "INSERT INTO customers VALUES (?, ?)",
        [(c["customer_id"], json.dumps(c)) for c in load("customers.json")],
    )


def connect() -> sqlite3.Connection:
    """Open the database named in UNDERCOVER_DB, creating and seeding it if new."""
    # FastAPI opens the connection and runs the route on different threads; each
    # connection is still used by one request at a time.
    conn = sqlite3.connect(
        os.getenv("UNDERCOVER_DB", str(DEFAULT_DB)), check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0:
            seed(conn)
    return conn
