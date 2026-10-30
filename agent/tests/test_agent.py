import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import __main__ as runner
from agent import main, service
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
        if isinstance(item, BaseException):
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
    memory = InMemorySaver()
    monkeypatch.setattr(main, "saver", lambda: memory)


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


def start(claim_id, run_id):
    body = {"run_id": run_id}
    return client.post(f"/api/claims/{claim_id}/start", json=body).json()


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
    args = decision("CLM-1001", 50.0, "decline")
    assert main.record_decision.invoke(args).startswith("Refused (422)")


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
        AIMessage(content="Approved, $420.00."),
    ]
    reply = main.work_claim("CLM-1001", new_run(1), stub(*replies))
    assert reply == "Approved, $420.00."
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("decided", 420.0)
    assert result["sections_read"] == ["claims_manual 6", "policy_wording 3"]
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert steps[0]["text"] == 'get_claim({"claim_id": "CLM-1001"})'
    assert steps[-2]["text"] == "Approved, $420.00."
    assert steps[-1]["text"] == "pay_claim: Paid $420.00"


def test_claim_agent_works_out_the_payout_with_the_tool():
    line = {"description": "Hotel", "amount": 300, "quantity": 1, "kind": "expense"}
    payout_args = {
        "policy_id": "POL-2001",
        "benefit": "trip_delay",
        "lines": [{**line, "nights": 0, "hours": 0}],
        "paid_by_others": 0,
    }
    replies = [
        calls(("calculate_payout", payout_args)),
        calls(("record_decision", decision("CLM-1001", 300.0))),
        AIMessage(content="Approved, $300.00."),
    ]
    main.work_claim("CLM-1001", new_run(1), stub(*replies))
    results = [s["text"] for s in client.get("/api/claims/CLM-1001/steps").json()]
    assert any(
        r.startswith("calculate_payout:") and '"payout":300.0' in r for r in results
    )
    assert claim("CLM-1001")["payout"] == 300.0


def hold(claim_id="CLM-1001", payout=1250.0):
    """Work a claim to an approval over the limit, which waits for sign-off."""
    replies = [
        calls(("record_decision", decision(claim_id, payout))),
        AIMessage(content=f"Approved, ${payout:,.2f}."),
    ]
    return main.work_claim(claim_id, new_run(1), stub(*replies))


def test_claim_agent_has_no_pay_tool():
    assert "pay_claim" not in [t.name for t in main.CLAIM_TOOLS]
    assert "pay_claim" not in client.get("/api/instructions").json()["claim"]


def test_claim_agent_pauses_before_a_payout_over_the_limit():
    reply = hold()
    assert reply == "CLM-1001 is waiting for a handler's sign-off."
    result = claim("CLM-1001")
    assert result["status"] == "waiting_for_sign_off"
    assert result["paid_amount"] is None
    saved = main.saver().get_tuple({"configurable": {"thread_id": "CLM-1001-1"}})
    assert saved is not None


def test_sign_off_resumes_the_claim_and_pays_it_once():
    hold()
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    assert main.run("CLM-1001", "CLM-1001-1", stub()) == ""
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("decided", 1250.0)
    assert result["signed_off_by"] == "HND-01"
    assert result["paid_at"] >= result["signed_off_at"]
    main.run("CLM-1001", "CLM-1001-1", stub())
    texts = [s["text"] for s in client.get("/api/claims/CLM-1001/steps").json()]
    assert "Signed off by HND-01" in texts
    assert len([t for t in texts if t.startswith("pay_claim:")]) == 1


def test_a_held_claim_without_a_sign_off_keeps_waiting():
    hold()
    reply = main.run("CLM-1001", "CLM-1001-1", stub())
    assert reply == "CLM-1001 is waiting for a handler's sign-off."
    assert claim("CLM-1001")["paid_amount"] is None


