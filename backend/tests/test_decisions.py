import pytest
from fastapi.testclient import TestClient

from backend import agent_service
from backend.main import app
from backend.tests.records import calculated, record_decision
from backend.tests.signing import AGENT, headers

client = TestClient(app, headers=AGENT)

SMALL = {
    "decision": "approve",
    "payout": 420.0,
    "reason": "Covered delay.",
    "policy_section": "3. Trip Delay",
}
LARGE = {**SMALL, "payout": 1250.0}


def decide(claim_id, body):
    return record_decision(client, f"/api/claims/{claim_id}/decision", body)


def pay(claim_id, amount):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def sign_off(claim_id):
    return client.post(f"/api/claims/{claim_id}/sign-off", headers=headers())


def start(claim_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": None})


def test_start_claim_clears_old_steps():
    client.post("/api/claims/CLM-1001/steps", json={"kind": "reply", "text": "old"})
    assert start("CLM-1001").json()["status"] == "in_progress"
    assert client.get("/api/claims/CLM-1001/steps", headers=headers()).json() == []


def test_start_decided_claim_is_409():
    decide("CLM-1001", SMALL)
    assert start("CLM-1001").status_code == 409


def test_steps_in_order():
    for text in ["one", "two", "three"]:
        client.post(
            "/api/claims/CLM-1002/steps", json={"kind": "tool_call", "text": text}
        )
    steps = client.get("/api/claims/CLM-1002/steps", headers=headers()).json()
    assert [s["text"] for s in steps] == ["one", "two", "three"]


def test_steps_on_unknown_claim_is_404():
    assert (
        client.get("/api/claims/CLM-0000/steps", headers=headers()).status_code == 404
    )
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


@pytest.mark.parametrize("claim_id", ["CLM-1001", "CLM-0000"])
def test_a_payment_over_the_limit_is_refused_before_the_claim_is_read(claim_id):
    response = pay(claim_id, 1000.01)
    assert response.status_code == 403
    assert response.json() == {
        "detail": "A payment over the approval limit of $1,000.00 is made by a"
        " handler's sign-off"
    }


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


def test_sign_off_pays_the_claim_and_decides_it():
    decide("CLM-1001", LARGE)
    claim = sign_off("CLM-1001").json()
    assert (claim["status"], claim["paid_amount"]) == ("decided", 1250.0)
    assert claim["signed_off_by"] == "HND-01"
    assert claim["signed_off_at"]
    assert claim["paid_at"] == claim["signed_off_at"]
    assert client.get("/api/claims/CLM-1001").json() == claim


def test_a_second_sign_off_is_refused_and_pays_nothing_more():
    decide("CLM-1001", LARGE)
    first = sign_off("CLM-1001").json()
    assert sign_off("CLM-1001").status_code == 409
    assert pay("CLM-1001", 1250.0).status_code == 403
    assert client.get("/api/claims/CLM-1001").json() == first


def test_a_signed_off_claim_cannot_be_released():
    decide("CLM-1001", LARGE)
    sign_off("CLM-1001")
    release = client.post("/api/claims/CLM-1001/release", json={"error": "boom"})
    assert release.status_code == 409


def test_sign_off_asks_the_agent_to_carry_on(monkeypatch):
    calls = []
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test")
    monkeypatch.setattr(
        agent_service.httpx, "post", lambda url, **kw: calls.append(url)
    )
    decide("CLM-1001", LARGE)
    sign_off("CLM-1001")
    assert calls == ["http://agent.test/claims/CLM-1001/resume"]


def test_sign_off_stands_when_the_agent_cannot_be_reached():
    decide("CLM-1001", LARGE)
    response = sign_off("CLM-1001")
    assert response.status_code == 200
    assert client.get("/api/claims/CLM-1001").json()["signed_off_by"] == "HND-01"


def test_sign_off_when_not_waiting_is_409():
    decide("CLM-1001", SMALL)
    assert sign_off("CLM-1001").status_code == 409


def test_start_gives_a_thread_that_a_second_start_keeps():
    first = start("CLM-1001")
    assert first.json()["thread_id"] == "CLM-1001-1"
    again = start("CLM-1001")
    assert again.json()["thread_id"] == "CLM-1001-1"
    assert again.json()["attempts"] == 0


def test_a_released_claim_gets_a_new_thread():
    start("CLM-1001")
    client.post("/api/claims/CLM-1001/release", json={"error": "boom"})
    assert client.get("/api/claims/CLM-1001").json()["thread_id"] is None
    assert start("CLM-1001").json()["thread_id"] == "CLM-1001-2"


def hold(claim_id, sent=AGENT):
    return TestClient(app).post(f"/api/claims/{claim_id}/hold", headers=sent)


def test_hold_moves_an_approved_unpaid_claim_to_waiting_for_sign_off():
    decide("CLM-1001", SMALL)
    held = hold("CLM-1001").json()
    assert (held["status"], held["held_by_gateway"]) == ("waiting_for_sign_off", True)
    assert pay("CLM-1001", 420.0).status_code == 403
    signed = sign_off("CLM-1001").json()
    assert (signed["status"], signed["paid_amount"]) == ("decided", 420.0)


def test_hold_needs_the_agents_key():
    decide("CLM-1001", SMALL)
    assert hold("CLM-1001", {}).status_code == 401
    assert hold("CLM-1001", headers()).status_code == 401


def test_hold_refuses_a_declined_a_paid_or_a_waiting_claim():
    decide("CLM-1001", {**SMALL, "decision": "decline", "payout": 0})
    decide("CLM-1002", SMALL)
    pay("CLM-1002", 420.0)
    decide("CLM-1003", LARGE)
    for claim_id in ["CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004"]:
        assert hold(claim_id).status_code == 409, claim_id
    assert client.get("/api/claims/CLM-1003").json()["held_by_gateway"] is False


# Claims manual section 14


def raw(claim_id, body):
    """Post a decision as it is, with no calculation put on file first."""
    return client.post(f"/api/claims/{claim_id}/decision", json=body)


MADE = {**SMALL, "decided_by": "UnderCover Agent, version 1"}
SHORT = [
    ({**MADE, "reason": " "}, "a decision record needs a reason in plain language"),
    ({**MADE, "policy_section": ""}, "a decision record needs the section relied on"),
    ({**MADE, "decided_by": ""}, "a decision record needs who or what made the"),
    ({**MADE, "payout": 0}, "an approval pays more than zero"),
    ({**MADE, "decision": "decline"}, "only an approved claim has a payout above"),
    ({**MADE, "decision": "refer"}, "only an approved claim has a payout above"),
]


@pytest.mark.parametrize(("body", "missing"), SHORT)
@pytest.mark.parametrize("claim_id", ["CLM-1001", "CLM-0000"])
def test_a_record_short_of_section_14_is_refused_on_any_claim(body, missing, claim_id):
    response = raw(claim_id, body)
    assert response.status_code == 422
    assert response.json()["detail"].startswith(f"Claims manual section 14: {missing}")


def test_an_approval_needs_a_calculation_on_file_for_this_attempt():
    detail = raw("CLM-1001", MADE).json()["detail"]
    assert detail.startswith("Claims manual section 14: an approval needs its")
    line = {
        "description": "Medical bill",
        "amount": 470,
        "quantity": 1,
        "kind": "expense",
        "nights": 0,
        "hours": 0,
    }
    body = {"benefit": "emergency_medical", "lines": [line], "paid_by_others": 0}
    assert client.post("/api/claims/CLM-1001/payout", json=body).json()["payout"] == 420
    wrong = raw("CLM-1001", {**MADE, "payout": 470.0})
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == (
        "Claims manual section 14: the payout must be the calculation's, $420.00"
    )
    approved = raw("CLM-1001", MADE).json()
    assert approved["calculation"]["payout"] == 420
    assert approved["calculation"]["lines"] == [
        {"step": "Medical bill", "amount": 470.0},
        {"step": "Less the deductible", "amount": -50.0},
    ]


def test_a_new_attempt_or_a_release_clears_the_calculation():
    calculated("CLM-1001", 420.0)
    start("CLM-1001")
    assert raw("CLM-1001", MADE).status_code == 422
    calculated("CLM-1001", 420.0)
    client.post("/api/claims/CLM-1001/release", json={"error": "boom"})
    start("CLM-1001")
    assert raw("CLM-1001", MADE).status_code == 422
    calculated("CLM-1001", 420.0)
    # The same attempt carried on keeps it.
    start("CLM-1001")
    assert raw("CLM-1001", MADE).status_code == 200


def test_the_record_holds_the_maker_the_time_and_the_manuals_version():
    approved = decide("CLM-1001", SMALL).json()
    assert approved["decided_by"] == "UnderCover Agent, version 1"
    assert approved["decided_at"]
    assert approved["manual_version"] == "2026.2"
    assert approved["calculation"]["payout"] == 420.0
    declined = decide("CLM-1002", {**SMALL, "decision": "decline", "payout": 0})
    assert declined.json()["calculation"] is None
    referred = {
        **SMALL,
        "decision": "refer",
        "payout": 0,
        "decided_by": "Security check",
    }
    found = decide("CLM-1003", referred).json()
    assert (found["decided_by"], found["calculation"]) == ("Security check", None)
    assert found["manual_version"] == "2026.2"
