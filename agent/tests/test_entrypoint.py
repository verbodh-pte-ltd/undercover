import importlib
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from agent import dispatcher, entrypoint, improver, main
from agent.tests.test_agent import (  # noqa: F401 (check and in_process_backend are fixtures)
    check,
    claim,
    hold,
    in_process_backend,
    invoke,
    ping,
    recording,
    settle,
    steps_of,
    stub,
)
from backend import agent_service, store
from backend.main import app
from backend.tests.records import new_run
from backend.tests.signing import AGENT, headers
from backend.tests.test_rounds import CHANGE

client = TestClient(app, headers=AGENT)
CREDENTIALS = {
    "AGENTCORE_CREDENTIAL_OPENROUTER_API_KEY_NAME": "undercover-openrouter",
    "AGENTCORE_CREDENTIAL_UNDERCOVER_API_KEY_NAME": "undercover-agent-key",
    "AGENTCORE_CREDENTIAL_DATABASE_URL_NAME": "undercover-database",
}


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    """Each test is a new process: the agent hasn't got ready yet."""
    monkeypatch.setattr(entrypoint, "ready", threading.Event())
    for name in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)


class Identity:
    """A stand-in for AgentCore Identity's requires_api_key, noting each fetch."""

    def __init__(self):
        self.fetched = []

    def requires_api_key(self, *, provider_name):
        def decorator(func):
            def wrapper(*args, **kwargs):
                self.fetched.append(provider_name)
                return func(*args, api_key=f"{provider_name}-value", **kwargs)

            return wrapper

        return decorator


@pytest.fixture
def on_runtime(monkeypatch):
    """The agent on AgentCore Runtime, with its keys in a stand-in Identity.

    The database stays the test's own SQLite file, so DATABASE_URL isn't a credential.
    """
    identity = Identity()
    for name, provider in CREDENTIALS.items():
        if "DATABASE_URL" not in name:
            monkeypatch.setenv(name, provider)
    # Set first, so the fetched values are put back after the test.
    for setting in ["OPENROUTER_API_KEY", "UNDERCOVER_API_KEY"]:
        monkeypatch.setenv(setting, "from .env")
    monkeypatch.setattr(entrypoint, "requires_api_key", identity.requires_api_key)
    return identity


@pytest.fixture
def waiting(monkeypatch):
    """A dispatcher and an improver that work until the test lets them finish."""
    finish = threading.Event()
    started = []

    def dispatch(run_id, n, llm=None):
        started.append(("dispatch", run_id, n))
        finish.wait(5)

    def work_round(round_id):
        started.append(("round", round_id))
        finish.wait(5)

    def answer(round_id, decision, reason):
        started.append(("answer", round_id, decision, reason))
        finish.wait(5)

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    monkeypatch.setattr(improver, "work_round", work_round)
    monkeypatch.setattr(improver, "answer", answer)
    yield finish, started
    finish.set()
    settle()


@pytest.fixture
def the_app_starts(monkeypatch):
    """The app starts runs and rounds as a handler asks, and the agent isn't asked."""
    monkeypatch.setattr(agent_service, "send", lambda payload: None)


def work(run_id):
    return {"operation": "work_run", "run_id": run_id}


def a_round():
    """A round the app has started, open and holding the slot."""
    return client.post("/api/rounds", headers=headers()).json()["round_id"]


def reviewed(round_id, decision="approve"):
    """The round's proposal, reviewed by a handler; its id."""
    found = client.post(f"/api/rounds/{round_id}/proposed-change", json=CHANGE).json()
    proposal_id = found["proposal"]["proposal_id"]
    body = {"reason": "Too broad"} if decision == "reject" else None
    client.post(f"/api/rounds/{round_id}/{decision}", json=body, headers=headers())
    return proposal_id


def run(run_id):
    return client.get(f"/api/runs/{run_id}").json()


# Getting ready


