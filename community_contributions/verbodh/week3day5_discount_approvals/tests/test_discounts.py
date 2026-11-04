"""The discount agent's graph with a scripted model, in memory: no model, no AWS, no database."""

import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discounts import app, graph  # noqa: E402


class Scripted(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> "Scripted":
        return self


class Remembered:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []

    def recall(self, rep_id: str, query: str) -> list[str]:
        return [f"{rep_id} likes the percent first"]

    def remember(self, rep_id: str, session_id: str, said: str, answered: str) -> None:
        self.saved.append((rep_id, answered))


def call(name: str, **args: Any) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


def agent(*replies: AIMessage, saver: Any = None) -> tuple[Any, Remembered]:
    notes = Remembered()
    built = graph.build(Scripted(messages=iter(replies)), saver or InMemorySaver(), notes)
    return built, notes


def ask(customer: str = "BLUEFIN", sku: str = "ANALYTICS", percent: float = 10) -> dict[str, Any]:
    return {
        "operation": "request",
        "rep_id": "REP-01",
        "customer_id": customer,
        "sku": sku,
        "seats": 120,
        "percent": percent,
        "justification": "Competitor quoted 12% lower.",
    }


def test_within_the_limit_the_agent_approves_on_its_own() -> None:
    built, notes = agent(
        call("get_customer", customer_id="BLUEFIN"),
        call("SubmitDecision", action="approve", percent=10, reason="Within the growth guide."),
    )
    result = app.handle(built, ask())
    assert result["status"] == "approved"
    assert result["decision"]["percent"] == 10
    assert notes.saved and notes.saved[0][0] == "REP-01"


def test_above_the_limit_waits_for_a_director_and_a_rep_cant_sign() -> None:
    saver = InMemorySaver()
    built, _ = agent(
        call("SubmitDecision", action="approve", percent=20, reason="Competitive replacement."),
        saver=saver,
    )
    result = app.handle(built, ask("ACME", percent=20))
    assert result["status"] == "waiting_for_director"
    assert result["card"]["proposed"] == 20

    refused = app.handle(built, {"operation": "sign_off", "thread_id": result["thread_id"], "person_id": "REP-01", "answer": "approve"})
    assert "refused" in refused and refused["status"] == "waiting_for_director"

    # A new graph on the same saver stands in for another session on Runtime.
    again, _ = agent(saver=saver)
    signed = app.handle(again, {"operation": "sign_off", "thread_id": result["thread_id"], "person_id": "DIR-01", "answer": "approve"})
    assert signed["status"] == "approved_by_director"
    assert signed["signed_by"] == "DIR-01"
    assert signed["decision"]["status"] == "approved_by_director"


def test_the_code_caps_at_the_floor_and_declines_a_customer_on_hold() -> None:
    built, _ = agent(call("SubmitDecision", action="approve", percent=22, reason="Big deal."))
    capped = app.handle(built, ask("ACME", sku="SUPPORT", percent=22))
    assert capped["decision"]["percent"] == 18 and capped["decision"]["action"] == "counter"
    assert capped["status"] == "waiting_for_director"

    held, _ = agent(call("SubmitDecision", action="approve", percent=5, reason="Fine."))
    assert app.handle(held, ask("DUNMORE", percent=5))["status"] == "declined"


@pytest.mark.parametrize("percent", [10, 30])
def test_a_model_that_never_decides_goes_to_a_director(percent: float) -> None:
    built, _ = agent(*[call("read_policy", section="Floors") for _ in range(graph.MAX_MODEL_CALLS)])
    result = app.handle(built, ask("EVERGREEN", sku="SECURITY", percent=percent))
    assert result["status"] == "waiting_for_director"
