"""The Copilot's own client for the app, which carries the handler's sign-in.

It never sends the agent's key: every call is the signed-in handler's, so the app
checks it as if the handler had pressed the button. The claim agent's client stays in
agent/main.py, which the Copilot never uses to reach the app.
"""

import os
from typing import Any

import httpx

APP_URL = "http://127.0.0.1:8000"
TIMEOUT = 30
# A step's detail is trimmed to this many characters, as the sidebar shows it.
DETAIL_LIMIT = 2000


def headers(token: str) -> dict[str, str]:
    """The handler's sign-in, as the app reads it."""
    return {"Authorization": f"Bearer {token}"}


def client(token: str) -> httpx.Client:
    """A client for the app at UNDERCOVER_API, with the handler's sign-in."""
    url = os.getenv("UNDERCOVER_API", APP_URL)
    return httpx.Client(base_url=url, timeout=TIMEOUT, headers=headers(token))


def get(token: str, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
    """Get from the app as the handler, refusals included."""
    with client(token) as app:
        return app.get(path, params=params)


def post(token: str, path: str, body: Any = None) -> httpx.Response:
    """Post to the app as the handler, refusals included."""
    with client(token) as app:
        return app.post(path, json=body)


def reason(response: httpx.Response) -> str:
    """Why the app refused, from its `detail`, or its text."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail or response.text or response.reason_phrase)


def refusal(response: httpx.Response) -> str:
    """A refusal as the model and the sidebar read it."""
    return f"Refused ({response.status_code}): {reason(response)}"


def signed_in_handler(token: str) -> httpx.Response:
    """Who the sign-in belongs to, or the app's refusal."""
    return get(token, "/api/handlers/me")


def configuration(token: str) -> dict[str, Any]:
    """The Copilot's model, its reasoning effort and its instructions."""
    found = get(token, "/api/copilot/configuration")
    found.raise_for_status()
    return found.json()


def trim(text: str | None) -> str | None:
    """Text trimmed to what a step's detail holds."""
    if text is None or len(text) <= DETAIL_LIMIT:
        return text
    return text[: DETAIL_LIMIT - 1] + "…"


def record_step(token: str, turn_id: int, step: dict[str, Any]) -> httpx.Response:
    """Record one step of the turn, or update it by its key."""
    return post(token, f"/api/copilot/turns/{turn_id}/steps", step)


def pause_turn(token: str, turn_id: int, claim_id: str) -> httpx.Response:
    """Pause the turn for the handler to confirm a sign-off; the app fills the card."""
    return post(token, f"/api/copilot/turns/{turn_id}/pause", {"claim_id": claim_id})


def finish_turn(token: str, turn_id: int, spent: dict[str, Any]) -> httpx.Response:
    """End the turn with its answer, its model calls, their cost and its time."""
    return post(token, f"/api/copilot/turns/{turn_id}/finish", spent)


def fail_turn(token: str, turn_id: int, spent: dict[str, Any]) -> httpx.Response:
    """End the turn as failed, with why, its model calls, their cost and its time."""
    return post(token, f"/api/copilot/turns/{turn_id}/fail", spent)
