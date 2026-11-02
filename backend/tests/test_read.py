import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import QueuePool, func, select

from backend import store
from backend.main import app
from backend.tests.records import record_decision
from backend.tests.signing import AGENT, headers

client = TestClient(app, headers=AGENT)

DECLINE = {
    "decision": "decline",
    "payout": 0,
    "reason": "Not covered.",
    "policy_section": "9. Exclusions",
}


def test_queue_is_seeded_from_claims_json_with_the_security_claim_last():
    queue_ids = [
        c["claim_id"] for c in json.loads(Path("data/claims.json").read_text())
    ]
    claims = client.get("/api/claims", headers=headers()).json()
    assert [c["claim_id"] for c in claims] == [*queue_ids, "CLM-1901"]
    assert len(claims) == 26
    assert {c["status"] for c in claims} == {"waiting"}
    assert len(queue_ids) == 25
    assert "CLM-1901" not in queue_ids


def test_the_security_claim_has_its_policy_and_customer_and_no_decision():
    found = client.get("/api/claims/CLM-1901").json()
    assert (found["policy_id"], found["customer_id"]) == ("POL-2901", "CUS-3901")
    assert found["customer_name"] == "Derek Vance"
    assert (found["status"], found["decision"]) == ("waiting", None)
    assert client.get("/api/policies/POL-2901").json()["plan"] == "premium"
    assert client.get("/api/customers/CUS-3901/claims").json() == []
    with store.connect() as conn:
        keys = conn.scalars(select(store.answer_key.c.claim_id)).all()
    assert len(keys) == 50
    assert "CLM-1901" not in keys


def test_list_claims_fields():
    first = client.get("/api/claims", headers=headers()).json()[0]
    assert first == {
        "claim_id": "CLM-1001",
        "summary": "Flight home canceled by the airline, rebooked the next morning",
        "claim_type": "trip_delay",
        "amount_claimed": 420.0,
        "customer_name": "Maya Patel",
        "status": "waiting",
        "decision": None,
        "payout": None,
        "paid_amount": None,
    }


def test_get_claim():
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["story"].startswith("My flight home from Rome")
    assert len(claim["items"]) == 3
    assert len(claim["documents"]) == 4
    assert claim["status"] == "waiting"


def test_get_policy():
    policy = client.get("/api/policies/POL-2001").json()
    assert policy["plan"] == "premium"
    assert policy["trip"]["destination"] == "Rome, Italy"


def test_claim_history_has_no_personal_details():
    response = client.get("/api/customers/CUS-3001/claims")
    assert [c["claim_id"] for c in response.json()] == ["CLM-0412"]
    for field in ["email", "phone", "address", "date_of_birth", "Jersey City"]:
        assert field not in response.text


def test_policy_wording_comes_from_context_without_its_front_matter():
    wording = client.get("/api/policy-wording").json()
    assert wording["version"] == "2026.1"
    source = Path("context/policy_wording.md").read_text(encoding="utf-8")
    assert source.endswith(wording["text"])
    assert wording["text"].lstrip().startswith("# UnderCover Travel Insurance")


def test_handlers():
    handlers = client.get("/api/handlers", headers=headers()).json()
    assert [h["handler_id"] for h in handlers] == ["HND-01", "HND-02", "HND-03"]
    assert all("email" not in h for h in handlers)


def test_the_handlers_need_a_sign_in():
    assert client.get("/api/handlers").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/claims/CLM-0000",
        "/api/policies/POL-0000",
        "/api/customers/CUS-0000/claims",
    ],
)
def test_unknown_is_404(path):
    assert client.get(path).status_code == 404


def count(conn, table):
    return conn.scalar(select(func.count()).select_from(table))


def test_opening_twice_does_not_reseed():
    store.connect().close()
    with store.connect() as conn:
        assert count(conn, store.claims) == 26
        assert count(conn, store.policies) == 51


def test_instructions():
    instructions = client.get("/api/configuration/current").json()["instructions"]
    assert instructions == Path("backend/instructions/claim.md").read_text(
        encoding="utf-8"
    )
    assert "read_section" in instructions
    assert "claims_manual" in instructions


def test_seeding_loads_the_plans_and_the_documents_from_context():
    with store.connect() as conn:
        found = {r.document: r.version for r in conn.execute(select(store.documents))}
        rows = {r.plan: json.loads(r.data) for r in conn.execute(select(store.plans))}
    assert found == {"policy_wording": "2026.1", "claims_manual": "2026.2"}
    assert sorted(rows) == ["economy", "first", "premium"]
    assert rows["premium"]["deductible"] == 50
    assert rows["first"]["deductible_applies_to"] == ["baggage", "emergency_medical"]
    assert rows["economy"]["add_ons"][2]["electronics_per_item_limit"] == 1200


def test_the_document_routes_have_moved_to_the_context_layer():
    assert client.get("/api/documents/claims_manual/sections").status_code == 404
    assert client.get("/api/documents/claims_manual/sections/9").status_code == 404


def test_connection_works_across_threads():
    with store.connect() as conn, ThreadPoolExecutor() as pool:
        claims = pool.submit(count, conn, store.claims)
        assert claims.result() == 26


def test_concurrent_first_requests_seed_once():
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(lambda _: client.get("/api/claims", headers=headers()), range(8))
        )
    assert {r.status_code for r in responses} == {200}
    assert len(client.get("/api/claims", headers=headers()).json()) == 26


def test_reseed_puts_the_queue_back():
    record_decision(client, "/api/claims/CLM-1001/decision", {**DECLINE})
    store.reseed()
    assert client.get("/api/claims/CLM-1001").json()["status"] == "waiting"
    with store.connect() as conn:
        assert count(conn, store.claims) == 26


def test_postgres_engine_has_a_pool_of_four():
    engine = store.postgres_engine("postgresql+psycopg://u:p@127.0.0.1:1/db")
    assert isinstance(engine.pool, QueuePool)
    assert (engine.pool.size(), engine.pool._max_overflow) == (4, 0)


def test_tests_never_see_the_database_in_dot_env():
    assert "DATABASE_URL" not in os.environ
    assert store.postgres_url() is None
