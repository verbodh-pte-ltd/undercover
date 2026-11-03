"""The improver's client for the backend: its round, its results and its proposals.

The claim agent's calls stay in agent/main.py, which has no way to these.
"""

from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage

from agent import main


def post(path: str, body: Any = None) -> httpx.Response:
    """Post to the backend and return its response, refusals included."""
    with main.backend() as client:
        return client.post(path, json=body)


def get(path: str) -> httpx.Response:
    """Get from the backend and return its response."""
    with main.backend() as client:
        return client.get(path)


def text(response: httpx.Response) -> str:
    """A response as the improver reads it, refusals included."""
    if response.is_success:
        return response.text
    return f"Refused ({response.status_code}): {response.json()['detail']}"


def read_results(round_id: int) -> httpx.Response:
    """What the improver reads: scores, mistakes, earlier rounds, rule and spending."""
    return get(f"/api/rounds/{round_id}/results")


def find_round(round_id: int) -> httpx.Response:
    """The round, or the app's answer when it has none."""
    return get(f"/api/rounds/{round_id}")


def get_round(round_id: int) -> dict[str, Any]:
    """The round: its stage, the version it tried and its verdict."""
    return find_round(round_id).json()


def review(round_id: int, proposal_id: int) -> httpx.Response:
    """A proposal with a handler's review of it, as the app recorded it."""
    return get(f"/api/rounds/{round_id}/proposals/{proposal_id}")


def recipe(version: int) -> str:
    """The text of one version of the recipe."""
    found = get("/api/recipes").json()
    return next(r["text"] for r in found if r["version"] == version)


def propose(round_id: int, name: str, args: dict[str, Any]) -> httpx.Response:
    """Record a proposal, a change or a recipe, to wait for a handler."""
    if name == "propose_recipe":
        return post(f"/api/rounds/{round_id}/proposed-recipe", args)
    change = {
        "instructions": args["instructions"],
        "model": args["model"],
        "effort": args["reasoning_effort"],
        "reason": args["reason"],
    }
    return post(f"/api/rounds/{round_id}/proposed-change", change)


def record_stage(round_id: int, stage: str) -> None:
    """Record where the round has got to; a stopped round refuses it."""
    post(f"/api/rounds/{round_id}/stage", {"stage": stage})


def create_evaluation(round_id: int, eval_set: str, version: int) -> httpx.Response:
    """Ask for one of the round's whole-set evaluations, or why not."""
    body = {"eval_set": eval_set, "version": version}
    return post(f"/api/rounds/{round_id}/evaluations", body)


def apply_verdict(round_id: int) -> httpx.Response:
    """Keep or roll back the version the round tried, by the rule."""
    return post(f"/api/rounds/{round_id}/verdict")


def record_model_call(round_id: int, reply: BaseMessage) -> None:
    """Record one of the improver's model calls, as OpenRouter reports its cost."""
    usage = reply.usage_metadata if isinstance(reply, AIMessage) else None
    body = {
        "input_tokens": usage["input_tokens"] if usage else 0,
        "output_tokens": usage["output_tokens"] if usage else 0,
        "cost": reply.response_metadata.get("token_usage", {}).get("cost") or 0.0,
    }
    post(f"/api/rounds/{round_id}/model-calls", body)


def stop_round(round_id: int, reason: str) -> None:
    """Stop this round, whose work failed, with the reason."""
    post(f"/api/rounds/{round_id}/failed", {"reason": reason})
