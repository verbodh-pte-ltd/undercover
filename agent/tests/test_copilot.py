import ast
import itertools
import json
import logging
from pathlib import Path

import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver

from agent import copilot, copilot_api, main
from agent.direct import Direct
from agent.tests.copilot_standin import DANA, MARCUS, StandIn
from agent.tests.test_agent import Script
from agent.tests.test_memory import FakeStore, item

CONVERSATION = "6f1c2a4e-0b7d-4c55-9a3e-2f8b1d6c7e90"
AGENT_KEY = "the-agents-key-of-at-least-32-characters"
COPILOT_MODULES = ["copilot.py", "copilot_api.py", "copilot_entrypoint.py"]


class Model(GenericFakeChatModel):
    """A stub model with scripted replies, which keeps what it was sent."""

    sent: list

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.sent.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


def stub(*items):
    return Model(messages=Script(*items), sent=[])


def calls(*tool_calls, first=0):
    """An AI message that calls the given tools, as (name, args) pairs."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{first + i}"}
            for i, (name, args) in enumerate(tool_calls)
        ],
    )


def forbidden():
    raise AssertionError("The Copilot used the claim agent's client")


@pytest.fixture
def saved():
    return InMemorySaver()


@pytest.fixture(autouse=True)
def the_app(monkeypatch, saved):
    """The contract's routes in process, a saver in memory, and the agent's key set,
    which nothing may send."""
    standin = StandIn()
    monkeypatch.setattr(
        copilot_api,
        "client",
        lambda token: TestClient(standin.app, headers=copilot_api.headers(token)),
    )
    monkeypatch.setattr(copilot, "saver", lambda: saved)
    monkeypatch.setattr(copilot, "memory_store", lambda: None)
    monkeypatch.setattr(main, "backend", forbidden)
    monkeypatch.setenv("UNDERCOVER_API_KEY", AGENT_KEY)
    monkeypatch.delenv("UNDERCOVER_GATEWAY_URL", raising=False)
    return standin


def turn(the_app, turn_id, message, llm, token=DANA):
    the_app.turn(turn_id)
    copilot.start_turn(CONVERSATION, turn_id, message, token, llm)
    return the_app.turns[turn_id]


def answer(the_app, turn_id, reply, llm, token=DANA):
    the_app.answered(turn_id)
    copilot.answer_confirmation(CONVERSATION, turn_id, reply, token, llm)
    return the_app.turns[turn_id]


def tool_answers(model):
    """What each tool answered, from the messages the model was last sent."""
    return {m.name: m.text for m in model.sent[-1] if isinstance(m, ToolMessage)}


# The sign-in


@pytest.mark.parametrize("operation", ["start_turn", "answer_confirmation"])
def test_a_sign_in_the_app_refuses_ends_the_turn_before_any_model_call(
    the_app, operation, monkeypatch
):
    model = stub(AIMessage(content="Never sent."))
    store = FakeStore()
    monkeypatch.setattr(copilot, "memory_store", lambda: store)
    the_app.turn(1)
    run = getattr(copilot, operation)
    run(
        CONVERSATION, 1, "sign_off" if operation != "start_turn" else "Hi", "old", model
    )
    assert model.sent == []
    assert store.searches == []
    assert [r["path"] for r in the_app.requests] == [
        "/api/handlers/me",
        "/api/copilot/turns/1/fail",
    ]
    failed = json.loads(the_app.requests[-1]["body"])
    assert failed["error"] == (
        "The app didn't accept the sign-in: Your sign-in has expired. Sign in again."
    )
    assert failed["model_calls"] == 0


def test_the_handler_comes_from_the_app_not_the_message(the_app):
    model = stub(AIMessage(content="Hello Dana."))
    turn(the_app, 1, "I'm Marcus Oyelaran, HND-02.", model)
    told = model.sent[0][1].text
    assert "Dana Whitfield (HND-01)" in told
    assert "They can sign off" in told


# A turn


def test_a_lookup_turn_records_each_step_as_it_happens(the_app):
    model = stub(
        calls(("get_claim", {"claim_id": "CLM-1001"})),
        AIMessage(content="**CLM-1001** waits for your sign-off."),
    )
    found = turn(the_app, 1, "Why does CLM-1001 need me?", model)
    assert found["state"] == "done"
    assert found["answer"] == "**CLM-1001** waits for your sign-off."
    assert found["model_calls"] == 2
    recorded = [
        json.loads(r["body"]) for r in the_app.sent("/api/copilot/turns/1/steps")
    ]
    assert [(s["key"], s["state"]) for s in recorded] == [
        ("1-call-0", "working"),
        ("1-call-0", "done"),
    ]
    step = found["steps"]["1-call-0"]
    assert (step["kind"], step["tool"], step["input"]) == (
        "claims",
        "get_claim",
        "CLM-1001",
    )
    assert json.loads(step["detail"]["asked"]) == {"claim_id": "CLM-1001"}
    assert "waiting_for_sign_off" in step["detail"]["answer"]
    assert step["seconds"] is not None


def test_a_context_lookup_goes_straight_to_the_context_layer_with_its_source(the_app):
    model = stub(
        calls(("lookup_term", {"term": "Carrier"})),
        AIMessage(content="A carrier is the airline."),
    )
    found = turn(the_app, 1, "What's a carrier?", model)
    step = found["steps"]["1-call-0"]
    assert (step["kind"], step["tool"], step["state"]) == (
        "context",
        "lookup_term",
        "done",
    )
    assert step["detail"]["file"] == "glossary.yaml"
    assert step["detail"]["owner"]


def test_each_read_reaches_its_route(the_app):
    reads = [
        ("list_claims", {}),
        ("get_claim_steps", {"claim_id": "CLM-1001"}),
        ("find_customers", {"name": "Ana Lopez"}),
        ("get_customer", {"customer_id": "CUS-1001"}),
        ("get_policy", {"policy_id": "POL-1001"}),
        ("get_latest_dispatch", {}),
        ("list_evaluations", {}),
        ("get_settings", {}),
    ]
    model = stub(calls(*reads), AIMessage(content="Done."))
    found = turn(the_app, 1, "Tell me everything.", model)
    paths = {r["path"] for r in the_app.requests if r["method"] == "GET"}
    assert {
        "/api/claims",
        "/api/claims/CLM-1001/steps",
        "/api/customers",
        "/api/customers/CUS-1001",
        "/api/policies/POL-1001",
        "/api/runs/current",
        "/api/evaluations",
        "/api/settings",
    } <= paths
    kinds = {s["tool"]: s["kind"] for s in found["steps"].values()}
    assert kinds["find_customers"] == "customers"
    assert kinds["get_latest_dispatch"] == "work"


def test_a_refusal_from_the_app_goes_back_to_the_model(the_app):
    the_app.refuse_start = "A dispatch is already running"
    model = stub(
        calls(("start_dispatch", {"agents": 10})),
        AIMessage(content="A dispatch is already running."),
    )
    found = turn(the_app, 1, "Work the queue with 10 agents.", model)
    assert tool_answers(model)["start_dispatch"] == (
        "Refused (409): A dispatch is already running"
    )
    step = found["steps"]["1-call-0"]
    assert (step["kind"], step["state"]) == ("action", "refused")
    assert found["state"] == "done"


def test_a_dispatch_uses_the_settings_number_unless_told(the_app):
    model = stub(
        calls(("start_dispatch", {"agents": None})),
        calls(("start_dispatch", {"agents": 10}), first=1),
        AIMessage(content="Started."),
    )
    turn(the_app, 1, "Work the queue.", model)
    assert the_app.started == [("dispatch", {"n": 4}), ("dispatch", {"n": 10})]
    assert json.loads(tool_answers(model)["start_dispatch"])["run_id"] == 7


def test_an_evaluation_can_use_the_tiny_set(the_app):
    args = {"eval_set": "evaluation", "tiny": True, "agents": None}
    model = stub(calls(("start_evaluation", args)), AIMessage(content="Started."))
    turn(the_app, 1, "Evaluate the tiny set.", model)
    assert the_app.started == [
        ("evaluation", {"n": 4, "eval_set": "evaluation", "tiny": True})
    ]


def test_a_send_back_carries_the_note(the_app):
    args = {"claim_id": "CLM-1001", "note": "Check the receipts again."}
    model = stub(calls(("send_back", args)), AIMessage(content="Sent back."))
    found = turn(the_app, 1, "Send it back.", model)
    assert the_app.sent_back == [("CLM-1001", "Check the receipts again.")]
    assert found["steps"]["1-call-0"]["kind"] == "action"


def test_a_turn_stops_at_25_model_calls(the_app):
    looping = (calls(("list_claims", {})) for _ in itertools.count())
    model = Model(messages=looping, sent=[])
    found = turn(the_app, 1, "Loop.", model)
    assert found["state"] == "failed"
    assert found["error"] == "RuntimeError: the Copilot reached 25 model calls"
    assert found["model_calls"] == 25
    assert len(model.sent) == 25


def test_a_failed_turn_ends_with_why_and_the_next_message_goes_on(the_app):
    model = stub(calls(("list_claims", {})), RuntimeError("the model is down"))
    found = turn(the_app, 1, "What's in the queue?", model)
    assert (found["state"], found["error"]) == (
        "failed",
        "RuntimeError: the model is down",
    )
    assert found["model_calls"] == 1
    model = stub(AIMessage(content="Two claims."))
    found = turn(the_app, 2, "What's in the queue?", model)
    assert (found["state"], found["answer"]) == ("done", "Two claims.")


def test_a_turn_stopped_partway_through_a_step_can_be_carried_on(the_app, monkeypatch):
    def broken(token, turn_id, step):
        raise ConnectionError("the app went away")

    real = copilot_api.record_step
    monkeypatch.setattr(copilot_api, "record_step", broken)
    model = stub(calls(("list_claims", {})))
    found = turn(the_app, 1, "What's in the queue?", model)
    assert found["state"] == "failed"
    monkeypatch.setattr(copilot_api, "record_step", real)
    model = stub(AIMessage(content="Two claims."))
    found = turn(the_app, 2, "What's in the queue?", model)
    assert found["state"] == "done"
    assert tool_answers(model)["list_claims"].startswith("Not done")


# The sign-off


def sign_off_turn(the_app, token=DANA):
    model = stub(
        calls(("get_claim", {"claim_id": "CLM-1001"})),
        calls(("sign_off", {"claim_id": "CLM-1001"}), first=1),
    )
    return turn(the_app, 1, "Sign off CLM-1001.", model, token), model


def test_a_sign_off_pauses_with_the_card_from_the_records(the_app):
    found, _ = sign_off_turn(the_app)
    assert found["state"] == "waiting_for_confirmation"
    assert found["card"] == {
        "claim_id": "CLM-1001",
        "customer_name": "Ana Lopez",
        "amount": 2500.0,
        "reason": "The flight was canceled for a crew problem. The costs are covered.",
    }
    assert the_app.paid == []


def test_the_pause_answered_by_a_fresh_copilot_pays_once_and_repeats_nothing(
    the_app,
):
    sign_off_turn(the_app)
    asked_before = len(the_app.sent("/api/claims/CLM-1001"))
    # A fresh graph and context: only the saved state carries the pause.
    resumed = stub(AIMessage(content="Signed off and paid **CLM-1001**: $2,500.00."))
    found = answer(the_app, 1, "sign_off", resumed)
    assert the_app.paid == ["CLM-1001"]
    assert found["state"] == "done"
    assert found["answer"] == "Signed off and paid **CLM-1001**: $2,500.00."
    assert len(resumed.sent) == 1
    assert found["model_calls"] == 3
    keys = [
        json.loads(r["body"])["key"] for r in the_app.sent("/api/copilot/turns/1/steps")
    ]
    assert keys.count("1-call-0") == 2  # get_claim, working then done, only once
    assert found["steps"]["2-call-1"]["state"] == "done"
    # Confirm only reads again before its pause.
    assert len(the_app.sent("/api/claims/CLM-1001")) == asked_before + 1


def test_cancel_leaves_the_claim_unpaid_and_asks_whats_next(the_app):
    sign_off_turn(the_app)
    resumed = stub(AIMessage(content="I haven't signed it off. What would you like?"))
    found = answer(the_app, 1, "cancel", resumed)
    assert the_app.paid == []
    assert the_app.claims["CLM-1001"]["status"] == "waiting_for_sign_off"
    assert "pressed Cancel" in tool_answers(resumed)["sign_off"]
    assert found["steps"]["2-call-1"]["state"] == "refused"
    assert found["state"] == "done"


def test_marcus_asking_to_sign_off_gets_the_reason_with_no_card(the_app):
    model = stub(
        calls(("sign_off", {"claim_id": "CLM-1001"})),
        AIMessage(content="You can't sign off payouts over the approval limit."),
    )
    found = turn(the_app, 1, "Sign off CLM-1001.", model, MARCUS)
    assert found["state"] == "done"
    assert found["card"] is None
    assert the_app.sent("/api/copilot/turns/1/pause") == []
    assert the_app.sent("/api/claims/CLM-1001/sign-off") == []
    why = "Marcus Oyelaran can't sign off payouts over the approval limit."
    assert tool_answers(model)["sign_off"] == why
    assert found["steps"]["1-call-0"]["state"] == "refused"


@pytest.mark.parametrize(
    ("claim_id", "why"),
    [
        ("CLM-1002", "CLM-1002 isn't waiting for sign-off: it's decided."),
        ("CLM-9999", "Refused (404): Claim CLM-9999 not found"),
    ],
)
def test_a_claim_that_isnt_waiting_is_refused_before_the_card(the_app, claim_id, why):
    model = stub(
        calls(("sign_off", {"claim_id": claim_id})), AIMessage(content="It isn't.")
    )
    found = turn(the_app, 1, "Sign it off.", model)
    assert tool_answers(model)["sign_off"] == why
    assert found["card"] is None


def test_an_evaluations_copy_is_never_signed_off(the_app):
    the_app.claims["CLM-1001"]["run_id"] = 8
    model = stub(
        calls(("sign_off", {"claim_id": "CLM-1001"})), AIMessage(content="It isn't.")
    )
    turn(the_app, 1, "Sign it off.", model)
    assert "evaluation's copy" in tool_answers(model)["sign_off"]


def test_a_sign_off_beside_another_tool_is_refused(the_app):
    model = stub(
        calls(("sign_off", {"claim_id": "CLM-1001"}), ("list_claims", {})),
        AIMessage(content="One at a time."),
    )
    found = turn(the_app, 1, "Sign it off.", model)
    answers = tool_answers(model)
    assert answers["sign_off"].startswith("Ask for a sign-off on its own")
    assert answers["list_claims"] == answers["sign_off"]
    assert found["card"] is None


def test_an_answer_with_nothing_waiting_fails_the_turn(the_app):
    the_app.turn(1)
    copilot.answer_confirmation(CONVERSATION, 1, "sign_off", DANA, stub())
    assert the_app.turns[1]["error"] == (
        "RuntimeError: Nothing in this conversation is waiting for an answer"
    )
    assert the_app.paid == []


# The sign-in is never sent as the agent, nor saved


def test_the_agents_key_is_never_sent(the_app):
    sign_off_turn(the_app)
    answer(the_app, 1, "sign_off", stub(AIMessage(content="Paid.")))
    assert the_app.requests
    for request in the_app.requests:
        headers = request["headers"]
        assert "x-api-key" not in headers
        assert headers["authorization"] == f"Bearer {DANA}"
        assert AGENT_KEY not in json.dumps(headers)


def test_no_copilot_module_uses_the_claim_agents_client_or_key():
    banned = {"backend", "key_header", "call"}
    for name in COPILOT_MODULES:
        source = (Path(main.__file__).parent / name).read_text(encoding="utf-8")
        assert "UNDERCOVER_API_KEY" not in source, name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module == "agent.main":
                assert not banned & {a.name for a in node.names}, name
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "main" and node.attr in banned), name


def test_the_sign_in_is_never_saved(the_app, saved, monkeypatch, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    store = FakeStore()
    monkeypatch.setattr(copilot, "memory_store", lambda: store)
    # A turn, a pause, and a failure after the answer.
    turn(the_app, 1, "Hello", stub(AIMessage(content="Hello Dana.")))
    the_app.turns[2] = {"state": "working", "steps": {}, "card": None}
    model = stub(
        calls(("sign_off", {"claim_id": "CLM-1001"})),
        RuntimeError("the model is down"),
    )
    copilot.start_turn(CONVERSATION, 2, "Sign off CLM-1001.", DANA, model)
    answer(the_app, 2, "sign_off", model)
    records = repr(dict(saved.storage)) + repr(saved.writes) + repr(dict(saved.blobs))
    assert "Sign off CLM-1001." in records  # the search finds what is there
    out = capsys.readouterr()
    everywhere = [
        records,
        json.dumps(the_app.turns),
        repr(store.puts),
        repr(store.searches),
        caplog.text,
        out.out,
        out.err,
    ]
    for found in everywhere:
        assert DANA not in found
    assert the_app.turns[2]["error"] == "RuntimeError: the model is down"


def test_a_failed_turns_reason_never_carries_the_sign_in():
    assert copilot.scrub(f"refused {DANA}", DANA) == "refused [the sign-in]"
    assert copilot.scrub("x" * 600, DANA) == "x" * 500


# Waiting for the agent's work


class Clock:
    """A stand-in clock that moves on only when the Copilot sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    found = Clock()
    monkeypatch.setattr(copilot, "clock", found.clock)
    monkeypatch.setattr(copilot, "sleep", found.sleep)
    return found


