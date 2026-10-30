import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import __main__ as runner
from agent import dispatcher, main, service
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


def new_run(kind="dispatch", eval_set=None, tiny=False, n=3):
    body = {
        "kind": kind,
        "n": n,
        "eval_set": eval_set,
        "tiny": tiny,
        "model": "stub",
        "effort": "none",
    }
    return client.post("/api/runs", json=body).json()["run_id"]


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


def steps_of(claim_id):
    return [s["text"] for s in client.get(f"/api/claims/{claim_id}/steps").json()]


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
    reply = main.work("CLM-1001", None, stub(*replies))
    assert reply == "Approved, $420.00."
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("decided", 420.0)
    assert result["sections_read"] == ["claims_manual 6", "policy_wording 3"]
    texts = steps_of("CLM-1001")
    assert texts[0] == 'get_claim({"claim_id": "CLM-1001"})'
    assert texts[-2:] == ["Approved, $420.00.", "pay_claim: Paid $420.00"]


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
    main.work("CLM-1001", None, stub(*replies))
    assert any(
        r.startswith("calculate_payout:") and '"payout":300.0' in r
        for r in steps_of("CLM-1001")
    )
    assert claim("CLM-1001")["payout"] == 300.0


def hold(claim_id="CLM-1001", payout=1250.0, run_id=None):
    """Work a claim to an approval over the limit, which waits for sign-off."""
    replies = [
        calls(("record_decision", decision(claim_id, payout))),
        AIMessage(content=f"Approved, ${payout:,.2f}."),
    ]
    return main.work(claim_id, run_id, stub(*replies))


def test_claim_agent_has_no_pay_tool():
    assert "pay_claim" not in [t.name for t in main.CLAIM_TOOLS]
    assert "pay_claim" not in client.get("/api/instructions").json()["claim"]


def test_claim_agent_pauses_before_a_payout_over_the_limit():
    assert hold() == "CLM-1001 is waiting for a handler's sign-off."
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("waiting_for_sign_off", None)
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
    texts = steps_of("CLM-1001")
    assert "Signed off by HND-01" in texts
    assert len([t for t in texts if t.startswith("pay_claim:")]) == 1


def test_a_held_claim_without_a_sign_off_keeps_waiting():
    hold()
    reply = main.run("CLM-1001", "CLM-1001-1", stub())
    assert reply == "CLM-1001 is waiting for a handler's sign-off."
    assert claim("CLM-1001")["paid_amount"] is None


def test_a_stopped_claim_carries_on_from_its_last_step():
    first = [calls(("get_claim", {"claim_id": "CLM-1001"})), KeyboardInterrupt()]
    with pytest.raises(KeyboardInterrupt):
        main.run("CLM-1001", start("CLM-1001", None)["thread_id"], stub(*first))
    rest = [
        calls(("record_decision", decision("CLM-1001", 420.0))),
        AIMessage(content="Approved, $420.00."),
    ]
    assert main.work("CLM-1001", None, stub(*rest)) == "Approved, $420.00."
    result = claim("CLM-1001")
    assert (result["thread_id"], result["attempts"]) == ("CLM-1001-1", 0)
    assert (result["status"], result["paid_amount"]) == ("decided", 420.0)
    texts = steps_of("CLM-1001")
    assert len([t for t in texts if t.startswith("get_claim(")]) == 1
    assert len([t for t in texts if t.startswith("record_decision(")]) == 1


def test_a_failed_claim_is_tried_once_more_afresh():
    replies = [
        calls(("get_claim", {"claim_id": "CLM-1001"})),
        TimeoutError("no reply"),
        *decide("CLM-1001"),
    ]
    assert main.work("CLM-1001", None, stub(*replies)) == "CLM-1001: decline, $0.00."
    result = claim("CLM-1001")
    assert (result["status"], result["attempts"]) == ("decided", 1)
    assert result["thread_id"] == "CLM-1001-2"
    fresh = main.saver().get_tuple({"configurable": {"thread_id": "CLM-1001-2"}})
    assert fresh is not None
    assert fresh.checkpoint["channel_values"]["model_calls"] == 2
    assert "TimeoutError: no reply" in steps_of("CLM-1001")


