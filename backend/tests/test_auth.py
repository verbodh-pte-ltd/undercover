import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from backend import agent_service
from backend.main import app
from backend.tests.signing import AGENT, DANA, MARCUS, headers, token

client = TestClient(app, headers=AGENT)

LARGE = {
    "decision": "approve",
    "payout": 1250.0,
    "reason": "Covered treatment.",
    "policy_section": "7. Emergency Medical",
}
ACTIONS = [
    ("/api/dispatches", {"n": 3}),
    ("/api/evaluations", {"n": 3, "eval_set": "evaluation", "tiny": True}),
    ("/api/claims/CLM-1001/sign-off", None),
]
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())
BAD_SIGN_INS = {
    "no token": {},
    "expired": {"Authorization": f"Bearer {token(DANA, expires_in=-60)}"},
    "unknown email": {"Authorization": f"Bearer {token('someone@example.com')}"},
    "another key": {"Authorization": f"Bearer {token(DANA, key=OTHER_KEY)}"},
    "not a token": {"Authorization": "Bearer nonsense"},
}


@pytest.fixture(autouse=True)
def no_agent(monkeypatch):
    monkeypatch.setattr(
        agent_service, "start", lambda path, body: {"status": "started"}
    )


def test_the_handlers_are_seeded_with_their_emails():
    found = {
        h["name"]: h for h in client.get("/api/handlers", headers=headers()).json()
    }
    assert client.get("/api/handlers/me", headers=headers()).json()["email"] == DANA
    assert found["Dana Whitfield"]["can_approve_over_limit"] is True
    assert found["Marcus Oyelaran"]["can_approve_over_limit"] is False
    assert found["Priya Raman"]["can_approve_over_limit"] is False


def test_the_signed_in_handler_is_found_by_the_tokens_email():
    me = client.get("/api/handlers/me", headers=headers()).json()
    assert (me["handler_id"], me["name"]) == ("HND-01", "Dana Whitfield")


@pytest.mark.parametrize("sent", BAD_SIGN_INS.values(), ids=list(BAD_SIGN_INS))
@pytest.mark.parametrize(("path", "body"), ACTIONS)
def test_every_handler_action_needs_a_valid_sign_in(path, body, sent):
    client.post("/api/claims/CLM-1001/decision", json=LARGE)
    response = client.post(path, json=body, headers=sent)
    assert response.status_code == 401
    assert client.get("/api/claims/CLM-1001").json()["signed_off_by"] is None


@pytest.mark.parametrize(("path", "body"), ACTIONS[:2])
def test_a_signed_in_handler_can_start_a_run(path, body):
    assert client.post(path, json=body, headers=headers()).status_code == 200


def test_a_sign_off_records_the_signed_in_handler():
    client.post("/api/claims/CLM-1001/decision", json=LARGE)
    claim = client.post("/api/claims/CLM-1001/sign-off", headers=headers()).json()
    assert claim["signed_off_by"] == "HND-01"


def test_marcus_cant_sign_off_over_the_limit():
    client.post("/api/claims/CLM-1001/decision", json=LARGE)
    response = client.post("/api/claims/CLM-1001/sign-off", headers=headers(MARCUS))
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Marcus Oyelaran can't sign off payouts over the approval limit"
    )
    claim = client.get("/api/claims/CLM-1001").json()
    assert (claim["status"], claim["signed_off_by"]) == ("waiting_for_sign_off", None)


def test_the_signed_in_handler_follows_the_api_rules():
    operation = client.get("/openapi.json").json()["paths"]["/api/handlers/me"]["get"]
    assert operation["operationId"] == "get_signed_in_handler"
    assert operation["description"].count("\n") == 0