def test_waiting_for_a_dispatch_makes_no_model_call_until_it_ends(the_app, clock):
    the_app.run_statuses = ["running", "running", "finished"]
    model = stub(
        calls(("wait_for_run", {"run_id": 7})), AIMessage(content="CLM-1001 needs you.")
    )
    found = turn(the_app, 1, "Tell me what needs me.", model)
    assert clock.sleeps == [5, 5]
    assert len(model.sent) == 2
    assert json.loads(tool_answers(model)["wait_for_run"])["status"] == "finished"
    assert found["steps"]["1-call-0"]["kind"] == "work"


def test_waiting_gives_up_at_15_minutes(the_app, clock):
    the_app.run_statuses = ["running"]
    model = stub(
        calls(("wait_for_run", {"run_id": 7})), AIMessage(content="Still going.")
    )
    turn(the_app, 1, "Tell me what needs me.", model)
    assert sum(clock.sleeps) == 15 * 60
    assert len(the_app.sent("/api/runs/7")) == 181
    assert len(model.sent) == 2
    waited = json.loads(tool_answers(model)["wait_for_run"])
    assert waited["still_going"] == (
        "Dispatch 7 is still going after 15 minutes. Follow it on the Queue page."
    )


def test_waiting_for_a_run_the_app_refuses_passes_the_refusal_on(the_app, clock):
    model = stub(calls(("wait_for_run", {"run_id": 99})), AIMessage(content="No."))
    found = turn(the_app, 1, "Wait.", model)
    assert tool_answers(model)["wait_for_run"] == "Refused (404): Run 99 not found"
    assert found["steps"]["1-call-0"]["state"] == "refused"


