"""The agent's service: starts dispatches, evaluations and rounds; resumes claims."""

import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import dispatcher, improver, improver_api, main

# One dispatch, evaluation or stretch of a round's work at a time; `doing` says which.
running = threading.Lock()
doing = {"what": ""}


class DispatchStart(BaseModel):
    n: int = Field(ge=1, le=25)


class EvaluationStart(BaseModel):
    n: int = Field(ge=1, le=25)
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool


class RoundStart(BaseModel):
    round_id: int
    thread_id: str


class RoundAnswer(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str
    handler_id: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """When the service starts, mark any run or round it was working as stopped."""
    # The backend may not be up yet; the next start then marks nothing. A round waiting
    # for a handler stays open, and carries on from its checkpoint when answered.
    with suppress(httpx.HTTPError), main.backend() as client:
        client.post("/api/runs/stop-running")
        client.post("/api/rounds/stop-working")
    yield


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


def carry_on_claim(claim_id: str) -> None:
    """Carry on a claim in progress, unless this process is already working it."""
    if not dispatcher.take(claim_id):
        return
    try:
        with main.backend() as client:
            claim = client.get(f"/api/claims/{claim_id}").json()
        if claim["status"] == "in_progress":
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


def take(what: str) -> None:
    """Take the one-run lock for some work, or refuse, saying what is running."""
    if not running.acquire(blocking=False):
        raise HTTPException(409, f"{doing['what']} is already running")
    doing["what"] = what


def in_background(work: Callable[..., None], *args: Any) -> dict[str, str]:
    """Do work that holds the lock in the background, letting go of it at the end."""

    def go() -> None:
        try:
            work(*args)
        finally:
            running.release()

    threading.Thread(target=go, daemon=True).start()
    return {"status": "started"}


def start(body: dict[str, Any]) -> dict[str, str]:
    """Start a run in the background, or refuse while something is running."""
    take("A dispatch" if body["kind"] == "dispatch" else "An evaluation")
    return in_background(work_run, body)


@app.get("/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent's service is running."""
    return {"status": "ok"}


@app.post("/claims/{claim_id}/resume", operation_id="resume_claim", status_code=202)
def resume_claim(claim_id: str, tasks: BackgroundTasks) -> dict[str, str]:
    """Carry on a claim that a handler has signed off, after replying."""
    tasks.add_task(carry_on_claim, claim_id)
    return {"status": "accepted"}


@app.post("/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(start_: DispatchStart) -> dict[str, str]:
    """Work the queue with up to n claim agents at once."""
    return start({"kind": "dispatch", "n": start_.n, "eval_set": None, "tiny": False})


@app.post("/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(start_: EvaluationStart) -> dict[str, str]:
    """Evaluate the agent on a labelled set, with up to n claim agents at once."""
    return start({"kind": "evaluation", **start_.model_dump()})


@app.post("/rounds", operation_id="start_round", status_code=202)
def start_round(start_: RoundStart) -> dict[str, str]:
    """Work a new round of the improver until it waits for a handler or finishes."""
    take(f"Round {start_.round_id}")
    return in_background(improver.work_round, start_.round_id)


@app.post(
    "/rounds/{round_id}/decision", operation_id="answer_proposal", status_code=202
)
def answer_proposal(round_id: int, answer: RoundAnswer) -> dict[str, str]:
    """Record a handler's answer on a round's proposal, then carry the round on."""
    take(f"Round {round_id}")
    try:
        recorded = improver_api.record_decision(round_id, answer.model_dump())
    except httpx.HTTPError:
        # Without this, the lock stays taken and every later start is refused.
        running.release()
        raise
    if not recorded.is_success:
        running.release()
        raise HTTPException(recorded.status_code, recorded.json()["detail"])
    return in_background(improver.answer, round_id, answer.decision, answer.reason)
