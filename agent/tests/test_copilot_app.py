"""The Copilot and the app joined: a handler's message goes through the real app to
the Copilot's AgentCore app, and the Copilot reports back to the real app with the
handler's sign-in. Only the model is a stub, and the conversation is saved in memory."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import copilot, copilot_api, copilot_entrypoint, main
from agent.tests.test_copilot import calls, stub
from backend import copilot_service
from backend.main import app
from backend.tests.records import record_decision
from backend.tests.signing import AGENT, DANA, headers

client = TestClient(app)
HELD = {
    "decision": "approve",
    "payout": 1250.0,
    "reason": "Covered trip delay.",
    "policy_section": "4. Trip delay",
}


def forbidden():
    raise AssertionError("The Copilot used the claim agent's client")


@pytest.fixture
def joined(monkeypatch):
    """The Copilot's client on the real app, the app's calls to the Copilot in
    process, and every path the Copilot posts to, in order."""
    posted: list[str] = []

    def to_app(token):
        return TestClient(app, headers=copilot_api.headers(token))

    def post(token, path, body=None):
        posted.append(path)
        with to_app(token) as found:
            return found.post(path, json=body)

    monkeypatch.setattr(copilot_api, "client", to_app)
    monkeypatch.setattr(copilot_api, "post", post)
    monkeypatch.setattr(copilot_service, "over_http", copilot_entrypoint.invoke)
    monkeypatch.setattr(copilot_entrypoint, "ready", threading.Event())
    saved = InMemorySaver()
    monkeypatch.setattr(copilot, "saver", lambda: saved)
    monkeypatch.setattr(copilot, "memory_store", lambda: None)
    monkeypatch.setattr(main, "backend", forbidden)
    monkeypatch.delenv("UNDERCOVER_GATEWAY_URL", raising=False)
    return posted


def with_model(monkeypatch, *replies):
    model = stub(*replies)
    monkeypatch.setattr(copilot, "model", lambda configuration: model)
    return model


def settle():
    """Wait until the Copilot's ping says the turn it was working has ended."""
    ping = TestClient(copilot_entrypoint.app)
    for _ in range(3000):
        if ping.get("/ping").json()["status"] == "Healthy":
            return
        time.sleep(0.01)
    raise AssertionError("the Copilot is still working")


def held_for_sign_off(claim_id="CLM-1001"):
    path = f"/api/claims/{claim_id}/decision"
    record_decision(client, path, HELD, headers=AGENT).raise_for_status()


def new_conversation():
    found = client.post("/api/conversations", headers=headers(DANA))
    found.raise_for_status()
    return found.json()["id"]


def latest_turn(conversation_id):
    found = client.get(f"/api/conversations/{conversation_id}", headers=headers(DANA))
    found.raise_for_status()
    return found.json()["turns"][-1]


def ask(conversation_id, message):
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"message": message},
        headers=headers(DANA),
    )
    sent.raise_for_status()
    settle()
    return latest_turn(conversation_id)


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}", headers=headers(DANA)).json()


def test_a_lookup_turn_through_the_app_ends_done_with_its_steps(joined, monkeypatch):
    held_for_sign_off()
    with_model(
        monkeypatch,
        calls(("get_claim", {"claim_id": "CLM-1001"})),
        AIMessage(content="**CLM-1001** waits for your sign-off."),
    )
    made = new_conversation()
    found = ask(made, "Why does CLM-1001 need me?")
    assert found["state"] == "done", found["error"]
    assert found["answer"] == "**CLM-1001** waits for your sign-off."
    assert (found["model_calls"], found["error"]) == (2, None)
    assert found["seconds"] is not None
    [step] = found["steps"]
    assert (step["key"], step["kind"], step["tool"], step["input"]) == (
        "1-call-0",
        "claims",
        "get_claim",
        "CLM-1001",
    )
    assert step["state"] == "done"
    assert "waiting_for_sign_off" in step["detail"]["answer"]
    assert joined == [
        "/api/copilot/turns/1/steps",
        "/api/copilot/turns/1/steps",
        "/api/copilot/turns/1/finish",
    ]


def test_a_sign_off_pauses_with_the_card_and_the_answer_pays_once(joined, monkeypatch):
    held_for_sign_off()
    with_model(
        monkeypatch,
        calls(("sign_off", {"claim_id": "CLM-1001"})),
        AIMessage(content="CLM-1001 is signed off and paid $1,250.00."),
    )
    made = new_conversation()
    paused = ask(made, "Sign off CLM-1001.")
    assert paused["state"] == "waiting_for_confirmation", paused["error"]
    assert paused["card"] == {
        "claim_id": "CLM-1001",
        "customer_name": "Maya Patel",
        "amount": 1250.0,
        "reason": "Covered trip delay.",
    }
    assert claim("CLM-1001")["status"] == "waiting_for_sign_off"
    answered = client.post(
        f"/api/conversations/{made}/confirmation",
        json={"answer": "sign_off"},
        headers=headers(DANA),
    )
    answered.raise_for_status()
    settle()
    found = latest_turn(made)
    assert found["state"] == "done", found["error"]
    assert found["answer"] == "CLM-1001 is signed off and paid $1,250.00."
    paid = claim("CLM-1001")
    assert (paid["status"], paid["paid_amount"], paid["signed_off_by"]) == (
        "decided",
        1250.0,
        "HND-01",
    )
    assert joined.count("/api/claims/CLM-1001/sign-off") == 1
    assert [(s["tool"], s["state"]) for s in found["steps"]] == [("sign_off", "done")]


def test_a_sign_in_the_app_refuses_fails_the_turn_with_the_reason(joined, monkeypatch):
    model = with_model(monkeypatch, AIMessage(content="Never sent."))
    made = new_conversation()
    # The app checks the sign-in itself first; the Copilot's check then refuses it,
    # as when the sign-in expires between the two.
    monkeypatch.setattr(
        copilot_api,
        "client",
        lambda token: TestClient(app, headers=copilot_api.headers("expired")),
    )
    sent = client.post(
        f"/api/conversations/{made}/messages",
        json={"message": "What needs me?"},
        headers=headers(DANA),
    ).json()
    assert sent["state"] == "failed"
    assert sent["error"].startswith("The app didn't accept the sign-in: ")
    assert latest_turn(made)["state"] == "failed"
    assert model.sent == []
    assert joined == []