def test_importing_the_agent_opens_nothing(monkeypatch):
    opened = []
    try:
        with pytest.MonkeyPatch.context() as patch:
            for name, provider in CREDENTIALS.items():
                patch.setenv(name, provider)
            patch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
            patch.setattr(
                "bedrock_agentcore.identity.auth.IdentityClient",
                lambda *args: opened.append("identity"),
            )
            patch.setattr(main, "make_pool", lambda: opened.append("pool"))
            patch.setattr(main, "backend", lambda: opened.append("backend"))
            importlib.reload(entrypoint)
            importlib.reload(importlib.import_module("runtime.main"))
            assert opened == []
            assert not entrypoint.ready.is_set()
    finally:
        importlib.reload(entrypoint)
        importlib.reload(importlib.import_module("runtime.main"))


def test_on_runtime_the_first_request_fetches_each_credential_once(
    on_runtime, monkeypatch
):
    name = "AGENTCORE_CREDENTIAL_DATABASE_URL_NAME"
    monkeypatch.setenv(name, CREDENTIALS[name])
    monkeypatch.setenv("DATABASE_URL", "from .env")
    opened = []
    monkeypatch.setattr(main, "pool", lambda: opened.append(os.environ["DATABASE_URL"]))
    reseeded = []
    real_seed = entrypoint.random.seed
    monkeypatch.setattr(
        entrypoint.random, "seed", lambda *args: reseeded.append(args) or real_seed()
    )
    assert invoke({"operation": "health"}, key=None).json() == {"healthy": True}
    assert invoke({"operation": "health"}, key=None).json() == {"healthy": True}
    assert sorted(on_runtime.fetched) == sorted(CREDENTIALS.values())
    assert opened == ["undercover-database-value"]
    assert os.environ["OPENROUTER_API_KEY"] == "undercover-openrouter-value"
    assert os.environ["UNDERCOVER_API_KEY"] == "undercover-agent-key-value"
    assert os.environ["DATABASE_URL"] == "undercover-database-value"
    assert reseeded == [()]


def test_off_runtime_nothing_is_fetched(monkeypatch):
    identity = Identity()
    monkeypatch.setattr(entrypoint, "requires_api_key", identity.requires_api_key)
    assert invoke({"operation": "health"}).json() == {"healthy": True}
    assert identity.fetched == []


def test_getting_ready_opens_the_pool_once_when_there_is_a_database(monkeypatch):
    opened = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setattr(main, "pool", lambda: opened.append("pool"))
    invoke({"operation": "health"})
    invoke({"operation": "health"})
    assert opened == ["pool"]


def test_without_the_key_a_request_answers_the_error_and_the_next_tries_again(
    monkeypatch,
):
    key = os.environ["UNDERCOVER_API_KEY"]
    monkeypatch.delenv("UNDERCOVER_API_KEY")
    response = invoke({"operation": "health"}, key=None)
    assert response.status_code == 500
    assert "UNDERCOVER_API_KEY is missing" in response.json()["error"]
    monkeypatch.setenv("UNDERCOVER_API_KEY", key)
    assert invoke({"operation": "health"}, key=None).json() == {"healthy": True}


def test_getting_ready_stops_nothing(the_app_starts):
    run_id = new_run()
    round_id = a_round()
    invoke({"operation": "health"})
    assert run(run_id)["status"] == "running"
    assert client.get(f"/api/rounds/{round_id}").json()["stage"] == "first_evaluations"


# The agent's key


def test_off_runtime_a_request_without_the_key_is_refused_but_health(monkeypatch):
    started = []
    monkeypatch.setattr(dispatcher, "dispatch", lambda *args: started.append(args))
    for key in [None, "the-wrong-key-of-at-least-32-characters"]:
        response = invoke(work(new_run()), key=key)
        assert response.status_code == 401
        assert response.json() == {"error": "The agent's key is missing or wrong"}
    assert invoke({"operation": "health"}, key=None).json() == {"healthy": True}
    assert started == []


