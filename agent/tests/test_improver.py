import json
import threading
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables.config import var_child_runnable_config
from sqlalchemy import select

from agent import dispatcher, entrypoint, improver, improver_api, main
from agent.tests.test_agent import (
    Script,
    StubModel,
    in_process_backend,  # noqa: F401 (an autouse fixture)
    invoke,
    settle,
)
from backend import agent_service, judge, store
from backend.main import app
from backend.tests.records import record_decision
from backend.tests.signing import AGENT, headers
from backend.tests.test_rounds import GRADED, answer_key

client = TestClient(app, headers=AGENT)

LUNA = {"model": "openai/gpt-6-luna", "reasoning_effort": "low"}
CHANGE = {
    "instructions": "Decline a claim with no receipt.",
    **LUNA,
    "reason": "Most misses were approvals with no receipt",
}
RECIPE = {"recipe": "1. Read the results.\n2. Propose a change.", "reason": "Shorter"}
COST = 0.01
bound: list[dict] = []


class ImproverStub(StubModel):
    """The improver's stub model, noting what each call binds with the tools."""

    def bind_tools(self, tools, **kwargs):
        bound.append(kwargs)
        return self


def asks(name, args=None):
    """The improver's reply calling one tool, with its cost as OpenRouter reports it."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {}, "id": uuid.uuid4().hex}],
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        response_metadata={"token_usage": {"cost": COST}},
    )


def ready():
    """An agent that has already got ready in this process."""
    found = threading.Event()
    found.set()
    return found


def restart(monkeypatch):
    """A new process: its first request gets ready, stopping what was left working."""
    monkeypatch.setattr(entrypoint, "ready", threading.Event())
    assert invoke({"operation": "health"}).json() == {"healthy": True}


@pytest.fixture(autouse=True)
def bridge(monkeypatch):
    """The backend reaches the agent's AgentCore app in process, as over HTTP."""
    agent = TestClient(entrypoint.app)

    def post(url, json, headers, timeout):
        return agent.post("/invocations", json=json, headers=headers)

    monkeypatch.setattr(agent_service.httpx, "post", post)
    # Already ready, so a first request never stops the round it was asked to work.
    monkeypatch.setattr(entrypoint, "ready", ready())
    monkeypatch.setattr(judge, "grade", lambda handler, agent: GRADED)
    bound.clear()


@pytest.fixture
def contexts(monkeypatch):
    """A dispatcher that decides each claim as the handler did, version 1 at $0.002 a
    claim and later ones at $0.001, noting the run config it was called inside."""
    seen = []

    def dispatch(run_id, n, llm=None):
        seen.append(var_child_runnable_config.get())
        version = client.get(f"/api/evaluations/{run_id}", headers=headers()).json()[
            "version"
        ]
        key = answer_key()
        for copy_id in client.get(f"/api/runs/{run_id}/claims").json():
            started = client.post(
                f"/api/claims/{copy_id}/start", json={"run_id": run_id}
            )
            if started.status_code != 200:
                continue
            expected = key[copy_id.split("-", 1)[1]]
            decision = {"decision": expected.decision, "payout": expected.payout}
            decision |= {"reason": "The handler's reason", "policy_section": "9"}
            record_decision(client, f"/api/claims/{copy_id}/decision", decision)
            cost = 0.002 if version == 1 else 0.001
            used = {"run_id": run_id, "input_tokens": 1, "output_tokens": 1}
            client.post(
                f"/api/claims/{copy_id}/model-calls", json=used | {"cost": cost}
            )
        client.post(f"/api/runs/{run_id}/finish")
        return []

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    return seen


def plays(monkeypatch, *replies):
    """The improver's replies over the whole round, across its pauses."""
    script = Script(*replies)
    model = ImproverStub(messages=script)
    monkeypatch.setattr(improver, "improver_model", lambda: model)
    return script


def act(path, body=None):
    response = client.post(f"/api/rounds{path}", json=body, headers=headers())
    settle()
    return response


