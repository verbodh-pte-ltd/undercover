"""The UnderCover backend."""

import difflib
import json
import os
import secrets
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx
import jwt
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import (
    Connection,
    RowMapping,
    case,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)

from backend import agent_service, auth, judge, payout, rule, store
from backend.store import (
    answer_key,
    claims,
    config_versions,
    customers,
    grades,
    handlers,
    held_out_claims,
    model_calls,
    policies,
    recipes,
    round_events,
    rounds,
    run_claims,
    runs,
    setting_changes,
    settings,
    steps,
    work_times,
)

app = FastAPI(title="UnderCover")

DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}
WRONGFUL_DECLINE = 1500
# The claim agent's operations, which its own API document gives the Gateway.
AGENT_OPERATIONS = [
    "get_claim",
    "get_policy",
    "get_claim_history",
    "list_sections",
    "read_section",
    "calculate_payout",
    "record_decision",
    "pay_claim",
]
NEEDLESS_REFERRAL = 50


class Decision(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float = Field(ge=0)
    reason: str
    policy_section: str


class Payment(BaseModel):
    amount: float = Field(gt=0)


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
    version: int


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


class Measures(BaseModel):
    accuracy: float = Field(ge=0, le=1)
    grade: float = Field(ge=0, le=3)
    cost_per_claim: float = Field(ge=0)
    seconds_per_claim: float = Field(ge=0)


class Objectives(BaseModel):
    accuracy: float = Field(ge=0, le=1)
    grade: float = Field(ge=0, le=3)


class Settings(BaseModel):
    approval_limit: float = Field(gt=0)
    objectives: Objectives
    noise: Measures
    tolerance: Measures
    round_spending_limit: float = Field(gt=0)
    agents_at_once: int = Field(ge=1, le=25)


MAX_ATTEMPTS = 2
# The held-out measures a version's scores average.
SCORED = ["accuracy", "grade", "cost_per_claim", "seconds_per_claim"]
# What the judge records on a claim; None on a claim it couldn't grade.
JUDGED = ["grade", "letter", "same_grounds", "same_section"]
# A claim the agent didn't decide gets an F without asking the judge.
UNDECIDED = {
    "grade": 0.0,
    "letter": "F",
    "same_grounds": 0.0,
    "same_section": 0.0,
    "judge_cost": 0.0,
}


def get_db() -> Iterator[Connection]:
    """Give each request its own connection to the store."""
    with store.connect() as conn:
        yield conn


Db = Annotated[Connection, Depends(get_db)]


def has_key(request: Request) -> bool:
    """Whether the request carries the agent's API key; never when no key is set."""
    expected = os.getenv("UNDERCOVER_API_KEY", "")
    sent = request.headers.get("X-API-Key", "")
    return bool(expected) and secrets.compare_digest(sent.encode(), expected.encode())


def agent_key(request: Request) -> None:
    """Refuse a request without the agent's API key with 401."""
    if not has_key(request):
        raise HTTPException(401, "This needs the agent's API key in X-API-Key")


def handler_for(token: str, db: Connection) -> RowMapping:
    """The handler whose Supabase access token this is, or raise 401."""
    try:
        email = auth.email(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            401, "Your sign-in has expired or isn't valid. Sign in again"
        ) from exc
    row = db.execute(select(handlers).where(handlers.c.email == email)).mappings()
    handler = row.first()
    if handler is None:
        raise HTTPException(
            401, f"{email} isn't a claims handler. Sign out and sign in as one"
        )
    return handler


def signed_in_handler(
    token: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())], db: Db
) -> RowMapping:
    """The handler whose Supabase access token came with the request, or raise 401."""
    return handler_for(token.credentials, db)


def key_or_handler(request: Request, db: Db) -> None:
    """Let in the agent's API key or a signed-in handler; refuse neither with 401."""
    if has_key(request):
        return
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            401, "This needs the agent's API key or a handler's sign-in"
        )
    handler_for(token, db)


Handler = Annotated[RowMapping, Depends(signed_in_handler)]
# Who may call a route, read from the headers so the operations get no inputs for it.
AGENT = [Depends(agent_key)]
SHARED = [Depends(key_or_handler)]
HANDLER = [Depends(signed_in_handler)]


def setting(db: Connection, name: str) -> Any:
    """One of the platform's settings."""
    query = select(settings.c.value).where(settings.c.name == name)
    return json.loads(db.execute(query).scalar_one())


def models_allowed() -> list[dict[str, Any]]:
    """The models the claim agent may use: MODEL_NAME alone, free, if it is set."""
    free = os.getenv("MODEL_NAME")
    if free:
        return [
            {
                "model": free,
                "reasoning_efforts": ["default"],
                "input_price": 0.0,
                "output_price": 0.0,
            }
        ]
    return store.load("models.json")


def settings_view(db: Connection) -> dict[str, Any]:
    """The settings, the models allowed, and who last changed each setting and when."""
    values = {r.name: json.loads(r.value) for r in db.execute(select(settings))}
    query = (
        select(setting_changes, handlers.c.name.label("handler"))
        .join(handlers, handlers.c.handler_id == setting_changes.c.handler_id)
        .order_by(setting_changes.c.id)
    )
    changes = {
        r["name"]: {"handler": r["handler"], "changed_at": r["changed_at"]}
        for r in db.execute(query).mappings()
    }
    return {
        "settings": Settings(**values).model_dump(),
        "models_allowed": models_allowed(),
        "changes": changes,
    }


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
        "version": row["version"],
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
    judged = db.execute(select(grades).where(grades.c.run_id == run_id)).mappings()
    graded = {g["claim_id"]: g for g in judged}
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
        grade = graded.get(copy["claim_id"])
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
                **{k: grade and grade[k] for k in JUDGED},
                "judge_cost": grade["judge_cost"] if grade else 0.0,
            }
        )
    count = len(rows) or 1
    known = [r["grade"] for r in rows if r["grade"] is not None]
    measures = {
        "decision_accuracy": sum(r["decision_right"] for r in rows) / count,
        "accuracy": sum(r["right"] for r in rows) / count,
        "cost_of_mistakes": sum(r["mistake_cost"] for r in rows),
        "straight_through": sum(r["agent"] != "refer" for r in rows) / count,
        # A run with no graded claims has no grade.
        "grade": sum(known) / len(known) if known else None,
        "judge_cost": sum(r["judge_cost"] for r in rows),
        "cost_per_claim": sum(r["cost"] for r in rows) / count,
        "seconds_per_claim": sum(r["seconds"] for r in rows) / count,
        "run_seconds": run_seconds(row),
    }
    keys = ["run_id", "eval_set", "tiny", "n", "model", "effort", "status", "version"]
    return {**{k: row[k] for k in keys + ["started_at"]}, **measures, "claims": rows}


