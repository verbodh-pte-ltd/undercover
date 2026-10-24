"""The UnderCover backend."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend import store

app = FastAPI(title="UnderCover")

INSTRUCTIONS = Path(__file__).parent / "instructions"
DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}


class Decision(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float = Field(ge=0)
    reason: str
    policy_section: str


class Payment(BaseModel):
    amount: float = Field(gt=0)


class SignOff(BaseModel):
    handler_id: str


class Step(BaseModel):
    kind: Literal["tool_call", "tool_result", "reply", "error"]
    text: str


class RunStart(BaseModel):
    cap: int = Field(gt=0)


class Todo(BaseModel):
    claim_id: str
    text: str
    state: Literal["to_do", "in_progress", "done", "left_for_handler"]


class RunFinish(BaseModel):
    summary: str


class ClaimStart(BaseModel):
    run_id: int


class Release(BaseModel):
    error: str


MAX_ATTEMPTS = 2


def get_db() -> Iterator[sqlite3.Connection]:
    """Give each request its own connection to the store."""
    conn = store.connect()
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


def claim_row(db: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
    """Return the claim's row, or raise 404."""
    row = db.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return row


def record(db: sqlite3.Connection, table: str, key: str, value: str) -> dict[str, Any]:
    """Return one policy or customer record, or raise 404."""
    row = db.execute(f"SELECT data FROM {table} WHERE {key} = ?", (value,)).fetchone()  # noqa: S608
    if row is None:
        raise HTTPException(404, f"{value} not found")
    return json.loads(row["data"])


def customer_name(db: sqlite3.Connection, customer_id: str) -> str:
    """Return the customer's name."""
    return record(db, "customers", "customer_id", customer_id)["name"]


