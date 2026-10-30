"""The UnderCover store: SQLite, or Postgres in DATABASE_URL, seeded from data/."""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Connection,
    Engine,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    event,
    func,
    insert,
    inspect,
    select,
    text,
)

DATA = Path(__file__).parent.parent / "data"
DEFAULT_DB = Path(__file__).parent / "undercover.db"
SCHEMA = "undercover"
POOL_SIZE = 4
CHECKPOINT_TABLES = ["checkpoint_writes", "checkpoint_blobs", "checkpoints"]

metadata = MetaData()

claims = Table(
    "claims",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("data", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("decision", String),
    Column("payout", Float),
    Column("reason", Text),
    Column("policy_section", String),
    Column("paid_amount", Float),
    Column("paid_at", String),
    Column("signed_off_by", String),
    Column("signed_off_at", String),
    Column("attempts", Integer, nullable=False, default=0),
    Column("thread_id", String),
)
policies = Table(
    "policies",
    metadata,
    Column("policy_id", String, primary_key=True),
    Column("data", Text, nullable=False),
)
customers = Table(
    "customers",
    metadata,
    Column("customer_id", String, primary_key=True),
    Column("data", Text, nullable=False),
)
steps = Table(
    "steps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("text", Text, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("cap", Integer, nullable=False),
    Column("todos", Text, nullable=False, default="[]"),
    Column("summary", Text),
)
run_claims = Table(
    "run_claims",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False),
    Column("claim_id", String, nullable=False),
)

engines: dict[str, Engine] = {}
engines_lock = threading.Lock()


def load(name: str) -> Any:
    """Read one JSON file from data/."""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def now() -> str:
    """The time now in UTC, as ISO 8601 text."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def seed(conn: Connection) -> None:
    """Copy the queue, the policies and the customers from data/."""
    queue = [
        {"claim_id": c["claim_id"], "position": i, "data": json.dumps(c)}
        for i, c in enumerate(load("claims.json"))
    ]
    conn.execute(insert(claims), [{**c, "status": "waiting"} for c in queue])
    conn.execute(
        insert(policies),
        [
            {"policy_id": p["policy_id"], "data": json.dumps(p)}
            for p in load("policies.json")
        ],
    )
    conn.execute(
        insert(customers),
        [
            {"customer_id": c["customer_id"], "data": json.dumps(c)}
            for c in load("customers.json")
        ],
    )


def postgres_url() -> str | None:
    """DATABASE_URL for SQLAlchemy's psycopg driver, or None for SQLite."""
    url = os.getenv("DATABASE_URL")
    return url and url.replace("postgresql://", "postgresql+psycopg://", 1)


def postgres_engine(url: str) -> Engine:
    """An engine for Postgres that works in the undercover schema, with a small pool."""
    return create_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=0,
        connect_args={"options": f"-c search_path={SCHEMA}"},
    )


def make_engine() -> Engine:
    """An engine for Postgres, its schema made, or for the SQLite file."""
    url = postgres_url()
    if url:
        engine = postgres_engine(url)
        with engine.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        return engine
    engine = create_engine(f"sqlite:///{os.getenv('UNDERCOVER_DB', str(DEFAULT_DB))}")

    # Every SQLite transaction takes the write lock at its start, so two requests
    # can't both pass a check before either writes.
    @event.listens_for(engine, "connect")
    def no_implicit_transactions(dbapi_conn: Any, _: Any) -> None:
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin_immediate(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def engine() -> Engine:
    """The engine for this database, created, made and seeded on first use."""
    key = postgres_url() or os.getenv("UNDERCOVER_DB", str(DEFAULT_DB))
    with engines_lock:
        if key not in engines:
            made = make_engine()
            metadata.create_all(made)
            with made.begin() as conn:
                if conn.scalar(select(func.count()).select_from(claims)) == 0:
                    seed(conn)
            engines[key] = made
        return engines[key]


def connect() -> Connection:
    """Open a connection to the store."""
    return engine().connect()


def reseed() -> None:
    """Empty every table, the checkpointer's included, and seed again from data/."""
    with engine().begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(delete(table))
        present = inspect(conn).get_table_names()
        for table in CHECKPOINT_TABLES:
            if table in present:
                conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
        seed(conn)