def current_version(db: Connection) -> RowMapping:
    """The current version of the claim agent's configuration: the newest one kept."""
    query = select(config_versions).where(config_versions.c.kept)
    query = query.order_by(config_versions.c.version.desc()).limit(1)
    return db.execute(query).mappings().one()


def configuration(row: RowMapping, model: str, effort: str) -> dict[str, Any]:
    """What a claim agent works to, with no scores."""
    return {
        "version": row["version"],
        "instructions": row["instructions"],
        "model": model,
        "effort": effort,
    }


def changed_lines(old: str, new: str) -> list[str]:
    """The lines that differ between two texts, with two lines around each change."""
    before, after = old.splitlines(), new.splitlines()
    # Spacing is evened out first, so a line only indented again isn't a change.
    matcher = difflib.SequenceMatcher(
        None,
        [" ".join(x.split()) for x in before],
        [" ".join(x.split()) for x in after],
    )
    found: list[str] = []
    for group in matcher.get_grouped_opcodes(2):
        # "…" stands for the unchanged text skipped between two changes.
        found += ["…"] if found else []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                found += [" " + line for line in after[j1:j2]]
                continue
            found += ["-" + line for line in before[i1:i2]]
            found += ["+" + line for line in after[j1:j2]]
    return found


def average(values: list[float | None]) -> float | None:
    """The average of the values that are known, or None if none is."""
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def version_scores(
    db: Connection, version: int, eval_set: str
) -> dict[str, Any] | None:
    """A version's scores on a set: the averages of the whole-set runs rounds made."""
    query = select(runs).where(
        runs.c.kind == "evaluation",
        runs.c.eval_set == eval_set,
        runs.c.tiny.is_(False),
        runs.c.status == "finished",
        runs.c.version == version,
        runs.c.round_id.is_not(None),
    )
    views = [evaluation_view(db, r) for r in db.execute(query).mappings()]
    if not views:
        return None
    averages = {m: average([v[m] for v in views]) for m in SCORED}
    return {**averages, "runs": len(views)}


def grade_claim(row: RowMapping) -> dict[str, Any] | None:
    """The judge's grade of one evaluation claim, or None if it couldn't grade it."""
    if row["decision"] is None:
        return UNDECIDED
    return judge.grade(
        {
            "decision": row["handler_decision"],
            "reason": row["handler_reason"],
            "reference": row["reference"],
        },
        {
            "decision": row["decision"],
            "rationale": row["reason"],
            "section": row["policy_section"],
        },
    )


def grade_claims(db: Connection, run_id: int) -> None:
    """Have the judge grade an evaluation's claims in parallel, those not yet graded."""
    # On a free model the judge isn't called, so every claim shows as not graded.
    if os.getenv("MODEL_NAME"):
        return
    query = (
        select(
            claims,
            answer_key.c.decision.label("handler_decision"),
            answer_key.c.reason.label("handler_reason"),
            answer_key.c.reference,
        )
        .join(answer_key, answer_key.c.claim_id == claims.c.copied_from)
        .where(
            claims.c.run_id == run_id,
            claims.c.claim_id.not_in(select(grades.c.claim_id)),
        )
    )
    rows = db.execute(query).mappings().all()
    # End the read, so no lock is held while the judge works.
    db.rollback()
    with ThreadPoolExecutor(max_workers=max(len(rows), 1)) as pool:
        found = list(pool.map(grade_claim, rows))
    graded = [
        {"claim_id": r["claim_id"], "run_id": run_id, **g}
        for r, g in zip(rows, found, strict=True)
        if g is not None
    ]
    if graded:
        db.execute(insert(grades), graded)
        db.commit()


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


