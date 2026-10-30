"""The UnderCover backend."""

import json
import os
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection, RowMapping, case, delete, insert, select, update

from backend import payout, store
from backend.store import claims, customers, policies, run_claims, runs, steps

app = FastAPI(title="UnderCover")

INSTRUCTIONS = Path(__file__).parent / "instructions"
DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}
AGENT_URL = "http://127.0.0.1:8002"
AGENT_TIMEOUT = 5


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


class PayoutQuestion(BaseModel):
    policy_id: str
    benefit: payout.Benefit
    lines: list[payout.Line]
    paid_by_others: float = Field(ge=0)


MAX_ATTEMPTS = 2


def get_db() -> Iterator[Connection]:
    """Give each request its own connection to the store."""
    with store.connect() as conn:
        yield conn


Db = Annotated[Connection, Depends(get_db)]


def claim_row(db: Connection, claim_id: str, lock: bool = False) -> RowMapping:
    """Return the claim's row, or raise 404; lock it for the transaction if asked."""
    query = select(claims).where(claims.c.claim_id == claim_id)
    row = db.execute(query.with_for_update() if lock else query).mappings().first()
    if row is None:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return row


def record(db: Connection, table: str, value: str) -> dict[str, Any]:
    """Return one policy or customer record, or raise 404."""
    found = {"policies": policies, "customers": customers}[table]
    key = next(iter(found.primary_key.columns))
    data = db.scalar(select(found.c.data).where(key == value))
    if data is None:
        raise HTTPException(404, f"{value} not found")
    return json.loads(data)


def customer_name(db: Connection, customer_id: str) -> str:
    """Return the customer's name."""
    return record(db, "customers", customer_id)["name"]


def claim_view(db: Connection, row: RowMapping) -> dict[str, Any]:
    """Combine the claim as filed with where it stands now."""
    claim = json.loads(row["data"])
    claim.pop("status")
    fields = ["status", "decision", "payout", "reason", "policy_section"]
    fields += ["paid_amount", "paid_at", "signed_off_by", "signed_off_at"]
    fields += ["attempts", "thread_id"]
    return {
        **claim,
        "customer_name": customer_name(db, claim["customer_id"]),
        **{f: row[f] for f in fields},
        "sections_read": sections_read(db, claim["claim_id"]),
    }


def sections_read(db: Connection, claim_id: str) -> list[str]:
    """The sections read on a claim, such as "claims_manual 9", taken from its steps."""
    texts = db.scalars(
        select(steps.c.text)
        .where(
            steps.c.claim_id == claim_id,
            steps.c.kind == "tool_call",
            steps.c.text.like("read_section(%"),
        )
        .order_by(steps.c.id)
    )
    found = []
    for step_text in texts:
        # The step holds the model's own arguments, which can be malformed.
        try:
            args = json.loads(step_text.removeprefix("read_section(")[:-1])
            section = f"{args['document']} {args['number']}"
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if section not in found:
            found.append(section)
    return found


def run_row(db: Connection, run_id: int, lock: bool = False) -> RowMapping:
    """Return the run's row, or raise 404; lock it for the transaction if asked."""
    query = select(runs).where(runs.c.run_id == run_id)
    row = db.execute(query.with_for_update() if lock else query).mappings().first()
    if row is None:
        raise HTTPException(404, f"Run {run_id} not found")
    return row


def run_view(db: Connection, row: RowMapping) -> dict[str, Any]:
    """A run with its to-do list, its summary and the claims it has started."""
    started = db.scalars(
        select(run_claims.c.claim_id)
        .where(run_claims.c.run_id == row["run_id"])
        .order_by(run_claims.c.id)
    )
    return {
        "run_id": row["run_id"],
        "cap": row["cap"],
        "todos": json.loads(row["todos"]),
        "summary": row["summary"],
        "claims_started": list(started),
    }


def set_claim(db: Connection, claim_id: str, **values: Any) -> None:
    """Change some of a claim's columns."""
    db.execute(update(claims).where(claims.c.claim_id == claim_id).values(**values))


def add_step_row(db: Connection, claim_id: str, kind: str, step_text: str) -> None:
    """Record one step on a claim."""
    db.execute(insert(steps).values(claim_id=claim_id, kind=kind, text=step_text))


