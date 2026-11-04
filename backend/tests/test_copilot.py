from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from backend import copilot_service, store
from backend.main import app
from backend.tests.records import record_decision
from backend.tests.signing import AGENT, DANA, MARCUS, headers, token

client = TestClient(app)
agent = TestClient(app, headers=AGENT)
LARGE = {
    "decision": "approve",
    "payout": 1250.0,
    "reason": "Covered trip delay.",
    "policy_section": "4. Trip delay",
}
STEP = {
    "key": "s1",
    "kind": "claims",
    "tool": "get_claim",
    "input": "CLM-1001",
    "state": "working",
    "seconds": None,
    "detail": None,
}
ENDED = {"model_calls": 3, "cost": 0.0042, "seconds": 12.5}


@pytest.fixture(autouse=True)
def copilot(monkeypatch):
    """A stand-in Copilot that takes every turn, keeping what it was sent."""
    sent = []
    script = {"refuse": None}

    def send(conversation_id, payload):
        sent.append((conversation_id, payload))
        if script["refuse"]:
            raise copilot_service.NotTaken(409, script["refuse"])

    monkeypatch.setattr(copilot_service, "send", send)
    return sent, script


def new_conversation(email=None):
    found = client.post("/api/conversations", headers=headers(email or DANA))
    assert found.status_code == 200
    return found.json()


def send(conversation_id, message="What needs me today?", email=None):
    return client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"message": message},
        headers=headers(email or DANA),
    )


def held_for_sign_off(claim_id="CLM-1001"):
    record_decision(
        agent, f"/api/claims/{claim_id}/decision", LARGE, headers=AGENT
    ).raise_for_status()


def turn_post(turn_id, what, body, email=None):
    return client.post(
        f"/api/copilot/turns/{turn_id}/{what}",
        json=body,
        headers=headers(email or DANA),
    )


def conversation(conversation_id):
    found = client.get(f"/api/conversations/{conversation_id}", headers=headers())
    return found.json()


def set_turn(turn_id, **values):
    with store.connect() as conn:
        conn.execute(
            update(store.turns).where(store.turns.c.id == turn_id).values(**values)
        )
        conn.commit()


def ago(minutes):
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def test_a_conversation_is_the_handlers_own_with_a_uuid():
    made = new_conversation()
    assert len(made["id"]) == 36
    assert made["title"] == "New conversation"
    other = new_conversation(MARCUS)
    listed = client.get("/api/conversations", headers=headers()).json()
    assert [c["id"] for c in listed] == [made["id"]]
    path = f"/api/conversations/{made['id']}"
    assert client.get(path, headers=headers(MARCUS)).status_code == 404
    assert send(made["id"], email=MARCUS).status_code == 404
    assert client.get(path, headers=AGENT).status_code == 401
    assert client.post("/api/conversations", headers=AGENT).status_code == 401
    assert conversation(other["id"])["detail"] == (
        f"Conversation {other['id']} not found"
    )


def test_conversations_are_listed_newest_first_and_titled_by_the_first_question():
    first = new_conversation()["id"]
    second = new_conversation()["id"]
    send(first, "Work the queue with 10 agents and tell me what needs me.")
    listed = client.get("/api/conversations", headers=headers()).json()
    assert [c["id"] for c in listed] == [second, first]
    assert listed[1]["title"] == (
        "Work the queue with 10 agents and tell me what needs me."
    )


def test_a_message_starts_a_turn_in_the_conversations_session(copilot):
    sent, _ = copilot
    made = new_conversation()
    signed_in = {"Authorization": f"Bearer {token(DANA)}"}
    turn = client.post(
        f"/api/conversations/{made['id']}/messages",
        json={"message": "  What needs me?  "},
        headers={**signed_in, "X-API-Key": ""},
    ).json()
    assert turn["state"] == "working"
    assert (turn["message"], turn["model_calls"], turn["cost"]) == (
        "What needs me?",
        0,
        0,
    )
    assert (turn["answer"], turn["card"], turn["steps"]) == (None, None, [])
    assert sent == [
        (
            made["id"],
            {
                "operation": "start_turn",
                "conversation_id": made["id"],
                "turn_id": turn["id"],
                "message": "What needs me?",
                "token": signed_in["Authorization"].split()[1],
            },
        )
    ]


