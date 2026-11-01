import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.tests.signing import MARCUS, headers

client = TestClient(app)

LAB = {
    "approval_limit": 1000.0,
    "objectives": {"accuracy": 0.6, "grade": 1.0},
    "noise": {
        "accuracy": 0.1,
        "grade": 0.15,
        "cost_per_claim": 0.15,
        "seconds_per_claim": 0.15,
    },
    "tolerance": {
        "accuracy": 0.1,
        "grade": 0.15,
        "cost_per_claim": 1.0,
        "seconds_per_claim": 0.25,
    },
    "round_spending_limit": 0.5,
    "agents_at_once": 25,
}
LARGE = {
    "decision": "approve",
    "payout": 1250.0,
    "reason": "Covered treatment.",
    "policy_section": "7. Emergency Medical",
}


def change(sent=None, **values):
    body = {**LAB, **values}
    return client.put(
        "/api/settings", json=body, headers=headers() if sent is None else sent
    )


def test_the_settings_start_with_the_labs_values_and_the_models_allowed(monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    found = client.get("/api/settings").json()
    assert found["settings"] == LAB
    assert found["changes"] == {}
    luna = found["models_allowed"][0]
    assert luna == {
        "model": "openai/gpt-6-luna",
        "reasoning_efforts": ["none", "low", "medium"],
        "input_price": 0.1,
        "output_price": 0.5,
    }
    assert len(found["models_allowed"]) == 3


def test_with_model_name_set_that_model_is_the_only_one_allowed(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert client.get("/api/settings").json()["models_allowed"] == [
        {
            "model": "openrouter/free",
            "reasoning_efforts": ["default"],
            "input_price": 0.0,
            "output_price": 0.0,
        }
    ]


def test_a_change_records_who_made_it_and_when():
    found = change(approval_limit=2000.0, agents_at_once=10).json()
    assert found["settings"] == {**LAB, "approval_limit": 2000.0, "agents_at_once": 10}
    assert set(found["changes"]) == {"approval_limit", "agents_at_once"}
    assert found["changes"]["approval_limit"]["handler"] == "Dana Whitfield"
    assert found["changes"]["approval_limit"]["changed_at"]
    assert client.get("/api/settings").json() == found


@pytest.mark.parametrize(
    "values",
    [
        {"approval_limit": 0},
        {"objectives": {"accuracy": 1.5, "grade": 1.0}},
        {"objectives": {"accuracy": 0.6, "grade": 4}},
        {"noise": {**LAB["noise"], "cost_per_claim": -0.1}},
        {"round_spending_limit": 0},
        {"agents_at_once": 26},
        {"agents_at_once": 0},
    ],
)
def test_each_value_is_checked(values):
    assert change(**values).status_code == 422
    assert client.get("/api/settings").json()["settings"] == LAB


def test_every_setting_is_needed():
    body = {k: v for k, v in LAB.items() if k != "agents_at_once"}
    assert client.put("/api/settings", json=body, headers=headers()).status_code == 422


def test_a_change_needs_a_sign_in():
    assert change(sent={}, approval_limit=2000.0).status_code == 401
    assert client.get("/api/settings").json()["settings"] == LAB


def test_marcus_cant_change_a_setting():
    response = change(sent=headers(MARCUS), approval_limit=2000.0)
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Marcus Oyelaran can't change the settings. Only a handler who can sign off"
        " payouts over the approval limit can"
    )
    assert client.get("/api/settings").json()["changes"] == {}


def test_a_new_approval_limit_applies_to_the_next_decision():
    client.post("/api/claims/CLM-1001/decision", json=LARGE)
    change(approval_limit=2000.0)
    client.post("/api/claims/CLM-1002/decision", json=LARGE)
    held = client.get("/api/claims/CLM-1001").json()
    assert held["status"] == "waiting_for_sign_off"
    pay = {"amount": 1250.0}
    assert client.post("/api/claims/CLM-1001/payment", json=pay).status_code == 403
    assert client.get("/api/claims/CLM-1002").json()["status"] == "decided"
    paid = client.post("/api/claims/CLM-1002/payment", json=pay).json()
    assert paid["paid_amount"] == 1250.0


def test_a_lower_limit_holds_the_next_decision_only():
    small = {**LARGE, "payout": 420.0}
    client.post("/api/claims/CLM-1001/decision", json=small)
    change(approval_limit=100.0)
    client.post("/api/claims/CLM-1002/decision", json=small)
    pay = {"amount": 420.0}
    assert client.post("/api/claims/CLM-1001/payment", json=pay).status_code == 200
    assert client.post("/api/claims/CLM-1002/payment", json=pay).status_code == 403


def test_the_settings_follow_the_api_rules():
    paths = client.get("/openapi.json").json()["paths"]["/api/settings"]
    assert paths["get"]["operationId"] == "get_settings"
    assert paths["put"]["operationId"] == "change_settings"
    for operation in paths.values():
        assert operation["description"].count("\n") == 0
