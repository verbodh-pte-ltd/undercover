import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent import __main__ as runner
from agent import main
from backend.main import app

client = TestClient(app)


class Script:
    """Scripted model replies; an exception in the script is raised as a model error."""

    def __init__(self, *items):
        self.items = list(items)

    def __iter__(self):
        return self

    def __next__(self):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies in place of a real model."""

    def bind_tools(self, tools, **kwargs):
        return self


def stub(*items):
    return StubModel(messages=Script(*items))


@pytest.fixture(autouse=True)
def in_process_backend(monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))
    monkeypatch.setattr(runner, "backend", lambda: TestClient(app))
    monkeypatch.setattr(main, "RETRY_DELAY", 0)


def calls(*tool_calls):
    """An AI message that calls the given tools, as (name, args) pairs."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": str(i)}
            for i, (name, args) in enumerate(tool_calls)
        ],
    )


def decision(claim_id, payout, verdict="approve"):
    return {
        "claim_id": claim_id,
        "decision": verdict,
        "payout": payout,
        "reason": "The flight was canceled for a crew problem. The costs are covered.",
        "policy_section": "3. Trip Delay",
    }


def decide(claim_id, verdict="decline"):
    """The claim agent's replies for a quick decision on a claim."""
    return [
        calls(("record_decision", decision(claim_id, 0, verdict))),
        AIMessage(content=f"{claim_id}: {verdict}, $0.00."),
    ]


def todos(*items):
    return [{"claim_id": c, "text": f"Decide {c}", "state": s} for c, s in items]


def new_run(cap):
    return client.post("/api/runs", json={"cap": cap}).json()["run_id"]


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def test_get_claim():
    assert json.loads(main.get_claim.invoke({"claim_id": "CLM-1001"}))["policy_id"] == (
        "POL-2001"
    )


def test_get_policy():
    policy = json.loads(main.get_policy.invoke({"policy_id": "POL-2001"}))
    assert policy["plan"] == "premium"


def test_get_claim_history():
    history = json.loads(main.get_claim_history.invoke({"customer_id": "CUS-3001"}))
    assert history[0]["claim_id"] == "CLM-0412"


def test_sections_tools():
    sections = json.loads(main.list_sections.invoke({"document": "claims_manual"}))
    assert sections[9]["title"] == "Meals without receipts"
    args = {"document": "policy_wording", "number": "3"}
    assert json.loads(main.read_section.invoke(args))["title"] == "Trip Delay"


def test_refusal_comes_back_as_text():
    reply = main.pay_claim.invoke({"claim_id": "CLM-1001", "amount": 420.0})
    assert reply.startswith("Refused (409)")


def test_claim_agent_reads_sections_approves_and_pays():
    replies = [
        calls(
            ("get_claim", {"claim_id": "CLM-1001"}),
            ("get_policy", {"policy_id": "POL-2001"}),
            ("get_claim_history", {"customer_id": "CUS-3001"}),
        ),
        calls(("list_sections", {"document": "claims_manual"})),
        calls(
            ("read_section", {"document": "claims_manual", "number": "6"}),
            ("read_section", {"document": "policy_wording", "number": "3"}),
        ),
        calls(("record_decision", decision("CLM-1001", 420.0))),
        calls(("pay_claim", {"claim_id": "CLM-1001", "amount": 420.0})),
        AIMessage(content="Approved, $420.00."),
    ]
    reply = main.work_claim("CLM-1001", new_run(1), stub(*replies))
    assert reply == "Approved, $420.00."
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("decided", 420.0)
    assert result["sections_read"] == ["claims_manual 6", "policy_wording 3"]
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert steps[0]["text"] == 'get_claim({"claim_id": "CLM-1001"})'
    assert steps[-1]["text"] == "Approved, $420.00."


def test_claim_agent_cannot_pay_over_the_limit():
    replies = [
        calls(("record_decision", decision("CLM-1001", 1250.0))),
        calls(("pay_claim", {"claim_id": "CLM-1001", "amount": 1250.0})),
        AIMessage(content="Approved, $1,250.00, waiting for sign-off."),
    ]
    main.work_claim("CLM-1001", new_run(1), stub(*replies))
    result = claim("CLM-1001")
    assert result["status"] == "waiting_for_sign_off"
    assert result["paid_amount"] is None
    results = [s["text"] for s in client.get("/api/claims/CLM-1001/steps").json()]
    assert any(r.startswith("pay_claim: Refused (403)") for r in results)


def test_model_error_releases_the_claim(monkeypatch):
    monkeypatch.setattr(main, "MODEL_RETRIES", 0)
    reply = main.work_claim("CLM-1001", new_run(1), stub(TimeoutError("no reply")))
    assert reply.startswith("failed: TimeoutError: no reply")
    result = claim("CLM-1001")
    assert (result["status"], result["attempts"]) == ("waiting", 1)
    last = client.get("/api/claims/CLM-1001/steps").json()[-1]
    assert (last["kind"], last["text"]) == ("error", "TimeoutError: no reply")


def test_model_error_is_retried():
    replies = [TimeoutError("no reply"), *decide("CLM-1001")]
    reply = main.work_claim("CLM-1001", new_run(1), stub(*replies))
    assert reply == "CLM-1001: decline, $0.00."
    assert claim("CLM-1001")["attempts"] == 0


def test_claim_agent_that_stops_without_a_decision_is_released():
    reply = main.work_claim("CLM-1001", new_run(1), stub(AIMessage(content="Hmm.")))
    assert reply.startswith("failed: the claim agent stopped without a decision")


