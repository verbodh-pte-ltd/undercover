"""The backend's calls to the agent's service, which works claims and runs."""

import os
from contextlib import suppress
from typing import Any

import httpx
from fastapi import HTTPException

AGENT_URL = "http://127.0.0.1:8002"
TIMEOUT = 5
CONFLICT = 409


def url() -> str:
    """The agent service's address: UNDERCOVER_AGENT, or port 8002 on this machine."""
    return os.getenv("UNDERCOVER_AGENT", AGENT_URL)


def resume_claim(claim_id: str) -> None:
    """Ask the service to carry on a signed-off claim; it may not be running."""
    # If it can't be reached, the agent carries on the claim the next time it runs.
    with suppress(httpx.HTTPError):
        httpx.post(f"{url()}/claims/{claim_id}/resume", timeout=TIMEOUT)


def start(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Ask the service to start a dispatch or an evaluation, passing on its refusal."""
    try:
        response = httpx.post(f"{url()}/{path}", json=body, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "The agent's service isn't running") from exc
    if response.status_code == CONFLICT:
        raise HTTPException(CONFLICT, response.json()["detail"])
    response.raise_for_status()
    return response.json()
