import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import agent_service, judge, rule, store
from backend.main import app
from backend.tests.signing import MARCUS, headers
from backend.tests.test_settings import LAB

client = TestClient(app)

LUNA = {"model": "openai/gpt-6-luna", "effort": "low"}
CHANGE = {
    "instructions": "Decline a claim with no receipt.",
    **LUNA,
    "reason": "Most misses were approvals with no receipt",
}
GRADED = {
    "grade": 2.0,
    "letter": "B",
    "same_grounds": 0.9,
    "same_section": 0.2,
    "judge_cost": 0.001,
}


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return self.body

    def raise_for_status(self):
        return None


@pytest.fixture
def service(monkeypatch):
    """A stand-in for the agent's service that accepts every call and records it."""
    sent = []

    def post(url, json, timeout):
        sent.append((url.removeprefix(agent_service.url()), json))
        return FakeResponse(202, {"status": "started"})

    monkeypatch.setattr(agent_service.httpx, "post", post)
    return sent


def answer_key():
    with store.connect() as conn:
        return {r.claim_id: r for r in conn.execute(select(store.answer_key))}


def start_round():
    response = client.post("/api/rounds", headers=headers())
    assert response.status_code == 200, response.text
    return response.json()["round_id"]


def evaluate(round_id, eval_set, version, right, cost=0.001):
    """A round's whole-set evaluation: the first `right` claims decided as the
    handler did, the rest wrongly, each costing `cost`."""
    body = {"eval_set": eval_set, "version": version}
    response = client.post(f"/api/rounds/{round_id}/evaluations", json=body)
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    key = answer_key()
    for i, copy_id in enumerate(client.get(f"/api/runs/{run_id}/claims").json()):
        expected = key[copy_id.split("-", 1)[1]]
        client.post(f"/api/claims/{copy_id}/start", json={"run_id": run_id})
        if i < right:
            decision = {"decision": expected.decision, "payout": expected.payout}
        else:
            wrong = "decline" if expected.decision == "refer" else "refer"
            decision = {"decision": wrong, "payout": 0}
        decision |= {"reason": f"Agent's reason {i}", "policy_section": "9"}
        client.post(f"/api/claims/{copy_id}/decision", json=decision)
        used = {"run_id": run_id, "input_tokens": 1, "output_tokens": 1, "cost": cost}
        client.post(f"/api/claims/{copy_id}/model-calls", json=used)
    client.post(f"/api/runs/{run_id}/finish")
    return run_id


def first_evaluations(round_id, right=20):
    evaluate(round_id, "evaluation", 1, right)
    evaluate(round_id, "held_out", 1, right)
    evaluate(round_id, "held_out", 1, right)


def propose_and_approve(round_id, change=CHANGE):
    client.post(f"/api/rounds/{round_id}/proposed-change", json=change)
    answer = {"decision": "approve", "handler_id": "HND-01", "reason": ""}
    return client.post(f"/api/rounds/{round_id}/decision", json=answer).json()


def current():
    return client.get("/api/configuration/current").json()["version"]


# The rule, with measures worked out by hand against the lab's settings.
OLD = {
    "accuracy": 0.70,
    "grade": 1.50,
    "cost_per_claim": 0.0010,
    "seconds_per_claim": 20,
}


@pytest.mark.parametrize(
    ("new", "kept", "why"),
    [
        # Accuracy up 0.12 > noise 0.10; cost up 90% < tolerance 100%: kept.
        (
            {**OLD, "accuracy": 0.82, "cost_per_claim": 0.0019},
            True,
            "accuracy improved",
        ),
        # Time down 20% > noise 15%, as a fraction of 20 seconds: kept.
        ({**OLD, "seconds_per_claim": 16}, True, "seconds_per_claim improved"),
        # Accuracy up 0.12, but time up 30% > tolerance 25%: rolled back.
        (
            {**OLD, "accuracy": 0.82, "seconds_per_claim": 26},
            False,
            "seconds_per_claim got worse",
        ),
        # The grade down 0.2 > tolerance 0.15, as a difference: rolled back.
        (
            {**OLD, "accuracy": 0.82, "grade": 1.30},
            False,
            "grade got worse",
        ),
        # Accuracy up 0.08 and time down 10%, both within the noise: rolled back.
        (
            {**OLD, "accuracy": 0.78, "seconds_per_claim": 18},
            False,
            "no measure improved by more than its noise",
        ),
        # Below the floor of 0.6 on accuracy: rolled back.
        (
            {**OLD, "accuracy": 0.55, "seconds_per_claim": 10},
            False,
            "it missed the objectives on accuracy",
        ),
        # No grade: rolled back, however good the rest.
        ({**OLD, "accuracy": 0.95, "grade": None}, False, "it has no grade"),
    ],
)
def test_the_rule(new, kept, why):
    assert rule.keep_or_roll_back(new, OLD, LAB) == (kept, why)


