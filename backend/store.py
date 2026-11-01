"""The UnderCover store: SQLite, or Postgres in DATABASE_URL, seeded from data/."""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
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
    event,
    func,
    insert,
    inspect,
    select,
    text,
)

DATA = Path(__file__).parent.parent / "data"
DEFAULT_DB = Path(__file__).parent / "undercover.db"
INSTRUCTIONS = Path(__file__).parent / "instructions"
FIRST_MODEL = "openai/gpt-6-luna"
FIRST_EFFORT = "low"
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
    # Set with the decision, from the approval limit at that moment.
    Column("needs_sign_off", Boolean, nullable=False, default=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("thread_id", String),
    # Set only on an evaluation's copy of a claim: its run and the claim it copies.
    Column("run_id", Integer),
    Column("copied_from", String),
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
    Column("kind", String, nullable=False),
    Column("n", Integer, nullable=False),
    Column("eval_set", String),
    Column("tiny", Boolean, nullable=False, default=False),
    Column("model", String, nullable=False),
    Column("effort", String, nullable=False),
    Column("status", String, nullable=False, default="running"),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
    # The configuration version the run's claim agents work to, and the round that
    # ran it, if any.
    Column("version", Integer, nullable=False),
    Column("round_id", Integer),
)
run_claims = Table(
    "run_claims",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, nullable=False),
    Column("claim_id", String, nullable=False),
)

model_calls = Table(
    "model_calls",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("run_id", Integer),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cost", Float, nullable=False),
)
work_times = Table(
    "work_times",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("run_id", Integer),
    Column("seconds", Float, nullable=False),
)
# The handlers' decisions on both labelled sets, and the held-out claims. No claim
# operation reads these; only scoring an evaluation does.
answer_key = Table(
    "answer_key",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("eval_set", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("payout", Float, nullable=False),
    Column("tiny", Boolean, nullable=False),
)
held_out_claims = Table(
    "held_out_claims",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("data", Text, nullable=False),
)
handlers = Table(
    "handlers",
    metadata,
    Column("handler_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("role", String, nullable=False),
    Column("email", String, nullable=False, unique=True),
    Column("can_approve_over_limit", Boolean, nullable=False),
)
# The platform's settings, one row each as JSON, and who changed which and when.
settings = Table(
    "settings",
    metadata,
    Column("name", String, primary_key=True),
    Column("value", Text, nullable=False),
)
setting_changes = Table(
    "setting_changes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("value", Text, nullable=False),
    Column("handler_id", String, nullable=False),
    Column("changed_at", String, nullable=False),
)
# The claim agent's configuration in versions. A version under test has kept None;
# the current version is the newest one kept.
config_versions = Table(
    "config_versions",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("instructions", Text, nullable=False),
    Column("model", String, nullable=False),
    Column("effort", String, nullable=False),
    Column("reason", Text, nullable=False),
    Column("proposed_by", String, nullable=False),
    Column("approved_by", String, nullable=False),
    Column("approved_at", String, nullable=False),
    Column("kept", Boolean),
)
# The improver's recipe in versions; the newest is the one it follows.
recipes = Table(
    "recipes",
    metadata,
    Column("version", Integer, primary_key=True),
    Column("text", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("proposed_by", String, nullable=False),
    Column("approved_by", String, nullable=False),
    Column("approved_at", String, nullable=False),
)
EVAL_SETS = {
    "evaluation": ("claims.json", "evals/decisions.json"),
    "held_out": ("evals/held_out_claims.json", "evals/held_out_decisions.json"),
}

engines: dict[str, Engine] = {}
engines_lock = threading.Lock()


def load(name: str) -> Any:
    """Read one JSON file from data/."""
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def now() -> str:
    """The time now in UTC, as ISO 8601 text."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def seed_settings(conn: Connection) -> None:
    """Copy the approval limit from company.json and the rest from improvement.json."""
    values = {
        "approval_limit": load("company.json")["payout_approval_limit"],
        **load("improvement.json"),
    }
    conn.execute(
        insert(settings),
        [{"name": name, "value": json.dumps(v)} for name, v in values.items()],
    )


def seed_versions(conn: Connection) -> None:
    """Make version 1 of the configuration and of the recipe from instructions/."""
    first = {"proposed_by": "seed", "approved_by": "seed", "approved_at": now()}
    conn.execute(
        insert(config_versions).values(
            version=1,
            instructions=(INSTRUCTIONS / "claim.md").read_text(encoding="utf-8"),
            model=FIRST_MODEL,
            effort=FIRST_EFFORT,
            reason="The claim agent as it was before versions",
            kept=True,
            **first,
        )
    )
    conn.execute(
        insert(recipes).values(
            version=1,
            text=(INSTRUCTIONS / "recipe.md").read_text(encoding="utf-8"),
            reason="The first recipe, from the lab",
            **first,
        )
    )


def seed(conn: Connection) -> None:
    """Copy the queue, the records, the handlers and the settings; make the versions."""
    conn.execute(insert(handlers), load("handlers.json"))
    seed_settings(conn)
    seed_versions(conn)
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
    conn.execute(
        insert(held_out_claims),
        [
            {"claim_id": c["claim_id"], "data": json.dumps(c)}
            for c in load("evals/held_out_claims.json")
        ],
    )
    for eval_set, (_, decisions) in EVAL_SETS.items():
        conn.execute(
            insert(answer_key),
            [
                {
                    "claim_id": d["claim_id"],
                    "eval_set": eval_set,
                    "decision": d["decision"],
                    "payout": d["payout"],
                    "tiny": d["tiny"],
                }
                for d in load(decisions)
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
    """Rebuild every table, empty the checkpointer's, and seed again from data/."""
    metadata.drop_all(engine())
    metadata.create_all(engine())
    with engine().begin() as conn:
        present = inspect(conn).get_table_names()
        for table in CHECKPOINT_TABLES:
            if table in present:
                conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
        seed(conn)