def claim_view(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Combine the claim as filed with where it stands now."""
    claim = json.loads(row["data"])
    claim.pop("status")
    return {
        **claim,
        "customer_name": customer_name(db, claim["customer_id"]),
        "status": row["status"],
        "decision": row["decision"],
        "payout": row["payout"],
        "reason": row["reason"],
        "policy_section": row["policy_section"],
        "paid_amount": row["paid_amount"],
        "signed_off_by": row["signed_off_by"],
        "attempts": row["attempts"],
        "sections_read": sections_read(db, claim["claim_id"]),
    }


def sections_read(db: sqlite3.Connection, claim_id: str) -> list[str]:
    """The sections read on a claim, such as "claims_manual 9", taken from its steps."""
    rows = db.execute(
        "SELECT text FROM steps WHERE claim_id = ? AND kind = 'tool_call'"
        " AND text LIKE 'read_section(%' ORDER BY id",
        (claim_id,),
    ).fetchall()
    found = []
    for row in rows:
        args = json.loads(row["text"].removeprefix("read_section(").removesuffix(")"))
        section = f"{args['document']} {args['number']}"
        if section not in found:
            found.append(section)
    return found


def run_row(db: sqlite3.Connection, run_id: int) -> sqlite3.Row:
    """Return the run's row, or raise 404."""
    row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return row


def run_view(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """A run with its to-do list, its summary and the claims it has started."""
    started = db.execute(
        "SELECT claim_id FROM run_claims WHERE run_id = ? ORDER BY rowid",
        (row["run_id"],),
    ).fetchall()
    return {
        "run_id": row["run_id"],
        "cap": row["cap"],
        "todos": json.loads(row["todos"]),
        "summary": row["summary"],
        "claims_started": [r["claim_id"] for r in started],
    }


def release(db: sqlite3.Connection, claim_id: str) -> None:
    """Put a claim back in the queue, or leave it for a handler after two failures."""
    db.execute(
        "UPDATE claims SET attempts = attempts + 1,"
        " status = CASE WHEN attempts + 1 >= ? THEN 'needs_handler' ELSE 'waiting' END"
        " WHERE claim_id = ?",
        (MAX_ATTEMPTS, claim_id),
    )


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims(db: Db) -> list[dict[str, Any]]:
    """List the claims queue in order, with where each claim stands."""
    rows = db.execute("SELECT * FROM claims ORDER BY position").fetchall()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "customer_name"]
    fields += ["status", "decision", "payout", "paid_amount"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/waiting", operation_id="list_waiting_claims")
def list_waiting_claims(db: Db) -> list[dict[str, Any]]:
    """List the waiting claims in queue order, with their failed attempts."""
    rows = db.execute(
        "SELECT * FROM claims WHERE status = 'waiting' ORDER BY position"
    ).fetchall()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "attempts"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str, db: Db) -> dict[str, Any]:
    """Get one claim in full: the story, the items, the documents and its decision."""
    return claim_view(db, claim_row(db, claim_id))


@app.get("/api/policies/{policy_id}", operation_id="get_policy")
def get_policy(policy_id: str, db: Db) -> dict[str, Any]:
    """Get a policy: the plan, the add-ons, the trip and the travelers."""
    return record(db, "policies", "policy_id", policy_id)


@app.get("/api/customers/{customer_id}/claims", operation_id="get_claim_history")
def get_claim_history(customer_id: str, db: Db) -> list[dict[str, Any]]:
    """Get the customer's earlier claims, with the outcome and amount paid."""
    return record(db, "customers", "customer_id", customer_id)["previous_claims"]


@app.get("/api/policy-wording", operation_id="get_policy_wording")
def get_policy_wording() -> dict[str, str]:
    """Get the policy wording, the contract that decisions follow."""
    return {"text": (store.DATA / "policy_wording.md").read_text(encoding="utf-8")}


def sections(document: str) -> list[dict[str, str]]:
    """Split the policy wording or the claims manual into its numbered sections."""
    if document not in DOCUMENTS:
        raise HTTPException(404, f"Document {document} not found")
    text = (store.DATA / DOCUMENTS[document]).read_text(encoding="utf-8")
    found = []
    for part in text.split("\n## ")[1:]:
        heading = part.partition("\n")[0]
        number, _, title = heading.partition(". ")
        found.append({"number": number, "title": title, "text": f"## {part.strip()}"})
    return found


@app.get("/api/documents/{document}/sections", operation_id="list_sections")
def list_sections(document: str) -> list[dict[str, str]]:
    """List the numbered sections of policy_wording or claims_manual."""
    return [{"number": s["number"], "title": s["title"]} for s in sections(document)]


@app.get("/api/documents/{document}/sections/{number}", operation_id="read_section")
def read_section(document: str, number: str) -> dict[str, str]:
    """Read one numbered section of policy_wording or claims_manual."""
    for section in sections(document):
        if section["number"] == number:
            return {"document": document, **section}
    raise HTTPException(404, f"Section {number} of {document} not found")


@app.get("/api/instructions", operation_id="get_instructions")
def get_instructions() -> dict[str, str]:
    """Get the agent's instructions for running the queue and for working a claim."""
    return {
        name: (INSTRUCTIONS / f"{name}.md").read_text(encoding="utf-8")
        for name in ["queue", "claim"]
    }


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    return store.load("handlers.json")


@app.post("/api/runs", operation_id="start_run")
def start_run(start: RunStart, db: Db) -> dict[str, Any]:
    """Start a run capped at a number of claims; stuck claims go back in the queue."""
    with db:
        stuck = db.execute(
            "SELECT claim_id FROM claims WHERE status = 'in_progress'"
        ).fetchall()
        for row in stuck:
            release(db, row["claim_id"])
        run_id = db.execute("INSERT INTO runs (cap) VALUES (?)", (start.cap,)).lastrowid
    return run_view(db, run_row(db, run_id or 0))


@app.get("/api/runs/current", operation_id="get_current_run")
def get_current_run(db: Db) -> dict[str, Any] | None:
    """Get the latest run with its to-do list and summary, or null if there is none."""
    row = db.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    return None if row is None else run_view(db, row)


@app.put("/api/runs/{run_id}/todos", operation_id="set_todos")
def set_todos(run_id: int, todos: list[Todo], db: Db) -> dict[str, Any]:
    """Replace the run's to-do list with the whole list as it now stands."""
    run_row(db, run_id)
    with db:
        db.execute(
            "UPDATE runs SET todos = ? WHERE run_id = ?",
            (json.dumps([t.model_dump() for t in todos]), run_id),
        )
    return run_view(db, run_row(db, run_id))


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: int, finish: RunFinish, db: Db) -> dict[str, Any]:
    """Finish the run with a summary for the handlers, once no claim is missed."""
    run = run_view(db, run_row(db, run_id))
    under_cap = len(run["claims_started"]) < run["cap"]
    waiting = [c["claim_id"] for c in list_waiting_claims(db)]
    missed = [c for c in waiting if under_cap or c in run["claims_started"]]
    if missed:
        raise HTTPException(
            409, f"These claims are still waiting within the cap: {', '.join(missed)}"
        )
    with db:
        db.execute(
            "UPDATE runs SET summary = ? WHERE run_id = ?", (finish.summary, run_id)
        )
    return run_view(db, run_row(db, run_id))


