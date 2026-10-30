"""The dispatcher: works a dispatch or an evaluation with up to N claim agents at once.

A LangGraph graph with no model, as in labs/week2/week2day4.ipynb: list_claims fans out
with one Send per claim to work_claim, and max_concurrency caps how many run at once.
It saves no progress of its own: each claim saves its own on its own thread.
"""

import operator
import threading
import time
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from agent import main

RATE_LIMIT_TRIES = 3

# The claims this process is working, by the dispatcher or after a sign-off, so
# neither picks up a claim the other has.
working: set[str] = set()
working_lock = threading.Lock()


class Dispatch(TypedDict):
    """What the dispatcher keeps for one run."""

    claims: list[str]
    results: Annotated[list, operator.add]


class ClaimTask(TypedDict):
    """What the dispatcher sends to one claim agent."""

    claim_id: str


def take(claim_id: str) -> bool:
    """Mark a claim as being worked here, unless it already is."""
    with working_lock:
        if claim_id in working:
            return False
        working.add(claim_id)
        return True


def let_go(claim_id: str) -> None:
    """Mark a claim as no longer being worked here."""
    with working_lock:
        working.discard(claim_id)


def now() -> str:
    """The time now in UTC, as ISO 8601 text."""
    return datetime.now(UTC).isoformat()


def build(run_id: int, llm: BaseChatModel | None = None) -> CompiledStateGraph:
    """The dispatcher for one run."""

    def list_claims(state: Dispatch) -> dict[str, Any]:
        """The run's claims to work, leaving out any already being worked here."""
        with main.backend() as client:
            listed = client.get(f"/api/runs/{run_id}/claims").json()
        with working_lock:
            return {"claims": [c for c in listed if c not in working]}

    def fan_out(state: Dispatch) -> list[Send]:
        """Send each claim to a claim agent."""
        return [Send("work_claim", {"claim_id": c}) for c in state["claims"]]

    def work_claim(state: ClaimTask) -> dict[str, Any]:
        """Work one claim; a rate limit or timeout waits and carries it on, 3 tries."""
        claim_id = state["claim_id"]
        started = now()
        if not take(claim_id):
            reply = f"{claim_id} is already being worked"
            return {"results": [{"claim_id": claim_id, "reply": reply}]}
        try:
            reply = ""
            for tries in range(1, RATE_LIMIT_TRIES + 1):
                try:
                    reply = main.work(claim_id, run_id, llm)
                    break
                except (ModelRateLimitError, ModelTimeoutError) as exc:
                    if tries == RATE_LIMIT_TRIES:
                        # Left in progress: the next dispatch carries it on.
                        reply = f"left in progress after {tries} tries: {exc}"
                        main.record_step(
                            claim_id, "error", reply[: main.RESULT_PREVIEW]
                        )
                    else:
                        time.sleep(main.RETRY_DELAY * 2 ** (tries - 1))
        finally:
            let_go(claim_id)
        result = {"claim_id": claim_id, "reply": reply, "started": started}
        return {"results": [{**result, "finished": now()}]}

    builder = StateGraph(Dispatch)
    builder.add_node("list_claims", list_claims)
    builder.add_node("work_claim", work_claim)
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges("list_claims", fan_out, ["work_claim"])
    builder.add_edge("work_claim", END)
    return builder.compile()


def dispatch(run_id: int, n: int, llm: BaseChatModel | None = None) -> list[dict]:
    """Work a run's claims with up to n claim agents at once, then finish the run."""
    result = build(run_id, llm).invoke({}, {"max_concurrency": n})
    with main.backend() as client:
        client.post(f"/api/runs/{run_id}/finish")
    return result.get("results", [])
