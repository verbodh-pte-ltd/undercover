"""The agent's service: starts dispatches and evaluations, and resumes claims."""

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent import dispatcher, main

running = threading.Lock()


class DispatchStart(BaseModel):
    n: int = Field(ge=1, le=25)


class EvaluationStart(BaseModel):
    n: int = Field(ge=1, le=25)
    eval_set: Literal["evaluation", "held_out"]
    tiny: bool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """When the service starts, mark any run it was working as stopped."""
    # The backend may not be up yet; the next start then marks nothing.
    with suppress(httpx.HTTPError), main.backend() as client:
        client.post("/api/runs/stop-running")
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
    """Create the run through the backend, then work it; runs in the background."""
    try:
        with main.backend() as client:
            run = client.post("/api/runs", json=body).json()
        dispatcher.dispatch(run["run_id"], body["n"])
    finally:
        running.release()


def start(body: dict[str, Any]) -> dict[str, str]:
    """Start a run in the background, or refuse while one is going."""
    if not running.acquire(blocking=False):
        raise HTTPException(409, "A dispatch or evaluation is already running")
    body = {**body, "model": main.model_name(), "effort": main.effort()}
    threading.Thread(target=work_run, args=(body,), daemon=True).start()
    return {"status": "started"}


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