def test_an_old_version_with_no_grade_compares_the_rest():
    old = {**OLD, "grade": None}
    assert rule.keep_or_roll_back({**OLD, "accuracy": 0.82}, old, LAB) == (
        True,
        "accuracy improved",
    )


def test_only_a_handler_who_can_sign_off_over_the_limit_acts_on_a_round(service):
    actions = [
        ("/api/rounds", None, "start a round"),
        ("/api/rounds/1/stop", None, "stop a round"),
        ("/api/rounds/1/approve", None, "approve the improver's proposals"),
        ("/api/rounds/1/reject", {"reason": "No"}, "reject the improver's proposals"),
    ]
    for path, body, action in actions:
        assert client.post(path, json=body).status_code == 401
        response = client.post(path, json=body, headers=headers(MARCUS))
        assert response.status_code == 403
        assert response.json()["detail"] == (
            f"Marcus Oyelaran can't {action}. Only a handler who can sign off"
            " payouts over the approval limit can"
        )
    assert service == []
    assert client.get("/api/rounds").json() == []


def test_a_round_starts_in_the_agents_service_one_at_a_time(service):
    round_id = start_round()
    found = client.get(f"/api/rounds/{round_id}").json()
    assert service == [
        ("/rounds", {"round_id": round_id, "thread_id": found["thread_id"]})
    ]
    assert found["thread_id"].startswith("round-")
    assert (found["stage"], found["version_before"], found["recipe_version"]) == (
        "first_evaluations",
        1,
        1,
    )
    assert found["started_by"] == "HND-01"
    response = client.post("/api/rounds", headers=headers())
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Round {round_id} is open. Stop it or wait for it to finish"
    )
    client.post(f"/api/rounds/{round_id}/stop", headers=headers())
    assert start_round() == round_id + 1


def test_a_refused_start_leaves_no_round(monkeypatch):
    refusal = FakeResponse(409, {"detail": "A dispatch is running"})
    monkeypatch.setattr(agent_service.httpx, "post", lambda *a, **kw: refusal)
    response = client.post("/api/rounds", headers=headers())
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "A dispatch is running",
    )
    assert client.get("/api/rounds").json() == []


def test_a_start_the_service_cant_take_leaves_no_round(monkeypatch):
    class NotFound(FakeResponse):
        def raise_for_status(self):
            raise httpx.HTTPStatusError("404", request=None, response=None)  # type: ignore[arg-type]

    monkeypatch.setattr(agent_service.httpx, "post", lambda *a, **kw: NotFound(404, {}))
    with pytest.raises(httpx.HTTPStatusError):
        client.post("/api/rounds", headers=headers())
    assert client.get("/api/rounds").json() == []


def test_a_round_keeps_its_settings_when_they_change(service):
    round_id = start_round()
    changed = {**LAB, "round_spending_limit": 2.0, "agents_at_once": 3}
    client.put("/api/settings", json=changed, headers=headers())
    found = client.get(f"/api/rounds/{round_id}").json()
    assert found["settings"] == LAB
    assert found["left"] == 0.5
    run_id = evaluate(round_id, "held_out", 1, 0, cost=0)
    assert client.get(f"/api/evaluations/{run_id}").json()["n"] == 25