def test_a_stopped_claim_carries_on_from_its_last_step():
    run_id = new_run(1)
    first = [calls(("get_claim", {"claim_id": "CLM-1001"})), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        main.run("CLM-1001", start("CLM-1001", run_id)["thread_id"], stub(*first))
    rest = [
        calls(("record_decision", decision("CLM-1001", 420.0))),
        AIMessage(content="Approved, $420.00."),
    ]
    reply = main.work_claim("CLM-1001", run_id, stub(*rest))
    assert reply == "Approved, $420.00."
    result = claim("CLM-1001")
    assert (result["thread_id"], result["attempts"]) == ("CLM-1001-1", 0)
    assert (result["status"], result["paid_amount"]) == ("decided", 420.0)
    texts = [s["text"] for s in client.get("/api/claims/CLM-1001/steps").json()]
    assert len([t for t in texts if t.startswith("get_claim(")]) == 1
    assert len([t for t in texts if t.startswith("record_decision(")]) == 1


def test_a_claim_that_fails_starts_afresh_on_a_new_thread(monkeypatch):
    monkeypatch.setattr(main, "MODEL_RETRIES", 0)
    run_id = new_run(2)
    first = [calls(("get_claim", {"claim_id": "CLM-1001"})), TimeoutError("no reply")]
    main.work_claim("CLM-1001", run_id, stub(*first))
    assert claim("CLM-1001")["status"] == "waiting"
    second = [
        calls(("record_decision", decision("CLM-1001", 0, "decline"))),
        AIMessage(content="Declined."),
    ]
    assert main.work_claim("CLM-1001", run_id, stub(*second)) == "Declined."
    assert claim("CLM-1001")["thread_id"] == "CLM-1001-2"
    fresh = main.saver().get_tuple({"configurable": {"thread_id": "CLM-1001-2"}})
    assert fresh is not None
    assert fresh.checkpoint["channel_values"]["model_calls"] == 2


def test_a_31st_model_call_is_refused(monkeypatch):
    monkeypatch.setattr(main, "MAX_MODEL_CALLS", 3)
    looping = [calls(("get_claim", {"claim_id": "CLM-1001"})) for _ in range(4)]
    reply = main.work_claim("CLM-1001", new_run(1), stub(*looping))
    assert reply.startswith("failed: RuntimeError: the claim agent reached 3 model")
    assert claim("CLM-1001")["status"] == "waiting"


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


def test_no_tracing_without_langfuse_keys():
    config = main.traced("CLM-1001-1")
    assert config == {"configurable": {"thread_id": "CLM-1001-1"}}


def test_tracing_puts_the_claim_in_its_thread_session(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(main, "CallbackHandler", lambda: "handler")
    config = main.traced("CLM-1001-1")
    assert config.get("callbacks") == ["handler"]
    assert config.get("metadata") == {"langfuse_session_id": "CLM-1001-1"}


def test_claim_agent_runs_on_the_claims_thread(monkeypatch):
    seen = []
    real = main.traced
    monkeypatch.setattr(main, "traced", lambda t: seen.append(t) or real(t))
    main.work_claim("CLM-1001", new_run(1), stub(*decide("CLM-1001")))
    assert seen == ["CLM-1001-1"]


def test_the_service_resumes_a_signed_off_claim_and_pays_it(monkeypatch):
    monkeypatch.setattr(main, "model", stub)
    hold()
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-02"})
    response = TestClient(service.app).post("/claims/CLM-1001/resume")
    assert response.status_code == 202
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("decided", 1250.0)
    assert TestClient(service.app).get("/health").json() == {"status": "ok"}


def test_the_service_leaves_a_claim_that_is_not_in_progress(monkeypatch):
    monkeypatch.setattr(main, "model", stub)
    hold()
    TestClient(service.app).post("/claims/CLM-1001/resume")
    assert claim("CLM-1001")["status"] == "waiting_for_sign_off"


def test_runner_carries_on_unfinished_claims_first(monkeypatch, capsys):
    run_id = new_run(2)
    first = [calls(("get_claim", {"claim_id": "CLM-1002"})), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        main.run("CLM-1002", start("CLM-1002", run_id)["thread_id"], stub(*first))
    hold("CLM-1001")
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    rest = [
        calls(("record_decision", decision("CLM-1002", 0, "decline"))),
        AIMessage(content="Declined."),
    ]
    monkeypatch.setattr(main, "model", lambda: stub(*rest))
    runner.main(["--cap", "0"])
    out = capsys.readouterr().out
    assert "CLM-1001: " in out
    assert "CLM-1002: Declined." in out
    assert claim("CLM-1001")["paid_amount"] == 1250.0
    assert (claim("CLM-1002")["status"], claim("CLM-1002")["attempts"]) == (
        "decided",
        0,
    )


def test_a_stopped_queue_run_is_finished_by_the_next_run():
    stopped = [
        calls(("set_todos", {"todos": todos(("CLM-1001", "to_do"))})),
        KeyboardInterrupt(),
    ]
    with pytest.raises(KeyboardInterrupt):
        main.run_queue(1, stub(*stopped))
    run_id = client.get("/api/runs/current").json()["run_id"]
    rest = [
        calls(("work_claim", {"claim_id": "CLM-1001"})),
        calls(("finish_run", {"summary": "One claim declined."})),
        AIMessage(content="Done."),
    ]
    assert main.resume_queue(stub(*rest), stub(*decide("CLM-1001"))) == "Done."
    run = client.get("/api/runs/current").json()
    assert (run["run_id"], run["summary"]) == (run_id, "One claim declined.")
    assert main.resume_queue(stub()) is None