def test_a_claim_that_fails_twice_is_left_for_a_handler():
    reply = main.work("CLM-1001", None, stub(TimeoutError("one"), TimeoutError("two")))
    assert reply.startswith("failed twice: TimeoutError: two")
    result = claim("CLM-1001")
    assert (result["status"], result["attempts"]) == ("needs_handler", 2)


def test_a_31st_model_call_is_refused(monkeypatch):
    monkeypatch.setattr(main, "MAX_MODEL_CALLS", 3)
    looping = [calls(("get_claim", {"claim_id": "CLM-1001"})) for _ in range(8)]
    reply = main.work("CLM-1001", None, stub(*looping))
    assert reply.startswith("failed twice: RuntimeError: the claim agent reached 3")


def test_claim_agent_that_stops_without_a_decision_is_released():
    reply = main.work("CLM-1001", None, stub(AIMessage(content="Hmm."), AIMessage("?")))
    assert reply.startswith("failed twice: the claim agent stopped without a decision")


def test_work_on_an_unknown_or_decided_claim():
    assert main.work("CLM-0000", None, stub()).startswith("not started:")
    main.work("CLM-1001", None, stub(*decide("CLM-1001")))
    assert main.work("CLM-1001", None, stub()).startswith("not started:")


def test_a_rate_limit_is_let_through_and_the_claim_stays_in_progress():
    replies = [
        calls(("get_claim", {"claim_id": "CLM-1001"})),
        ModelRateLimitError("429"),
    ]
    with pytest.raises(ModelRateLimitError):
        main.work("CLM-1001", None, stub(*replies))
    result = claim("CLM-1001")
    assert (result["status"], result["attempts"]) == ("in_progress", 0)


def test_cost_is_recorded_for_each_model_call_with_its_run():
    run_id = new_run()
    reply = AIMessage(
        content="",
        tool_calls=calls(
            ("record_decision", decision("CLM-1001", 0, "decline"))
        ).tool_calls,
        usage_metadata={
            "input_tokens": 1200,
            "output_tokens": 80,
            "total_tokens": 1280,
        },
        response_metadata={"token_usage": {"cost": 0.0021}},
    )
    main.work("CLM-1001", run_id, stub(reply, AIMessage(content="Declined.")))
    run = client.get("/api/runs/current").json()
    assert run["cost"] == pytest.approx(0.0021)
    assert run["claims"][0]["claim_id"] == "CLM-1001"
    assert claim("CLM-1001")["cost"] == pytest.approx(0.0021)


def test_time_is_recorded_with_its_run_and_leaves_out_a_sign_off_wait(monkeypatch):
    ticks = iter([0.0, 7.0, 1000.0, 1002.0])
    monkeypatch.setattr(main, "clock", lambda: next(ticks))
    run_id = new_run()
    hold(run_id=run_id)
    client.post("/api/claims/CLM-1001/sign-off", json={"handler_id": "HND-01"})
    main.run("CLM-1001", "CLM-1001-1", stub())
    run = client.get("/api/runs/current").json()
    assert run["seconds"] == pytest.approx(7.0)
    assert claim("CLM-1001")["seconds"] == pytest.approx(9.0)


def test_a_failed_attempts_cost_and_time_count_toward_its_run():
    run_id = new_run()
    costly = AIMessage(
        content="",
        tool_calls=calls(("get_claim", {"claim_id": "CLM-1001"})).tool_calls,
        response_metadata={"token_usage": {"cost": 0.01}},
    )
    main.work("CLM-1001", run_id, stub(costly, TimeoutError("one"), TimeoutError("2")))
    run = client.get("/api/runs/current").json()
    assert run["cost"] == pytest.approx(0.01)
    assert len(run["claims"]) == 1
    assert run["seconds"] > 0