def test_on_runtime_no_key_is_asked_for(on_runtime, waiting):
    finish, started = waiting
    assert invoke(work(new_run()), key=None).json() == {"accepted": True}
    finish.set()
    settle()
    assert [s[0] for s in started] == ["dispatch"]


# The payloads


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ({"operation": "work_run"}, "run_id: Field required"),
        ({"operation": "work_run", "run_id": "x"}, "run_id: Input should be a valid"),
        ({"operation": "work_round"}, "round_id: Field required"),
        ({"operation": "carry_round_on", "round_id": 1}, "proposal_id: Field"),
        ({"operation": "resume_claim"}, "claim_id: Field required"),
        ({"operation": "health", "extra": 1}, "extra: Extra inputs"),
        ({"operation": "start_dispatch", "n": 3}, "does not match any of the expected"),
        ({"n": 3}, "Unable to extract tag using discriminator 'operation'"),
        ([1, 2], "operation"),
    ],
)
def test_a_payload_with_an_input_missing_or_wrong_gets_400(payload, problem):
    response = invoke(payload)
    assert response.status_code == 400
    assert problem in response.json()["error"]


# Each operation, and the ping


def test_a_run_is_worked_by_its_id_while_the_ping_is_busy(waiting):
    finish, started = waiting
    run_id = new_run("evaluation", "held_out", tiny=True, n=2)
    assert ping() == "Healthy"
    assert invoke(work(run_id)).json() == {"accepted": True}
    assert ping() == "HealthyBusy"
    finish.set()
    settle()
    assert ping() == "Healthy"
    assert started == [("dispatch", run_id, 2)]


def test_a_run_that_isnt_running_is_refused(waiting):
    _, started = waiting
    run_id = new_run()
    client.post(f"/api/runs/{run_id}/finish")
    assert invoke(work(run_id)).json() == {"refused": f"Run {run_id} is finished"}
    assert invoke(work(999)).json() == {"refused": "Run 999 not found"}
    assert started == []


def test_a_round_is_worked_by_its_id_and_refused_once_closed(waiting, the_app_starts):
    finish, started = waiting
    round_id = a_round()
    payload = {"operation": "work_round", "round_id": round_id}
    assert invoke(payload).json() == {"accepted": True}
    finish.set()
    settle()
    assert started == [("round", round_id)]
    client.post(f"/api/rounds/{round_id}/stop", headers=headers())
    assert invoke(payload).json() == {"refused": f"Round {round_id} is stopped"}
    missing = {"operation": "work_round", "round_id": 999}
    assert invoke(missing).json() == {"refused": "Round 999 not found"}


@pytest.mark.parametrize(
    ("decision", "reason"), [("approve", ""), ("reject", "Too broad")]
)
def test_a_round_is_carried_on_from_the_review_the_app_recorded(
    waiting, the_app_starts, decision, reason
):
    finish, started = waiting
    round_id = a_round()
    proposal_id = reviewed(round_id, decision)
    payload = {
        "operation": "carry_round_on",
        "round_id": round_id,
        "proposal_id": proposal_id,
    }
    assert invoke(payload).json() == {"accepted": True}
    finish.set()
    settle()
    assert started == [("answer", round_id, decision, reason)]


def test_carrying_on_is_refused_without_a_review_or_on_a_closed_round(
    waiting, the_app_starts
):
    _, started = waiting
    round_id = a_round()
    found = client.post(f"/api/rounds/{round_id}/proposed-change", json=CHANGE).json()
    proposal_id = found["proposal"]["proposal_id"]
    payload = {
        "operation": "carry_round_on",
        "round_id": round_id,
        "proposal_id": proposal_id,
    }
    refused = {"refused": f"Proposal {proposal_id} hasn't been reviewed"}
    assert invoke(payload).json() == refused
    missing = {**payload, "proposal_id": 999}
    assert invoke(missing).json() == {
        "refused": f"Proposal 999 not found in round {round_id}"
    }
    client.post(f"/api/rounds/{round_id}/stop", headers=headers())
    assert invoke(payload).json() == {"refused": f"Round {round_id} is stopped"}
    assert started == []