# The success criteria


def criteria(the_app):
    model = stub(calls(("check_success_criteria", {})), AIMessage(content="Table."))
    found = turn(the_app, 1, "Are we meeting the success criteria we agreed?", model)
    return json.loads(tool_answers(model)["check_success_criteria"]), found


def test_the_success_criteria_come_from_the_measures_settings_and_counts(
    the_app, clock
):
    the_app.run_statuses = ["running", "finished"]
    checked, found = criteria(the_app)
    assert the_app.started == [
        ("evaluation", {"n": 4, "eval_set": "held_out", "tiny": False})
    ]
    rows = checked["criteria"]
    assert [(r["criterion"], r["figure"], r["met"]) for r in rows] == [
        ("Straight through", "75.0%", True),
        ("Accuracy", "85.0%", True),
        ("Over the limit", "2 of 2 paid over the limit signed off", True),
    ]
    assert rows[1]["target"] == "at least 80.0%, the accuracy objective in Settings"
    assert checked["all_met"] is True
    assert "handler" not in json.dumps(checked)
    assert found["steps"]["1-call-0"]["kind"] == "action"


def test_a_criterion_missed_is_not_met(the_app, clock):
    the_app.measures = {"straight_through": 0.65, "accuracy": 0.85}
    the_app.counts = {"approval_limit": 1000.0, "paid_over_limit": 3, "signed_off": 2}
    checked, _ = criteria(the_app)
    assert [r["met"] for r in checked["criteria"]] == [False, True, False]
    assert checked["all_met"] is False


