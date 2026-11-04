import importlib
import os
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import copilot, copilot_api, copilot_entrypoint, entrypoint, main
from agent.tests.copilot_standin import DANA, StandIn
from agent.tests.test_copilot import CONVERSATION, stub
from agent.tests.test_entrypoint import CREDENTIALS, Identity

EXPIRED = "a-sign-in-the-app-no-longer-accepts"  # noqa: S105


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    """Each test is a new process: the Copilot hasn't got ready yet."""
    monkeypatch.setattr(copilot_entrypoint, "ready", threading.Event())
    for name in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture(autouse=True)
def standin(monkeypatch):
    """The app's routes in process, which the sign-in check asks first."""
    found = StandIn()
    found.turn(1)
    monkeypatch.setattr(
        copilot_api,
        "client",
        lambda token: TestClient(found.app, headers=copilot_api.headers(token)),
    )
    return found


def invoke(payload):
    """Post a payload to the Copilot's AgentCore app, with no key."""
    return TestClient(copilot_entrypoint.app).post("/invocations", json=payload)


def ping():
    return TestClient(copilot_entrypoint.app).get("/ping").json()["status"]


def settle():
    """Wait until the Copilot's ping says its turn has ended."""
    for _ in range(3000):
        if ping() == "Healthy":
            return
        time.sleep(0.01)
    raise AssertionError("the Copilot is still working")


def start(turn_id=1, token=DANA):
    return {
        "operation": "start_turn",
        "conversation_id": CONVERSATION,
        "turn_id": turn_id,
        "message": "What needs me?",
        "token": token,
    }


def test_importing_the_copilot_opens_nothing():
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
            patch.setattr(copilot_api, "client", lambda token: opened.append("app"))
            importlib.reload(copilot_entrypoint)
            importlib.reload(importlib.import_module("runtime.copilot"))
            assert opened == []
            assert not copilot_entrypoint.ready.is_set()
    finally:
        importlib.reload(copilot_entrypoint)
        importlib.reload(importlib.import_module("runtime.copilot"))


def test_health_answers_with_no_key():
    assert invoke({"operation": "health"}).json() == {"healthy": True}


def test_on_runtime_only_its_two_credentials_are_fetched(monkeypatch):
    identity = Identity()
    for name, provider in CREDENTIALS.items():
        monkeypatch.setenv(name, provider)
    for setting in ["OPENROUTER_API_KEY", "DATABASE_URL"]:
        monkeypatch.setenv(setting, "from .env")
    monkeypatch.setattr(entrypoint, "requires_api_key", identity.requires_api_key)
    opened = []
    monkeypatch.setattr(main, "pool", lambda: opened.append(os.environ["DATABASE_URL"]))
    assert invoke({"operation": "health"}).json() == {"healthy": True}
    assert invoke({"operation": "health"}).json() == {"healthy": True}
    assert sorted(identity.fetched) == ["undercover-database", "undercover-openrouter"]
    assert opened == ["undercover-database-value"]
    assert os.environ["OPENROUTER_API_KEY"] == "undercover-openrouter-value"


def test_off_runtime_nothing_is_fetched(monkeypatch):
    identity = Identity()
    monkeypatch.setattr(entrypoint, "requires_api_key", identity.requires_api_key)
    invoke({"operation": "health"})
    assert identity.fetched == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"token": None}, "token: Input should be a valid string"),
        ({"conversation_id": "too-short"}, "conversation_id: String should match"),
        ({"turn_id": "x"}, "turn_id: Input should be a valid integer"),
        ({"operation": "pay"}, "does not match any of the expected tags"),
    ],
)
def test_a_payload_with_an_input_missing_or_wrong_gets_400(change, problem):
    response = invoke({**start(), **change})
    assert response.status_code == 400
    assert problem in response.json()["error"]
    assert DANA not in response.text


def test_an_answer_must_be_sign_off_or_cancel():
    payload = {**start(), "operation": "answer_confirmation", "answer": "maybe"}
    del payload["message"]
    response = invoke(payload)
    assert response.status_code == 400
    assert "answer: Input should be 'sign_off' or 'cancel'" in response.json()["error"]


@pytest.mark.parametrize(
    ("operation", "extra"),
    [("start_turn", {}), ("answer_confirmation", {"answer": "cancel"})],
)
def test_a_turn_is_accepted_at_once_and_the_ping_is_busy_until_it_ends(
    monkeypatch, operation, extra
):
    finish = threading.Event()
    worked = []

    def work(*args):
        worked.append(args)
        finish.wait(5)

    monkeypatch.setattr(copilot, operation, work)
    payload = {**start(), "operation": operation, **extra}
    if extra:
        del payload["message"]
    assert ping() == "Healthy"
    assert invoke(payload).json() == {"accepted": True}
    assert ping() == "HealthyBusy"
    finish.set()
    settle()
    assert worked == [(CONVERSATION, 1, extra.get("answer", "What needs me?"), DANA)]


def test_the_ping_is_healthy_after_a_turn_that_raised(monkeypatch):
    def work(*args):
        raise RuntimeError("the app went away")

    monkeypatch.setattr(copilot, "start_turn", work)
    assert invoke(start()).json() == {"accepted": True}
    settle()


@pytest.mark.parametrize(
    ("operation", "extra"),
    [("start_turn", {}), ("answer_confirmation", {"answer": "sign_off"})],
)
def test_a_refused_sign_in_is_refused_at_once_with_nothing_started(
    standin, monkeypatch, operation, extra
):
    built = []
    monkeypatch.setattr(copilot, "model", lambda configuration: built.append(1))
    monkeypatch.setattr(copilot, operation, lambda *args: built.append(args))
    payload = {**start(token=EXPIRED), "operation": operation, **extra}
    if extra:
        del payload["message"]
    assert invoke(payload).json() == {
        "refused": "The app didn't accept the sign-in: Your sign-in has expired."
        " Sign in again."
    }
    assert ping() == "Healthy"
    assert built == []
    assert [r["path"] for r in standin.requests] == ["/api/handlers/me"]


def test_an_app_that_cant_be_reached_refuses_the_turn(monkeypatch):
    def unreachable(token):
        raise httpx.ConnectError(f"no app for {token}")

    monkeypatch.setattr(copilot_api, "signed_in_handler", unreachable)
    monkeypatch.setattr(copilot, "start_turn", lambda *args: None)
    refused = invoke(start()).json()["refused"]
    assert refused == "The Copilot couldn't reach the app: no app for [the sign-in]"
    assert ping() == "Healthy"


def test_a_turn_through_the_app_finishes(standin, monkeypatch):
    monkeypatch.setattr(copilot, "memory_store", lambda: None)
    model = stub(AIMessage(content="Nothing needs you."))
    monkeypatch.setattr(copilot, "model", lambda configuration: model)
    assert invoke(start()).json() == {"accepted": True}
    settle()
    assert standin.turns[1]["state"] == "done"
    assert standin.turns[1]["answer"] == "Nothing needs you."