@app.get("/api/claims", operation_id="list_claims", dependencies=HANDLER)
def list_claims(db: Db) -> list[dict[str, Any]]:
    """List the claims queue in order, with where each claim stands."""
    query = select(claims).where(claims.c.run_id.is_(None))
    rows = db.execute(query.order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "customer_name"]
    fields += ["status", "decision", "payout", "paid_amount"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/waiting", operation_id="list_waiting_claims", dependencies=AGENT)
def list_waiting_claims(db: Db) -> list[dict[str, Any]]:
    """List the waiting claims in queue order, with their failed attempts."""
    query = select(claims).where(
        claims.c.status == "waiting", claims.c.run_id.is_(None)
    )
    rows = db.execute(query.order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "summary", "claim_type", "amount_claimed", "attempts"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get(
    "/api/claims/unfinished", operation_id="list_unfinished_claims", dependencies=AGENT
)
def list_unfinished_claims(db: Db) -> list[dict[str, Any]]:
    """List the claims in progress, stopped or signed off, for the agent to carry on."""
    query = select(claims).where(
        claims.c.status == "in_progress", claims.c.run_id.is_(None)
    )
    rows = db.execute(query.order_by(claims.c.position)).mappings().all()
    fields = ["claim_id", "thread_id", "attempts", "decision", "signed_off_by"]
    return [{f: v for f, v in claim_view(db, r).items() if f in fields} for r in rows]


@app.get("/api/claims/{claim_id}", operation_id="get_claim", dependencies=SHARED)
def get_claim(claim_id: str, db: Db) -> dict[str, Any]:
    """Get one claim in full: the story, the items, the documents and its decision."""
    return claim_view(db, claim_row(db, claim_id))


@app.get(
    "/api/agent/openapi.json", operation_id="get_agent_openapi", dependencies=SHARED
)
def get_agent_openapi() -> dict[str, Any]:
    """Get the API document of the claim agent's own operations, for its Gateway."""
    chosen = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.operation_id in AGENT_OPERATIONS
    ]
    return get_openapi(title="UnderCover Agent", version=app.version, routes=chosen)


@app.get("/api/policies/{policy_id}", operation_id="get_policy", dependencies=SHARED)
def get_policy(policy_id: str, db: Db) -> dict[str, Any]:
    """Get a policy: the plan, the add-ons, the trip and the travelers."""
    return record(db, "policies", policy_id)


@app.get(
    "/api/customers/{customer_id}/claims",
    operation_id="get_claim_history",
    dependencies=SHARED,
)
def get_claim_history(customer_id: str, db: Db) -> list[dict[str, Any]]:
    """Get the customer's earlier claims, with the outcome and amount paid."""
    return record(db, "customers", customer_id)["previous_claims"]


@app.get("/api/policy-wording", operation_id="get_policy_wording", dependencies=AGENT)
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


@app.get(
    "/api/documents/{document}/sections",
    operation_id="list_sections",
    dependencies=AGENT,
)
def list_sections(document: str) -> list[dict[str, str]]:
    """List the numbered sections of policy_wording or claims_manual."""
    return [{"number": s["number"], "title": s["title"]} for s in sections(document)]


@app.get(
    "/api/documents/{document}/sections/{number}",
    operation_id="read_section",
    dependencies=AGENT,
)
def read_section(document: str, number: str) -> dict[str, str]:
    """Read one numbered section of policy_wording or claims_manual."""
    for section in sections(document):
        if section["number"] == number:
            return {"document": document, **section}
    raise HTTPException(404, f"Section {number} of {document} not found")


@app.post("/api/payouts", operation_id="calculate_payout", dependencies=AGENT)
def calculate_payout(question: PayoutQuestion, db: Db) -> payout.Payout:
    """Work out the payout on an approved claim with UnderCover's payout rules."""
    policy = record(db, "policies", question.policy_id)
    return payout.work_out_payout(
        policy,
        question.benefit,
        question.lines,
        question.paid_by_others,
        setting(db, "approval_limit"),
    )


@app.get(
    "/api/configuration/current",
    operation_id="get_current_configuration",
    dependencies=AGENT,
)
def get_current_configuration(db: Db) -> dict[str, Any]:
    """Get the current version of the claim agent's instructions, model and effort."""
    row = current_version(db)
    return configuration(row, row["model"], row["effort"])


@app.get(
    "/api/configuration/versions", operation_id="list_versions", dependencies=HANDLER
)
def list_versions(db: Db) -> list[dict[str, Any]]:
    """List every configuration version, newest first, with its scores and changes."""
    query = select(config_versions).order_by(config_versions.c.version)
    rows = db.execute(query).mappings().all()
    current = current_version(db)["version"]
    found = []
    for before, row in zip([None, *rows], rows, strict=False):
        found.append(
            {
                **row,
                "current": row["version"] == current,
                "held_out": version_scores(db, row["version"], "held_out"),
                "before": before and {k: before[k] for k in ["model", "effort"]},
                "changed_lines": before
                and changed_lines(before["instructions"], row["instructions"]),
            }
        )
    return found[::-1]


@app.get(
    "/api/runs/{run_id}/configuration",
    operation_id="get_run_configuration",
    dependencies=AGENT,
)
def get_run_configuration(run_id: int, db: Db) -> dict[str, Any]:
    """Get the instructions, model and effort a run's claim agents work to."""
    run = run_row(db, run_id)
    query = select(config_versions).where(config_versions.c.version == run["version"])
    found = configuration(
        db.execute(query).mappings().one(), run["model"], run["effort"]
    )
    # The round, if any, only tags the claims' traces.
    return {**found, "round_id": run["round_id"]}


@app.get("/api/recipes", operation_id="list_recipes", dependencies=SHARED)
def list_recipes(db: Db) -> list[dict[str, Any]]:
    """List every version of the improver's recipe, newest first, with its changes."""
    rows = db.execute(select(recipes).order_by(recipes.c.version)).mappings().all()
    found = [
        {**row, "changed_lines": before and changed_lines(before["text"], row["text"])}
        for before, row in zip([None, *rows], rows, strict=False)
    ]
    return found[::-1]


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers(_: Handler, db: Db) -> list[dict[str, Any]]:
    """List the claims handlers, without their email addresses."""
    query = select(handlers).order_by(handlers.c.handler_id)
    keep = ["handler_id", "name", "role", "can_approve_over_limit"]
    return [{k: r[k] for k in keep} for r in db.execute(query).mappings()]


@app.get("/api/handlers/me", operation_id="get_signed_in_handler")
def get_signed_in_handler(handler: Handler) -> dict[str, Any]:
    """Get the handler who is signed in."""
    return dict(handler)


@app.get("/api/settings", operation_id="get_settings", dependencies=HANDLER)
def get_settings(db: Db) -> dict[str, Any]:
    """Get the platform's settings, the models allowed and who last changed each."""
    return settings_view(db)


@app.put("/api/settings", operation_id="change_settings")
def change_settings(changed: Settings, handler: Handler, db: Db) -> dict[str, Any]:
    """Change the platform's settings, as a handler who can sign off over the limit."""
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            403,
            f"{handler['name']} can't change the settings. Only a handler who can"
            " sign off payouts over the approval limit can",
        )
    for name, value in changed.model_dump().items():
        if setting(db, name) == value:
            continue
        text = json.dumps(value)
        db.execute(update(settings).where(settings.c.name == name).values(value=text))
        db.execute(
            insert(setting_changes).values(
                name=name,
                value=text,
                handler_id=handler["handler_id"],
                changed_at=store.now(),
            )
        )
    db.commit()
    return settings_view(db)


@app.post("/api/dispatches", operation_id="start_dispatch")
def start_dispatch(start: DispatchStart, _: Handler) -> dict[str, Any]:
    """Ask the agent's service to work the queue with up to n claim agents at once."""
    return agent_service.start("dispatches", start.model_dump())


@app.post("/api/evaluations", operation_id="start_evaluation")
def start_evaluation(start: EvaluationStart, _: Handler) -> dict[str, Any]:
    """Ask the agent's service to evaluate the agent on a labelled set of claims."""
    return agent_service.start("evaluations", start.model_dump())


def add_run(db: Connection, create: RunCreate, round_id: int | None) -> dict[str, Any]:
    """Record a new run, in a round or none, unless another is running."""
    running = select(runs.c.run_id).where(runs.c.status == "running")
    if db.execute(running.with_for_update()).first() is not None:
        raise HTTPException(409, "A dispatch or evaluation is already running")
    if (create.kind == "evaluation") != (create.eval_set is not None):
        raise HTTPException(422, "Only an evaluation has a set")
    known = select(config_versions.c.version)
    if db.scalar(known.where(config_versions.c.version == create.version)) is None:
        raise HTTPException(404, f"Version {create.version} not found")
    new_run = insert(runs).values(
        **create.model_dump(), round_id=round_id, started_at=store.now()
    )
    run_id = db.execute(new_run.returning(runs.c.run_id)).scalar_one()
    if create.eval_set is not None:
        make_copies(db, run_id, create.eval_set, create.tiny)
    db.commit()
    return run_view(db, run_row(db, run_id))


@app.post("/api/runs", operation_id="create_run", dependencies=AGENT)
def create_run(create: RunCreate, db: Db) -> dict[str, Any]:
    """Record a new dispatch or evaluation; an evaluation's copies are made here."""
    return add_run(db, create, None)