def test_a_failed_evaluation_gives_no_figures_but_the_third(the_app, clock):
    the_app.run_statuses = ["stopped"]
    the_app.stopped_reason = "The agent's work failed: RuntimeError: down"
    checked, _ = criteria(the_app)
    first, second, third = checked["criteria"]
    for row in (first, second):
        assert (row["figure"], row["met"]) == (None, None)
        assert row["why"] == (
            "Evaluation 8 stopped partway: The agent's work failed: RuntimeError: down"
        )
    assert third["met"] is True
    assert checked["all_met"] is None


def test_an_evaluation_still_going_or_refused_gives_no_figures(the_app, clock):
    the_app.run_statuses = ["running"]
    checked, _ = criteria(the_app)
    assert checked["criteria"][0]["why"].startswith("Evaluation 8 is still going")
    the_app.refuse_start = "An evaluation is already running"
    checked = copilot.criteria(DANA)
    assert checked["criteria"][0]["why"] == (
        "The app didn't start the evaluation: An evaluation is already running"
    )
    assert checked["criteria"][2]["met"] is True


def test_with_nothing_paid_over_the_limit_the_third_is_met_and_says_why(the_app, clock):
    the_app.counts = {"approval_limit": 1000.0, "paid_over_limit": 0, "signed_off": 0}
    checked, _ = criteria(the_app)
    third = checked["criteria"][2]
    assert (third["figure"], third["met"]) == ("none paid over the limit yet", True)
    assert "Nothing in the queue has been paid over the $1,000.00" in third["why"]


