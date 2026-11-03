import logging
from datetime import UTC, datetime

import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.base import BaseStore, SearchItem

from agent import main, memory
from agent.tests.test_agent import (  # noqa: F401 (check and in_process_backend are fixtures)
    INSTRUCTION,
    StubModel,
    calls,
    check,
    claim,
    decide,
    hold,
    in_process_backend,
    new_run,
    screening,
    steps_of,
    stub,
)
from backend.main import app
from backend.tests.signing import AGENT, headers

client = TestClient(app, headers=AGENT)
NOW = datetime.now(UTC)


class FakeStore(BaseStore):
    """A stand-in for AgentCore Memory that keeps what is put and answers searches."""

    def __init__(self, found=(), fail=None):
        self.puts = []
        self.searches = []
        self.found = list(found)
        self.fail = fail

    def batch(self, ops):
        raise NotImplementedError

    async def abatch(self, ops):
        raise NotImplementedError

    def put(self, namespace, key, value, index=None, *, ttl=None):
        if self.fail:
            raise self.fail
        self.puts.append((namespace, key, value))

    def search(self, namespace_prefix, /, *, query=None, limit=10, **kwargs):
        if self.fail:
            raise self.fail
        self.searches.append((namespace_prefix, query, limit))
        return self.found


def memory_on(monkeypatch, store):
    monkeypatch.setattr(memory, "memory_store", lambda: store)
    return store


def item(namespace, text):
    return SearchItem(
        namespace=namespace,
        key=text[:8],
        value={"content": text},
        created_at=NOW,
        updated_at=NOW,
    )


def test_the_store_is_agentcore_memory_with_the_id_in_the_region(monkeypatch):
    made = []
    monkeypatch.setattr(
        memory, "AgentCoreMemoryStore", lambda **kwargs: made.append(kwargs) or "store"
    )
    assert memory.memory_store() is None
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "undercover_memory-abc")
    assert memory.memory_store() == "store"
    assert made == [{"memory_id": "undercover_memory-abc", "region_name": "us-east-1"}]


def test_a_decided_claim_saves_two_events_without_its_story(monkeypatch):
    store = memory_on(monkeypatch, FakeStore())
    main.work("CLM-1001", None, stub(*decide("CLM-1001")))
    assert [(n, k) for n, k, _ in store.puts] == [
        (("CUS-3001", "CLM-1001"), "claim"),
        (("CUS-3001", "CLM-1001"), "decision"),
    ]
    customer, agent = (v["message"] for _, _, v in store.puts)
    assert isinstance(customer, HumanMessage)
    assert isinstance(agent, AIMessage)
    filed = claim("CLM-1001")
    for part in [filed["summary"], "trip_delay", "2026-06-18", "Rome", "Airport"]:
        assert part in customer.text, part
    for part in ["decline", "$0.00", "3. Trip Delay", "crew problem"]:
        assert part in agent.text, part
    for text in [customer.text, agent.text]:
        assert filed["story"][:40] not in text
        assert filed["documents"][0]["content"][:40] not in text
    assert any(t.startswith("remember: Claim CLM-1001") for t in steps_of("CLM-1001"))


def test_work_stopped_before_remember_saves_once_when_carried_on(monkeypatch):
    store = memory_on(monkeypatch, FakeStore())
    real = main.remember
    stops = [KeyboardInterrupt()]

    def remember(state, runtime):
        if stops:
            raise stops.pop()
        return real(state, runtime)

    monkeypatch.setattr(main, "remember", remember)
    with pytest.raises(KeyboardInterrupt):
        main.work("CLM-1001", None, stub(*decide("CLM-1001")))
    assert claim("CLM-1001")["status"] == "decided"
    assert store.puts == []
    for _ in range(2):
        main.run("CLM-1001", "CLM-1001-1", stub())
        assert len(store.puts) == 2


def test_a_claim_over_the_limit_is_saved_once_after_its_sign_off(monkeypatch):
    store = memory_on(monkeypatch, FakeStore())
    monkeypatch.setattr(main, "model", lambda version: stub())
    assert hold() == "CLM-1001 is waiting for a handler's sign-off."
    assert store.puts == []
    client.post("/api/claims/CLM-1001/sign-off", headers=headers())
    assert main.resume_claim("CLM-1001") == "CLM-1001 is decided: approve"
    assert [k for _, k, _ in store.puts] == ["claim", "decision"]
    texts = steps_of("CLM-1001")
    assert texts.index("Signed off by HND-01") < texts.index(
        next(t for t in texts if t.startswith("remember: "))
    )
    main.run("CLM-1001", "CLM-1001-1", stub())
    assert len(store.puts) == 2


