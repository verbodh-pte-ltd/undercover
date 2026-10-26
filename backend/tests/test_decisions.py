import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

SMALL = {
    "decision": "approve",
    "payout": 420.0,
    "reason": "Covered delay.",
    "policy_section": "3. Trip Delay",
}
LARGE = {**SMALL, "payout": 1250.0}


def decide(claim_id, body):
    return client.post(f"/api/claims/{claim_id}/decision", json=body)


def pay(claim_id, amount):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def sign_off(claim_id, handler_id):
    return client.post(
        f"/api/claims/{claim_id}/sign-off", json={"handler_id": handler_id}
    )


def start(claim_id):
    run_id = client.post("/api/runs", json={"cap": 5}).json()["run_id"]
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def test_start_claim_clears_old_steps():
    client.post("/api/claims/CLM-1001/steps", json={"kind": "reply", "text": "old"})
    assert start("CLM-1001").json()["status"] == "in_progress"
    assert client.get("/api/claims/CLM-1001/steps").json() == []


def test_start_decided_claim_is_409():
    decide("CLM-1001", SMALL)
    assert start("CLM-1001").status_code == 409


def test_steps_in_order():
    for text in ["one", "two", "three"]:
        client.post(
            "/api/claims/CLM-1002/steps", json={"kind": "tool_call", "text": text}
        )
    steps = client.get("/api/claims/CLM-1002/steps").json()
    assert [s["text"] for s in steps] == ["one", "two", "three"]


def test_steps_on_unknown_claim_is_404():
    assert client.get("/api/claims/CLM-0000/steps").status_code == 404
    step = {"kind": "reply", "text": "x"}
    assert client.post("/api/claims/CLM-0000/steps", json=step).status_code == 404


def test_small_approve_is_decided():
    claim = decide("CLM-1001", SMALL).json()
    assert claim["status"] == "decided"
    assert claim["reason"] == "Covered delay."
    assert claim["policy_section"] == "3. Trip Delay"


def test_large_approve_waits_for_sign_off():
    assert decide("CLM-1001", LARGE).json()["status"] == "waiting_for_sign_off"


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_payout_on_decline_or_refer_is_422(decision):
    assert decide("CLM-1001", {**SMALL, "decision": decision}).status_code == 422
    body = {**SMALL, "decision": decision, "payout": 0}
    assert decide("CLM-1001", body).json()["status"] == "decided"


def test_deciding_twice_is_409():
    decide("CLM-1001", SMALL)
    assert decide("CLM-1001", SMALL).status_code == 409


def test_pay_small_claim():
    decide("CLM-1001", SMALL)
    assert pay("CLM-1001", 420.0).json()["paid_amount"] == 420.0


@pytest.mark.parametrize("amount", [1250.0, 1000.0, 250.0])
def test_large_payout_is_blocked_before_sign_off(amount):
    decide("CLM-1001", LARGE)
    assert pay("CLM-1001", amount).status_code == 403
    assert client.get("/api/claims/CLM-1001").json()["paid_amount"] is None


def test_wrong_amount_is_422():
    decide("CLM-1001", SMALL)
    assert pay("CLM-1001", 400.0).status_code == 422


def test_paying_twice_is_409():
    decide("CLM-1001", SMALL)
    pay("CLM-1001", 420.0)
    assert pay("CLM-1001", 420.0).status_code == 409


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_paying_declined_or_referred_is_409(decision):
    decide("CLM-1001", {**SMALL, "decision": decision, "payout": 0})
    assert pay("CLM-1001", 420.0).status_code == 409


def test_paying_undecided_is_409():
    assert pay("CLM-1001", 420.0).status_code == 409


def test_sign_off_pays():
    decide("CLM-1001", LARGE)
    claim = sign_off("CLM-1001", "HND-01").json()
    assert claim["status"] == "decided"
    assert claim["signed_off_by"] == "HND-01"
    assert claim["paid_amount"] == 1250.0
    assert pay("CLM-1001", 1250.0).status_code == 409


def test_sign_off_when_not_waiting_is_409():
    decide("CLM-1001", SMALL)
    assert sign_off("CLM-1001", "HND-01").status_code == 409


def test_sign_off_unknown_handler_is_404():
    decide("CLM-1001", LARGE)
    assert sign_off("CLM-1001", "HND-99").status_code == 404