# Memory


def test_memory_uses_the_signed_in_handler_and_the_conversation(the_app, monkeypatch):
    note = "Dana likes the amounts first, in short bullet points."
    store = FakeStore(found=[item(("handlers", "HND-01", "preferences"), note)])
    monkeypatch.setattr(copilot, "memory_store", lambda: store)
    model = stub(calls(("list_claims", {})), AIMessage(content="- $2,500.00: CLM-1001"))
    found = turn(the_app, 1, "What needs me?", model)
    assert store.searches == [(("handlers", "HND-01"), "What needs me?", 5)]
    step = found["steps"]["remember"]
    assert (step["kind"], step["tool"], step["state"]) == (
        "memory",
        "search_memory",
        "done",
    )
    assert note in step["detail"]["answer"]
    assert note in model.sent[0][1].text
    assert [(p[0], p[1]) for p in store.puts] == [
        (("HND-01", CONVERSATION), "turn-1-message"),
        (("HND-01", CONVERSATION), "turn-1-answer"),
    ]
    saved = [p[2]["message"] for p in store.puts]
    assert isinstance(saved[0], HumanMessage)
    assert [m.text for m in saved] == ["What needs me?", "- $2,500.00: CLM-1001"]


def test_memory_that_cant_be_reached_doesnt_stop_the_turn(the_app, monkeypatch):
    store = FakeStore(fail=EndpointConnectionError(endpoint_url="https://memory"))
    monkeypatch.setattr(copilot, "memory_store", lambda: store)
    found = turn(the_app, 1, "Hi", stub(AIMessage(content="Hello.")))
    assert found["state"] == "done"
    assert found["steps"]["remember"]["state"] == "failed"


