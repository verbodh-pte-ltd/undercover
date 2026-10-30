"""The UnderCover backend."""

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Connection,
    RowMapping,
    case,
    delete,
    func,
    insert,
    select,
    update,
)

from backend import agent_service, payout, store
from backend.store import (
    answer_key,
    claims,
    customers,
    held_out_claims,
    model_calls,
    policies,
    run_claims,
    runs,
    steps,
    work_times,
)

app = FastAPI(title="UnderCover")

INSTRUCTIONS = Path(__file__).parent / "instructions"
DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}
WRONGFUL_DECLINE = 1500
NEEDLESS_REFERRAL = 50


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


class ClaimStart(BaseModel):
    run_id: int | None


class DispatchStart(BaseModel):
    n: int = Field(ge=1, le=25)


class EvaluationStart(BaseModel):
    n: int = Field(ge=1, le=25)
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool


class RunCreate(BaseModel):
    kind: Literal["dispatch", "evaluation"]
    n: int = Field(ge=1, le=25)
    eval_set: Literal["evaluation", "held_out"] | None
    tiny: bool
    model: str
    effort: str


class ModelCall(BaseModel):
    run_id: int | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)


class WorkTime(BaseModel):
    run_id: int | None
    seconds: float = Field(ge=0)


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
    fields += ["attempts", "thread_id", "run_id", "copied_from"]
    return {
        **claim,
        "customer_name": customer_name(db, claim["customer_id"]),
        **{f: row[f] for f in fields},
        "sections_read": sections_read(db, claim["claim_id"]),
        **claim_totals(db, row["claim_id"], None),
    }


def claim_totals(db: Connection, claim_id: str, run_id: int | None) -> dict[str, float]:
    """A claim's model cost and working seconds, in one run or in all."""
    cost = select(func.coalesce(func.sum(model_calls.c.cost), 0.0))
    cost = cost.where(model_calls.c.claim_id == claim_id)
    seconds = select(func.coalesce(func.sum(work_times.c.seconds), 0.0))
    seconds = seconds.where(work_times.c.claim_id == claim_id)
    if run_id is not None:
        cost = cost.where(model_calls.c.run_id == run_id)
        seconds = seconds.where(work_times.c.run_id == run_id)
    return {"cost": db.scalar(cost) or 0.0, "seconds": db.scalar(seconds) or 0.0}


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


def run_claim_ids(db: Connection, run_id: int) -> list[str]:
    """The claims a run has worked, in the order it started them."""
    query = select(run_claims.c.claim_id).where(run_claims.c.run_id == run_id)
    return list(db.scalars(query.order_by(run_claims.c.id)))


def run_view(db: Connection, row: RowMapping) -> dict[str, Any]:
    """A run with each of its claims, where it stands, and the run's totals."""
    run_id = row["run_id"]
    worked = []
    for claim_id in run_claim_ids(db, run_id):
        claim = claim_row(db, claim_id)
        worked.append(
            {
                "claim_id": claim_id,
                "status": claim["status"],
                "decision": claim["decision"],
                "payout": claim["payout"],
                **claim_totals(db, claim_id, run_id),
            }
        )
    return {
        **{k: row[k] for k in ["run_id", "kind", "n", "eval_set", "tiny", "model"]},
        **{k: row[k] for k in ["effort", "status", "started_at", "finished_at"]},
        "claims": worked,
        "cost": sum(c["cost"] for c in worked),
        "seconds": sum(c["seconds"] for c in worked),
        "run_seconds": run_seconds(row),
        "summary": summary(worked),
    }


def run_seconds(row: RowMapping) -> float | None:
    """The time from the run's start to its finish, or None until it finishes."""
    if row["finished_at"] is None:
        return None
    started = datetime.fromisoformat(row["started_at"])
    return (datetime.fromisoformat(row["finished_at"]) - started).total_seconds()


