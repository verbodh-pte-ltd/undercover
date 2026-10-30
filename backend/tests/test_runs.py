import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import agent_service, store
from backend.main import app

client = TestClient(app)

DECLINE = {
    "decision": "decline",
    "payout": 0,
    "reason": "Not covered.",
    "policy_section": "9",
}


def new_run(kind="dispatch", eval_set=None, tiny=False, n=3):
    body = {
        "kind": kind,
        "n": n,
        "eval_set": eval_set,
        "tiny": tiny,
        "model": "openai/gpt-6-luna",
        "effort": "low",
    }
    return client.post("/api/runs", json=body)


def start(claim_id, run_id):
    return client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})


def release(claim_id):
    return client.post(f"/api/claims/{claim_id}/release", json={"error": "timeout"})


def decide(claim_id, body):
    return client.post(f"/api/claims/{claim_id}/decision", json=body)


def status(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()["status"]


def approve(payout):
    return {**DECLINE, "decision": "approve", "payout": payout}


def test_no_run_yet():
    assert client.get("/api/runs/current").json() is None


def test_create_a_dispatch():
    run = new_run().json()
    assert {k: run[k] for k in ["kind", "n", "eval_set", "model", "effort"]} == {
        "kind": "dispatch",
        "n": 3,
        "eval_set": None,
        "model": "openai/gpt-6-luna",
        "effort": "low",
    }
    assert (run["status"], run["claims"], run["finished_at"]) == ("running", [], None)
    assert client.get("/api/runs/current").json()["run_id"] == run["run_id"]


def test_only_one_run_at_a_time():
    run_id = new_run().json()["run_id"]
    assert new_run().status_code == 409
    client.post(f"/api/runs/{run_id}/finish")
    assert new_run().status_code == 200


def test_only_an_evaluation_has_a_set():
    assert new_run(eval_set="evaluation").status_code == 422
    assert new_run(kind="evaluation").status_code == 422


def test_stopped_runs_are_marked_when_the_service_starts():
    run_id = new_run().json()["run_id"]
    assert client.post("/api/runs/stop-running").json() == {"stopped": 1}
    assert client.get("/api/runs/current").json()["status"] == "stopped"
    assert client.get("/api/runs/current").json()["run_id"] == run_id


def test_unknown_run_is_404():
    assert start("CLM-1001", 999).status_code == 404
    assert client.get("/api/runs/999/claims").status_code == 404


def test_a_claim_can_start_with_no_run():
    started = start("CLM-1001", None).json()
    assert (started["status"], started["thread_id"]) == ("in_progress", "CLM-1001-1")


def submitted(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()["submitted_on"]


def test_a_dispatch_lists_claims_in_progress_then_oldest_submitted():
    start("CLM-1020", None)
    run_id = new_run().json()["run_id"]
    listed = client.get(f"/api/runs/{run_id}/claims").json()
    assert listed[0] == "CLM-1020"
    rest = listed[1:]
    assert len(rest) == 24
    assert [submitted(c) for c in rest] == sorted(submitted(c) for c in rest)


def test_the_summary_is_built_from_the_records():
    run_id = new_run().json()["run_id"]
    for claim_id in ["CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004", "CLM-1005"]:
        start(claim_id, run_id)
    decide("CLM-1001", approve(420))
    decide("CLM-1002", DECLINE)
    decide("CLM-1003", approve(1250))
    release("CLM-1004")
    release("CLM-1004")
    run = client.get("/api/runs/current").json()
    assert [c["claim_id"] for c in run["claims"]][:2] == ["CLM-1001", "CLM-1002"]
    assert run["summary"] == {
        "approved": 1,
        "declined": 1,
        "referred": 0,
        "waiting_for_sign_off": [{"claim_id": "CLM-1003", "payout": 1250.0}],
        "left_for_handler": [],
        "in_progress": ["CLM-1005"],
    }
    assert status("CLM-1004") == "waiting"


def test_release_twice_leaves_it_for_a_handler():
    start("CLM-1001", None)
    assert release("CLM-1001").json()["status"] == "waiting"
    start("CLM-1001", None)
    claim = release("CLM-1001").json()
    assert (claim["status"], claim["attempts"]) == ("needs_handler", 2)


def test_release_refuses_a_decided_claim():
    start("CLM-1001", None)
    decide("CLM-1001", DECLINE)
    assert release("CLM-1001").status_code == 409


def test_cost_and_time_are_recorded_with_their_run():
    run_id = new_run().json()["run_id"]
    start("CLM-1001", run_id)
    for run, cost in [(run_id, 0.01), (run_id, 0.02), (None, 0.5)]:
        used = {"run_id": run, "input_tokens": 100, "output_tokens": 20, "cost": cost}
        client.post("/api/claims/CLM-1001/model-calls", json=used)
    for run, seconds in [(run_id, 3.0), (None, 2.0)]:
        spent = {"run_id": run, "seconds": seconds}
        client.post("/api/claims/CLM-1001/work-times", json=spent)
    run = client.get("/api/runs/current").json()
    assert run["cost"] == pytest.approx(0.03)
    assert run["seconds"] == pytest.approx(3.0)
    claim = client.get("/api/claims/CLM-1001").json()
    assert (claim["cost"], claim["seconds"]) == (pytest.approx(0.53), 5.0)


def test_finished_run_has_its_time():
    run_id = new_run().json()["run_id"]
    run = client.post(f"/api/runs/{run_id}/finish").json()
    assert run["status"] == "finished"
    assert run["run_seconds"] >= 0


def test_sections_read_skips_malformed_steps():
    for text in [
        'read_section({"document": "claims_manual", "number": "9"})',
        'read_section({"document": "claims_manual"})',
        "read_section(not json)",
    ]:
        step = {"kind": "tool_call", "text": text}
        client.post("/api/claims/CLM-1001/steps", json=step)
    assert client.get("/api/claims/CLM-1001").json()["sections_read"] == [
        "claims_manual 9"
    ]


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        return None


def test_start_dispatch_asks_the_service_with_n(monkeypatch):
    sent = []

    def post(url, json, timeout):
        sent.append((url, json))
        return FakeResponse(202, {"status": "started"})

    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test")
    monkeypatch.setattr(agent_service.httpx, "post", post)
    assert client.post("/api/dispatches", json={"n": 3}).json() == {"status": "started"}
    body = {"n": 5, "eval_set": "held_out", "tiny": True}
    client.post("/api/evaluations", json=body)
    assert sent == [
        ("http://agent.test/dispatches", {"n": 3}),
        ("http://agent.test/evaluations", body),
    ]


def test_a_second_start_is_refused(monkeypatch):
    refusal = FakeResponse(409, {"detail": "A dispatch is already running"})
    monkeypatch.setattr(agent_service.httpx, "post", lambda *a, **kw: refusal)
    response = client.post("/api/dispatches", json={"n": 3})
    assert response.status_code == 409
    assert response.json()["detail"] == "A dispatch is already running"


def test_start_says_when_the_service_is_not_running(monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(agent_service.httpx, "post", refuse)
    response = client.post("/api/dispatches", json={"n": 3})
    assert response.status_code == 503
    assert response.json()["detail"] == "The agent's service isn't running"


def test_n_is_from_1_to_25():
    assert client.post("/api/dispatches", json={"n": 0}).status_code == 422
    assert client.post("/api/dispatches", json={"n": 26}).status_code == 422


def counts():
    found = {}
    for claim in client.get("/api/claims").json():
        found[claim["status"]] = found.get(claim["status"], 0) + 1
    return found


def test_an_evaluation_copies_its_claims_out_of_the_queue():
    before = counts()
    run = new_run("evaluation", "evaluation").json()
    copies = client.get(f"/api/runs/{run['run_id']}/claims").json()
    assert len(copies) == 25
    assert copies[0] == f"E{run['run_id']}-CLM-1001"
    copy = client.get(f"/api/claims/{copies[0]}").json()
    assert (copy["claim_id"], copy["copied_from"]) == (copies[0], "CLM-1001")
    assert copy["run_id"] == run["run_id"]
    assert counts() == before
    assert len(client.get("/api/claims/waiting").json()) == 25


def test_a_tiny_held_out_evaluation_has_five_claims():
    run = new_run("evaluation", "held_out", tiny=True).json()
    copies = client.get(f"/api/runs/{run['run_id']}/claims").json()
    assert len(copies) == 5
    assert all(c.startswith(f"E{run['run_id']}-CLM-11") for c in copies)


def test_a_copy_holds_nothing_from_the_handlers_decisions():
    run = new_run("evaluation", "evaluation").json()
    copy = client.get(f"/api/claims/E{run['run_id']}-CLM-1001").json()
    key = json.loads(json.dumps(store.load("evals/decisions.json")[0]))
    for field in ["handler_id", "calculation", "reference", "tests", "tiny"]:
        assert field not in copy
    assert key["reason"] not in json.dumps(copy)


def test_a_sign_off_on_a_copy_is_refused():
    run = new_run("evaluation", "evaluation").json()
    copy_id = f"E{run['run_id']}-CLM-1003"
    start(copy_id, run["run_id"])
    decide(copy_id, approve(3750))
    response = client.post(
        f"/api/claims/{copy_id}/sign-off", json={"handler_id": "HND-01"}
    )
    assert response.status_code == 409


def key(claim_id):
    found = [d for d in store.load("evals/decisions.json") if d["claim_id"] == claim_id]
    return found[0]


def test_the_measures_match_ones_worked_out_by_hand():
    run_id = new_run("evaluation", "evaluation", tiny=True).json()["run_id"]
    copies = client.get(f"/api/runs/{run_id}/claims").json()
    originals = [c.split("-", 1)[1] for c in copies]
    first, second, third, fourth, _ = originals
    # Right; right decision with the wrong amount (or a wrong decision); a wrongful
    # decline; a needless referral; and one left undecided, which counts as a referral.
    answers = {
        first: (key(first)["decision"], key(first)["payout"]),
        second: ("approve", key(second)["payout"] + 10),
        third: ("decline", 0),
        fourth: ("refer", 0),
    }
    for original, (decision, payout) in answers.items():
        copy_id = f"E{run_id}-{original}"
        start(copy_id, run_id)
        decide(copy_id, {**DECLINE, "decision": decision, "payout": payout})
        used = {"run_id": run_id, "input_tokens": 10, "output_tokens": 5, "cost": 0.01}
        client.post(f"/api/claims/{copy_id}/model-calls", json=used)
        spent = {"run_id": run_id, "seconds": 4.0}
        client.post(f"/api/claims/{copy_id}/work-times", json=spent)
    client.post(f"/api/runs/{run_id}/finish")

    def cost(original, decision, payout):
        handler = key(original)
        if decision == handler["decision"]:
            return abs(payout - handler["payout"]) if decision == "approve" else 0
        return {"decline": 1500, "approve": payout, "refer": 50}[decision]

    graded = [(o, *answers.get(o, ("refer", 0))) for o in originals]
    expected_right = sum(
        d == key(o)["decision"] and abs(p - key(o)["payout"]) <= 1 for o, d, p in graded
    )
    evaluation = client.get(f"/api/evaluations/{run_id}").json()
    assert evaluation["decision_accuracy"] == pytest.approx(
        sum(d == key(o)["decision"] for o, d, _ in graded) / 5
    )
    assert evaluation["accuracy"] == pytest.approx(expected_right / 5)
    assert evaluation["cost_of_mistakes"] == pytest.approx(
        sum(cost(o, d, p) for o, d, p in graded)
    )
    assert evaluation["straight_through"] == pytest.approx(
        sum(d != "refer" for _, d, _ in graded) / 5
    )
    assert evaluation["cost_per_claim"] == pytest.approx(0.04 / 5)
    assert evaluation["seconds_per_claim"] == pytest.approx(16 / 5)
    assert evaluation["run_seconds"] >= 0
    assert [c["claim_id"] for c in evaluation["claims"]] == copies
    assert client.get("/api/evaluations").json()[0]["run_id"] == run_id


def test_evaluations_are_listed_newest_first_and_stopped_shows():
    first = new_run("evaluation", "held_out", tiny=True).json()["run_id"]
    client.post("/api/runs/stop-running")
    second = new_run("evaluation", "evaluation", tiny=True).json()["run_id"]
    listed = client.get("/api/evaluations").json()
    assert [e["run_id"] for e in listed] == [second, first]
    assert [e["status"] for e in listed] == ["running", "stopped"]
    assert client.get("/api/evaluations/999").status_code == 404


def test_a_dispatch_is_not_an_evaluation():
    run_id = new_run().json()["run_id"]
    assert client.get(f"/api/evaluations/{run_id}").status_code == 404


def test_new_operations_follow_the_api_rules():
    schema = client.get("/openapi.json").json()
    for path, method in [
        ("/api/runs", "post"),
        ("/api/dispatches", "post"),
        ("/api/evaluations", "post"),
        ("/api/claims/{claim_id}/model-calls", "post"),
        ("/api/claims/{claim_id}/work-times", "post"),
    ]:
        operation = schema["paths"][path][method]
        assert operation["operationId"]
        assert operation["description"].count("\n") == 0
    components = schema["components"]["schemas"]
    for name in [
        "RunCreate",
        "DispatchStart",
        "EvaluationStart",
        "ModelCall",
        "WorkTime",
    ]:
        assert set(components[name]["required"]) == set(components[name]["properties"])