def get_round(round_id):
    return client.get(f"/api/rounds/{round_id}").json()


def runs_of(round_id):
    return [
        (r["eval_set"], r["version"], r["status"]) for r in get_round(round_id)["runs"]
    ]


def messages(round_id):
    found = get_round(round_id)
    graph = improver.make_improver(round_id, "")
    return graph.get_state(improver.traced(round_id, found["thread_id"])).values[
        "messages"
    ]


def improver_costs(round_id):
    with store.connect() as conn:
        query = select(store.model_calls).where(
            store.model_calls.c.round_id == round_id
        )
        return [r.cost for r in conn.execute(query)]


def versions():
    return [
        v["version"]
        for v in client.get("/api/configuration/versions", headers=headers()).json()
    ]


def test_a_whole_round(monkeypatch, contexts):
    plays(
        monkeypatch,
        asks("read_results"),
        asks("propose_change", CHANGE),
        asks("run_evaluation"),
        asks("propose_recipe", RECIPE),
        AIMessage("The round is done."),
    )
    round_id = act("", None).json()["round_id"]
    found = get_round(round_id)
    first = [("evaluation", 1, "finished"), ("held_out", 1, "finished")]
    first.append(("held_out", 1, "finished"))
    assert runs_of(round_id) == first
    assert found["stage"] == "change_waiting"
    assert found["proposal"]["instructions"] == CHANGE["instructions"]
    # Nothing changes until a handler approves it.
    assert versions() == [1]

    assert act(f"/{round_id}/approve").status_code == 200
    found = get_round(round_id)
    then = [("evaluation", 2, "finished"), ("held_out", 2, "finished")]
    assert runs_of(round_id) == first + then + [("held_out", 2, "finished")]
    # Halving the cost per claim, with the same accuracy and grade, keeps it.
    assert (found["version"], found["kept"]) == (2, True)
    assert found["why"] == "cost_per_claim improved"
    assert found["stage"] == "recipe_waiting"
    assert found["proposal"]["recipe"] == RECIPE["recipe"]
    assert client.get("/api/configuration/current").json()["version"] == 2

    assert act(f"/{round_id}/approve").status_code == 200
    found = get_round(round_id)
    assert found["stage"] == "finished"
    assert [e["kind"] for e in found["events"]] == ["change", "recipe"]
    assert client.get("/api/recipes").json()[0]["version"] == 2
    # Each evaluation ran outside the improver's own run, in a fresh context.
    assert contexts == [None] * 6
    # The last reply, a plain message in the stub, has no cost.
    assert improver_costs(round_id) == [COST] * 4 + [0.0]
    assert all(b.get("parallel_tool_calls") is False for b in bound)
    assert len(bound) == 5


def test_a_rejections_reason_reaches_the_improver_which_proposes_again(
    monkeypatch, contexts
):
    again = {**CHANGE, "instructions": "Refer a claim with no receipt."}
    plays(
        monkeypatch,
        asks("read_results"),
        asks("propose_change", CHANGE),
        asks("propose_change", again),
    )
    round_id = act("").json()["round_id"]
    reason = "A missing receipt is a referral, not a decline"
    assert act(f"/{round_id}/reject", {"reason": reason}).status_code == 200
    found = get_round(round_id)
    assert found["stage"] == "change_waiting"
    assert found["proposal"]["instructions"] == again["instructions"]
    assert [(e["decision"], e["reason"]) for e in found["events"]] == [
        ("reject", reason)
    ]
    answers = [m.text for m in messages(round_id) if isinstance(m, ToolMessage)]
    assert reason in answers[-1]
    assert versions() == [1]