def test_a_failed_run_stops_only_itself(monkeypatch):
    failing, other = new_run(), new_run()

    def dispatch(run_id, n, llm=None):
        raise RuntimeError("the dispatch failed")

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    assert invoke(work(failing)).json() == {"accepted": True}
    settle()
    assert (run(failing)["status"], run(failing)["stopped_reason"]) == (
        "stopped",
        "The agent's work failed: RuntimeError: the dispatch failed",
    )
    assert run(other)["status"] == "running"


def test_a_failed_round_stops_only_itself(monkeypatch, the_app_starts):
    round_id = a_round()

    def work_round(round_id):
        raise RuntimeError("the model is down")

    monkeypatch.setattr(improver, "work_round", work_round)
    invoke({"operation": "work_round", "round_id": round_id})
    settle()
    found = client.get(f"/api/rounds/{round_id}").json()
    assert (found["stage"], found["stopped_reason"]) == (
        "stopped",
        "The agent's work failed: RuntimeError: the model is down",
    )


def test_the_agent_tells_the_app_every_minute_while_it_works(
    waiting, the_app_starts, monkeypatch
):
    finish, _ = waiting
    monkeypatch.setattr(entrypoint, "HEARD_EVERY", 0.01)
    run_id = client.post("/api/dispatches", json={"n": 3}, headers=headers()).json()[
        "run_id"
    ]
    long_ago = "2026-01-01T00:00:00+00:00"
    with store.engine().begin() as db:
        db.execute(update(store.agent_work).values(heard_at=long_ago))
    invoke(work(run_id))
    heard = long_ago
    for _ in range(500):
        with store.connect() as db:
            heard = db.execute(store.agent_work.select()).mappings().one()["heard_at"]
        if heard != long_ago:
            break
        time.sleep(0.01)
    assert heard > long_ago
    finish.set()


@pytest.mark.parametrize("fails", [False, True])
def test_the_ping_is_healthy_once_work_has_finished_or_failed(monkeypatch, fails):
    finish = threading.Event()

    def dispatch(run_id, n, llm=None):
        finish.wait(5)
        if fails:
            raise RuntimeError("the dispatch failed")

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    assert invoke(work(new_run())).json() == {"accepted": True}
    assert ping() == "HealthyBusy"
    finish.set()
    settle()
    assert ping() == "Healthy"


# Resuming a claim


def test_a_resume_finishes_a_signed_off_claim_without_paying(monkeypatch):
    monkeypatch.setattr(main, "model", lambda version: stub())
    hold()
    client.post("/api/claims/CLM-1001/sign-off", headers=headers())
    sent = recording(monkeypatch)
    response = invoke({"operation": "resume_claim", "claim_id": "CLM-1001"})
    assert response.json() == {"accepted": True}
    settle()
    assert "Signed off by HND-01" in steps_of("CLM-1001")
    assert not [r for r in sent if r.endswith("/payment")]
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("decided", 1250.0)


def test_a_resume_refuses_a_claim_without_an_answer(monkeypatch):
    monkeypatch.setattr(main, "model", lambda version: stub())
    hold()
    response = invoke({"operation": "resume_claim", "claim_id": "CLM-1001"})
    assert response.json() == {
        "refused": "CLM-1001 is waiting for a handler's sign-off"
    }
    settle()
    assert claim("CLM-1001")["status"] == "waiting_for_sign_off"


def test_a_resume_runs_beside_a_dispatch(waiting, monkeypatch):
    resumed = []
    monkeypatch.setattr(entrypoint, "resume", resumed.append)
    monkeypatch.setattr(main, "resume_refusal", lambda claim_id: None)
    assert invoke(work(new_run())).json() == {"accepted": True}
    response = invoke({"operation": "resume_claim", "claim_id": "CLM-1001"})
    assert response.json() == {"accepted": True}
    finish, _ = waiting
    finish.set()
    settle()
    assert resumed == ["CLM-1001"]
