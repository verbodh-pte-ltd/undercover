import importlib
import os
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import dispatcher, entrypoint, improver, improver_api, main
from agent.tests.test_agent import (  # noqa: F401 (check and in_process_backend are fixtures)
    check,
    claim,
    hold,
    in_process_backend,
    invoke,
    new_run,
    ping,
    recording,
    settle,
    steps_of,
    stub,
)
from backend.main import app
from backend.tests.signing import AGENT, headers

client = TestClient(app, headers=AGENT)
DISPATCH = {"operation": "start_dispatch", "n": 4}
EVALUATION = {
    "operation": "start_evaluation",
    "n": 2,
    "eval_set": "held_out",
    "tiny": True,
}
ROUND = {"operation": "start_round", "round_id": 3, "thread_id": "round-x"}
ANSWER = {
    "operation": "answer_proposal",
    "round_id": 3,
    "decision": "approve",
    "reason": "",
    "handler_id": "HND-01",
}
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

    def carry_on(round_id, decision, reason):
        started.append(("answer", round_id, decision, reason))
        finish.wait(5)

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    monkeypatch.setattr(improver, "work_round", work_round)
    monkeypatch.setattr(improver, "answer", carry_on)
    yield finish, started
    finish.set()
    settle()


def recorded(status, detail="Recorded"):
    return lambda round_id, body: httpx.Response(status, json={"detail": detail})


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
    # The app's in-process stand-in has no Postgres to stop runs in.
    monkeypatch.setattr(entrypoint, "stop_what_was_running", lambda: None)
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
    # The app's in-process stand-in has no Postgres to stop runs in.
    monkeypatch.setattr(entrypoint, "stop_what_was_running", lambda: None)
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


def test_the_first_request_marks_a_run_left_running_as_stopped():
    new_run()
    invoke({"operation": "health"})
    run = client.get("/api/runs/current", headers=headers()).json()
    assert run["status"] == "stopped"


# The agent's key


def test_off_runtime_a_request_without_the_key_is_refused_but_health(monkeypatch):
    started = []
    monkeypatch.setattr(dispatcher, "dispatch", lambda *args: started.append(args))
    for key in [None, "the-wrong-key-of-at-least-32-characters"]:
        response = invoke(DISPATCH, key=key)
        assert response.status_code == 401
        assert response.json() == {"error": "The agent's key is missing or wrong"}
    assert invoke({"operation": "health"}, key=None).json() == {"healthy": True}
    assert started == []


def test_on_runtime_no_key_is_asked_for(on_runtime, waiting):
    finish, started = waiting
    assert invoke(DISPATCH, key=None).json() == {"accepted": True}
    finish.set()
    settle()
    assert [s[0] for s in started] == ["dispatch"]


# The payloads


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ({"operation": "start_dispatch"}, "n: Field required"),
        ({"operation": "start_dispatch", "n": 26}, "n: Input should be less than"),
        ({"operation": "start_evaluation", "n": 2, "tiny": True}, "eval_set: Field"),
        ({"operation": "resume_claim"}, "claim_id: Field required"),
        ({"operation": "health", "extra": 1}, "extra: Extra inputs"),
        ({"operation": "fly"}, "does not match any of the expected tags"),
        ({"n": 3}, "Unable to extract tag using discriminator 'operation'"),
        ([1, 2], "operation"),
    ],
)
def test_a_payload_with_an_input_missing_or_wrong_gets_400(payload, problem):
    response = invoke(payload)
    assert response.status_code == 400
    assert problem in response.json()["error"]


# Each operation, and the ping


def test_a_dispatch_is_accepted_the_ping_is_busy_and_a_second_start_is_refused(
    waiting,
):
    finish, started = waiting
    assert ping() == "Healthy"
    assert invoke(DISPATCH).json() == {"accepted": True}
    assert ping() == "HealthyBusy"
    refused = {"refused": "A dispatch is already running"}
    for payload in [DISPATCH, EVALUATION, ROUND, ANSWER]:
        assert invoke(payload).json() == refused
    finish.set()
    settle()
    assert ping() == "Healthy"
    run = client.get("/api/runs/current", headers=headers()).json()
    assert started == [("dispatch", run["run_id"], 4)]
    assert run["n"] == 4


def test_an_evaluation_is_accepted_and_refuses_a_dispatch(waiting):
    finish, started = waiting
    assert invoke(EVALUATION).json() == {"accepted": True}
    assert invoke(DISPATCH).json() == {"refused": "An evaluation is already running"}
    finish.set()
    settle()
    evaluation = client.get("/api/evaluations", headers=headers()).json()[0]
    assert (evaluation["eval_set"], evaluation["n"]) == ("held_out", 2)
    assert started == [("dispatch", evaluation["run_id"], 2)]


def test_a_round_is_accepted_and_refuses_a_dispatch(waiting):
    finish, started = waiting
    assert invoke(ROUND).json() == {"accepted": True}
    assert invoke(DISPATCH).json() == {"refused": "Round 3 is already running"}
    finish.set()
    settle()
    assert started == [("round", 3)]


def test_an_answer_is_recorded_then_carries_the_round_on(waiting, monkeypatch):
    finish, started = waiting
    sent = []
    monkeypatch.setattr(
        improver_api,
        "record_decision",
        lambda round_id, body: sent.append((round_id, body)) or recorded(200)(0, {}),
    )
    assert invoke(ANSWER).json() == {"accepted": True}
    assert ping() == "HealthyBusy"
    finish.set()
    settle()
    assert sent == [(3, {"decision": "approve", "reason": "", "handler_id": "HND-01"})]
    assert started == [("answer", 3, "approve", "")]


def test_an_answer_the_app_refuses_is_refused_and_lets_go(monkeypatch):
    monkeypatch.setattr(improver_api, "record_decision", recorded(409, "No proposal"))
    assert invoke(ANSWER).json() == {"refused": "No proposal"}
    monkeypatch.setattr(improver_api, "record_decision", recorded(404, "No round 3"))
    response = invoke(ANSWER)
    assert (response.status_code, response.json()) == (404, {"error": "No round 3"})
    assert not entrypoint.running.locked()
    assert ping() == "Healthy"


@pytest.mark.parametrize("fails", [False, True])
def test_the_ping_is_healthy_once_work_has_finished_or_failed(monkeypatch, fails):
    finish = threading.Event()

    def dispatch(run_id, n, llm=None):
        finish.wait(5)
        if fails:
            raise RuntimeError("the dispatch failed")

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    assert invoke(DISPATCH).json() == {"accepted": True}
    assert ping() == "HealthyBusy"
    finish.set()
    settle()
    assert ping() == "Healthy"
    assert not entrypoint.running.locked()


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
    assert invoke(DISPATCH).json() == {"accepted": True}
    response = invoke({"operation": "resume_claim", "claim_id": "CLM-1001"})
    assert response.json() == {"accepted": True}
    finish, _ = waiting
    finish.set()
    settle()
    assert resumed == ["CLM-1001"]