def test_a_proposal_outside_the_models_allowed_goes_back_to_the_improver(
    monkeypatch, contexts
):
    plays(
        monkeypatch,
        asks("propose_change", {**CHANGE, "model": "openai/gpt-7"}),
        AIMessage("I'll leave the configuration as it is."),
    )
    round_id = act("").json()["round_id"]
    found = get_round(round_id)
    assert (found["stage"], found["version"], found["events"]) == ("finished", None, [])
    answers = [m.text for m in messages(round_id) if isinstance(m, ToolMessage)]
    assert "openai/gpt-7 at low isn't allowed" in answers[0]


def test_a_round_with_no_proposal_finishes_with_no_version_tried(monkeypatch, contexts):
    plays(monkeypatch, asks("read_results"), AIMessage("Nothing to change."))
    round_id = act("").json()["round_id"]
    found = get_round(round_id)
    assert (found["stage"], found["version"], found["kept"]) == ("finished", None, None)
    assert versions() == [1]


def test_an_approval_while_a_dispatch_runs_is_refused_with_nothing_recorded(
    monkeypatch, contexts
):
    plays(monkeypatch, asks("propose_change", CHANGE))
    round_id = act("").json()["round_id"]
    entrypoint.take("A dispatch")
    try:
        response = client.post(f"/api/rounds/{round_id}/approve", headers=headers())
    finally:
        entrypoint.running.release()
    assert response.status_code == 409
    assert response.json()["detail"] == "A dispatch is already running"
    found = get_round(round_id)
    assert (found["stage"], found["events"], found["version"]) == (
        "change_waiting",
        [],
        None,
    )
    assert versions() == [1]


def test_a_second_round_needs_no_first_evaluations(monkeypatch, contexts):
    plays(monkeypatch, AIMessage("Nothing."), AIMessage("Nothing again."))
    first = act("").json()["round_id"]
    second = act("").json()["round_id"]
    assert len(runs_of(first)) == 3
    assert runs_of(second) == []


def test_run_evaluation_without_a_new_version_is_refused(monkeypatch, contexts):
    plays(monkeypatch, asks("run_evaluation"), AIMessage("Done."))
    round_id = act("").json()["round_id"]
    answers = [m.text for m in messages(round_id) if isinstance(m, ToolMessage)]
    assert answers == ["There's no new version to evaluate. Propose a change first."]


def test_an_evaluation_over_the_limit_rolls_the_version_back(monkeypatch, contexts):
    plays(
        monkeypatch,
        asks("propose_change", CHANGE),
        asks("run_evaluation"),
        AIMessage("Done."),
    )
    round_id = act("").json()["round_id"]
    limit = {**get_round(round_id)["settings"], "round_spending_limit": 0.0001}
    with store.engine().begin() as conn:
        query = store.rounds.update().where(store.rounds.c.round_id == round_id)
        conn.execute(query.values(settings=json.dumps(limit)))
    act(f"/{round_id}/approve")
    found = get_round(round_id)
    assert (found["kept"], found["stage"]) == (False, "finished")
    assert found["why"] == "the round couldn't afford to evaluate it"
    answers = [m.text for m in messages(round_id) if isinstance(m, ToolMessage)]
    assert answers[-1].startswith("Refused (409): Refused: the evaluation could cost")


def test_a_stopped_round_makes_no_more_model_calls_and_lets_go(monkeypatch, contexts):
    script = plays(monkeypatch, asks("read_results"), AIMessage("Never reached."))
    reading = improver_api.read_results

    def stopped_while_reading(round_id):
        if get_round(round_id)["stage"] == "improving":
            client.post(f"/api/rounds/{round_id}/stop", headers=headers())
        return reading(round_id)

    monkeypatch.setattr(improver_api, "read_results", stopped_while_reading)
    round_id = act("").json()["round_id"]
    assert get_round(round_id)["stage"] == "stopped"
    assert len(script.items) == 1
    assert not entrypoint.running.locked()


def test_a_failed_round_is_stopped_and_lets_go(monkeypatch, contexts):
    plays(monkeypatch, RuntimeError("the model is down"))
    round_id = act("").json()["round_id"]
    assert get_round(round_id)["stage"] == "stopped"
    assert not entrypoint.running.locked()


