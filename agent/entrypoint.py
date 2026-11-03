"""The agent as an AgentCore app: each payload names an operation and its inputs.

npm run dev serves `app` on the laptop, and runtime/main.py runs it on AgentCore
Runtime. Nothing happens at import: the agent gets ready on its first request, once
per process. The app keeps one run or round going at a time and creates each one
before it asks; the agent works it by its id and keeps no lock of its own.
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
ACCEPTED = {"accepted": True}
CLOSED = {"finished", "stopped"}
# How often, in seconds, the agent tells the app it's still working a run or a round.
HEARD_EVERY = 60

app = BedrockAgentCoreApp()
getting_ready = threading.Lock()
ready = threading.Event()


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(Operation):
    operation: Literal["health"]


class WorkRun(Operation):
    operation: Literal["work_run"]
    run_id: int


class WorkRound(Operation):
    operation: Literal["work_round"]
    round_id: int


class CarryRoundOn(Operation):
    operation: Literal["carry_round_on"]
    round_id: int
    proposal_id: int


class ResumeClaim(Operation):
    operation: Literal["resume_claim"]
    claim_id: str


Payload = Annotated[
    Health | WorkRun | WorkRound | CarryRoundOn | ResumeClaim,
    Field(discriminator="operation"),
]
PAYLOAD: TypeAdapter[Payload] = TypeAdapter(Payload)
# The run or round a piece of work holds: ("run", 7) or ("round", 3).
Holding = tuple[Literal["run", "round"], int]


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


def tell_the_app(holding: Holding, done: threading.Event) -> None:
    """Tell the app every minute that the work goes on, until it's done."""
    kind, work_id = holding
    while not done.wait(HEARD_EVERY):
        # A word that doesn't arrive is made up by the next one.
        with suppress(httpx.HTTPError), main.backend() as client:
            client.post("/api/work/heard", json={"kind": kind, "work_id": work_id})


def stop(holding: Holding, exc: Exception) -> None:
    """Stop the run or round whose work failed, and nothing else."""
    kind, work_id = holding
    reason = f"The agent's work failed: {type(exc).__name__}: {exc}"
    print(f"{kind.capitalize()} {work_id} stopped. {reason}")
    if kind == "round":
        improver_api.stop_round(work_id, reason)
        return
    with main.backend() as client:
        client.post(f"/api/runs/{work_id}/failed", json={"reason": reason})


def in_background(
    work: Callable[..., Any], *args: Any, holding: Holding | None = None
) -> dict:
    """Do work on a thread of its own, registered with the app until it ends.

    Work on a run or a round tells the app every minute while it goes on, and
    stops its run or round if it fails.
    """
    task = app.add_async_task(getattr(work, "__name__", "work"))
    done = threading.Event()

    def go() -> None:
        try:
            work(*args)
        except Exception as exc:
            if holding is None:
                raise
            # Unreached, the app counts the work stopped once it has been quiet.
            with suppress(httpx.HTTPError):
                stop(holding, exc)
        finally:
            done.set()
            app.complete_async_task(task)

    if holding is not None:
        threading.Thread(target=tell_the_app, args=(holding, done), daemon=True).start()
    threading.Thread(target=go, daemon=True).start()
    return ACCEPTED


def resume(claim_id: str) -> None:
    """Resume a paused claim with the handler's answer, unless this process has it."""
    if not dispatcher.take(claim_id):
        return
    try:
        print(f"{claim_id}: {main.resume_claim(claim_id)}")
    finally:
        dispatcher.let_go(claim_id)


def take_resume(claim_id: str) -> dict:
    """Resume a paused claim in the background, or refuse with the reason."""
    refused = main.resume_refusal(claim_id)
    if refused:
        return {"refused": refused}
    return in_background(resume, claim_id)


def work_run(run_id: int) -> dict:
    """Work a dispatch or an evaluation the app has created, while it's running."""
    with main.backend() as client:
        found = client.get(f"/api/runs/{run_id}")
    if not found.is_success:
        return {"refused": found.json()["detail"]}
    run = found.json()
    if run["status"] != "running":
        return {"refused": f"Run {run_id} is {run['status']}"}
    return in_background(dispatcher.dispatch, run_id, run["n"], holding=("run", run_id))


def closed(round_id: int) -> str | None:
    """Why the agent can't work a round, when it's missing or closed; or None."""
    found = improver_api.find_round(round_id)
    if not found.is_success:
        return found.json()["detail"]
    stage = found.json()["stage"]
    return f"Round {round_id} is {stage}" if stage in CLOSED else None


def work_round(round_id: int) -> dict:
    """Work a new round the app has created, while it's open."""
    why = closed(round_id)
    if why:
        return {"refused": why}
    return in_background(improver.work_round, round_id, holding=("round", round_id))


def carry_round_on(round_id: int, proposal_id: int) -> dict:
    """Carry a round on from a handler's review of its proposal, read from the app."""
    why = closed(round_id)
    if why:
        return {"refused": why}
    found = improver_api.review(round_id, proposal_id)
    if not found.is_success:
        return {"refused": found.json()["detail"]}
    review = found.json()
    if review["decision"] is None:
        return {"refused": f"Proposal {proposal_id} hasn't been reviewed"}
    return in_background(
        improver.answer,
        round_id,
        review["decision"],
        review["reason"],
        holding=("round", round_id),
    )


def operate(operation: Payload) -> dict:
    """Do what the operation asks: answer at once, and work in the background."""
    match operation:
        case Health():
            return {"healthy": True}
        case WorkRun(run_id=run_id):
            return work_run(run_id)
        case WorkRound(round_id=round_id):
            return work_round(round_id)
        case CarryRoundOn(round_id=round_id, proposal_id=proposal_id):
            return carry_round_on(round_id, proposal_id)
        case ResumeClaim(claim_id=claim_id):
            return take_resume(claim_id)


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