def test_nothing_is_saved_for_a_claim_the_check_referred(request, monkeypatch):
    store = memory_on(monkeypatch, FakeStore())
    request.getfixturevalue("check").script(screening(True, INSTRUCTION))
    main.work("CLM-1901", None, stub())
    assert claim("CLM-1901")["decision"] == "refer"
    assert store.puts == []


def test_nothing_is_saved_for_an_evaluations_copy(monkeypatch):
    store = memory_on(monkeypatch, FakeStore())
    run_id = new_run("evaluation", "evaluation", tiny=True, n=1)
    copy_id = client.get(f"/api/runs/{run_id}/claims").json()[0]
    main.work(copy_id, run_id, stub(*decide(copy_id)))
    assert claim(copy_id)["decision"] == "decline"
    assert store.puts == []


def test_a_search_is_kept_to_the_claims_customer_and_leaves_out_this_claim(
    monkeypatch,
):
    found = [
        item(("customers", "CUS-3005", "facts"), "Oliver Grant travels to Kenya."),
        item(
            ("customers", "CUS-3005", "claims", "CLM-1005"),
            "A giraffe ate his passport; approved.",
        ),
        item(("customers", "CUS-3005", "claims", "CLM-1006"), "This claim, again."),
    ]
    store = memory_on(monkeypatch, FakeStore(found))
    replies = [
        calls(("search_memory", {"query": "earlier claims"})),
        *decide("CLM-1006", "refer"),
    ]
    main.work("CLM-1006", None, stub(*replies))
    assert store.searches == [(("customers", "CUS-3005"), "earlier claims", 5)]
    result = [t for t in steps_of("CLM-1006") if t.startswith("search_memory: ")][0]
    assert "Oliver Grant travels to Kenya." in result
    assert "giraffe" in result
    assert "This claim, again." not in result


class Binding(StubModel):
    bound: list

    def bind_tools(self, tools, **kwargs):
        self.bound.extend(t.name for t in tools)
        return self


def tools_bound(monkeypatch, claim_id, run_id=None):
    model = Binding(messages=iter(decide(claim_id)), bound=[])
    main.work(claim_id, run_id, model)
    return model.bound


def test_no_memory_tool_when_memory_is_off_or_on_an_evaluations_copy(monkeypatch):
    assert "search_memory" not in tools_bound(monkeypatch, "CLM-1001")
    memory_on(monkeypatch, FakeStore())
    assert "search_memory" in tools_bound(monkeypatch, "CLM-1002")
    run_id = new_run("evaluation", "evaluation", tiny=True, n=1)
    copy_id = client.get(f"/api/runs/{run_id}/claims").json()[0]
    assert "search_memory" not in tools_bound(monkeypatch, copy_id, run_id)


def test_when_agentcore_cant_be_reached_the_decision_stands(monkeypatch, caplog):
    unreachable = EndpointConnectionError(endpoint_url="https://agentcore.test")
    memory_on(monkeypatch, FakeStore(fail=unreachable))
    replies = [
        calls(("search_memory", {"query": "earlier claims"})),
        *decide("CLM-1001"),
    ]
    with caplog.at_level(logging.WARNING, logger="agent.memory"):
        main.work("CLM-1001", None, stub(*replies))
    assert claim("CLM-1001")["decision"] == "decline"
    assert "Couldn't save CLM-1001 to memory" in caplog.text
    texts = steps_of("CLM-1001")
    assert any(
        t.startswith("search_memory: Refused: memory can't be reached") for t in texts
    )
    assert "remember: memory can't be reached, nothing saved" in texts


def test_a_memory_search_and_a_save_are_steps_with_what_they_found_and_saved(
    monkeypatch,
):
    found = [item(("customers", "CUS-3001", "facts"), "Flies Premium to Rome.")]
    memory_on(monkeypatch, FakeStore(found))
    replies = [
        calls(("search_memory", {"query": "earlier claims"})),
        *decide("CLM-1001"),
    ]
    main.work("CLM-1001", None, stub(*replies))
    found_steps = client.get("/api/claims/CLM-1001/steps", headers=headers()).json()
    kinds = {s["kind"]: s for s in found_steps}
    assert kinds["memory_search"]["detail"] == {
        "tool": "search_memory",
        "query": "earlier claims",
        "found": ["Flies Premium to Rome."],
    }
    saved = kinds["memory_save"]["detail"]["saved"]
    assert saved[0].startswith("Claim CLM-1001")
    assert saved[1].startswith("Decision on CLM-1001: decline")
