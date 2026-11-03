"""The one piece of the agent's work going at a time: a run or a round.

It lives in the agent_work row. Taking it is one conditional update of that row,
checking the row count, which holds on SQLite, on Postgres and through a transaction
pooler, where a check followed by an insert doesn't.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import Connection, RowMapping, select, update

from backend import store
from backend.store import agent_work

# Work the app hasn't heard about for this long counts as stopped.
QUIET = timedelta(minutes=5)
Kind = Literal["run", "round"]


def holder(db: Connection) -> RowMapping:
    """The slot's row: what holds it, when it started and when it was last heard."""
    return db.execute(select(agent_work).where(agent_work.c.id == 1)).mappings().one()


def quiet(row: RowMapping) -> bool:
    """Whether the work holding the slot has gone five minutes without a word."""
    cutoff = (datetime.now(UTC) - QUIET).isoformat(timespec="seconds")
    return row["heard_at"] < cutoff


def free_or_quiet(row: RowMapping) -> bool:
    """Whether new work may take the slot from what holds it."""
    return row["kind"] is None or quiet(row)


def swap(db: Connection, before: RowMapping, kind: Kind, work_id: int) -> bool:
    """Take the slot from what held it when read; False if anything changed since."""
    now = store.now()
    query = update(agent_work).where(
        agent_work.c.id == 1,
        agent_work.c.kind.is_not_distinct_from(before["kind"]),
        agent_work.c.work_id.is_not_distinct_from(before["work_id"]),
        agent_work.c.heard_at.is_not_distinct_from(before["heard_at"]),
    )
    taken = db.execute(
        query.values(kind=kind, work_id=work_id, started_at=now, heard_at=now)
    )
    return taken.rowcount == 1


def holding(kind: Kind, work_id: int) -> list:
    """The conditions on the slot's row for one run or round holding it."""
    return [agent_work.c.kind == kind, agent_work.c.work_id == work_id]


def free(db: Connection, kind: Kind, work_id: int) -> None:
    """Free the slot if this run or round holds it."""
    query = update(agent_work).where(*holding(kind, work_id))
    db.execute(query.values(kind=None, work_id=None, started_at=None, heard_at=None))


def heard(db: Connection, kind: Kind, work_id: int) -> bool:
    """Note the agent's word on a run or round; False if it no longer holds the slot."""
    query = update(agent_work).where(*holding(kind, work_id))
    return db.execute(query.values(heard_at=store.now())).rowcount == 1
