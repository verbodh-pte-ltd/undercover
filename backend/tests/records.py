"""Runs and decisions as the app and the agent record them: a decision with a
matching calculation first, and a maker."""

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update

from backend import store
from backend.main import RunCreate, insert_run

MAKER = "UnderCover Agent, version 1"


def new_run(
    kind: str = "dispatch",
    eval_set: str | None = None,
    tiny: bool = False,
    n: int = 3,
    version: int = 1,
) -> int:
    """Record a run, with an evaluation's copies, as the app does when a handler starts
    one, without the slot or the agent."""
    create = RunCreate.model_validate(
        {
            "kind": kind,
            "n": n,
            "eval_set": eval_set,
            "tiny": tiny,
            "model": "stub",
            "effort": "none",
            "version": version,
        }
    )
    with store.connect() as db:
        run_id = insert_run(db, create, None)
        db.commit()
    return run_id


def calculated(claim_id: str, payout: float) -> None:
    """Put a calculation that comes to the payout on file, as calculate_payout does."""
    found = {"lines": [{"step": "Covered costs", "amount": payout}], "payout": payout}
    with store.connect() as conn:
        conn.execute(
            update(store.claims)
            .where(store.claims.c.claim_id == claim_id)
            .values(latest_calculation=json.dumps(found))
        )
        conn.commit()


def record_decision(client: TestClient, path: str, body: dict, **kwargs: Any) -> Any:
    """POST a decision to its path, with a calculation for an approval that pays."""
    if body["decision"] == "approve" and body["payout"] > 0:
        calculated(path.split("/")[3], body["payout"])
    return client.post(path, json={"decided_by": MAKER, **body}, **kwargs)