def test_a_whole_round_keeps_a_better_version(service, monkeypatch):
    monkeypatch.setattr(judge, "grade", lambda handler, agent: GRADED)
    round_id = start_round()
    client.post(f"/api/rounds/{round_id}/stage", json={"stage": "first_evaluations"})
    first_evaluations(round_id, right=15)
    client.post(f"/api/rounds/{round_id}/stage", json={"stage": "improving"})
    improver_call = {"input_tokens": 100, "output_tokens": 50, "cost": 0.01}
    client.post(f"/api/rounds/{round_id}/model-calls", json=improver_call)
    found = propose_and_approve(round_id)
    assert (found["version"], found["stage"]) == (2, "improving")
    assert current() == 1
    client.post(f"/api/rounds/{round_id}/stage", json={"stage": "evaluations"})
    evaluate(round_id, "evaluation", 2, 24)
    evaluate(round_id, "held_out", 2, 24)
    evaluate(round_id, "held_out", 2, 24)
    found = client.post(f"/api/rounds/{round_id}/verdict").json()
    assert (found["stage"], found["kept"], found["why"]) == (
        "verdict",
        True,
        "accuracy improved",
    )
    assert found["held_out_before"]["accuracy"] < found["held_out_after"]["accuracy"]
    assert current() == 2
    # The improver's call, six runs of claim calls and the judge on each claim.
    with store.connect() as conn:
        claims = conn.execute(select(store.claims).where(store.claims.c.run_id > 0))
        copies = len(claims.all())
    assert found["spent"] == pytest.approx(0.01 + copies * (0.001 + 0.001))
    client.post(f"/api/rounds/{round_id}/stage", json={"stage": "finished"})
    # A round stopped after its verdict can't be stopped, and keeps it.
    response = client.post(f"/api/rounds/{round_id}/stop", headers=headers())
    assert response.status_code == 409
    assert current() == 2


def test_a_version_with_no_grade_is_rolled_back(service):
    round_id = start_round()
    first_evaluations(round_id)
    propose_and_approve(round_id)
    for eval_set in ["evaluation", "held_out", "held_out"]:
        evaluate(round_id, eval_set, 2, 25)
    found = client.post(f"/api/rounds/{round_id}/verdict").json()
    assert (found["kept"], found["why"]) == (False, "it has no grade")
    assert current() == 1
    versions = client.get("/api/configuration/versions").json()
    assert (versions[0]["version"], versions[0]["kept"]) == (2, False)


def test_a_proposal_changes_nothing_until_approved(service):
    round_id = start_round()
    found = client.post(f"/api/rounds/{round_id}/proposed-change", json=CHANGE).json()
    assert found["stage"] == "change_waiting"
    assert found["proposal"]["before"] == LUNA
    assert found["proposal"]["changed_lines"][-1] == "+Decline a claim with no receipt."
    assert len(client.get("/api/configuration/versions").json()) == 1
    response = client.post(f"/api/rounds/{round_id}/approve", headers=headers())
    assert response.json() == {"status": "started"}
    assert service[-1] == (
        f"/rounds/{round_id}/decision",
        {"decision": "approve", "reason": "", "handler_id": "HND-01"},
    )
    # The backend records nothing of its own: the service records the answer.
    assert client.get(f"/api/rounds/{round_id}").json()["events"] == []
    assert len(client.get("/api/configuration/versions").json()) == 1


def test_a_rejection_records_the_handlers_reason(service):
    round_id = start_round()
    client.post(f"/api/rounds/{round_id}/proposed-change", json=CHANGE)
    reason = {"reason": "Too broad: some claims have no receipt by design"}
    client.post(f"/api/rounds/{round_id}/reject", json=reason, headers=headers())
    assert service[-1][1]["reason"] == reason["reason"]
    answer = {"decision": "reject", "handler_id": "HND-01", **reason}
    found = client.post(f"/api/rounds/{round_id}/decision", json=answer).json()
    assert (found["stage"], found["proposal"], found["version"]) == (
        "improving",
        None,
        None,
    )
    [event] = found["events"]
    assert (event["kind"], event["decision"], event["handler"], event["reason"]) == (
        "change",
        "reject",
        "Dana Whitfield",
        reason["reason"],
    )
    assert len(client.get("/api/configuration/versions").json()) == 1
    response = client.post(f"/api/rounds/{round_id}/approve", headers=headers())
    assert response.status_code == 409


def test_an_approval_refused_by_the_service_records_nothing(monkeypatch):
    monkeypatch.setattr(
        agent_service.httpx, "post", lambda *a, **kw: FakeResponse(202, {})
    )
    round_id = start_round()
    client.post(f"/api/rounds/{round_id}/proposed-change", json=CHANGE)
    refusal = FakeResponse(409, {"detail": "A dispatch is running"})
    monkeypatch.setattr(agent_service.httpx, "post", lambda *a, **kw: refusal)
    response = client.post(f"/api/rounds/{round_id}/approve", headers=headers())
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "A dispatch is running",
    )
    found = client.get(f"/api/rounds/{round_id}").json()
    assert (found["stage"], found["events"]) == ("change_waiting", [])
    assert found["proposal"]["instructions"] == CHANGE["instructions"]