def release(db: Connection, claim_id: str) -> None:
    """Put a claim back in the queue, or leave it for a handler after two failures."""
    set_claim(
        db,
        claim_id,
        attempts=claims.c.attempts + 1,
        thread_id=None,
        status=case(
            (claims.c.attempts + 1 >= MAX_ATTEMPTS, "needs_handler"), else_="waiting"
        ),
    )


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims(db: Db) -> list[dict[str, Any]]:
    """List the claims queue in order, with where each claim stands."""
    rows = db.execute(select(claims).order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "customer_name"]
    fields += ["status", "decision", "payout", "paid_amount"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/waiting", operation_id="list_waiting_claims")
def list_waiting_claims(db: Db) -> list[dict[str, Any]]:
    """List the waiting claims in queue order, with their failed attempts."""
    query = select(claims).where(claims.c.status == "waiting")
    rows = db.execute(query.order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "attempts"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/unfinished", operation_id="list_unfinished_claims")
def list_unfinished_claims(db: Db) -> list[dict[str, Any]]:
    """List the claims in progress, stopped or signed off, for the agent to carry on."""
    query = select(claims).where(claims.c.status == "in_progress")
    rows = db.execute(query.order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "thread_id", "attempts", "decision", "signed_off_by"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str, db: Db) -> dict[str, Any]:
    """Get one claim in full: the story, the items, the documents and its decision."""
    return claim_view(db, claim_row(db, claim_id))


@app.get("/api/policies/{policy_id}", operation_id="get_policy")
def get_policy(policy_id: str, db: Db) -> dict[str, Any]:
    """Get a policy: the plan, the add-ons, the trip and the travelers."""
    return record(db, "policies", policy_id)


@app.get("/api/customers/{customer_id}/claims", operation_id="get_claim_history")
def get_claim_history(customer_id: str, db: Db) -> list[dict[str, Any]]:
    """Get the customer's earlier claims, with the outcome and amount paid."""
    return record(db, "customers", customer_id)["previous_claims"]


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


@app.post("/api/payouts", operation_id="work_out_payout")
def work_out_payout(question: PayoutQuestion, db: Db) -> payout.Payout:
    """Work out the payout on an approved claim with UnderCover's payout rules."""
    policy = record(db, "policies", question.policy_id)
    return payout.work_out_payout(
        policy, question.benefit, question.lines, question.paid_by_others
    )


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
    """Start a run capped at a number of claims."""
    new_run = insert(runs).values(cap=start.cap).returning(runs.c.run_id)
    run_id = db.execute(new_run).scalar_one()
    db.commit()
    return run_view(db, run_row(db, run_id))


@app.get("/api/runs/current", operation_id="get_current_run")
def get_current_run(db: Db) -> dict[str, Any] | None:
    """Get the latest run with its to-do list and summary, or null if there is none."""
    query = select(runs).order_by(runs.c.run_id.desc()).limit(1)
    row = db.execute(query).mappings().first()
    return None if row is None else run_view(db, row)


@app.put("/api/runs/{run_id}/todos", operation_id="set_todos")
def set_todos(run_id: int, todos: list[Todo], db: Db) -> dict[str, Any]:
    """Replace the run's to-do list with the whole list as it now stands."""
    run_row(db, run_id)
    todo_text = json.dumps([t.model_dump() for t in todos])
    db.execute(update(runs).where(runs.c.run_id == run_id).values(todos=todo_text))
    db.commit()
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
    query = update(runs).where(runs.c.run_id == run_id)
    db.execute(query.values(summary=finish.summary))
    db.commit()
    return run_view(db, run_row(db, run_id))


@app.post("/api/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(claim_id: str, start: ClaimStart, db: Db) -> dict[str, Any]:
    """Start a claim in a run on its thread; a first try clears any old steps."""
    # Lock the run first, so two starts can't both pass the cap.
    run = run_view(db, run_row(db, start.run_id, lock=True))
    row = claim_row(db, claim_id, lock=True)
    if row["status"] not in {"waiting", "in_progress"}:
        raise HTTPException(409, f"Claim {claim_id} is not waiting")
    started = run["claims_started"]
    if claim_id not in started:
        if len(started) >= run["cap"]:
            cap = run["cap"]
            raise HTTPException(409, f"This run has reached its cap of {cap} claims")
        db.execute(insert(run_claims).values(run_id=start.run_id, claim_id=claim_id))
    # A new try gets a new thread; a claim already in progress keeps its own.
    thread_id = row["thread_id"] or f"{claim_id}-{row['attempts'] + 1}"
    set_claim(db, claim_id, status="in_progress", thread_id=thread_id)
    if row["attempts"] == 0:
        db.execute(delete(steps).where(steps.c.claim_id == claim_id))
    db.commit()
    return get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/release", operation_id="release_claim")
def release_claim(claim_id: str, failure: Release, db: Db) -> dict[str, Any]:
    """Release a claim that failed before its decision, recording the error."""
    row = claim_row(db, claim_id, lock=True)
    if row["status"] != "in_progress" or row["decision"] is not None:
        raise HTTPException(
            409, f"Claim {claim_id} is not in progress before a decision"
        )
    add_step_row(db, claim_id, "error", failure.error)
    release(db, claim_id)
    db.commit()
    return get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/steps", operation_id="add_step")
def add_step(claim_id: str, step: Step, db: Db) -> dict[str, str]:
    """Record one step the agent took on a claim."""
    claim_row(db, claim_id)
    add_step_row(db, claim_id, step.kind, step.text)
    db.commit()
    return {"status": "recorded"}


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, db: Db) -> list[dict[str, Any]]:
    """List the steps the agent took on a claim, in order."""
    claim_row(db, claim_id)
    query = select(steps.c.id, steps.c.kind, steps.c.text)
    query = query.where(steps.c.claim_id == claim_id).order_by(steps.c.id)
    return [dict(r) for r in db.execute(query).mappings()]


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_decision(claim_id: str, decision: Decision, db: Db) -> dict[str, Any]:
    """Approve, decline or refer a claim, with payout, reasons and policy section."""
    row = claim_row(db, claim_id, lock=True)
    if row["decision"] is not None:
        # A step run again after a restart repeats its decision, which is done.
        same = (row["decision"], row["payout"]) == (decision.decision, decision.payout)
        if same:
            return get_claim(claim_id, db)
        raise HTTPException(409, f"Claim {claim_id} has already been decided")
    if row["status"] not in {"waiting", "in_progress"}:
        raise HTTPException(409, f"Claim {claim_id} is not waiting or in progress")
    if decision.decision != "approve" and decision.payout > 0:
        raise HTTPException(422, "Only an approved claim has a payout above zero")
    over_limit = decision.payout > store.load("company.json")["payout_approval_limit"]
    status = "waiting_for_sign_off" if over_limit else "decided"
    set_claim(db, claim_id, status=status, **decision.model_dump())
    db.commit()
    return get_claim(claim_id, db)


@app.post("/api/claims/{claim_id}/payment", operation_id="pay_claim")
def pay_claim(claim_id: str, payment: Payment, db: Db) -> dict[str, Any]:
    """Pay an approved claim its payout. A payout over $1,000 needs sign-off first."""
    row = claim_row(db, claim_id, lock=True)
    if row["decision"] != "approve":
        raise HTTPException(409, f"Claim {claim_id} is not approved")
    if row["paid_amount"] is not None:
        # A step run again after a restart repeats its payment, which is done.
        if round(payment.amount, 2) == round(row["paid_amount"], 2):
            return get_claim(claim_id, db)
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
    set_claim(
        db, claim_id, status="decided", paid_amount=row["payout"], paid_at=store.now()
    )
    db.commit()
    return get_claim(claim_id, db)


def tell_agent(claim_id: str) -> None:
    """Ask the agent's service to carry on a signed-off claim; it may not be running."""
    url = os.getenv("UNDERCOVER_AGENT", AGENT_URL)
    # If it can't be reached, the agent carries on the claim the next time it runs.
    with suppress(httpx.HTTPError):
        httpx.post(f"{url}/claims/{claim_id}/resume", timeout=AGENT_TIMEOUT)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off")
def sign_off(
    claim_id: str, sign_off: SignOff, db: Db, tasks: BackgroundTasks
) -> dict[str, Any]:
    """Sign off a payout over $1,000 as a handler; the agent then pays it."""
    handlers = {h["handler_id"] for h in store.load("handlers.json")}
    if sign_off.handler_id not in handlers:
        raise HTTPException(404, f"Handler {sign_off.handler_id} not found")
    row = claim_row(db, claim_id, lock=True)
    if row["status"] != "waiting_for_sign_off":
        raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off")
    set_claim(
        db,
        claim_id,
        status="in_progress",
        signed_off_by=sign_off.handler_id,
        signed_off_at=store.now(),
    )
    db.commit()
    tasks.add_task(tell_agent, claim_id)
    return get_claim(claim_id, db)
