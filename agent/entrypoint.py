"""The agent as an AgentCore app: each payload names an operation and its inputs.

npm run dev serves `app` on the laptop, and runtime/main.py runs it on AgentCore
Runtime. Nothing happens at import: the agent gets ready on its first request, once
per process.
"""

import os
import random
import re
import secrets
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Annotated, Any, Literal

import httpx
from bedrock_agentcore.identity.auth import requires_api_key
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.exceptions import HTTPException

from agent import dispatcher, improver, improver_api, main

# On Runtime the deployment names each credential's provider in AgentCore Identity.
CREDENTIAL = re.compile(r"AGENTCORE_CREDENTIAL_(\w+)_NAME")
CONFLICT = 409
ACCEPTED = {"accepted": True}

app = BedrockAgentCoreApp()
getting_ready = threading.Lock()
ready = threading.Event()
# One dispatch, evaluation or stretch of a round's work at a time; `doing` says which.
running = threading.Lock()
doing = {"what": ""}


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(Operation):
    operation: Literal["health"]


class StartDispatch(Operation):
    operation: Literal["start_dispatch"]
    n: int = Field(ge=1, le=25)


class StartEvaluation(Operation):
    operation: Literal["start_evaluation"]
    n: int = Field(ge=1, le=25)
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool


class StartRound(Operation):
    operation: Literal["start_round"]
    round_id: int
    thread_id: str


class AnswerProposal(Operation):
    operation: Literal["answer_proposal"]
    round_id: int
    decision: Literal["approve", "reject"]
    reason: str
    handler_id: str


class ResumeClaim(Operation):
    operation: Literal["resume_claim"]
    claim_id: str


Payload = Annotated[
    Health
    | StartDispatch
    | StartEvaluation
    | StartRound
    | AnswerProposal
    | ResumeClaim,
    Field(discriminator="operation"),
]
PAYLOAD: TypeAdapter[Payload] = TypeAdapter(Payload)


def credentials() -> dict[str, str]:
    """Each credential's setting and its provider in AgentCore Identity, on Runtime."""
    found = (CREDENTIAL.fullmatch(name) for name in list(os.environ))
    return {m[1]: os.environ[m[0]] for m in found if m}


def on_runtime() -> bool:
    """Whether the agent runs on AgentCore Runtime, which gives it its credentials."""
    return bool(credentials())


def fetch(provider: str) -> str:
    """A credential's value from AgentCore Identity, with the request's token."""

    @requires_api_key(provider_name=provider)
    def value(**fetched: str) -> str:
        return fetched["api_key"]

    return value()


def stop_what_was_running() -> None:
    """Mark any run or round this agent was working as stopped."""
    # The backend may not be up yet; the next start then marks nothing. A round waiting
    # for a handler stays open, and carries on from its checkpoint when answered.
    with suppress(httpx.HTTPError), main.backend() as client:
        client.post("/api/runs/stop-running")
        client.post("/api/rounds/stop-working")


def get_ready() -> None:
    """Once per process, on the first request: credentials, the key, the pool."""
    with getting_ready:
        if ready.is_set():
            return
        if on_runtime():
            # Every session on Runtime starts from one snapshot, random numbers and all.
            random.seed()
            for setting, provider in credentials().items():
                os.environ[setting] = fetch(provider)
        if not os.getenv("UNDERCOVER_API_KEY"):
            raise RuntimeError(
                "UNDERCOVER_API_KEY is missing. Add it to .env: the agent needs it for"
                " every call to the backend"
            )
        if os.getenv("DATABASE_URL"):
            main.pool()
        stop_what_was_running()
        ready.set()


def check_key(context: RequestContext) -> None:
    """Off Runtime, refuse a request without the agent's key; on Runtime AWS checks."""
    if on_runtime():
        return
    sent = (context.request_headers or {}).get("Authorization", "")
    expected = f"Bearer {os.environ['UNDERCOVER_API_KEY']}"
    if not secrets.compare_digest(sent.encode(), expected.encode()):
        raise HTTPException(401, "The agent's key is missing or wrong")