def test_a_proposal_outside_the_models_allowed_goes_back(service):
    round_id = start_round()
    for model, effort in [("openai/gpt-6", "low"), ("openai/gpt-6-luna", "high")]:
        body = {**CHANGE, "model": model, "effort": effort}
        response = client.post(f"/api/rounds/{round_id}/proposed-change", json=body)
        assert response.status_code == 422
        assert response.json()["detail"] == (
            f"{model} at {effort} isn't allowed. Choose from the models allowed"
        )
    assert client.get(f"/api/rounds/{round_id}").json()["proposal"] is None


def test_an_approved_recipe_becomes_the_next(service):
    round_id = start_round()
    recipe = {"recipe": "1. Read the results.\n", "reason": "Shorter"}
    found = client.post(f"/api/rounds/{round_id}/proposed-recipe", json=recipe).json()
    assert found["stage"] == "recipe_waiting"
    assert "+1. Read the results." in found["proposal"]["changed_lines"]
    answer = {"decision": "approve", "handler_id": "HND-01", "reason": ""}
    client.post(f"/api/rounds/{round_id}/decision", json=answer)
    with store.connect() as conn:
        latest = conn.execute(select(store.recipes).order_by(store.recipes.c.version))
        newest = latest.mappings().all()[-1]
    assert (newest["version"], newest["text"]) == (2, recipe["recipe"])
    assert newest["proposed_by"] == f"improver (round {round_id})"
    client.post(f"/api/rounds/{round_id}/stage", json={"stage": "finished"})
    assert start_round() == round_id + 1
    assert client.get(f"/api/rounds/{round_id + 1}").json()["recipe_version"] == 2


def test_a_decision_needs_a_handler_who_can_sign_off_over_the_limit(service):
    round_id = start_round()
    client.post(f"/api/rounds/{round_id}/proposed-change", json=CHANGE)
    answer = {"decision": "approve", "handler_id": "HND-02", "reason": ""}
    response = client.post(f"/api/rounds/{round_id}/decision", json=answer)
    assert response.status_code == 403
    answer["handler_id"] = "HND-99"
    response = client.post(f"/api/rounds/{round_id}/decision", json=answer)
    assert response.status_code == 404


def test_the_improvers_view_holds_nothing_from_the_held_out_set_but_scores(
    service, monkeypatch
):
    monkeypatch.setattr(judge, "grade", lambda handler, agent: GRADED)
    round_id = start_round()
    first_evaluations(round_id, right=18)
    found = client.get(f"/api/rounds/{round_id}/results").json()
    view = found["evaluation_set"]
    assert len(view["wrong"]) == 7
    assert len(view["explained_least_well"]) == 5
    miss = view["wrong"][0]
    expected = answer_key()[miss["claim_id"]]
    assert miss["handler"] == {
        "decision": expected.decision,
        "payout": expected.payout,
        "reason": expected.reason,
        "reference": expected.reference,
    }
    assert miss["judge"] == {
        "grade": 2.0,
        "letter": "B",
        "same_grounds": True,
        "same_section": False,
    }
    assert miss["agent"]["section"] == "9"
    assert found["scores"]["held_out"]["runs"] == 2
    assert found["scores"]["evaluation"]["runs"] == 1
    assert found["rule"] == rule.RULE
    assert found["objectives_on_the_held_out_set"] == LAB["objectives"]
    assert (found["noise"], found["tolerance"]) == (LAB["noise"], LAB["tolerance"])
    assert found["spending_left"] < LAB["round_spending_limit"]
    assert len(found["models_allowed"]) == 3
    text = json.dumps(found)
    held_out = [k for k in answer_key().values() if k.eval_set == "held_out"]
    assert len(held_out) > 0
    for key in held_out:
        assert key.claim_id not in text
        assert key.reason not in text


def test_earlier_rounds_are_in_the_improvers_view(service):
    first = start_round()
    first_evaluations(first)
    propose_and_approve(first)
    client.post(f"/api/rounds/{first}/stop", headers=headers())
    second = start_round()
    [earlier] = client.get(f"/api/rounds/{second}/results").json()["earlier_rounds"]
    assert (earlier["round"], earlier["version"], earlier["stage"]) == (
        first,
        2,
        "stopped",
    )
    assert earlier["reason"] == CHANGE["reason"]