@app.get("/api/runs/current", operation_id="get_current_run", dependencies=HANDLER)
def get_current_run(db: Db) -> dict[str, Any] | None:
    """Get the latest dispatch with its claims and summary, or null if there is none."""
    query = select(runs).where(runs.c.kind == "dispatch")
    row = db.execute(query.order_by(runs.c.run_id.desc()).limit(1)).mappings().first()
    return None if row is None else run_view(db, row)


@app.get(
    "/api/runs/{run_id}/claims", operation_id="list_run_claims", dependencies=AGENT
)
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


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run", dependencies=AGENT)
def finish_run(run_id: int, db: Db) -> dict[str, Any]:
    """Mark a running run finished, then have the judge grade an evaluation's claims."""
    kind = run_row(db, run_id)["kind"]
    query = update(runs).where(runs.c.run_id == run_id, runs.c.status == "running")
    finished = db.execute(query.values(status="finished", finished_at=store.now()))
    db.commit()
    # Only the call that finished the run grades it, so the judge is paid once, and a
    # run that was stopped isn't graded.
    if finished.rowcount and kind == "evaluation":
        grade_claims(db, run_id)
    return run_view(db, run_row(db, run_id))


@app.post(
    "/api/runs/stop-running", operation_id="stop_running_runs", dependencies=AGENT
)
def stop_running_runs(db: Db) -> dict[str, int]:
    """Mark every run still running as stopped, when the agent's service starts."""
    query = update(runs).where(runs.c.status == "running")
    stopped = db.execute(query.values(status="stopped", finished_at=store.now()))
    db.commit()
    return {"stopped": stopped.rowcount}


@app.get("/api/evaluations", operation_id="list_evaluations", dependencies=HANDLER)
def list_evaluations(db: Db) -> list[dict[str, Any]]:
    """List the evaluations, newest first, with their measures."""
    query = select(runs).where(runs.c.kind == "evaluation")
    rows = db.execute(query.order_by(runs.c.run_id.desc())).mappings().all()
    return [
        {k: v for k, v in evaluation_view(db, r).items() if k != "claims"} for r in rows
    ]


@app.get(
    "/api/evaluations/{run_id}", operation_id="get_evaluation", dependencies=HANDLER
)
def get_evaluation(run_id: int, db: Db) -> dict[str, Any]:
    """Get one evaluation: its measures and each claim beside the handler's decision."""
    row = run_row(db, run_id)
    if row["kind"] != "evaluation":
        raise HTTPException(404, f"Evaluation {run_id} not found")
    return evaluation_view(db, row)


@app.post(
    "/api/claims/{claim_id}/start", operation_id="start_claim", dependencies=AGENT
)
def start_claim(claim_id: str, start: ClaimStart, db: Db) -> dict[str, Any]:
    """Start a claim in a run, or none, on its thread; a first try clears old steps."""
    row = claim_row(db, claim_id, lock=True)
    if row["status"] not in {"waiting", "in_progress"}:
        raise HTTPException(409, f"Claim {claim_id} is not waiting")
    if start.run_id is not None:
        held_back = held_back_by_round(db, start.run_id)
        if held_back:
            db.commit()
            raise HTTPException(409, held_back)
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


@app.post(
    "/api/claims/{claim_id}/release", operation_id="release_claim", dependencies=AGENT
)
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


@app.post("/api/claims/{claim_id}/steps", operation_id="add_step", dependencies=AGENT)
def add_step(claim_id: str, step: Step, db: Db) -> dict[str, str]:
    """Record one step the agent took on a claim."""
    claim_row(db, claim_id)
    add_step_row(db, claim_id, step.kind, step.text)
    db.commit()
    return {"status": "recorded"}


@app.post(
    "/api/claims/{claim_id}/model-calls",
    operation_id="record_model_call",
    dependencies=AGENT,
)
def record_model_call(claim_id: str, used: ModelCall, db: Db) -> dict[str, str]:
    """Record one model call's tokens and cost on a claim, with its run or none."""
    claim_row(db, claim_id)
    db.execute(insert(model_calls).values(claim_id=claim_id, **used.model_dump()))
    db.commit()
    return {"status": "recorded"}


@app.post(
    "/api/claims/{claim_id}/work-times",
    operation_id="record_work_time",
    dependencies=AGENT,
)
def record_work_time(claim_id: str, spent: WorkTime, db: Db) -> dict[str, str]:
    """Record seconds the agent spent working a claim, with its run or none."""
    claim_row(db, claim_id)
    db.execute(insert(work_times).values(claim_id=claim_id, **spent.model_dump()))
    db.commit()
    return {"status": "recorded"}


@app.get(
    "/api/claims/{claim_id}/steps", operation_id="list_steps", dependencies=HANDLER
)
def list_steps(claim_id: str, db: Db) -> list[dict[str, Any]]:
    """List the steps the agent took on a claim, in order."""
    claim_row(db, claim_id)
    query = select(steps.c.id, steps.c.kind, steps.c.text)
    query = query.where(steps.c.claim_id == claim_id).order_by(steps.c.id)
    return [dict(r) for r in db.execute(query).mappings()]


@app.post(
    "/api/claims/{claim_id}/decision",
    operation_id="record_decision",
    dependencies=AGENT,
)
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
    over_limit = decision.payout > setting(db, "approval_limit")
    status = "waiting_for_sign_off" if over_limit else "decided"
    set_claim(
        db, claim_id, status=status, needs_sign_off=over_limit, **decision.model_dump()
    )
    db.commit()
    return get_claim(claim_id, db)


