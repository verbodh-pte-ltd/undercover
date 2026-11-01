import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from openrouter import errors
from openrouter.components import DecisionsResponse
from sqlalchemy import select

from backend import judge, main, store
from backend.main import app

# The real grade, before the tests' stub takes its place.
real_grade = judge.grade
client = TestClient(app)

HANDLER = {"decision": "decline", "reason": "Not covered.", "reference": "9.2"}
AGENT = {"decision": "decline", "rationale": "Excluded by 9.2.", "section": "9.2"}
B = {
    "grade": 2.0,
    "letter": "B",
    "same_grounds": 0.9,
    "same_section": 0.2,
    "judge_cost": 0.001,
}


def response(probabilities, score, cost=0.00003):
    return DecisionsResponse.model_validate(
        {
            "model": judge.JEV,
            "answers": {
                "grade": {
                    "type": "score",
                    "score": score,
                    "probabilities": probabilities,
                },
                "same_grounds": {"type": "noul", "noul": 0.8},
                "same_section": {"type": "noul", "noul": 0.3},
            },
            "usage": {"input_tokens": 300, "output_tokens": 4, "cost": cost},
        }
    )


def test_seeding_stores_the_handlers_reasons_and_references():
    with store.connect() as conn:
        rows = {r.claim_id: r for r in conn.execute(select(store.answer_key))}
    for _, decisions in store.EVAL_SETS.values():
        for d in store.load(decisions):
            row = rows[d["claim_id"]]
            assert (row.reason, row.reference) == (d["reason"], d["reference"])


def test_the_letter_is_the_most_likely_level_not_the_score_rounded(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        # The score is 1.6, which rounds to B, but F is the most likely level.
        return response({"0": 0.4, "1": 0.1, "2": 0.2, "3": 0.3}, 1.6)

    monkeypatch.setattr(judge.client.alpha.decisions, "create", create)
    assert real_grade(HANDLER, AGENT) == {
        "grade": 1.6,
        "letter": "F",
        "same_grounds": 0.8,
        "same_section": 0.3,
        "judge_cost": 0.00003,
    }
    [call] = calls
    assert call["model"] == "typesafe/jev-1.13"
    assert call["state"] == {"handler": HANDLER, "agent": AGENT}
    assert call["questions"] == judge.JUDGE_QUESTIONS
    assert call["server_url"] == judge.SERVER_URL
    assert call["timeout_ms"] == 10_000
    assert call["retries"].strategy == "none"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("timed out"),
        errors.OpenRouterError("overloaded", httpx.Response(529)),
    ],
)
def test_a_failed_call_is_not_graded(monkeypatch, failure):
    def create(**kwargs):
        raise failure

    monkeypatch.setattr(judge.client.alpha.decisions, "create", create)
    assert real_grade(HANDLER, AGENT) is None


def test_an_answer_without_its_levels_is_not_graded(monkeypatch):
    monkeypatch.setattr(
        judge.client.alpha.decisions,
        "create",
        lambda **kwargs: response({"4": 1.0}, 3.0),
    )
    assert real_grade(HANDLER, AGENT) is None


def evaluation(eval_set="evaluation", tiny=False):
    body = {
        "kind": "evaluation",
        "n": 3,
        "eval_set": eval_set,
        "tiny": tiny,
        "model": "openai/gpt-6-luna",
        "effort": "low",
        "version": 1,
    }
    run_id = client.post("/api/runs", json=body).json()["run_id"]
    return run_id, client.get(f"/api/runs/{run_id}/claims").json()


def decide(copy_id, run_id):
    client.post(f"/api/claims/{copy_id}/start", json={"run_id": run_id})
    decision = {
        "decision": "refer",
        "payout": 0,
        "reason": "Needs a handler.",
        "policy_section": "claims_manual 2",
    }
    client.post(f"/api/claims/{copy_id}/decision", json=decision)


def test_finishing_grades_in_parallel_and_an_undecided_claim_is_an_f(monkeypatch):
    run_id, copies = evaluation()
    for copy_id in copies[1:]:
        decide(copy_id, run_id)
    asked = []
    lock = threading.Lock()

    def slow_judge(handler, agent):
        with lock:
            asked.append((handler, agent))
        time.sleep(1)
        return B

    monkeypatch.setattr(judge, "grade", slow_judge)
    started = time.monotonic()
    client.post(f"/api/runs/{run_id}/finish")
    # 24 calls of a second each, one after another, would take 24 seconds.
    assert time.monotonic() - started < 20
    assert len(asked) == 24
    handler, agent = asked[0]
    assert set(handler) == {"decision", "reason", "reference"}
    assert agent["rationale"] == "Needs a handler."
    found = client.get(f"/api/evaluations/{run_id}").json()
    undecided, decided = found["claims"][0], found["claims"][1]
    assert {k: undecided[k] for k in B} == {**B, **main.UNDECIDED}
    assert {k: decided[k] for k in B} == B
    assert found["grade"] == pytest.approx(2.0 * 24 / 25)
    # The judge's cost is the evaluation's own, apart from the claim agent's.
    assert found["judge_cost"] == pytest.approx(0.024)
    assert found["cost_per_claim"] == 0


def test_a_claim_the_judge_cant_grade_is_left_out_of_the_average(monkeypatch):
    run_id, copies = evaluation(tiny=True)
    for copy_id in copies:
        decide(copy_id, run_id)
    grades = iter([None, B, {**B, "grade": 3.0, "letter": "A"}, B, B])
    lock = threading.Lock()

    def judge_(handler, agent):
        with lock:
            return next(grades)

    monkeypatch.setattr(judge, "grade", judge_)
    client.post(f"/api/runs/{run_id}/finish")
    found = client.get(f"/api/evaluations/{run_id}").json()
    graded = [c["grade"] for c in found["claims"] if c["grade"] is not None]
    assert sorted(graded) == [2.0, 2.0, 2.0, 3.0]
    assert found["grade"] == pytest.approx(9 / 4)
    [ungraded] = [c for c in found["claims"] if c["grade"] is None]
    assert (ungraded["letter"], ungraded["same_grounds"]) == (None, None)


def test_a_run_with_no_graded_claims_has_no_grade():
    run_id, copies = evaluation(tiny=True)
    for copy_id in copies:
        decide(copy_id, run_id)
    client.post(f"/api/runs/{run_id}/finish")
    assert client.get(f"/api/evaluations/{run_id}").json()["grade"] is None
    assert main.average([2.0, None, 3.0]) == 2.5
    assert main.average([None]) is None


def test_on_a_free_model_the_judge_isnt_called(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    asked = []
    monkeypatch.setattr(judge, "grade", lambda h, a: asked.append(h) or B)
    run_id, copies = evaluation(tiny=True)
    decide(copies[0], run_id)
    client.post(f"/api/runs/{run_id}/finish")
    found = client.get(f"/api/evaluations/{run_id}").json()
    assert asked == []
    assert all(c["grade"] is None for c in found["claims"])
    assert (found["grade"], found["judge_cost"]) == (None, 0)