def test_an_evaluation_copy_held_for_sign_off_is_finished_and_cleared():
    run_id = new_run("evaluation", "evaluation", tiny=True)
    copy_id = client.get(f"/api/runs/{run_id}/claims").json()[0]
    hold(copy_id, run_id=run_id)
    assert claim(copy_id)["status"] == "waiting_for_sign_off"
    thread = claim(copy_id)["thread_id"]
    assert main.saver().get_tuple({"configurable": {"thread_id": thread}}) is None
    assert "is waiting for a handler" not in " ".join(steps_of(copy_id))


def test_agent_never_reads_data():
    for source in Path("agent").glob("*.py"):
        assert "data/" not in source.read_text(encoding="utf-8")


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = main.model()
    assert default.model_name == "openai/gpt-6-luna"
    assert (default.reasoning_effort, main.effort()) == ("low", "low")
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    chosen = main.model()
    assert (chosen.model_name, chosen.reasoning_effort) == ("openrouter/free", None)
    assert main.effort() == "none"


def test_no_tracing_without_langfuse_keys():
    config = main.traced("CLM-1001-1", 7)
    assert config == {"configurable": {"thread_id": "CLM-1001-1", "run_id": 7}}


def test_tracing_puts_the_claim_in_its_session_tagged_with_its_run(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(main, "CallbackHandler", lambda: "handler")
    config = main.traced("CLM-1001-1", 7)
    assert config.get("callbacks") == ["handler"]
    assert config.get("metadata") == {
        "langfuse_session_id": "CLM-1001-1",
        "langfuse_tags": ["run-7"],
    }
    assert main.traced("CLM-1001-1").get("metadata") == {
        "langfuse_session_id": "CLM-1001-1"
    }


def test_runner_works_named_claims_with_no_run(monkeypatch, capsys):
    worked = []
    monkeypatch.setattr(runner, "work", lambda c, run_id: worked.append((c, run_id)))
    runner.main(["CLM-1003", "CLM-1001"])
    assert worked == [("CLM-1003", None), ("CLM-1001", None)]
    runner.main([])
    assert "Usage: uv run -m agent CLAIM_ID" in capsys.readouterr().out


# The dispatcher


def fake_work(record, seconds=0.05):
    """A stand-in for the harness that records when each claim starts and finishes."""

    def work(claim_id, run_id, llm=None):
        started = time.perf_counter()
        time.sleep(seconds)
        record.append((claim_id, started, time.perf_counter()))
        return f"{claim_id}: done"

    return work


def most_at_once(record):
    events = sorted([(s, 1) for _, s, _ in record] + [(f, -1) for _, _, f in record])
    at_once = most = 0
    for _, change in events:
        at_once += change
        most = max(most, at_once)
    return most


def test_never_more_than_n_claims_at_once(monkeypatch):
    record = []
    monkeypatch.setattr(main, "work", fake_work(record))
    run_id = new_run(n=3)
    results = dispatcher.dispatch(run_id, 3)
    assert len(results) == 25
    assert len(record) == 25
    assert most_at_once(record) == 3
    assert client.get("/api/runs/current").json()["status"] == "finished"


def test_a_dispatch_works_claims_in_progress_first(monkeypatch):
    record = []
    monkeypatch.setattr(main, "work", fake_work(record, 0))
    start("CLM-1020", None)
    dispatcher.dispatch(new_run(n=1), 1)
    assert record[0][0] == "CLM-1020"


def test_a_dispatch_skips_a_claim_being_worked_here(monkeypatch):
    record = []
    monkeypatch.setattr(main, "work", fake_work(record, 0))
    assert dispatcher.take("CLM-1001")
    try:
        dispatcher.dispatch(new_run(n=5), 5)
    finally:
        dispatcher.let_go("CLM-1001")
    assert "CLM-1001" not in [c for c, _, _ in record]
    assert len(record) == 24


def test_a_rate_limit_carries_on_as_the_same_attempt_with_one_decision():
    run_id = new_run(n=1)
    stopped = start("CLM-1001", None)
    # Work only CLM-1001: every other claim is taken, as if being worked here.
    others = client.get(f"/api/runs/{run_id}/claims").json()[1:]
    for other in others:
        dispatcher.take(other)
    replies = [
        calls(("get_claim", {"claim_id": "CLM-1001"})),
        ModelRateLimitError("429"),
        calls(("record_decision", decision("CLM-1001", 0, "decline"))),
        AIMessage(content="Declined."),
    ]
    try:
        results = dispatcher.dispatch(run_id, 1, stub(*replies))
    finally:
        for other in others:
            dispatcher.let_go(other)
    assert results[0]["reply"] == "Declined."
    result = claim("CLM-1001")
    assert (result["thread_id"], result["attempts"]) == (stopped["thread_id"], 0)
    texts = steps_of("CLM-1001")
    assert len([t for t in texts if t.startswith("record_decision(")]) == 1
    assert len([t for t in texts if t.startswith("get_claim(")]) == 1


def test_a_claim_still_rate_limited_stays_in_progress_and_the_dispatch_goes_on(
    monkeypatch,
):
    def work(claim_id, run_id, llm=None):
        if claim_id == "CLM-1001":
            main.call("POST", f"/api/claims/{claim_id}/start", {"run_id": run_id})
            raise ModelRateLimitError("429")
        return f"{claim_id}: done"

    monkeypatch.setattr(main, "work", work)
    results = dispatcher.dispatch(new_run(n=5), 5)
    replies = {r["claim_id"]: r["reply"] for r in results}
    assert replies["CLM-1001"].startswith("left in progress after 3 tries")
    assert len(replies) == 25
    assert claim("CLM-1001")["status"] == "in_progress"
    assert client.get("/api/runs/current").json()["status"] == "finished"


def test_an_evaluation_works_its_copies_and_leaves_the_queue_alone():
    before = [(c["claim_id"], c["status"]) for c in client.get("/api/claims").json()]
    run_id = new_run("evaluation", "evaluation", tiny=True, n=1)
    copies = client.get(f"/api/runs/{run_id}/claims").json()
    replies = [m for copy_id in copies for m in decide(copy_id)]
    dispatcher.dispatch(run_id, 1, stub(*replies))
    evaluation = client.get(f"/api/evaluations/{run_id}").json()
    assert [c["agent"] for c in evaluation["claims"]] == ["decline"] * 5
    assert evaluation["status"] == "finished"
    after = [(c["claim_id"], c["status"]) for c in client.get("/api/claims").json()]
    assert after == before


# The agent's service


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


def test_the_service_starts_a_dispatch_and_refuses_a_second(monkeypatch):
    started = []
    finish = threading.Event()

    def dispatch(run_id, n, llm=None):
        started.append((run_id, n))
        finish.wait(5)

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    with TestClient(service.app) as agent:
        assert agent.post("/dispatches", json={"n": 4}).status_code == 202
        second = agent.post(
            "/evaluations", json={"n": 2, "eval_set": "held_out", "tiny": True}
        )
        assert second.status_code == 409
        finish.set()
        for _ in range(100):
            if not service.running.locked():
                break
            time.sleep(0.01)
    run = client.get("/api/runs/current").json()
    assert started == [(run["run_id"], 4)]
    assert (run["n"], run["model"]) == (4, main.model_name())


def test_the_service_marks_a_stopped_run_when_it_starts():
    new_run()
    with TestClient(service.app):
        pass
    assert client.get("/api/runs/current").json()["status"] == "stopped"


def test_claim_agents_starting_together_share_one_pool(monkeypatch):
    made = []

    class FakePool:
        def __init__(self, url, **kwargs):
            time.sleep(0.05)
            made.append(kwargs["max_size"])

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            return None

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(main, "ConnectionPool", FakePool)
    monkeypatch.setattr(main.psycopg, "connect", lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(main.PostgresSaver, "setup", lambda self: None)
    main.make_pool.cache_clear()
    try:
        workers = [threading.Thread(target=main.pool) for _ in range(25)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    finally:
        main.make_pool.cache_clear()
    assert made == [4]
