from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, update

from backend import store
from backend.main import app

client = TestClient(app)

CLAIM = Path("backend/instructions/claim.md").read_text(encoding="utf-8")
RECIPE = Path("backend/instructions/recipe.md").read_text(encoding="utf-8")


def new_run(
    kind="evaluation", eval_set: str | None = "held_out", tiny=False, version=1
):
    body = {
        "kind": kind,
        "n": 3,
        "eval_set": eval_set,
        "tiny": tiny,
        "model": "openai/gpt-6-luna",
        "effort": "low",
        "version": version,
    }
    return client.post("/api/runs", json=body)


def add_version(version, instructions, kept, model="openai/gpt-6-luna"):
    with store.engine().begin() as conn:
        conn.execute(
            insert(store.config_versions).values(
                version=version,
                instructions=instructions,
                model=model,
                effort="medium",
                reason="A rule for each mistake",
                proposed_by="improver (round 1)",
                approved_by="HND-01",
                approved_at="2026-11-01T09:00:00+00:00",
                kept=kept,
            )
        )


def finished_in_a_round(run_id):
    with store.engine().begin() as conn:
        query = update(store.runs).where(store.runs.c.run_id == run_id)
        conn.execute(query.values(round_id=1))
    client.post(f"/api/runs/{run_id}/finish")


def test_seeding_makes_version_1_of_the_configuration_and_the_recipe():
    [first] = client.get("/api/configuration/versions").json()
    assert {k: first[k] for k in ["version", "instructions", "model", "effort"]} == {
        "version": 1,
        "instructions": CLAIM,
        "model": "openai/gpt-6-luna",
        "effort": "low",
    }
    assert (first["proposed_by"], first["current"]) == ("seed", True)
    assert (first["before"], first["changed_lines"], first["held_out"]) == (
        None,
        None,
        None,
    )
    with store.connect() as conn:
        recipe = conn.execute(select(store.recipes)).mappings().one()
    assert (recipe["version"], recipe["text"]) == (1, RECIPE)
    assert "propose_recipe" in RECIPE


def test_reseeding_takes_both_back_to_version_1():
    add_version(2, "New instructions.", True)
    with store.engine().begin() as conn:
        conn.execute(
            insert(store.recipes).values(
                version=2,
                text="A new recipe.",
                reason="Better",
                proposed_by="improver (round 1)",
                approved_by="HND-01",
                approved_at="2026-11-01T09:00:00+00:00",
            )
        )
    store.reseed()
    assert [v["version"] for v in client.get("/api/configuration/versions").json()] == [
        1
    ]
    with store.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(store.recipes)) == 1


def test_the_current_configuration_holds_no_scores():
    assert client.get("/api/configuration/current").json() == {
        "version": 1,
        "instructions": CLAIM,
        "model": "openai/gpt-6-luna",
        "effort": "low",
    }


def test_the_current_version_is_the_newest_kept():
    add_version(2, CLAIM + "Kept rule.\n", True)
    add_version(3, CLAIM + "Rolled back rule.\n", False)
    assert client.get("/api/configuration/current").json()["version"] == 2
    found = client.get("/api/configuration/versions").json()
    assert [(v["version"], v["current"]) for v in found] == [
        (3, False),
        (2, True),
        (1, False),
    ]


def test_each_version_shows_what_changed_from_the_one_before():
    changed = CLAIM.replace("Work the claim in this order:", "Work in this order:")
    add_version(2, changed + "Never approve without a receipt.\n", True, "x/model")
    second = client.get("/api/configuration/versions").json()[0]
    assert second["before"] == {"model": "openai/gpt-6-luna", "effort": "low"}
    assert (second["model"], second["effort"]) == ("x/model", "medium")
    assert second["changed_lines"] == [
        "-Work the claim in this order:",
        "+Work in this order:",
        "+Never approve without a receipt.",
    ]


def test_a_dispatch_and_an_evaluation_record_their_version():
    add_version(2, "Version 2.", True)
    dispatch = new_run("dispatch", None, version=2).json()
    assert dispatch["version"] == 2
    client.post("/api/runs/stop-running")
    run_id = new_run(version=1).json()["run_id"]
    assert client.get(f"/api/evaluations/{run_id}").json()["version"] == 1
    assert client.get("/api/evaluations").json()[0]["version"] == 1


def test_a_run_on_an_unknown_version_is_refused():
    response = new_run(version=9)
    assert response.status_code == 404
    assert response.json()["detail"] == "Version 9 not found"


def test_a_runs_configuration_is_its_version_with_its_model_and_effort():
    add_version(2, "Version 2.", True)
    body = {"model": "openrouter/free", "effort": "default", "version": 2}
    run = new_run("dispatch", None).json()
    client.post("/api/runs/stop-running")
    with store.engine().begin() as conn:
        query = update(store.runs).where(store.runs.c.run_id == run["run_id"])
        conn.execute(query.values(**body))
    assert client.get(f"/api/runs/{run['run_id']}/configuration").json() == {
        **body,
        "instructions": "Version 2.",
    }


def test_scores_average_only_whole_held_out_evaluations_that_rounds_ran():
    outside = new_run().json()["run_id"]
    client.post(f"/api/runs/{outside}/finish")
    assert client.get("/api/configuration/versions").json()[0]["held_out"] is None
    counted = []
    for eval_set, tiny in [
        ("held_out", False),
        ("held_out", True),
        ("evaluation", False),
        ("held_out", False),
    ]:
        run_id = new_run(eval_set=eval_set, tiny=tiny).json()["run_id"]
        if eval_set == "held_out" and not tiny:
            counted.append(run_id)
            copy_id = client.get(f"/api/runs/{run_id}/claims").json()[0]
            cost = 2.5 * len(counted)
            used = {
                "run_id": run_id,
                "input_tokens": 1,
                "output_tokens": 1,
                "cost": cost,
            }
            client.post(f"/api/claims/{copy_id}/model-calls", json=used)
        finished_in_a_round(run_id)
    scores = client.get("/api/configuration/versions").json()[0]["held_out"]
    # Two runs count: 25 claims each, none decided, $2.50 and $5.00 between them.
    accuracy = client.get(f"/api/evaluations/{counted[0]}").json()["accuracy"]
    assert scores == {
        "accuracy": accuracy,
        "cost_per_claim": (0.1 + 0.2) / 2,
        "seconds_per_claim": 0.0,
        "runs": 2,
    }


def test_the_configuration_operations_follow_the_api_rules():
    schema = client.get("/openapi.json").json()
    for path in [
        "/api/configuration/current",
        "/api/configuration/versions",
        "/api/runs/{run_id}/configuration",
    ]:
        operation = schema["paths"][path]["get"]
        assert operation["operationId"]
        assert operation["description"].count("\n") == 0
    assert "/api/instructions" not in schema["paths"]