@app.post("/api/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(claim_id: str, start: ClaimStart, db: Db) -> dict[str, Any]:
    """Mark a claim as in progress in a run; a first try clears any old steps."""
    with db:
        # One write lock for the checks and writes, so two starts can't pass the cap.
        db.execute("BEGIN IMMEDIATE")
        row = claim_row(db, claim_id)
        if row["status"] not in {"waiting", "in_progress"}:
            raise HTTPException(409, f"Claim {claim_id} is not waiting")
        run = run_view(db, run_row(db, start.run_id))
        started = run["claims_started"]
        if claim_id not in started and len(started) >= run["cap"]:
            cap = run["cap"]
            raise HTTPException(409, f"This run has reached its cap of {cap} claims")
        db.execute(
            "INSERT OR IGNORE INTO run_claims VALUES (?, ?)", (start.run_id, claim_id)
        )
        db.execute(
            "UPDATE claims SET status = 'in_progress' WHERE claim_id = ?", (claim_id,)
        )
        if row["attempts"] == 0:
            db.execute("DELETE FROM steps WHERE claim_id = ?", (claim_id,))
    return get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/release", operation_id="release_claim")
def release_claim(claim_id: str, failure: Release, db: Db) -> dict[str, Any]:
    """Release a claim that failed before its decision, recording the error."""
    if claim_row(db, claim_id)["status"] != "in_progress":
        raise HTTPException(409, f"Claim {claim_id} is not in progress")
    with db:
        db.execute(
            "INSERT INTO steps (claim_id, kind, text) VALUES (?, 'error', ?)",
            (claim_id, failure.error),
        )
        release(db, claim_id)
    return get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/steps", operation_id="add_step")
def add_step(claim_id: str, step: Step, db: Db) -> dict[str, str]:
    """Record one step the agent took on a claim."""
    claim_row(db, claim_id)
    with db:
        db.execute(
            "INSERT INTO steps (claim_id, kind, text) VALUES (?, ?, ?)",
            (claim_id, step.kind, step.text),
        )
    return {"status": "recorded"}


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, db: Db) -> list[dict[str, Any]]:
    """List the steps the agent took on a claim, in order."""
    claim_row(db, claim_id)
    rows = db.execute(
        "SELECT id, kind, text FROM steps WHERE claim_id = ? ORDER BY id", (claim_id,)
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_decision(claim_id: str, decision: Decision, db: Db) -> dict[str, Any]:
    """Approve, decline or refer a claim, with payout, reasons and policy section."""
    if claim_row(db, claim_id)["status"] not in {"waiting", "in_progress"}:
        raise HTTPException(409, f"Claim {claim_id} has already been decided")
    if decision.decision != "approve" and decision.payout > 0:
        raise HTTPException(422, "Only an approved claim has a payout above zero")
    over_limit = decision.payout > store.load("company.json")["payout_approval_limit"]
    with db:
        db.execute(
            "UPDATE claims SET status = ?, decision = ?, payout = ?, reason = ?,"
            " policy_section = ? WHERE claim_id = ?",
            (
                "waiting_for_sign_off" if over_limit else "decided",
                decision.decision,
                decision.payout,
                decision.reason,
                decision.policy_section,
                claim_id,
            ),
        )
    return get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim(claim_id: str, payment: Payment, db: Db) -> dict[str, Any]:
    """Pay an approved claim its payout. A payout over $1,000 needs sign-off first."""
    row = claim_row(db, claim_id)
    if row["decision"] != "approve":
        raise HTTPException(409, f"Claim {claim_id} is not approved")
    if row["paid_amount"] is not None:
        raise HTTPException(409, f"Claim {claim_id} has already been paid")
    limit = store.load("company.json")["payout_approval_limit"]
    if row["payout"] > limit and row["signed_off_by"] is None:
        raise HTTPException(
            403, f"A payout over ${limit:,.2f} needs a handler's sign-off first"
        )
    if round(payment.amount, 2) != round(row["payout"], 2):
        raise HTTPException(
            422, f"The amount must be the approved payout, ${row['payout']:,.2f}"
        )
    with db:
        db.execute(
            "UPDATE claims SET paid_amount = payout WHERE claim_id = ?", (claim_id,)
        )
    return get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off")
def sign_off(claim_id: str, sign_off: SignOff, db: Db) -> dict[str, Any]:
    """Sign off a payout over $1,000 as a handler, which pays it."""
    handlers = {h["handler_id"] for h in store.load("handlers.json")}
    if sign_off.handler_id not in handlers:
        raise HTTPException(404, f"Handler {sign_off.handler_id} not found")
    if claim_row(db, claim_id)["status"] != "waiting_for_sign_off":
        raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off")
    with db:
        db.execute(
            "UPDATE claims SET status = 'decided', signed_off_by = ?,"
            " paid_amount = payout WHERE claim_id = ?",
            (sign_off.handler_id, claim_id),
        )
    return get_claim(claim_id, db)