def summary(worked: list[dict[str, Any]]) -> dict[str, Any]:
    """What a run did, built from the records with no model."""
    decided = [c for c in worked if c["status"] == "decided"]
    return {
        "approved": sum(c["decision"] == "approve" for c in decided),
        "declined": sum(c["decision"] == "decline" for c in decided),
        "referred": sum(c["decision"] == "refer" for c in decided),
        "waiting_for_sign_off": [
            {"claim_id": c["claim_id"], "payout": c["payout"]}
            for c in worked
            if c["status"] == "waiting_for_sign_off"
        ],
        "left_for_handler": [
            c["claim_id"] for c in worked if c["status"] == "needs_handler"
        ],
        "in_progress": [c["claim_id"] for c in worked if c["status"] == "in_progress"],
    }


def submitted(row: RowMapping) -> str:
    """The date a claim was submitted, for working the oldest first."""
    return json.loads(row["data"])["submitted_on"]


def make_copies(db: Connection, run_id: int, eval_set: str, tiny: bool) -> None:
    """Copy an evaluation's claims under new ids, out of the queue."""
    key = select(answer_key.c.claim_id).where(answer_key.c.eval_set == eval_set)
    if tiny:
        key = key.where(answer_key.c.tiny)
    wanted = set(db.scalars(key))
    if eval_set == "evaluation":
        query = select(claims.c.claim_id, claims.c.data).where(
            claims.c.run_id.is_(None)
        )
    else:
        query = select(held_out_claims.c.claim_id, held_out_claims.c.data)
    originals = [r for r in db.execute(query).mappings() if r["claim_id"] in wanted]
    for position, original in enumerate(originals):
        data = json.loads(original["data"])
        data["claim_id"] = f"E{run_id}-{original['claim_id']}"
        data["status"] = "submitted"
        db.execute(
            insert(claims).values(
                claim_id=data["claim_id"],
                position=1000 * run_id + position,
                data=json.dumps(data),
                status="waiting",
                run_id=run_id,
                copied_from=original["claim_id"],
            )
        )


def mistake_cost(
    agent: str, payout: float, handler: str, handler_payout: float
) -> float:
    """What the agent's answer costs, against the handler's decision."""
    if agent == handler:
        return abs(payout - handler_payout) if agent == "approve" else 0.0
    if agent == "decline":
        return WRONGFUL_DECLINE
    if agent == "approve":
        return payout
    return NEEDLESS_REFERRAL


