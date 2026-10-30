"""The agent's service: the backend asks it to carry on a claim after a sign-off."""

import threading

from fastapi import BackgroundTasks, FastAPI

from agent import main

app = FastAPI(title="UnderCover Agent")
working: set[str] = set()
working_lock = threading.Lock()


def carry_on_claim(claim_id: str) -> None:
    """Carry on a claim in progress, unless this service is already working it."""
    with working_lock:
        if claim_id in working:
            return
        working.add(claim_id)
    try:
        with main.backend() as client:
            claim = client.get(f"/api/claims/{claim_id}").json()
        if claim["status"] == "in_progress":
            print(f"{claim_id}: {main.carry_on(claim_id, claim['thread_id'])}")
    finally:
        with working_lock:
            working.discard(claim_id)


@app.get("/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the agent's service is running."""
    return {"status": "ok"}


@app.post("/claims/{claim_id}/resume", operation_id="resume_claim", status_code=202)
def resume_claim(claim_id: str, tasks: BackgroundTasks) -> dict[str, str]:
    """Carry on a claim that a handler has signed off, after replying."""
    tasks.add_task(carry_on_claim, claim_id)
    return {"status": "accepted"}