@app.post(
    "/api/claims/{claim_id}/payment", operation_id="pay_claim", dependencies=AGENT
)
def pay_claim(claim_id: str, payment: Payment, db: Db) -> dict[str, Any]:
    """Pay an approved claim its payout, up to the approval limit."""
    # The limit comes first, so an amount over it gets the same answer on any claim.
    limit = setting(db, "approval_limit")
    if payment.amount > limit:
        raise HTTPException(
            403,
            f"A payment over the approval limit of ${limit:,.2f} is made by a"
            " handler's sign-off",
        )
    row = claim_row(db, claim_id, lock=True)
    if row["decision"] != "approve":
        raise HTTPException(409, f"Claim {claim_id} is not approved")
    if row["paid_amount"] is not None:
        # A step run again after a restart repeats its payment, which is done.
        if round(payment.amount, 2) == round(row["paid_amount"], 2):
            return get_claim(claim_id, db)
        raise HTTPException(409, f"Claim {claim_id} has already been paid")
    if row["needs_sign_off"] and row["signed_off_by"] is None:
        raise HTTPException(
            403, "A payout over the approval limit needs a handler's sign-off first"
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
    claim_id: str, handler: Handler, db: Db, tasks: BackgroundTasks
) -> dict[str, Any]:
    """Sign off and pay a payout over the approval limit as the signed-in handler."""
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            403, f"{handler['name']} can't sign off payouts over the approval limit"
        )
    row = claim_row(db, claim_id, lock=True)
    if row["run_id"] is not None:
        raise HTTPException(409, f"Claim {claim_id} is an evaluation's copy")
    if row["status"] != "waiting_for_sign_off":
        raise HTTPException(409, f"Claim {claim_id} is not waiting for sign-off")
    signed = store.now()
    set_claim(
        db,
        claim_id,
        status="decided",
        signed_off_by=handler["handler_id"],
        signed_off_at=signed,
        paid_amount=row["payout"],
        paid_at=signed,
    )
    db.commit()
    # The agent finishes its paused thread without paying; it may not be running.
    tasks.add_task(agent_service.resume_claim, claim_id)
    return get_claim(claim_id, db)


# The improver's rounds. The handler's actions need a handler who can sign off over
# the limit; the agent's service records the rest of a round with the agent's API key,
# as it does its runs.
CLOSED = {"finished", "stopped"}
# The stages in which a round waits for a handler and does no work.
WAITING = {"change_waiting", "recipe_waiting"}
# A version's evaluations in a round: once on the evaluation set, twice on the held-out.
NEEDED = {"evaluation": 1, "held_out": 2}
# A check of the judge's at this probability or above counts as yes.
YES = 0.5


class RoundStage(BaseModel):
    stage: Literal["first_evaluations", "improving", "evaluations", "finished"]


class ProposedChange(BaseModel):
    instructions: str = Field(min_length=1)
    model: str
    effort: str
    reason: str = Field(min_length=1)


class ProposedRecipe(BaseModel):
    recipe: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RoundDecision(BaseModel):
    decision: Literal["approve", "reject"]
    handler_id: str
    reason: str


class Rejection(BaseModel):
    reason: str = Field(min_length=1)


class RoundEvaluation(BaseModel):
    eval_set: Literal["evaluation", "held_out"]
    version: int


class ImproverCall(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)


def may_act(handler: RowMapping, action: str) -> None:
    """Refuse a handler who can't sign off over the limit, saying why."""
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            403,
            f"{handler['name']} can't {action}. Only a handler who can sign off"
            " payouts over the approval limit can",
        )


def round_row(db: Connection, round_id: int, lock: bool = False) -> RowMapping:
    """Return the round's row, or raise 404; lock it for the transaction if asked."""
    query = select(rounds).where(rounds.c.round_id == round_id)
    row = db.execute(query.with_for_update() if lock else query).mappings().first()
    if row is None:
        raise HTTPException(404, f"Round {round_id} not found")
    return row


def open_round(db: Connection, round_id: int) -> RowMapping:
    """Return an open round's row, locked, or refuse anything a closed one records."""
    row = round_row(db, round_id, lock=True)
    if row["stage"] in CLOSED:
        raise HTTPException(409, f"Round {round_id} is {row['stage']}")
    return row


def round_spent(db: Connection, round_id: int) -> float:
    """What a round has spent: the improver's calls, its evaluations' and the judge."""
    run_ids = select(runs.c.run_id).where(runs.c.round_id == round_id)
    calls = select(func.coalesce(func.sum(model_calls.c.cost), 0.0)).where(
        or_(model_calls.c.round_id == round_id, model_calls.c.run_id.in_(run_ids))
    )
    judged = select(func.coalesce(func.sum(grades.c.judge_cost), 0.0)).where(
        grades.c.run_id.in_(run_ids)
    )
    return (db.scalar(calls) or 0.0) + (db.scalar(judged) or 0.0)


def round_left(db: Connection, row: RowMapping) -> float:
    """What's left of a round's spending limit."""
    limit = json.loads(row["settings"])["round_spending_limit"]
    return limit - round_spent(db, row["round_id"])


def held_back_by_round(db: Connection, run_id: int) -> str | None:
    """Why a run's round lets no new claim start, marking the run stopped; or None."""
    round_id = run_row(db, run_id)["round_id"]
    if round_id is None:
        return None
    row = round_row(db, round_id)
    if row["stage"] == "stopped":
        why = f"Round {round_id} was stopped"
    elif round_left(db, row) <= 0:
        why = f"Round {round_id} has spent its limit"
    else:
        return None
    query = update(runs).where(runs.c.run_id == run_id, runs.c.status == "running")
    db.execute(query.values(status="stopped", finished_at=store.now()))
    return why


def highest_cost_per_claim(db: Connection, model: str) -> float | None:
    """The highest model cost of one claim in one run on a model, or None if no runs."""
    per_claim = (
        select(func.sum(model_calls.c.cost).label("cost"))
        .join(runs, runs.c.run_id == model_calls.c.run_id)
        .where(runs.c.model == model, model_calls.c.claim_id.is_not(None))
        .group_by(model_calls.c.run_id, model_calls.c.claim_id)
        .subquery()
    )
    return db.scalar(select(func.max(per_claim.c.cost)))


def estimate(db: Connection, model: str, count: int) -> float:
    """An evaluation's likely cost: its claims times the highest cost per claim seen."""
    seen = highest_cost_per_claim(db, model)
    if seen is None:
        # A model with no runs: the current model's, scaled by the larger price ratio.
        current = current_version(db)["model"]
        prices = {m["model"]: m for m in models_allowed()}
        new, old = prices.get(model), prices.get(current)
        ratio = 1.0
        if new and old and old["input_price"] and old["output_price"]:
            ratio = max(
                new["input_price"] / old["input_price"],
                new["output_price"] / old["output_price"],
            )
        seen = (highest_cost_per_claim(db, current) or 0.0) * ratio
    return count * seen


def loaded(value: str | None) -> Any:
    """A JSON column's value, or None."""
    return value and json.loads(value)


def proposal_view(db: Connection, row: RowMapping) -> dict[str, Any] | None:
    """The proposal waiting for a handler, with what it changes."""
    proposal = loaded(row["proposal"])
    if proposal is None:
        return None
    if proposal["kind"] == "recipe":
        query = select(recipes.c.text).order_by(recipes.c.version.desc())
        before = db.scalars(query.limit(1)).one()
        return {**proposal, "changed_lines": changed_lines(before, proposal["recipe"])}
    query = select(config_versions).where(
        config_versions.c.version == row["version_before"]
    )
    old = db.execute(query).mappings().one()
    return {
        **proposal,
        "before": {"model": old["model"], "effort": old["effort"]},
        "changed_lines": changed_lines(old["instructions"], proposal["instructions"]),
    }


