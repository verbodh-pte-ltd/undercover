from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

DECLINE = {
    "decision": "decline",
    "payout": 0,
    "reason": "Not covered.",
    "policy_section": "9",
}


def new_run(cap):
    return client.post("/api/runs", json={"cap": cap}).json()


def start(claim_id, run_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def release(claim_id):
    return client.post(f"/api/claims/{claim_id}/release", json={"error": "timeout"})


def status(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()["status"]


def test_no_run_yet():
    assert client.get("/api/runs/current").json() is None


def test_start_run_and_set_todos():
    run = new_run(5)
    assert run == {
        "run_id": run["run_id"],
        "cap": 5,
        "todos": [],
        "summary": None,
        "claims_started": [],
    }
    todos = [{"claim_id": "CLM-1001", "text": "Decide CLM-1001", "state": "to_do"}]
    client.put(f"/api/runs/{run['run_id']}/todos", json=todos)
    assert client.get("/api/runs/current").json()["todos"] == todos


def test_bad_todo_state_is_422():
    run = new_run(5)
    todos = [{"claim_id": "CLM-1001", "text": "Decide", "state": "maybe"}]
    response = client.put(f"/api/runs/{run['run_id']}/todos", json=todos)
    assert response.status_code == 422


def test_unknown_run_is_404():
    assert start("CLM-1001", 999).status_code == 404


def test_list_waiting_claims():
    run = new_run(5)
    start("CLM-1001", run["run_id"])
    waiting = client.get("/api/claims/waiting").json()
    assert len(waiting) == 24
    assert waiting[0] == {
        "claim_id": "CLM-1002",
        "summary": waiting[0]["summary"],
        "claim_type": waiting[0]["claim_type"],
        "amount_claimed": waiting[0]["amount_claimed"],
        "attempts": 0,
    }


def test_cap_refuses_a_sixth_claim():
    run_id = new_run(5)["run_id"]
    ids = [c["claim_id"] for c in client.get("/api/claims/waiting").json()][:6]
    for claim_id in ids[:5]:
        assert start(claim_id, run_id).status_code == 200
    assert start(ids[5], run_id).status_code == 409
    assert status(ids[5]) == "waiting"


def test_release_twice_leaves_it_for_a_handler():
    run_id = new_run(5)["run_id"]
    start("CLM-1001", run_id)
    assert release("CLM-1001").json()["status"] == "waiting"
    assert start("CLM-1001", run_id).status_code == 200
    claim = release("CLM-1001").json()
    assert (claim["status"], claim["attempts"]) == ("needs_handler", 2)
    errors = [
        s
        for s in client.get("/api/claims/CLM-1001/steps").json()
        if s["kind"] == "error"
    ]
    assert len(errors) == 2
    assert start("CLM-1001", run_id).status_code == 409


def test_release_refuses_a_decided_claim():
    run_id = new_run(5)["run_id"]
    start("CLM-1001", run_id)
    client.post("/api/claims/CLM-1001/decision", json=DECLINE)
    assert release("CLM-1001").status_code == 409
    assert release("CLM-1002").status_code == 409


def test_new_run_releases_stuck_claims():
    start("CLM-1001", new_run(5)["run_id"])
    new_run(5)
    claim = client.get("/api/claims/CLM-1001").json()
    assert (claim["status"], claim["attempts"]) == ("waiting", 1)


def test_finish_refuses_while_claims_are_missed():
    run_id = new_run(1)["run_id"]
    finish = {"summary": "Done."}
    response = client.post(f"/api/runs/{run_id}/finish", json=finish)
    assert response.status_code == 409
    assert "CLM-1001" in response.json()["detail"]
    start("CLM-1001", run_id)
    client.post("/api/claims/CLM-1001/decision", json=DECLINE)
    assert client.post(f"/api/runs/{run_id}/finish", json=finish).status_code == 200
    assert client.get("/api/runs/current").json()["summary"] == "Done."


def test_finish_refuses_a_released_claim_in_the_run():
    run_id = new_run(1)["run_id"]
    start("CLM-1001", run_id)
    release("CLM-1001")
    response = client.post(f"/api/runs/{run_id}/finish", json={"summary": "Done."})
    assert response.status_code == 409
