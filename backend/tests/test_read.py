import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import store
from backend.main import app

client = TestClient(app)


def test_queue_is_seeded_from_claims_json():
    queue_ids = [
        c["claim_id"] for c in json.loads(Path("data/claims.json").read_text())
    ]
    claims = client.get("/api/claims").json()
    assert [c["claim_id"] for c in claims] == queue_ids
    assert len(claims) == 25
    assert {c["status"] for c in claims} == {"waiting"}
    assert "CLM-1901" not in queue_ids


def test_list_claims_fields():
    first = client.get("/api/claims").json()[0]
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


def test_policy_wording():
    text = client.get("/api/policy-wording").json()["text"]
    assert text == Path("data/policy_wording.md").read_text(encoding="utf-8")


def test_handlers():
    handlers = client.get("/api/handlers").json()
    assert [h["handler_id"] for h in handlers] == ["HND-01", "HND-02", "HND-03"]


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


def test_opening_twice_does_not_reseed():
    store.connect().close()
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 25
    assert conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0] == 50
    conn.close()


def test_instructions():
    texts = client.get("/api/instructions").json()
    assert set(texts) == {"queue", "claim"}
    for name, text in texts.items():
        path = Path(f"backend/instructions/{name}.md")
        assert text == path.read_text(encoding="utf-8")
    assert "read_section" in texts["claim"]
    assert "claims_manual" in texts["claim"]
    assert "set_todos" in texts["queue"]
    assert "work_claim" in texts["queue"]
    assert "finish_run" in texts["queue"]


def test_list_sections():
    manual = client.get("/api/documents/claims_manual/sections").json()
    assert len(manual) == 14
    assert manual[8] == {"number": "9", "title": "Repeat claims"}
    wording = client.get("/api/documents/policy_wording/sections").json()
    assert len(wording) == 12
    assert wording[2] == {"number": "3", "title": "Trip Delay"}


def test_read_section():
    section = client.get("/api/documents/claims_manual/sections/9").json()
    assert section["title"] == "Repeat claims"
    assert section["text"].startswith("## 9. Repeat claims")
    assert "third claim" in section["text"]
    assert "Meals without receipts" not in section["text"]


def test_unknown_document_or_section_is_404():
    assert client.get("/api/documents/evals/sections").status_code == 404
    assert client.get("/api/documents/claims_manual/sections/99").status_code == 404


def test_connection_works_across_threads():
    conn = store.connect()
    with ThreadPoolExecutor() as pool:
        count = pool.submit(
            lambda: conn.execute("SELECT COUNT(*) FROM claims").fetchone()
        )
    assert count.result()[0] == 25
    conn.close()


def test_concurrent_first_requests_seed_once():
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.get("/api/claims"), range(8)))
    assert {r.status_code for r in responses} == {200}
    assert len(client.get("/api/claims").json()) == 25
