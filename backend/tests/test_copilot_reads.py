import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from backend import agent_service, store
from backend.main import app
from backend.tests.records import new_run, record_decision
from backend.tests.signing import AGENT, headers

client = TestClient(app)
agent = TestClient(app, headers=AGENT)
LARGE = {
    "decision": "approve",
    "payout": 1250.0,
    "reason": "Covered trip delay.",
    "policy_section": "4. Trip delay",
}


@pytest.fixture(autouse=True)
def no_agent(monkeypatch):
    monkeypatch.setattr(agent_service, "send", lambda payload: None)


def read(path):
    found = client.get(path, headers=headers())
    assert found.status_code == 200, found.text
    return found.json()


def test_customers_are_found_by_part_of_their_name_in_any_case():
    assert read("/api/customers?name=maya pat") == [
        {
            "customer_id": "CUS-3001",
            "name": "Maya Patel",
            "email": "maya.patel@example.com",
        }
    ]
    assert read("/api/customers?name=Nobody Here") == []
    assert client.get("/api/customers", headers=headers()).status_code == 422


def test_a_customer_has_their_policies_and_earlier_and_queue_claims():
    record_decision(
        agent,
        "/api/claims/CLM-1001/decision",
        {**LARGE, "payout": 300.0},
    ).raise_for_status()
    found = read("/api/customers/CUS-3001")
    assert (found["name"], found["email"]) == ("Maya Patel", "maya.patel@example.com")
    assert [p["policy_id"] for p in found["policies"]][0] == "POL-2001"
    assert {p["customer_id"] for p in found["policies"]} == {"CUS-3001"}
    assert found["claims"][0] == {
        "claim_id": "CLM-0412",
        "status": "approved",
        "submitted_at": "2024-08-19",
        "payout": 140.0,
    }
    assert {
        "claim_id": "CLM-1001",
        "status": "decided",
        "submitted_at": "2026-06-24",
        "payout": 300.0,
    } in found["claims"]
    assert client.get("/api/customers/CUS-0000", headers=headers()).status_code == 404


def test_a_customer_shows_no_evaluation_copies():
    new_run("evaluation", "evaluation", tiny=False)
    claims = read("/api/customers/CUS-3001")["claims"]
    assert not [c for c in claims if c["claim_id"].startswith("E")]


def test_success_counts_the_queues_payouts_over_the_limit_and_their_sign_offs():
    assert read("/api/success-counts") == {
        "approval_limit": 1000.0,
        "paid_over_limit": 0,
        "signed_off": 0,
    }
    record_decision(agent, "/api/claims/CLM-1001/decision", LARGE).raise_for_status()
    signed = client.post("/api/claims/CLM-1001/sign-off", headers=headers())
    assert signed.status_code == 200
    with store.connect() as conn:
        conn.execute(
            update(store.claims)
            .where(store.claims.c.claim_id == "CLM-1002")
            .values(paid_amount=1500.0)
        )
        conn.commit()
    assert read("/api/success-counts") == {
        "approval_limit": 1000.0,
        "paid_over_limit": 2,
        "signed_off": 1,
    }


def test_a_run_is_open_to_a_handler_with_an_evaluations_measures_and_no_labels():
    run_id = new_run("evaluation", "held_out", tiny=True)
    found = read(f"/api/runs/{run_id}")
    assert found["status"] == "running"
    assert found["summary"]["approved"] == 0
    assert found["measures"]["straight_through"] == 0
    assert set(found["measures"]) >= {"accuracy", "straight_through", "grade"}
    with store.connect() as conn:
        labels = conn.scalars(
            select(store.answer_key.c.reason).where(
                store.answer_key.c.eval_set == "held_out", store.answer_key.c.tiny
            )
        ).all()
    text = json.dumps(found)
    assert labels
    assert not any(label in text for label in labels)
    assert '"handler' not in text
    dispatch = new_run()
    assert read(f"/api/runs/{dispatch}")["measures"] is None
    assert agent.get(f"/api/runs/{dispatch}").status_code == 200


def test_starting_a_dispatch_or_an_evaluation_gives_the_runs_id():
    started = client.post("/api/dispatches", json={"n": 3}, headers=headers())
    assert started.json()["run_id"] == read("/api/runs/current")["run_id"]
    client.post(f"/api/runs/{started.json()['run_id']}/finish", headers=AGENT)
    evaluation = client.post(
        "/api/evaluations",
        json={"n": 3, "eval_set": "held_out", "tiny": False},
        headers=headers(),
    )
    assert read(f"/api/runs/{evaluation.json()['run_id']}")["kind"] == "evaluation"


def test_the_copilots_configuration_names_its_model_and_instructions(monkeypatch):
    found = read("/api/copilot/configuration")
    assert (found["model"], found["reasoning_effort"]) == ("openai/gpt-6-luna", "low")
    assert found["instructions"].startswith("You are the UnderCover Copilot.")
    assert "70%" in found["instructions"]
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    found = read("/api/copilot/configuration")
    assert (found["model"], found["reasoning_effort"]) == (
        "openrouter/free",
        "default",
    )