def test_one_turn_at_a_time_and_a_message_after_it_ends():
    made = new_conversation()["id"]
    turn = send(made).json()
    second = send(made)
    assert second.status_code == 409
    assert (
        second.json()["detail"] == "The Copilot is still working on your last message"
    )
    finished = turn_post(turn["id"], "finish", {"answer": "Nothing.", **ENDED})
    assert finished.json()["state"] == "done"
    assert finished.json()["answer"] == "Nothing."
    assert send(made).status_code == 200


def test_a_copilot_that_didnt_take_the_turn_fails_it_and_a_message_can_follow(copilot):
    _, script = copilot
    made = new_conversation()["id"]
    script["refuse"] = "The Copilot isn't running"
    turn = send(made).json()
    assert (turn["state"], turn["error"]) == ("failed", "The Copilot isn't running")
    script["refuse"] = None
    assert send(made).json()["state"] == "working"


def test_a_copilot_that_may_have_taken_it_leaves_the_turn_working(monkeypatch):
    def unknown(conversation_id, payload):
        raise copilot_service.may_have_started()

    monkeypatch.setattr(copilot_service, "send", unknown)
    made = new_conversation()["id"]
    assert send(made).json()["state"] == "working"


def test_a_turn_working_twenty_minutes_shows_stopped_and_a_message_can_follow():
    made = new_conversation()["id"]
    turn = send(made).json()
    set_turn(turn["id"], started_at=ago(19))
    assert conversation(made)["turns"][0]["state"] == "working"
    set_turn(turn["id"], started_at=ago(21))
    assert conversation(made)["turns"][0]["state"] == "stopped"
    assert turn_post(turn["id"], "steps", STEP).status_code == 409
    assert send(made).status_code == 200
    with store.connect() as conn:
        states = conn.scalars(select(store.turns.c.state)).all()
    assert states == ["working", "working"]


def test_a_waiting_turn_is_never_stopped_and_an_answered_one_counts_from_its_answer(
    copilot,
):
    sent, _ = copilot
    held_for_sign_off()
    made = new_conversation()["id"]
    turn = send(made, "Sign off CLM-1001").json()
    turn_post(turn["id"], "pause", {"claim_id": "CLM-1001"}).raise_for_status()
    set_turn(turn["id"], started_at=ago(60 * 24))
    assert conversation(made)["turns"][0]["state"] == "waiting_for_confirmation"
    refused = send(made)
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Answer the confirmation first: sign off and pay, or cancel"
    )
    answered = client.post(
        f"/api/conversations/{made}/confirmation",
        json={"answer": "sign_off"},
        headers=headers(),
    ).json()
    assert answered["state"] == "working"
    assert answered["answered_at"] is not None
    assert sent[-1][1]["operation"] == "answer_confirmation"
    assert sent[-1][1]["answer"] == "sign_off"
    assert sent[-1][0] == made
    set_turn(turn["id"], answered_at=ago(21))
    assert conversation(made)["turns"][0]["state"] == "stopped"


def test_a_confirmation_answer_needs_one_waiting():
    made = new_conversation()["id"]
    path = f"/api/conversations/{made}/confirmation"
    assert (
        client.post(path, json={"answer": "cancel"}, headers=headers()).status_code
        == 409
    )
    send(made)
    assert (
        client.post(path, json={"answer": "cancel"}, headers=headers()).status_code
        == 409
    )
    assert (
        client.post(path, json={"answer": "maybe"}, headers=headers()).status_code
        == 422
    )


def test_the_card_is_filled_from_the_records_whatever_the_copilot_sends():
    held_for_sign_off()
    made = new_conversation()["id"]
    turn = send(made).json()
    paused = turn_post(
        turn["id"],
        "pause",
        {"claim_id": "CLM-1001", "amount": 1.0, "customer_name": "Someone else"},
    ).json()
    assert paused["state"] == "waiting_for_confirmation"
    assert paused["card"] == {
        "claim_id": "CLM-1001",
        "customer_name": "Maya Patel",
        "amount": 1250.0,
        "reason": "Covered trip delay.",
    }
    assert conversation(made)["turns"][0]["card"] == paused["card"]


