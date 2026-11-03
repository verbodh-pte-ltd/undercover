"""The app keeps one piece of the agent's work going: a dispatch, an evaluation, or a
round while it works."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import update

from backend import agent_service, slot, store
from backend.main import QUIET_REASON, app
from backend.tests.records import record_decision
from backend.tests.signing import AGENT, headers
from backend.tests.test_rounds import CHANGE

client = TestClient(app, headers=AGENT)
EVALUATION = {"n": 5, "eval_set": "held_out", "tiny": True}


class Agent:
    """A stand-in for the agent: it takes the work unless told otherwise."""

    def __init__(self):
        self.sent = []
        self.answer: Exception | None = None

    def send(self, payload):
        self.sent.append(payload)
        if self.answer is not None:
            raise self.answer


@pytest.fixture(autouse=True)
def agent(monkeypatch):
    found = Agent()
    monkeypatch.setattr(agent_service, "send", found.send)
    return found


def dispatch():
    return client.post("/api/dispatches", json={"n": 3}, headers=headers())


def evaluation():
    return client.post("/api/evaluations", json=EVALUATION, headers=headers())


def start_round():
    return client.post("/api/rounds", headers=headers())


def held_by():
    with store.connect() as db:
        row = slot.holder(db)
    return (row["kind"], row["work_id"])


def gone_quiet(minutes=6):
    """The agent's last word on what holds the slot was this long ago."""
    then = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )
    with store.engine().begin() as db:
        db.execute(update(store.agent_work).values(started_at=then, heard_at=then))


def run(run_id):
    return client.get(f"/api/runs/{run_id}").json()


def a_round(round_id):
    return client.get(f"/api/rounds/{round_id}").json()


@pytest.mark.parametrize(
    ("first", "what"),
    [
        (dispatch, "A dispatch is already running"),
        (evaluation, "An evaluation is already running"),
        (start_round, "Round {} is already running"),
    ],
)
def test_a_second_start_is_refused_naming_what_is_going(first, what):
    started = first().json()
    what = what.format(started.get("round_id"))
    for second in [dispatch, evaluation]:
        response = second()
        assert (response.status_code, response.json()["detail"]) == (409, what)
    if "round_id" not in started:
        response = start_round()
        assert (response.status_code, response.json()["detail"]) == (409, what)


def test_a_round_waiting_for_review_frees_the_slot_and_its_review_takes_it():
    round_id = start_round().json()["round_id"]
    assert held_by() == ("round", round_id)
    client.post(f"/api/rounds/{round_id}/proposed-change", json=CHANGE)
    assert held_by() == (None, None)
    run_id = dispatch().json()["run_id"]
    response = client.post(f"/api/rounds/{round_id}/approve", headers=headers())
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "A dispatch is already running",
    )
    assert a_round(round_id)["stage"] == "change_waiting"
    client.post(f"/api/runs/{run_id}/finish")
    assert client.post(f"/api/rounds/{round_id}/approve", headers=headers()).is_success
    assert held_by() == ("round", round_id)


def test_a_rounds_evaluation_works_under_the_rounds_slot():
    round_id = start_round().json()["round_id"]
    body = {"eval_set": "held_out", "version": 1}
    run_id = client.post(f"/api/rounds/{round_id}/evaluations", json=body).json()[
        "run_id"
    ]
    client.post(f"/api/runs/{run_id}/finish")
    assert held_by() == ("round", round_id)
    client.post(f"/api/rounds/{round_id}/stage", json={"stage": "finished"})
    assert held_by() == (None, None)


def test_two_starts_at_the_same_moment_one_goes_ahead():
    barrier = threading.Barrier(2)
    answers = []

    def start(path, body):
        own = TestClient(app)
        barrier.wait()
        answers.append(own.post(path, json=body, headers=headers()).status_code)

    starts = [("/api/dispatches", {"n": 3}), ("/api/evaluations", EVALUATION)]
    threads = [threading.Thread(target=start, args=s) for s in starts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(answers) == [200, 409]
    with store.connect() as db:
        running = db.execute(
            store.runs.select().where(store.runs.c.status == "running")
        ).all()
    assert len(running) == 1
    assert held_by() == ("run", running[0].run_id)


def test_the_slot_is_taken_by_one_conditional_update_on_what_was_read():
    with store.connect() as db:
        before = slot.holder(db)
        assert slot.swap(db, before, "run", 1)
        # A second start that read the slot free before the first took it.
        assert not slot.swap(db, before, "run", 2)
        db.commit()
    assert held_by() == ("run", 1)


def test_a_start_after_five_quiet_minutes_stops_the_old_run_and_goes_ahead():
    old = dispatch().json()["run_id"]
    gone_quiet(minutes=4)
    assert dispatch().status_code == 409
    gone_quiet()
    new = evaluation().json()["run_id"]
    assert (run(old)["status"], run(old)["stopped_reason"]) == ("stopped", QUIET_REASON)
    assert run(new)["status"] == "running"
    assert held_by() == ("run", new)


def test_a_start_after_a_round_went_quiet_stops_it_with_its_evaluation():
    round_id = start_round().json()["round_id"]
    body = {"eval_set": "held_out", "version": 1}
    run_id = client.post(f"/api/rounds/{round_id}/evaluations", json=body).json()[
        "run_id"
    ]
    gone_quiet()
    assert dispatch().status_code == 200
    found = a_round(round_id)
    assert (found["stage"], found["stopped_reason"]) == ("stopped", QUIET_REASON)
    assert (run(run_id)["status"], run(run_id)["stopped_reason"]) == (
        "stopped",
        QUIET_REASON,
    )


def test_the_agents_word_keeps_its_work_going():
    run_id = dispatch().json()["run_id"]
    gone_quiet()
    heard = {"kind": "run", "work_id": run_id}
    assert client.post("/api/work/heard", json=heard).json() == {"heard": True}
    assert dispatch().status_code == 409
    other = {"kind": "round", "work_id": run_id}
    assert client.post("/api/work/heard", json=other).json() == {"heard": False}
    assert (
        client.post(
            "/api/work/heard", json=heard, headers={"X-API-Key": ""}
        ).status_code
        == 401
    )


def test_a_start_the_agent_didnt_take_is_undone(agent):
    agent.answer = agent_service.NotTaken(503, "The agent isn't running")
    response = dispatch()
    assert (response.status_code, response.json()["detail"]) == (
        503,
        "The agent isn't running",
    )
    stopped = client.get("/api/runs/current", headers=headers()).json()
    assert (stopped["status"], stopped["stopped_reason"]) == (
        "stopped",
        "The agent isn't running",
    )
    assert held_by() == (None, None)
    agent.answer = None
    assert dispatch().status_code == 200


def test_a_timeout_leaves_the_work_going(agent):
    agent.answer = HTTPException(504, "Reload the page")
    assert dispatch().status_code == 504
    going = client.get("/api/runs/current", headers=headers()).json()
    assert going["status"] == "running"
    assert held_by() == ("run", going["run_id"])
    agent.answer = None
    assert dispatch().status_code == 409


def test_a_resume_runs_beside_a_dispatch(agent):
    dispatch()
    calculated = {"decision": "approve", "payout": 1250.0}
    calculated |= {"reason": "Covered.", "policy_section": "3"}
    client.post("/api/claims/CLM-1001/start", json={"run_id": None})
    record_decision(client, "/api/claims/CLM-1001/decision", calculated)
    signed = client.post("/api/claims/CLM-1001/sign-off", headers=headers())
    assert signed.json()["status"] == "decided"
    assert agent.sent[-1] == {"operation": "resume_claim", "claim_id": "CLM-1001"}
