import pytest
from fastapi.testclient import TestClient

from backend import main
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


def test_the_same_decision_again_counts_as_done():
    decide("CLM-1001", SMALL)
    again = decide("CLM-1001", SMALL)
    assert again.status_code == 200
    assert (again.json()["decision"], again.json()["payout"]) == ("approve", 420.0)


def test_a_different_decision_is_409():
    decide("CLM-1001", SMALL)
    assert decide("CLM-1001", {**SMALL, "payout": 300.0}).status_code == 409
    declined = {**SMALL, "decision": "decline", "payout": 0}
    assert decide("CLM-1001", declined).status_code == 409


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


def test_the_same_payment_again_counts_as_done_and_pays_once():
    decide("CLM-1001", SMALL)
    first = pay("CLM-1001", 420.0).json()
    again = pay("CLM-1001", 420.0)
    assert again.status_code == 200
    assert again.json()["paid_at"] == first["paid_at"]
    assert again.json()["paid_amount"] == 420.0


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_paying_declined_or_referred_is_409(decision):
    decide("CLM-1001", {**SMALL, "decision": decision, "payout": 0})
    assert pay("CLM-1001", 420.0).status_code == 409


def test_paying_undecided_is_409():
    assert pay("CLM-1001", 420.0).status_code == 409


def test_sign_off_records_who_and_when_and_pays_nothing():
    decide("CLM-1001", LARGE)
    claim = sign_off("CLM-1001", "HND-01").json()
    assert claim["status"] == "in_progress"
    assert claim["signed_off_by"] == "HND-01"
    assert claim["signed_off_at"]
    assert claim["paid_amount"] is None


def test_payment_after_sign_off_pays_and_decides():
    decide("CLM-1001", LARGE)
    sign_off("CLM-1001", "HND-01")
    claim = pay("CLM-1001", 1250.0).json()
    assert (claim["status"], claim["paid_amount"]) == ("decided", 1250.0)
    assert claim["paid_at"]
    assert claim["signed_off_by"] == "HND-01"


def test_a_signed_off_claim_cannot_be_released():
    decide("CLM-1001", LARGE)
    sign_off("CLM-1001", "HND-01")
    release = client.post("/api/claims/CLM-1001/release", json={"error": "boom"})
    assert release.status_code == 409


def test_sign_off_asks_the_agent_to_carry_on(monkeypatch):
    calls = []
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test")
    monkeypatch.setattr(main.httpx, "post", lambda url, **kw: calls.append(url))
    decide("CLM-1001", LARGE)
    sign_off("CLM-1001", "HND-01")
    assert calls == ["http://agent.test/claims/CLM-1001/resume"]


def test_sign_off_stands_when_the_agent_cannot_be_reached():
    decide("CLM-1001", LARGE)
    response = sign_off("CLM-1001", "HND-01")
    assert response.status_code == 200
    assert client.get("/api/claims/CLM-1001").json()["signed_off_by"] == "HND-01"


def test_sign_off_when_not_waiting_is_409():
    decide("CLM-1001", SMALL)
    assert sign_off("CLM-1001", "HND-01").status_code == 409


def test_sign_off_unknown_handler_is_404():
    decide("CLM-1001", LARGE)
    assert sign_off("CLM-1001", "HND-99").status_code == 404


def test_start_gives_a_thread_that_a_second_start_keeps():
    run_id = client.post("/api/runs", json={"cap": 5}).json()["run_id"]
    first = client.post("/api/claims/CLM-1001/start", json={"run_id": run_id})
    assert first.json()["thread_id"] == "CLM-1001-1"
    again = client.post("/api/claims/CLM-1001/start", json={"run_id": run_id})
    assert again.json()["thread_id"] == "CLM-1001-1"
    assert again.json()["attempts"] == 0


def test_a_released_claim_gets_a_new_thread():
    start("CLM-1001")
    client.post("/api/claims/CLM-1001/release", json={"error": "boom"})
    assert client.get("/api/claims/CLM-1001").json()["thread_id"] is None
    assert start("CLM-1001").json()["thread_id"] == "CLM-1001-2"