def test_without_the_setting_memory_does_nothing(the_app):
    found = turn(the_app, 1, "Hi", stub(AIMessage(content="Hello.")))
    assert "remember" not in found["steps"]
    assert found["state"] == "done"


def test_the_memory_is_the_copilots_own_in_its_region(monkeypatch):
    made = []
    monkeypatch.undo()
    monkeypatch.setattr(
        copilot, "AgentCoreMemoryStore", lambda **kwargs: made.append(kwargs) or "store"
    )
    monkeypatch.delenv("UNDERCOVER_COPILOT_MEMORY_ID", raising=False)
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "undercover_memory-abc")
    assert copilot.memory_store() is None
    monkeypatch.setenv("UNDERCOVER_COPILOT_MEMORY_ID", "undercover_copilot_memory-abc")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    assert copilot.memory_store() == "store"
    assert made == [
        {"memory_id": "undercover_copilot_memory-abc", "region_name": "us-east-1"}
    ]


# What the graph is built from


def test_the_model_is_the_one_the_app_names(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    chosen = copilot.model(
        {"model": "openai/gpt-6-luna", "reasoning_effort": "low", "instructions": ""}
    )
    assert (chosen.model_name, chosen.reasoning_effort) == ("openai/gpt-6-luna", "low")
    chosen = copilot.model(
        {"model": "openrouter/free", "reasoning_effort": "default", "instructions": ""}
    )
    assert chosen.reasoning_effort is None


def test_the_route_is_the_gateway_when_set_and_never_the_app(monkeypatch):
    monkeypatch.setattr(copilot, "Gateway", lambda url: ("gateway", url))
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", "https://gateway.example/mcp")
    assert copilot.route() == ("gateway", "https://gateway.example/mcp")
    monkeypatch.delenv("UNDERCOVER_GATEWAY_URL")
    direct = copilot.route()
    assert isinstance(direct, Direct)
    with pytest.raises(RuntimeError, match="only with the handler's sign-in"):
        direct.use("get_claim", {"claim_id": "CLM-1001"})


def test_conversations_are_saved_in_memory_without_a_database(monkeypatch):
    monkeypatch.undo()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert copilot.saver() is copilot.saver()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(copilot, "pool", lambda: "pool")
    found = copilot.saver()
    assert isinstance(found, PostgresSaver)
    assert found.conn == "pool"


def test_a_long_detail_is_trimmed():
    assert copilot_api.trim("x" * 5000) == "x" * 1999 + "…"
    assert copilot_api.trim(None) is None