def test_a_pause_is_refused_for_a_claim_not_held_or_a_handler_who_cant_sign_off():
    made = new_conversation()["id"]
    turn = send(made).json()
    refused = turn_post(turn["id"], "pause", {"claim_id": "CLM-1002"})
    assert refused.status_code == 409
    assert turn_post(turn["id"], "pause", {"claim_id": "CLM-9999"}).status_code == 404
    held_for_sign_off()
    marcus = new_conversation(MARCUS)["id"]
    his = send(marcus, email=MARCUS).json()
    refused = turn_post(his["id"], "pause", {"claim_id": "CLM-1001"}, email=MARCUS)
    assert refused.status_code == 403
    assert "can't sign off" in refused.json()["detail"]


def test_steps_are_recorded_and_updated_by_their_key():
    made = new_conversation()["id"]
    turn = send(made).json()
    first = turn_post(turn["id"], "steps", STEP).json()
    assert first["state"] == "working"
    assert first["started_at"]
    detail = {
        "asked": "CLM-1001",
        "answer": "A trip delay.",
        "file": None,
        "owner": None,
    }
    done = {**STEP, "state": "done", "seconds": 0.4, "detail": detail}
    again = turn_post(turn["id"], "steps", done).json()
    assert again == {**done, "started_at": first["started_at"]}
    turn_post(turn["id"], "steps", {**STEP, "key": "s2", "tool": "get_policy"})
    steps = conversation(made)["turns"][0]["steps"]
    assert [s["key"] for s in steps] == ["s1", "s2"]
    assert steps[0]["detail"] == detail
    bad = turn_post(turn["id"], "steps", {**STEP, "kind": "anything"})
    assert bad.status_code == 422


def test_the_copilots_routes_take_only_the_handlers_own_working_turn():
    made = new_conversation()["id"]
    turn = send(made).json()
    assert turn_post(turn["id"], "steps", STEP, email=MARCUS).status_code == 404
    assert turn_post(999, "steps", STEP).status_code == 404
    assert (
        client.post(f"/api/copilot/turns/{turn['id']}/steps", json=STEP, headers=AGENT)
    ).status_code == 401
    failed = turn_post(turn["id"], "fail", {"error": "The model failed", **ENDED})
    assert failed.json()["state"] == "failed"
    assert failed.json()["error"] == "The model failed"
    assert failed.json()["model_calls"] == 3
    again = turn_post(turn["id"], "finish", {"answer": "Late", **ENDED})
    assert again.status_code == 409
    assert again.json()["detail"] == f"Turn {turn['id']} is failed, not working"


def test_reseeding_empties_the_conversations():
    made = new_conversation()["id"]
    turn = send(made).json()
    turn_post(turn["id"], "steps", STEP)
    store.reseed()
    with store.connect() as conn:
        for table in [store.conversations, store.turns, store.copilot_steps]:
            assert conn.scalar(select(func.count()).select_from(table)) == 0


def test_the_sign_in_is_never_saved(copilot):
    _, script = copilot
    held_for_sign_off()
    signed_in = {"Authorization": f"Bearer {token(DANA)}", "X-API-Key": ""}
    made = client.post("/api/conversations", headers=signed_in).json()["id"]
    path = f"/api/conversations/{made}/messages"
    turn = client.post(path, json={"message": "Sign off"}, headers=signed_in).json()
    turn_post(turn["id"], "pause", {"claim_id": "CLM-1001"})
    script["refuse"] = "The Copilot isn't running"
    client.post(
        f"/api/conversations/{made}/confirmation",
        json={"answer": "sign_off"},
        headers=signed_in,
    )
    with store.connect() as conn:
        saved = [
            str(dict(row))
            for table in [store.conversations, store.turns, store.copilot_steps]
            for row in conn.execute(select(table)).mappings()
        ]
    assert conversation(made)["turns"][0]["state"] == "failed"
    assert not any(signed_in["Authorization"].split()[1] in row for row in saved)


def test_the_new_operations_follow_the_api_rules():
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        if not path.startswith(("/api/conversations", "/api/copilot", "/api/cust")):
            continue
        for operation in operations.values():
            assert operation["operationId"]
            assert operation["description"].count("\n") == 0
    components = schema["components"]["schemas"]
    for name in [
        "Message",
        "ConfirmationAnswer",
        "CopilotStep",
        "StepDetail",
        "Pause",
        "TurnEnd",
        "TurnFailure",
    ]:
        assert set(components[name]["required"]) == set(components[name]["properties"])
