from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from backend import agent_service, store
from backend.main import app
from backend.tests.records import record_decision
from backend.tests.signing import AGENT, MARCUS, headers

client = TestClient(app, headers=AGENT)
LARGE = {
    "decision": "approve",
    "payout": 1250.0,
    "reason": "Covered treatment.",
    "policy_section": "7. Emergency Medical",
}
NOTE = "The bill includes a hotel night. Check section 7 again."


def decide(claim_id, body=LARGE):
    return record_decision(client, f"/api/claims/{claim_id}/decision", body)


def send_back(claim_id, note=NOTE, email=None):
    sign_in = headers(email) if email else headers()
    return client.post(
        f"/api/claims/{claim_id}/send-back", json={"note": note}, headers=sign_in
    )


def sign_off(claim_id):
    return client.post(f"/api/claims/{claim_id}/sign-off", headers=headers())


def take(claim_id):
    return client.post(f"/api/claims/{claim_id}/take")


def start(claim_id, run_id=None):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def dispatch():
    body = {
        "kind": "dispatch",
        "n": 3,
        "eval_set": None,
        "tiny": False,
        "model": "stub",
        "effort": "none",
        "version": 1,
    }
    return client.post("/api/runs", json=body).json()["run_id"]


def heard(claim_id, minutes_ago):
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    with store.connect() as conn:
        conn.execute(
            update(store.claims)
            .where(store.claims.c.claim_id == claim_id)
            .values(resume_heard_at=at.isoformat(timespec="seconds"))
        )
        conn.commit()


def test_a_send_back_keeps_the_decision_with_its_note_and_clears_the_claims():
    start("CLM-1001")
    decide("CLM-1001")
    held = claim("CLM-1001")
    sent = send_back("CLM-1001")
    assert sent.status_code == 200
    found = sent.json()
    assert found["status"] == "sent_back"
    for field in ["decision", "payout", "reason", "policy_section", "calculation"]:
        assert found[field] is None, field
    assert (found["decided_by"], found["held_by_gateway"]) == (None, False)
    assert found["thread_id"] == held["thread_id"]
    [kept] = found["sent_back"]
    assert {k: kept[k] for k in ["decision", "payout", "reason", "policy_section"]} == {
        "decision": "approve",
        "payout": 1250.0,
        "reason": "Covered treatment.",
        "policy_section": "7. Emergency Medical",
    }
    assert kept["calculation"] == held["calculation"]
    assert (kept["decided_by"], kept["decided_at"]) == (
        held["decided_by"],
        held["decided_at"],
    )
    assert (kept["note"], kept["sent_back_by"], kept["sent_back_by_name"]) == (
        NOTE,
        "HND-01",
        "Dana Whitfield",
    )
    assert kept["sent_back_at"]
    steps = client.get("/api/claims/CLM-1001/steps", headers=headers()).json()
    assert steps[-1]["kind"] == "sent_back"
    assert steps[-1]["text"] == f"Sent back by Dana Whitfield: {NOTE}"
    # Nothing can pay the decision sent back.
    payment = client.post("/api/claims/CLM-1001/payment", json={"amount": 420})
    assert payment.status_code == 409


def test_a_claim_can_be_sent_back_more_than_once_newest_first():
    decide("CLM-1001")
    send_back("CLM-1001", "First look.")
    take("CLM-1001")
    decide("CLM-1001", {**LARGE, "payout": 1100.0})
    send_back("CLM-1001", "Second look.")
    found = claim("CLM-1001")["sent_back"]
    assert [(s["note"], s["payout"]) for s in found] == [
        ("Second look.", 1100.0),
        ("First look.", 1250.0),
    ]


def test_a_send_back_needs_a_handler_who_can_sign_off_over_the_limit():
    decide("CLM-1001")
    refused = send_back("CLM-1001", email=MARCUS)
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "Marcus Oyelaran can't send back payouts over the approval limit"
    )
    assert claim("CLM-1001")["status"] == "waiting_for_sign_off"


def test_a_send_back_is_refused_for_a_claim_not_waiting_for_sign_off():
    assert send_back("CLM-1001").status_code == 409
    decide("CLM-1001", {**LARGE, "payout": 420.0})
    refused = send_back("CLM-1001")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Claim CLM-1001 is not waiting for sign-off"
    assert claim("CLM-1001")["sent_back"] == []