def progress(
    db: Connection, row: RowMapping, worked: list[dict[str, Any]]
) -> dict[str, int] | None:
    """The evaluations the round's stage needs, and how many it has started."""
    if row["stage"] == "first_evaluations":
        # Only what earlier rounds left the current version short of.
        version = row["version_before"]
        query = (
            select(runs.c.eval_set, func.count())
            .where(
                runs.c.kind == "evaluation",
                runs.c.tiny.is_(False),
                runs.c.status == "finished",
                runs.c.version == version,
                runs.c.round_id < row["round_id"],
            )
            .group_by(runs.c.eval_set)
        )
        earlier: dict[str, int] = {s: n for s, n in db.execute(query)}
        needed = sum(max(n - earlier.get(s, 0), 0) for s, n in NEEDED.items())
    elif row["stage"] == "evaluations":
        version, needed = row["version"], sum(NEEDED.values())
    else:
        return None
    started = len([r for r in worked if r["version"] == version])
    return {"started": started, "needed": needed}


def round_view(db: Connection, row: RowMapping) -> dict[str, Any]:
    """A round: where it has got to, what it tried, its verdict and its spending."""
    round_id = row["round_id"]
    query = select(runs).where(runs.c.round_id == round_id).order_by(runs.c.run_id)
    worked = [
        {k: r[k] for k in ["run_id", "eval_set", "version", "status"]}
        for r in db.execute(query).mappings()
    ]
    query = (
        select(round_events, handlers.c.name.label("handler"))
        .join(handlers, handlers.c.handler_id == round_events.c.handler_id)
        .where(round_events.c.round_id == round_id)
        .order_by(round_events.c.id)
    )
    keep = ["kind", "decision", "handler_id", "handler", "reason", "decided_at"]
    events = [{k: e[k] for k in keep} for e in db.execute(query).mappings()]
    spent = round_spent(db, round_id)
    simple = ["round_id", "stage", "recipe_version", "thread_id", "version_before"]
    simple += ["version", "reason", "kept", "why", "started_by", "started_at"]
    simple += ["stopped_by", "finished_at"]
    settings_ = json.loads(row["settings"])
    return {
        **{k: row[k] for k in simple},
        "settings": settings_,
        "held_out_before": loaded(row["held_out_before"]),
        "held_out_after": loaded(row["held_out_after"]),
        "proposal": proposal_view(db, row),
        "events": events,
        "runs": worked,
        "progress": progress(db, row, worked),
        "spent": spent,
        "left": settings_["round_spending_limit"] - spent,
    }