def test_the_improvers_model_calls_are_capped(monkeypatch, contexts):
    replies = [asks("read_results") for _ in range(improver.MAX_MODEL_CALLS + 1)]
    script = plays(monkeypatch, *replies)
    round_id = act("").json()["round_id"]
    assert get_round(round_id)["stage"] == "finished"
    assert len(script.items) == 1
    assert len(improver_costs(round_id)) == improver.MAX_MODEL_CALLS


def test_a_restart_stops_a_working_round_and_a_waiting_one_carries_on(
    monkeypatch, contexts
):
    plays(monkeypatch, asks("propose_change", CHANGE), AIMessage("Done."))
    waiting = act("").json()["round_id"]
    restart(monkeypatch)
    assert get_round(waiting)["stage"] == "change_waiting"
    act(f"/{waiting}/reject", {"reason": "No"})
    assert get_round(waiting)["stage"] == "finished"
    with store.engine().begin() as conn:
        conn.execute(
            store.rounds.update()
            .where(store.rounds.c.round_id == waiting)
            .values(stage="improving")
        )
    restart(monkeypatch)
    assert get_round(waiting)["stage"] == "stopped"


def test_the_improvers_work_is_one_langfuse_session_for_the_round(monkeypatch):
    assert improver.traced(3, "round-x") == {
        "configurable": {"thread_id": "round-x", "round_id": 3},
        "recursion_limit": main.RECURSION_LIMIT,
    }
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(improver, "CallbackHandler", lambda: "handler")
    config = improver.traced(3, "round-x")
    assert config.get("callbacks") == ["handler"]
    assert config.get("metadata") == {"langfuse_session_id": "Round 3"}


def test_a_rounds_claims_are_tagged_with_the_round_and_the_run(monkeypatch, contexts):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(main, "CallbackHandler", lambda: "handler")
    config = main.traced("CLM-1001-1", 7, 3)
    assert config.get("metadata", {}).get("langfuse_tags") == ["run-7", "round-3"]
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    plays(monkeypatch, AIMessage("Nothing."))
    round_id = act("").json()["round_id"]
    run_id = get_round(round_id)["runs"][0]["run_id"]
    found = client.get(f"/api/runs/{run_id}/configuration").json()
    assert found["round_id"] == round_id
    tagged = []
    real = main.traced
    monkeypatch.setattr(
        main, "traced", lambda *args: tagged.append(args) or real(*args)
    )
    copy_id = client.get(f"/api/evaluations/{run_id}", headers=headers()).json()[
        "claims"
    ][0]["claim_id"]
    main.run(
        copy_id, f"{copy_id}-1", StubModel(messages=Script(AIMessage("?"))), run_id
    )
    assert tagged == [(f"{copy_id}-1", run_id, round_id)]


def test_the_improvers_instructions_wrap_its_recipe():
    instructions = improver.INSTRUCTIONS.format(recipe="1. Read the results.")
    assert instructions.endswith("# Your recipe\n\n1. Read the results.\n")
    assert "never names a claim" in instructions
    assert [t.name for t in improver.TOOLS] == [
        "read_results",
        "propose_change",
        "run_evaluation",
        "propose_recipe",
    ]


def test_the_improver_is_luna_at_medium_unless_model_name_is_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    luna = improver.improver_model()
    assert (luna.model_name, luna.reasoning_effort) == ("openai/gpt-6-luna", "medium")
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    free = improver.improver_model()
    assert (free.model_name, free.reasoning_effort) == ("openrouter/free", None)


def test_an_answer_lets_go_of_the_lock_when_the_backend_cant_be_reached(monkeypatch):
    def unreachable(round_id, body):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(improver_api, "record_decision", unreachable)
    answer = {"decision": "approve", "reason": "", "handler_id": "HND-01"}
    response = invoke({"operation": "answer_proposal", "round_id": 1, **answer})
    assert response.status_code == 500
    assert not entrypoint.running.locked()