def test_a_send_back_is_refused_for_an_evaluations_copy():
    body = {
        "kind": "evaluation",
        "n": 1,
        "eval_set": "evaluation",
        "tiny": True,
        "model": "stub",
        "effort": "none",
        "version": 1,
    }
    run_id = client.post("/api/runs", json=body).json()["run_id"]
    copy_id = client.get(f"/api/runs/{run_id}/claims").json()[0]
    decide(copy_id)
    refused = send_back(copy_id)
    assert refused.status_code == 409
    assert refused.json()["detail"] == f"Claim {copy_id} is an evaluation's copy"


@pytest.mark.parametrize("note", ["", "   ", "x" * 1001])
def test_a_send_back_needs_a_note_of_1_to_1000_characters(note):
    decide("CLM-1001")
    assert send_back("CLM-1001", note).status_code == 422
    assert claim("CLM-1001")["status"] == "waiting_for_sign_off"
    assert send_back("CLM-1001", "x" * 1000).status_code == 200


def test_a_sign_off_and_a_send_back_at_the_same_moment_one_goes_through():
    decide("CLM-1001")
    with ThreadPoolExecutor(2) as pool:
        answers = [
            pool.submit(sign_off, "CLM-1001"),
            pool.submit(send_back, "CLM-1001"),
        ]
    codes = sorted(a.result().status_code for a in answers)
    assert codes == [200, 409]
    found = claim("CLM-1001")
    if found["status"] == "decided":
        assert (found["paid_amount"], found["sent_back"]) == (1250.0, [])
    else:
        assert (found["status"], found["paid_amount"]) == ("sent_back", None)
        assert len(found["sent_back"]) == 1


def test_a_send_back_asks_the_agent_to_resume(monkeypatch):
    asked = []
    monkeypatch.setattr(agent_service, "resume_claim", asked.append)
    decide("CLM-1001")
    assert send_back("CLM-1001").json()["status"] == "sent_back"
    assert asked == ["CLM-1001"]


def test_a_send_back_stands_when_the_agent_cant_be_reached():
    # Nothing listens at UNDERCOVER_AGENT in the tests.
    decide("CLM-1002")
    assert send_back("CLM-1002").json()["status"] == "sent_back"
    assert claim("CLM-1002")["status"] == "sent_back"


def test_a_sent_back_claim_is_taken_once():
    decide("CLM-1001")
    send_back("CLM-1001")
    with ThreadPoolExecutor(2) as pool:
        answers = [pool.submit(take, "CLM-1001"), pool.submit(start, "CLM-1001")]
    codes = sorted(a.result().status_code for a in answers)
    assert codes == [200, 409]
    assert claim("CLM-1001")["status"] == "in_progress"
    assert take("CLM-1001").status_code == 409


def test_taking_gives_the_claim_with_its_note_and_is_the_agents_alone():
    decide("CLM-1001")
    send_back("CLM-1001")
    refused = TestClient(app).post("/api/claims/CLM-1001/take", headers=headers())
    assert refused.status_code == 401
    taken = take("CLM-1001").json()
    assert taken["status"] == "in_progress"
    assert taken["sent_back"][0]["note"] == NOTE


def test_a_dispatch_works_sent_back_claims_but_leaves_one_being_resumed():
    decide("CLM-1001")
    send_back("CLM-1001")
    decide("CLM-1002")
    send_back("CLM-1002")
    run_id = dispatch()
    listed = client.get(f"/api/runs/{run_id}/claims").json()
    assert set(listed[:2]) == {"CLM-1001", "CLM-1002"}
    assert len(listed) == 26
    take("CLM-1001")
    listed = client.get(f"/api/runs/{run_id}/claims").json()
    assert "CLM-1001" not in listed
    refused = start("CLM-1001", run_id)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Claim CLM-1001 is being resumed"
    # A resume that told the app a minute ago still holds its claim.
    client.post("/api/claims/CLM-1001/resume-heard")
    heard("CLM-1001", 4)
    assert "CLM-1001" not in client.get(f"/api/runs/{run_id}/claims").json()
    # Quiet for five minutes, it counts as stopped, and the dispatch takes it.
    heard("CLM-1001", 6)
    assert client.get(f"/api/runs/{run_id}/claims").json()[0] == "CLM-1001"
    assert start("CLM-1001", run_id).json()["status"] == "in_progress"


def test_resume_heard_is_recorded():
    decide("CLM-1001")
    send_back("CLM-1001")
    take("CLM-1001")
    heard("CLM-1001", 30)
    assert client.post("/api/claims/CLM-1001/resume-heard").json() == {
        "status": "recorded"
    }
    run_id = dispatch()
    assert "CLM-1001" not in client.get(f"/api/runs/{run_id}/claims").json()