def test_work_claim_on_an_unknown_claim():
    assert main.work_claim("CLM-0000", new_run(1), stub()).startswith("not started:")


def test_refused_start_leaves_another_runs_claim_alone():
    other, run_id = new_run(1), new_run(1)
    client.post("/api/claims/CLM-1001/start", json={"run_id": other})
    client.post("/api/claims/CLM-1002/start", json={"run_id": run_id})
    reply = main.work_claim("CLM-1001", run_id, stub())
    assert reply.startswith("not started:")
    assert "cap of 1" in reply
    result = claim("CLM-1001")
    assert (result["status"], result["attempts"]) == ("in_progress", 0)


def test_queue_agent_works_the_queue():
    queue = stub(
        calls(("list_waiting_claims", {})),
        calls(
            (
                "set_todos",
                {"todos": todos(("CLM-1001", "to_do"), ("CLM-1002", "to_do"))},
            )
        ),
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("work_claim", {"claim_id": "CLM-1002"})),
        calls(
            ("set_todos", {"todos": todos(("CLM-1001", "done"), ("CLM-1002", "done"))})
        ),
        calls(("finish_run", {"summary": "Two claims declined."})),
        AIMessage(content="Two claims declined."),
    )
    reply = main.run_queue(2, queue, stub(*decide("CLM-1001"), *decide("CLM-1002")))
    assert reply == "Two claims declined."
    run = client.get("/api/runs/current").json()
    assert run["summary"] == "Two claims declined."
    assert run["todos"] == todos(("CLM-1001", "done"), ("CLM-1002", "done"))
    assert [claim(c)["status"] for c in ["CLM-1001", "CLM-1002", "CLM-1003"]] == [
        "decided",
        "decided",
        "waiting",
    ]


def test_cap_stops_a_third_claim():
    queue = stub(
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("work_claim", {"claim_id": "CLM-1002"})),
        calls(("work_claim", {"claim_id": "CLM-1003"})),
        AIMessage(content="Done."),
    )
    main.run_queue(2, queue, stub(*decide("CLM-1001"), *decide("CLM-1002")))
    assert claim("CLM-1003")["status"] == "waiting"
    assert len(client.get("/api/claims/waiting").json()) == 23


def test_failed_claim_does_not_stop_the_run_and_is_tried_again(monkeypatch):
    monkeypatch.setattr(main, "MODEL_RETRIES", 0)
    queue = stub(
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("work_claim", {"claim_id": "CLM-1002"})),
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("finish_run", {"summary": "Both declined."})),
        AIMessage(content="Both declined."),
    )
    claims = stub(TimeoutError("no reply"), *decide("CLM-1002"), *decide("CLM-1001"))
    main.run_queue(2, queue, claims)
    first, second = claim("CLM-1001"), claim("CLM-1002")
    assert (first["status"], first["attempts"]) == ("decided", 1)
    assert second["status"] == "decided"
    assert client.get("/api/runs/current").json()["summary"] == "Both declined."


def test_claim_that_fails_twice_is_left_for_a_handler(monkeypatch):
    monkeypatch.setattr(main, "MODEL_RETRIES", 0)
    queue = stub(
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("set_todos", {"todos": todos(("CLM-1001", "left_for_handler"))})),
        calls(("finish_run", {"summary": "CLM-1001 is left for a handler."})),
        AIMessage(content="Done."),
    )
    main.run_queue(1, queue, stub(TimeoutError("one"), TimeoutError("two")))
    result = claim("CLM-1001")
    assert (result["status"], result["attempts"]) == ("needs_handler", 2)
    run = client.get("/api/runs/current").json()
    assert run["todos"][0]["state"] == "left_for_handler"
    assert run["summary"] == "CLM-1001 is left for a handler."


def test_finishing_early_is_refused_and_the_missed_claim_is_worked():
    queue = stub(
        calls(("finish_run", {"summary": "Nothing to do."})),
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("finish_run", {"summary": "One claim declined."})),
        AIMessage(content="One claim declined."),
    )
    main.run_queue(1, queue, stub(*decide("CLM-1001")))
    assert claim("CLM-1001")["status"] == "decided"
    assert client.get("/api/runs/current").json()["summary"] == "One claim declined."


def test_runner_with_a_cap(monkeypatch):
    caps = []
    monkeypatch.setattr(runner, "run_queue", caps.append)
    runner.main(["--cap", "5"])
    runner.main([])
    assert caps == [5, 25]


def test_runner_with_nothing_waiting(monkeypatch, capsys):
    monkeypatch.setattr(runner, "backend", lambda: FakeClient([]))
    runner.main([])
    assert "No claims are waiting." in capsys.readouterr().out


class FakeClient:
    def __init__(self, waiting):
        self.waiting = waiting

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, path):
        return type("Response", (), {"json": lambda _: self.waiting})()


def test_runner_works_named_claims(monkeypatch):
    worked = []
    monkeypatch.setattr(
        runner, "work_claim", lambda claim_id, run_id: worked.append(claim_id)
    )
    runner.main(["CLM-1003", "CLM-1001"])
    assert worked == ["CLM-1003", "CLM-1001"]
    assert client.get("/api/runs/current").json()["cap"] == 2


def test_agent_never_reads_data():
    for source in Path("agent").glob("*.py"):
        assert "data/" not in source.read_text(encoding="utf-8")


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = main.model()
    assert default.model_name == "deepseek/deepseek-v4.1-flash"
    assert default.reasoning_effort == "low"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"