def problems(exc: ValidationError) -> str:
    """What's wrong with a payload, input by input."""
    return "; ".join(
        f"{e['loc'][-1] if e['loc'] else 'operation'}: {e['msg']}" for e in exc.errors()
    )


def in_background(work: Callable[..., Any], *args: Any, holding: bool) -> dict:
    """Do work on a thread of its own, registered with the app until it ends."""
    task = app.add_async_task(getattr(work, "__name__", "work"))

    def go() -> None:
        try:
            work(*args)
        finally:
            if holding:
                running.release()
            app.complete_async_task(task)

    threading.Thread(target=go, daemon=True).start()
    return ACCEPTED


def take(what: str) -> bool:
    """Take the one-run lock for some work, unless something is running."""
    if not running.acquire(blocking=False):
        return False
    doing["what"] = what
    return True


def refusal() -> dict[str, str]:
    """The answer while something else is running."""
    return {"refused": f"{doing['what']} is already running"}


def resume(claim_id: str) -> None:
    """Carry on a claim in progress or signed off, unless this process has it."""
    if not dispatcher.take(claim_id):
        return
    try:
        with main.backend() as client:
            claim = client.get(f"/api/claims/{claim_id}").json()
        if claim["status"] == "in_progress" or claim["signed_off_by"]:
            print(f"{claim_id}: {main.carry_on(claim_id, claim['thread_id'])}")
    finally:
        dispatcher.let_go(claim_id)


def work_run(body: dict[str, Any]) -> None:
    """Create the run on the current version, then work it."""
    with main.backend() as client:
        version = client.get("/api/configuration/current").json()
        body = {
            **body,
            "version": version["version"],
            "model": main.model_name(version),
            "effort": main.effort(version),
        }
        run = client.post("/api/runs", json=body).json()
    dispatcher.dispatch(run["run_id"], body["n"])


def start(what: str, work: Callable[..., Any], *args: Any) -> dict:
    """Start work that holds the one-run lock, or refuse while something is running."""
    if not take(what):
        return refusal()
    return in_background(work, *args, holding=True)


def answer(proposal: AnswerProposal) -> dict:
    """Record a handler's answer on a round's proposal, then carry the round on."""
    if not take(f"Round {proposal.round_id}"):
        return refusal()
    body = proposal.model_dump(include={"decision", "reason", "handler_id"})
    try:
        recorded = improver_api.record_decision(proposal.round_id, body)
    except httpx.HTTPError:
        # Without this, the lock stays taken and every later start is refused.
        running.release()
        raise
    if not recorded.is_success:
        running.release()
        detail = recorded.json()["detail"]
        if recorded.status_code == CONFLICT:
            return {"refused": detail}
        raise HTTPException(recorded.status_code, detail)
    return in_background(
        improver.answer,
        proposal.round_id,
        proposal.decision,
        proposal.reason,
        holding=True,
    )


def operate(operation: Payload) -> dict:
    """Do what the operation asks: answer at once, and work in the background."""
    match operation:
        case Health():
            return {"healthy": True}
        case StartDispatch(n=n):
            body = {"kind": "dispatch", "n": n, "eval_set": None, "tiny": False}
            return start("A dispatch", work_run, body)
        case StartEvaluation():
            body = operation.model_dump(exclude={"operation"})
            return start("An evaluation", work_run, {"kind": "evaluation", **body})
        case StartRound(round_id=round_id):
            return start(f"Round {round_id}", improver.work_round, round_id)
        case AnswerProposal():
            return answer(operation)
        case ResumeClaim(claim_id=claim_id):
            return in_background(resume, claim_id, holding=False)


@app.entrypoint
def invoke(payload: Any, context: RequestContext) -> dict:
    """Take one operation's payload, check it, and answer."""
    get_ready()
    try:
        operation = PAYLOAD.validate_python(payload)
    except ValidationError as exc:
        raise HTTPException(400, problems(exc)) from exc
    if not isinstance(operation, Health):
        check_key(context)
    return operate(operation)