def evaluation_view(db: Connection, row: RowMapping) -> dict[str, Any]:
    """An evaluation's claims beside the handlers' decisions, with its measures."""
    run_id = row["run_id"]
    copies = db.execute(
        select(claims).where(claims.c.run_id == run_id).order_by(claims.c.position)
    ).mappings()
    rows = []
    for copy in copies:
        key = (
            db.execute(
                select(answer_key).where(answer_key.c.claim_id == copy["copied_from"])
            )
            .mappings()
            .one()
        )
        # A claim the agent didn't decide counts as a referral.
        agent = copy["decision"] or "refer"
        payout_ = copy["payout"] or 0.0
        rows.append(
            {
                "claim_id": copy["claim_id"],
                "copied_from": copy["copied_from"],
                "status": copy["status"],
                "agent": agent,
                "agent_payout": payout_,
                "reason": copy["reason"],
                "handler": key["decision"],
                "handler_payout": key["payout"],
                "decision_right": agent == key["decision"],
                "right": agent == key["decision"] and abs(payout_ - key["payout"]) <= 1,
                "mistake_cost": mistake_cost(
                    agent, payout_, key["decision"], key["payout"]
                ),
                **claim_totals(db, copy["claim_id"], run_id),
            }
        )
    count = len(rows) or 1
    measures = {
        "decision_accuracy": sum(r["decision_right"] for r in rows) / count,
        "accuracy": sum(r["right"] for r in rows) / count,
        "cost_of_mistakes": sum(r["mistake_cost"] for r in rows),
        "straight_through": sum(r["agent"] != "refer" for r in rows) / count,
        "cost_per_claim": sum(r["cost"] for r in rows) / count,
        "seconds_per_claim": sum(r["seconds"] for r in rows) / count,
        "run_seconds": run_seconds(row),
    }
    keys = ["run_id", "eval_set", "tiny", "n", "model", "effort", "status"]
    return {**{k: row[k] for k in keys + ["started_at"]}, **measures, "claims": rows}


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
    query = select(claims).where(claims.c.run_id.is_(None))
    rows = db.execute(query.order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "customer_name"]
    fields += ["status", "decision", "payout", "paid_amount"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/waiting", operation_id="list_waiting_claims")
def list_waiting_claims(db: Db) -> list[dict[str, Any]]:
    """List the waiting claims in queue order, with their failed attempts."""
    query = select(claims).where(
        claims.c.status == "waiting", claims.c.run_id.is_(None)
    )
    rows = db.execute(query.order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "attempts"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/unfinished", operation_id="list_unfinished_claims")
def list_unfinished_claims(db: Db) -> list[dict[str, Any]]:
    """List the claims in progress, stopped or signed off, for the agent to carry on."""
    query = select(claims).where(
        claims.c.status == "in_progress", claims.c.run_id.is_(None)
    )
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
    """Get the claim agent's instructions."""
    return {
        name: (INSTRUCTIONS / f"{name}.md").read_text(encoding="utf-8")
        for name in ["claim"]
    }


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    return store.load("handlers.json")


@app.post("/api/dispatches", operation_id="start_dispatch")
def start_dispatch(start: DispatchStart) -> dict[str, Any]:
    """Ask the agent's service to work the queue with up to n claim agents at once."""
    return agent_service.start("dispatches", start.model_dump())


@app.post("/api/evaluations", operation_id="start_evaluation")
def start_evaluation(start: EvaluationStart) -> dict[str, Any]:
    """Ask the agent's service to evaluate the agent on a labelled set of claims."""
    return agent_service.start("evaluations", start.model_dump())


@app.post("/api/runs", operation_id="create_run")
def create_run(create: RunCreate, db: Db) -> dict[str, Any]:
    """Record a new dispatch or evaluation; an evaluation's copies are made here."""
    running = select(runs.c.run_id).where(runs.c.status == "running")
    if db.execute(running.with_for_update()).first() is not None:
        raise HTTPException(409, "A dispatch or evaluation is already running")
    if (create.kind == "evaluation") != (create.eval_set is not None):
        raise HTTPException(422, "Only an evaluation has a set")
    new_run = insert(runs).values(**create.model_dump(), started_at=store.now())
    run_id = db.execute(new_run.returning(runs.c.run_id)).scalar_one()
    if create.eval_set is not None:
        make_copies(db, run_id, create.eval_set, create.tiny)
    db.commit()
    return run_view(db, run_row(db, run_id))


@app.get("/api/runs/current", operation_id="get_current_run")
def get_current_run(db: Db) -> dict[str, Any] | None:
    """Get the latest dispatch with its claims and summary, or null if there is none."""
    query = select(runs).where(runs.c.kind == "dispatch")
    row = db.execute(query.order_by(runs.c.run_id.desc()).limit(1)).mappings().first()
    return None if row is None else run_view(db, row)


@app.get("/api/runs/{run_id}/claims", operation_id="list_run_claims")
def list_run_claims(run_id: int, db: Db) -> list[str]:
    """List the claims a run should work: in progress first, then oldest submitted."""
    row = run_row(db, run_id)
    if row["kind"] == "evaluation":
        query = select(claims).where(
            claims.c.run_id == run_id, claims.c.status.in_(["waiting", "in_progress"])
        )
        return [
            r["claim_id"]
            for r in db.execute(query.order_by(claims.c.position)).mappings()
        ]
    queue = select(claims).where(claims.c.run_id.is_(None))
    stopped = db.execute(queue.where(claims.c.status == "in_progress")).mappings().all()
    waiting = db.execute(queue.where(claims.c.status == "waiting")).mappings().all()
    ordered = sorted(stopped, key=submitted) + sorted(waiting, key=submitted)
    return [r["claim_id"] for r in ordered]


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: int, db: Db) -> dict[str, Any]:
    """Mark a dispatch or evaluation finished."""
    run_row(db, run_id, lock=True)
    query = update(runs).where(runs.c.run_id == run_id)
    db.execute(query.values(status="finished", finished_at=store.now()))
    db.commit()
    return run_view(db, run_row(db, run_id))


@app.post("/api/runs/stop-running", operation_id="stop_running_runs")
def stop_running_runs(db: Db) -> dict[str, int]:
    """Mark every run still running as stopped, when the agent's service starts."""
    query = update(runs).where(runs.c.status == "running")
    stopped = db.execute(query.values(status="stopped", finished_at=store.now()))
    db.commit()
    return {"stopped": stopped.rowcount}


@app.get("/api/evaluations", operation_id="list_evaluations")
def list_evaluations(db: Db) -> list[dict[str, Any]]:
    """List the evaluations, newest first, with their measures."""
    query = select(runs).where(runs.c.kind == "evaluation")
    rows = db.execute(query.order_by(runs.c.run_id.desc())).mappings().all()
    return [
        {k: v for k, v in evaluation_view(db, r).items() if k != "claims"} for r in rows
    ]


@app.get("/api/evaluations/{run_id}", operation_id="get_evaluation")
def get_evaluation(run_id: int, db: Db) -> dict[str, Any]:
    """Get one evaluation: its measures and each claim beside the handler's decision."""
    row = run_row(db, run_id)
    if row["kind"] != "evaluation":
        raise HTTPException(404, f"Evaluation {run_id} not found")
    return evaluation_view(db, row)


@app.post("/api/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(claim_id: str, start: ClaimStart, db: Db) -> dict[str, Any]:
    """Start a claim in a run, or none, on its thread; a first try clears old steps."""
    row = claim_row(db, claim_id, lock=True)
    if row["status"] not in {"waiting", "in_progress"}:
        raise HTTPException(409, f"Claim {claim_id} is not waiting")
    if start.run_id is not None:
        run_row(db, start.run_id)
        if claim_id not in run_claim_ids(db, start.run_id):
            db.execute(
                insert(run_claims).values(run_id=start.run_id, claim_id=claim_id)
            )
    # A new try gets a new thread; a claim already in progress keeps its own.
    thread_id = row["thread_id"] or f"{claim_id}-{row['attempts'] + 1}"
    set_claim(db, claim_id, status="in_progress", thread_id=thread_id)
    if row["attempts"] == 0 and row["thread_id"] is None:
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


@app.post("/api/claims/{claim_id}/model-calls", operation_id="record_model_call")
def record_model_call(claim_id: str, used: ModelCall, db: Db) -> dict[str, str]:
    """Record one model call's tokens and cost on a claim, with its run or none."""
    claim_row(db, claim_id)
    db.execute(insert(model_calls).values(claim_id=claim_id, **used.model_dump()))
    db.commit()
    return {"status": "recorded"}


@app.post("/api/claims/{claim_id}/work-times", operation_id="record_work_time")
def record_work_time(claim_id: str, spent: WorkTime, db: Db) -> dict[str, str]:
    """Record seconds the agent spent working a claim, with its run or none."""
    claim_row(db, claim_id)
    db.execute(insert(work_times).values(claim_id=claim_id, **spent.model_dump()))
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


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off")
def sign_off(
    claim_id: str, sign_off: SignOff, db: Db, tasks: BackgroundTasks
) -> dict[str, Any]:
    """Sign off a payout over $1,000 as a handler; the agent then pays it."""
    handlers = {h["handler_id"] for h in store.load("handlers.json")}
    if sign_off.handler_id not in handlers:
        raise HTTPException(404, f"Handler {sign_off.handler_id} not found")
    row = claim_row(db, claim_id, lock=True)
    if row["run_id"] is not None:
        raise HTTPException(409, f"Claim {claim_id} is an evaluation's copy")
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
    tasks.add_task(agent_service.resume_claim, claim_id)
    return get_claim(claim_id, db)