def test_an_evaluation_over_the_rounds_limit_is_refused(service):
    round_id = start_round()
    # $0.03 a claim, 25 claims: $0.75 spent, over the $0.50 limit.
    evaluate(round_id, "evaluation", 1, 25, cost=0.03)
    body = {"eval_set": "held_out", "version": 1}
    response = client.post(f"/api/rounds/{round_id}/evaluations", json=body)
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Refused: the evaluation could cost")


GEMINI = {"model": "google/gemini-3.5-flash-lite", "effort": "low"}


def test_a_model_with_no_runs_is_estimated_from_the_current_models_prices(service):
    round_id = start_round()
    # $0.001 a claim on Luna; Gemini's output price is 5 times Luna's, its input 3.
    evaluate(round_id, "evaluation", 1, 25, cost=0.001)
    propose_and_approve(round_id, {**CHANGE, **GEMINI})
    count = sum(k.eval_set == "held_out" for k in answer_key().values())
    left = client.get(f"/api/rounds/{round_id}").json()["left"]
    likely = count * 0.001 * 5
    assert likely < left
    body = {"eval_set": "held_out", "version": 2}
    assert (
        client.post(f"/api/rounds/{round_id}/evaluations", json=body).status_code == 200
    )


def test_a_rounds_evaluation_runs_on_the_versions_own_model(service, monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    round_id = start_round()
    propose_and_approve(round_id, {**CHANGE, **GEMINI})
    # A model in the body is no part of the operation, so it can't swap the version's.
    body = {"eval_set": "held_out", "version": 2, **LUNA}
    run = client.post(f"/api/rounds/{round_id}/evaluations", json=body).json()
    assert (run["model"], run["effort"]) == (GEMINI["model"], GEMINI["effort"])
    client.post("/api/runs/stop-running")
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    run = client.post(f"/api/rounds/{round_id}/evaluations", json=body).json()
    assert (run["model"], run["effort"]) == ("openrouter/free", "default")
    found = client.get(f"/api/runs/{run['run_id']}/configuration").json()
    assert (found["version"], found["round_id"]) == (2, round_id)


def test_a_restart_stops_a_working_round_and_leaves_a_waiting_one(service):
    round_id = start_round()
    assert client.post("/api/rounds/stop-working").json() == {"stopped": 1}
    found = client.get(f"/api/rounds/{round_id}").json()
    assert (found["stage"], found["stopped_by"]) == ("stopped", None)
    waiting = start_round()
    client.post(f"/api/rounds/{waiting}/proposed-change", json=CHANGE)
    assert client.post("/api/rounds/stop-working").json() == {"stopped": 0}
    assert client.get(f"/api/rounds/{waiting}").json()["stage"] == "change_waiting"


def test_the_recipes_versions_list_newest_first_with_their_changes(service):
    round_id = start_round()
    recipe = {"recipe": "1. Read the results.\n", "reason": "Shorter"}
    client.post(f"/api/rounds/{round_id}/proposed-recipe", json=recipe)
    answer = {"decision": "approve", "handler_id": "HND-01", "reason": ""}
    client.post(f"/api/rounds/{round_id}/decision", json=answer)
    newest, first = client.get("/api/recipes").json()
    assert (newest["version"], first["version"]) == (2, 1)
    assert "+1. Read the results." in newest["changed_lines"]
    assert first["changed_lines"] is None


def test_an_evaluation_cut_short_by_the_limit_is_stopped_and_rolled_back(service):
    round_id = start_round()
    first_evaluations(round_id)
    propose_and_approve(round_id)
    body = {"eval_set": "evaluation", "version": 2}
    run_id = client.post(f"/api/rounds/{round_id}/evaluations", json=body).json()[
        "run_id"
    ]
    first, second = client.get(f"/api/runs/{run_id}/claims").json()[:2]
    client.post(f"/api/claims/{first}/start", json={"run_id": run_id})
    used = {"run_id": run_id, "input_tokens": 1, "output_tokens": 1, "cost": 0.5}
    client.post(f"/api/claims/{first}/model-calls", json=used)
    response = client.post(f"/api/claims/{second}/start", json={"run_id": run_id})
    assert response.status_code == 409
    assert response.json()["detail"] == f"Round {round_id} has spent its limit"
    client.post(f"/api/runs/{run_id}/finish")
    assert client.get(f"/api/evaluations/{run_id}").json()["status"] == "stopped"
    found = client.post(f"/api/rounds/{round_id}/verdict").json()
    assert (found["kept"], found["why"]) == (False, "its evaluations were stopped")
    assert current() == 1


def test_a_version_the_round_cant_afford_is_rolled_back(service):
    round_id = start_round()
    first_evaluations(round_id)
    propose_and_approve(round_id)
    evaluate(round_id, "evaluation", 2, 25)
    found = client.post(f"/api/rounds/{round_id}/verdict").json()
    assert (found["kept"], found["why"]) == (
        False,
        "the round couldn't afford to evaluate it",
    )


def test_a_stopped_round_refuses_what_it_records_and_leaves_the_current_version(
    service,
):
    round_id = start_round()
    first_evaluations(round_id)
    propose_and_approve(round_id)
    body = {"eval_set": "evaluation", "version": 2}
    run_id = client.post(f"/api/rounds/{round_id}/evaluations", json=body).json()[
        "run_id"
    ]
    found = client.post(f"/api/rounds/{round_id}/stop", headers=headers()).json()
    assert (found["stage"], found["stopped_by"]) == ("stopped", "HND-01")
    assert client.get(f"/api/evaluations/{run_id}").json()["status"] == "stopped"
    copy_id = client.get(f"/api/runs/{run_id}/claims").json()[0]
    response = client.post(f"/api/claims/{copy_id}/start", json={"run_id": run_id})
    assert response.json()["detail"] == f"Round {round_id} was stopped"
    for path, sent in [
        ("stage", {"stage": "improving"}),
        ("proposed-change", CHANGE),
        ("proposed-recipe", {"recipe": "1. Stop.", "reason": "Shorter"}),
        ("decision", {"decision": "approve", "handler_id": "HND-01", "reason": ""}),
        ("model-calls", {"input_tokens": 1, "output_tokens": 1, "cost": 0.1}),
        ("evaluations", {"eval_set": "held_out", "version": 1}),
        ("verdict", None),
    ]:
        response = client.post(f"/api/rounds/{round_id}/{path}", json=sent)
        assert response.status_code == 409, path
        assert response.json()["detail"] == f"Round {round_id} is stopped"
    assert current() == 1
    versions = client.get("/api/configuration/versions").json()
    assert (versions[0]["version"], versions[0]["kept"]) == (2, False)


def test_a_round_evaluates_only_its_versions(service):
    round_id = start_round()
    body = {"eval_set": "held_out", "version": 2}
    response = client.post(f"/api/rounds/{round_id}/evaluations", json=body)
    assert response.status_code == 409
    assert (
        response.json()["detail"] == f"Version 2 isn't under test in round {round_id}"
    )


def test_finishing_twice_grades_once(service, monkeypatch):
    asked = []
    monkeypatch.setattr(judge, "grade", lambda h, a: asked.append(h) or GRADED)
    body = {**LUNA, "kind": "evaluation", "n": 5, "eval_set": "held_out"}
    run_id = client.post("/api/runs", json={**body, "tiny": True, "version": 1}).json()[
        "run_id"
    ]
    copy_id = client.get(f"/api/runs/{run_id}/claims").json()[0]
    client.post(f"/api/claims/{copy_id}/start", json={"run_id": run_id})
    decision = {"decision": "refer", "payout": 0, "reason": "?", "policy_section": "9"}
    client.post(f"/api/claims/{copy_id}/decision", json=decision)
    first = client.post(f"/api/runs/{run_id}/finish").json()
    second = client.post(f"/api/runs/{run_id}/finish").json()
    assert len(asked) == 1
    assert first["finished_at"] == second["finished_at"]


def test_the_round_operations_follow_the_api_rules():
    schema = client.get("/openapi.json").json()
    found = {
        operation["operationId"]
        for path, item in schema["paths"].items()
        if path.startswith("/api/rounds")
        for operation in item.values()
    }
    assert found == {
        "start_round",
        "list_rounds",
        "get_round",
        "stop_round",
        "approve_proposal",
        "reject_proposal",
        "read_round_results",
        "record_round_stage",
        "record_proposed_change",
        "record_proposed_recipe",
        "record_round_decision",
        "record_improver_call",
        "create_round_evaluation",
        "apply_verdict",
        "stop_working_rounds",
    }
    for path, item in schema["paths"].items():
        if path.startswith("/api/rounds"):
            for operation in item.values():
                assert operation["description"].count("\n") == 0