def beside_handler(db: Connection, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluation claims, the agent's answer beside the handler's, and the grade."""
    found = []
    for r in rows:
        query = select(answer_key).where(answer_key.c.claim_id == r["copied_from"])
        key = db.execute(query).mappings().one()
        copy = claim_row(db, r["claim_id"])
        checks = {
            c: None if r[c] is None else r[c] >= YES
            for c in ["same_grounds", "same_section"]
        }
        found.append(
            {
                "claim_id": r["copied_from"],
                "summary": json.loads(copy["data"])["summary"],
                "agent": {
                    "decision": r["agent"],
                    "payout": r["agent_payout"],
                    "rationale": r["reason"],
                    "section": copy["policy_section"],
                },
                "handler": {
                    k: key[k] for k in ["decision", "payout", "reason", "reference"]
                },
                "judge": {"grade": r["grade"], "letter": r["letter"], **checks},
            }
        )
    return found


def mistakes(db: Connection, version: int) -> dict[str, Any] | None:
    """The wrong claims and the five right ones explained least well, from a version's
    latest whole evaluation-set run; None before it has one."""
    query = select(runs).where(
        runs.c.kind == "evaluation",
        runs.c.eval_set == "evaluation",
        runs.c.tiny.is_(False),
        runs.c.status == "finished",
        runs.c.version == version,
    )
    latest = db.execute(query.order_by(runs.c.run_id.desc()).limit(1)).mappings()
    row = latest.first()
    if row is None:
        return None
    rows = evaluation_view(db, row)["claims"]
    right = [r for r in rows if r["right"]]
    weakest = sorted(right, key=lambda r: (r["grade"] is None, r["grade"] or 0))[:5]
    return {
        "run_id": row["run_id"],
        "wrong": beside_handler(db, [r for r in rows if not r["right"]]),
        "explained_least_well": beside_handler(db, weakest),
    }


def earlier_round(db: Connection, row: RowMapping) -> dict[str, Any]:
    """What an earlier round tried and what came of it."""
    tried = None
    if row["version"] is not None:
        query = select(config_versions).where(
            config_versions.c.version == row["version"]
        )
        tried = db.execute(query).mappings().one()
    return {
        "round": row["round_id"],
        "recipe_version": row["recipe_version"],
        "version": row["version"],
        "model": tried and tried["model"],
        "effort": tried and tried["effort"],
        "reason": row["reason"],
        "kept": row["kept"],
        "why": row["why"],
        "held_out_before": loaded(row["held_out_before"]),
        "held_out_after": loaded(row["held_out_after"]),
        "stage": row["stage"],
    }


@app.post("/api/rounds", operation_id="start_round")
def start_round(handler: Handler, db: Db) -> dict[str, Any]:
    """Start a round of the improver, as a handler who can sign off over the limit."""
    may_act(handler, "start a round")
    query = select(rounds.c.round_id).where(rounds.c.stage.not_in(CLOSED))
    already = db.execute(query.with_for_update()).first()
    if already is not None:
        raise HTTPException(
            409, f"Round {already.round_id} is open. Stop it or wait for it to finish"
        )
    values = {
        "stage": "first_evaluations",
        "recipe_version": db.scalar(select(func.max(recipes.c.version))),
        "settings": json.dumps(settings_view(db)["settings"]),
        "thread_id": f"round-{uuid.uuid4().hex}",
        "version_before": current_version(db)["version"],
        "started_by": handler["handler_id"],
        "started_at": store.now(),
    }
    new_round = insert(rounds).values(**values).returning(rounds.c.round_id)
    round_id = db.execute(new_round).scalar_one()
    db.commit()
    body = {"round_id": round_id, "thread_id": values["thread_id"]}
    try:
        agent_service.start("rounds", body)
    # A round the service didn't start must not stay open and block the next one.
    except (HTTPException, httpx.HTTPError):
        db.execute(delete(rounds).where(rounds.c.round_id == round_id))
        db.commit()
        raise
    return round_view(db, round_row(db, round_id))


@app.get("/api/rounds", operation_id="list_rounds", dependencies=HANDLER)
def list_rounds(db: Db) -> list[dict[str, Any]]:
    """List the improver's rounds, newest first."""
    query = select(rounds).order_by(rounds.c.round_id.desc())
    return [round_view(db, r) for r in db.execute(query).mappings().all()]


@app.get("/api/rounds/{round_id}", operation_id="get_round", dependencies=AGENT)
def get_round(round_id: int, db: Db) -> dict[str, Any]:
    """Get one round: its stage, its proposal waiting, its verdict and its spending."""
    return round_view(db, round_row(db, round_id))


@app.post("/api/rounds/{round_id}/stop", operation_id="stop_round")
def stop_round(round_id: int, handler: Handler, db: Db) -> dict[str, Any]:
    """Stop an open round; a version not yet judged is rolled back."""
    may_act(handler, "stop a round")
    stop(db, open_round(db, round_id), handler["handler_id"])
    db.commit()
    return round_view(db, round_row(db, round_id))


def stop(db: Connection, row: RowMapping, by: str | None) -> None:
    """Stop a round and its running runs; a version not yet judged is rolled back."""
    round_id = row["round_id"]
    db.execute(
        update(rounds)
        .where(rounds.c.round_id == round_id)
        .values(stage="stopped", proposal=None, stopped_by=by, finished_at=store.now())
    )
    if row["version"] is not None and row["kept"] is None:
        query = update(config_versions)
        query = query.where(config_versions.c.version == row["version"])
        db.execute(query.values(kept=False))
    query = update(runs).where(runs.c.round_id == round_id, runs.c.status == "running")
    db.execute(query.values(status="stopped", finished_at=store.now()))


@app.post(
    "/api/rounds/stop-working", operation_id="stop_working_rounds", dependencies=AGENT
)
def stop_working_rounds(db: Db) -> dict[str, int]:
    """Stop any round at work, not waiting for a handler, when the service starts."""
    query = select(rounds).where(rounds.c.stage.not_in(CLOSED | WAITING))
    working = db.execute(query.with_for_update()).mappings().all()
    for row in working:
        stop(db, row, None)
    db.commit()
    return {"stopped": len(working)}


def answer_proposal(
    db: Connection, round_id: int, handler: RowMapping, decision: str, reason: str
) -> dict[str, Any]:
    """Pass a handler's answer on a proposal to the agent's service to record."""
    row = open_round(db, round_id)
    if row["proposal"] is None:
        raise HTTPException(409, f"Round {round_id} has no proposal waiting")
    # End the read, so the service can record the answer while this waits.
    db.commit()
    body = {"decision": decision, "reason": reason, "handler_id": handler["handler_id"]}
    return agent_service.start(f"rounds/{round_id}/decision", body)


@app.post("/api/rounds/{round_id}/approve", operation_id="approve_proposal")
def approve_proposal(round_id: int, handler: Handler, db: Db) -> dict[str, Any]:
    """Approve a round's proposal, as a handler who can sign off over the limit."""
    may_act(handler, "approve the improver's proposals")
    return answer_proposal(db, round_id, handler, "approve", "")


@app.post("/api/rounds/{round_id}/reject", operation_id="reject_proposal")
def reject_proposal(
    round_id: int, rejection: Rejection, handler: Handler, db: Db
) -> dict[str, Any]:
    """Reject a round's proposal with a reason that goes back to the improver."""
    may_act(handler, "reject the improver's proposals")
    return answer_proposal(db, round_id, handler, "reject", rejection.reason)


@app.get(
    "/api/rounds/{round_id}/results",
    operation_id="read_round_results",
    dependencies=AGENT,
)
def read_round_results(round_id: int, db: Db) -> dict[str, Any]:
    """Get what the improver reads: scores, mistakes, earlier rounds, rule, budget."""
    row = round_row(db, round_id)
    settings_ = json.loads(row["settings"])
    current = current_version(db)
    version = current["version"]
    query = select(rounds).where(rounds.c.round_id < round_id)
    earlier = db.execute(query.order_by(rounds.c.round_id)).mappings().all()
    return {
        "current_version": configuration(current, current["model"], current["effort"]),
        "scores": {
            s: version_scores(db, version, s) for s in ["evaluation", "held_out"]
        },
        "evaluation_set": mistakes(db, version),
        "earlier_rounds": [earlier_round(db, r) for r in earlier],
        "objectives_on_the_held_out_set": settings_["objectives"],
        "noise": settings_["noise"],
        "tolerance": settings_["tolerance"],
        "rule": rule.RULE,
        "spending_left": round_left(db, row),
        "models_allowed": models_allowed(),
    }


@app.post(
    "/api/rounds/{round_id}/stage",
    operation_id="record_round_stage",
    dependencies=AGENT,
)
def record_round_stage(round_id: int, stage: RoundStage, db: Db) -> dict[str, Any]:
    """Record where an open round has got to; finished closes it."""
    open_round(db, round_id)
    values: dict[str, Any] = {"stage": stage.stage}
    if stage.stage == "finished":
        values["finished_at"] = store.now()
    db.execute(update(rounds).where(rounds.c.round_id == round_id).values(**values))
    db.commit()
    return round_view(db, round_row(db, round_id))


def wait_for_handler(db: Connection, round_id: int, proposal: dict[str, Any]) -> None:
    """Record a proposal waiting for a handler."""
    stage = f"{proposal['kind']}_waiting"
    query = update(rounds).where(rounds.c.round_id == round_id)
    db.execute(query.values(stage=stage, proposal=json.dumps(proposal)))
    db.commit()


@app.post(
    "/api/rounds/{round_id}/proposed-change",
    operation_id="record_proposed_change",
    dependencies=AGENT,
)
def record_proposed_change(
    round_id: int, change: ProposedChange, db: Db
) -> dict[str, Any]:
    """Record a new configuration the improver proposes, to wait for a handler."""
    row = open_round(db, round_id)
    if row["version"] is not None:
        raise HTTPException(409, f"Round {round_id} has already tried a version")
    allowed = {m["model"]: m["reasoning_efforts"] for m in models_allowed()}
    if change.effort not in allowed.get(change.model, []):
        raise HTTPException(
            422,
            f"{change.model} at {change.effort} isn't allowed."
            " Choose from the models allowed",
        )
    wait_for_handler(db, round_id, {"kind": "change", **change.model_dump()})
    return round_view(db, round_row(db, round_id))


@app.post(
    "/api/rounds/{round_id}/proposed-recipe",
    operation_id="record_proposed_recipe",
    dependencies=AGENT,
)
def record_proposed_recipe(
    round_id: int, recipe: ProposedRecipe, db: Db
) -> dict[str, Any]:
    """Record a new recipe the improver proposes, to wait for a handler."""
    open_round(db, round_id)
    wait_for_handler(db, round_id, {"kind": "recipe", **recipe.model_dump()})
    return round_view(db, round_row(db, round_id))


def approved(db: Connection, round_id: int, proposal: dict[str, Any], by: str) -> None:
    """Record an approved proposal: a version under test, or the next recipe."""
    table = recipes if proposal["kind"] == "recipe" else config_versions
    record_ = {
        "version": (db.scalar(select(func.max(table.c.version))) or 0) + 1,
        "reason": proposal["reason"],
        "proposed_by": f"improver (round {round_id})",
        "approved_by": by,
        "approved_at": store.now(),
    }
    if proposal["kind"] == "recipe":
        db.execute(insert(recipes).values(**record_, text=proposal["recipe"]))
        return
    db.execute(
        insert(config_versions).values(
            **record_,
            instructions=proposal["instructions"],
            model=proposal["model"],
            effort=proposal["effort"],
            kept=None,
        )
    )
    query = update(rounds).where(rounds.c.round_id == round_id)
    db.execute(query.values(version=record_["version"], reason=proposal["reason"]))


@app.post(
    "/api/rounds/{round_id}/decision",
    operation_id="record_round_decision",
    dependencies=AGENT,
)
def record_round_decision(
    round_id: int, answer: RoundDecision, db: Db
) -> dict[str, Any]:
    """Record a handler's approval or rejection of a round's proposal."""
    row = open_round(db, round_id)
    proposal = loaded(row["proposal"])
    if proposal is None:
        raise HTTPException(409, f"Round {round_id} has no proposal waiting")
    query = select(handlers).where(handlers.c.handler_id == answer.handler_id)
    handler = db.execute(query).mappings().first()
    if handler is None:
        raise HTTPException(404, f"Handler {answer.handler_id} not found")
    may_act(handler, "approve or reject the improver's proposals")
    if answer.decision == "approve":
        approved(db, round_id, proposal, answer.handler_id)
    db.execute(
        insert(round_events).values(
            round_id=round_id,
            kind=proposal["kind"],
            decision=answer.decision,
            handler_id=answer.handler_id,
            reason=answer.reason,
            decided_at=store.now(),
        )
    )
    query = update(rounds).where(rounds.c.round_id == round_id)
    db.execute(query.values(stage="improving", proposal=None))
    db.commit()
    return round_view(db, round_row(db, round_id))


@app.post(
    "/api/rounds/{round_id}/model-calls",
    operation_id="record_improver_call",
    dependencies=AGENT,
)
def record_improver_call(round_id: int, used: ImproverCall, db: Db) -> dict[str, str]:
    """Record one of the improver's own model calls with its round."""
    open_round(db, round_id)
    db.execute(insert(model_calls).values(round_id=round_id, **used.model_dump()))
    db.commit()
    return {"status": "recorded"}


@app.post(
    "/api/rounds/{round_id}/evaluations",
    operation_id="create_round_evaluation",
    dependencies=AGENT,
)
def create_round_evaluation(
    round_id: int, wanted: RoundEvaluation, db: Db
) -> dict[str, Any]:
    """Record a round's whole-set evaluation, unless it would go over the limit."""
    row = open_round(db, round_id)
    if wanted.version not in {row["version_before"], row["version"]}:
        raise HTTPException(
            409, f"Version {wanted.version} isn't under test in round {round_id}"
        )
    query = select(config_versions).where(config_versions.c.version == wanted.version)
    version = db.execute(query).mappings().one()
    # On a free model, as the agent's service records a run: MODEL_NAME at `default`.
    model = os.getenv("MODEL_NAME") or version["model"]
    effort = "default" if os.getenv("MODEL_NAME") else version["effort"]
    query = select(func.count()).select_from(answer_key)
    count = db.scalar(query.where(answer_key.c.eval_set == wanted.eval_set)) or 0
    likely, left = estimate(db, model, count), round_left(db, row)
    if likely > left:
        raise HTTPException(
            409,
            f"Refused: the evaluation could cost about ${likely:.4f} and ${left:.4f}"
            " of the round's spending limit is left",
        )
    create = RunCreate(
        kind="evaluation",
        n=json.loads(row["settings"])["agents_at_once"],
        eval_set=wanted.eval_set,
        tiny=False,
        model=model,
        effort=effort,
        version=wanted.version,
    )
    return add_run(db, create, round_id)


def verdict(db: Connection, row: RowMapping) -> tuple[bool, str]:
    """Keep or roll back the version a round tried, by the rule."""
    query = select(runs).where(
        runs.c.round_id == row["round_id"], runs.c.version == row["version"]
    )
    tried = db.execute(query).mappings().all()
    if any(r["status"] == "stopped" for r in tried):
        return False, "its evaluations were stopped"
    for eval_set, needed in NEEDED.items():
        done = [
            r for r in tried if r["eval_set"] == eval_set and r["status"] == "finished"
        ]
        if len(done) < needed:
            return False, "the round couldn't afford to evaluate it"
    before = version_scores(db, row["version_before"], "held_out")
    if before is None:
        return False, "the current version has no held-out scores to compare with"
    after = version_scores(db, row["version"], "held_out")
    return rule.keep_or_roll_back(after or {}, before, json.loads(row["settings"]))


@app.post(
    "/api/rounds/{round_id}/verdict", operation_id="apply_verdict", dependencies=AGENT
)
def apply_verdict(round_id: int, db: Db) -> dict[str, Any]:
    """Keep or roll back the version a round tried, by the rule, from its runs."""
    row = open_round(db, round_id)
    if row["version"] is None or row["kept"] is not None:
        raise HTTPException(
            409, f"Round {round_id} has no version waiting for a verdict"
        )
    kept, why = verdict(db, row)
    before = version_scores(db, row["version_before"], "held_out")
    after = version_scores(db, row["version"], "held_out")
    query = update(config_versions).where(config_versions.c.version == row["version"])
    db.execute(query.values(kept=kept))
    db.execute(
        update(rounds)
        .where(rounds.c.round_id == round_id)
        .values(
            stage="verdict",
            kept=kept,
            why=why,
            held_out_before=json.dumps(before),
            held_out_after=json.dumps(after),
        )
    )
    db.commit()
    return round_view(db, round_row(db, round_id))
