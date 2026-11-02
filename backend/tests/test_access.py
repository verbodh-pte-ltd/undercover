import re
import secrets

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend import agent_service
from backend import main as backend_main
from backend.main import app
from backend.tests.signing import AGENT, MARCUS, headers
from backend.tests.test_settings import LAB as LAB_SETTINGS

# No sign-in and no key unless a test sends one.
client = TestClient(app)

DECISION = {
    "decision": "refer",
    "payout": 0,
    "reason": "Needs a handler.",
    "policy_section": "Manual 2",
}
LARGE = {**DECISION, "decision": "approve", "payout": 1250.0}

# Who may call each route, by who calls it today (docs/PLAN_SECURE.md, choice 3).
SHARED = {
    "get_claim",
    "get_policy",
    "get_claim_history",
    "list_recipes",
    "get_agent_openapi",
}
HANDLER = {
    "list_claims",
    "list_steps",
    "get_settings",
    "get_current_run",
    "list_versions",
    "list_evaluations",
    "get_evaluation",
    "list_handlers",
    "get_signed_in_handler",
    "list_rounds",
    "change_settings",
    "start_dispatch",
    "start_evaluation",
    "sign_off",
    "start_round",
    "stop_round",
    "approve_proposal",
    "reject_proposal",
}
KEY = {
    "list_sections",
    "read_section",
    "calculate_payout",
    "record_decision",
    "pay_claim",
    "hold_for_sign_off",
    "start_claim",
    "release_claim",
    "add_step",
    "record_model_call",
    "record_work_time",
    "get_current_configuration",
    "get_run_configuration",
    "create_run",
    "list_run_claims",
    "finish_run",
    "stop_running_runs",
    "get_round",
    "read_round_results",
    "record_round_stage",
    "record_proposed_change",
    "record_proposed_recipe",
    "record_round_decision",
    "record_improver_call",
    "create_round_evaluation",
    "apply_verdict",
    "stop_working_rounds",
    "list_waiting_claims",
    "list_unfinished_claims",
    "get_policy_wording",
}
GROUPS = {
    backend_main.key_or_handler: "shared",
    backend_main.signed_in_handler: "handler",
    backend_main.agent_key: "key",
}
# Every handler action, with a body it would accept.
ACTIONS = [
    ("post", "/api/claims/CLM-1001/sign-off", None),
    ("post", "/api/dispatches", {"n": 3}),
    ("post", "/api/evaluations", {"n": 3, "eval_set": "evaluation", "tiny": True}),
    ("post", "/api/rounds", None),
    ("post", "/api/rounds/1/stop", None),
    ("post", "/api/rounds/1/approve", None),
    ("post", "/api/rounds/1/reject", {"reason": "Too broad"}),
    ("put", "/api/settings", LAB_SETTINGS),
]


@pytest.fixture(autouse=True)
def no_agent(monkeypatch):
    monkeypatch.setattr(
        agent_service, "start", lambda path, body: {"status": "started"}
    )


def routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def groups(route: APIRoute) -> list[str]:
    """The groups whose checks a route's dependencies include."""
    found, waiting = [], list(route.dependant.dependencies)
    while waiting:
        dependency = waiting.pop()
        if dependency.call in GROUPS:
            found.append(GROUPS[dependency.call])
        waiting += dependency.dependencies
    return found


def test_an_agent_route_needs_the_right_key():
    path = "/api/claims/CLM-1001/decision"
    assert client.post(path, json=DECISION).status_code == 401
    wrong = {"X-API-Key": "wrong-key-of-at-least-32-characters-long"}
    assert client.post(path, json=DECISION, headers=wrong).status_code == 401
    assert client.post(path, json=DECISION, headers=headers()).status_code == 401
    assert client.post(path, json=DECISION, headers=AGENT).status_code == 200


def test_the_key_is_compared_in_constant_time(monkeypatch):
    compared, real = [], secrets.compare_digest

    def compare_digest(a: bytes, b: bytes) -> bool:
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(backend_main.secrets, "compare_digest", compare_digest)
    client.get("/api/documents/policy_wording/sections", headers=AGENT)
    assert compared == [(AGENT["X-API-Key"].encode(), AGENT["X-API-Key"].encode())]


def test_a_shared_read_takes_the_key_or_a_handler():
    path = "/api/claims/CLM-1001"
    assert client.get(path, headers=AGENT).status_code == 200
    assert client.get(path, headers=headers()).status_code == 200
    assert client.get(path).status_code == 401
    bad = {"Authorization": "Bearer nonsense"}
    assert client.get(path, headers=bad).status_code == 401


def test_a_dashboard_read_needs_a_handler_not_the_key():
    assert client.get("/api/claims", headers=AGENT).status_code == 401
    assert client.get("/api/claims", headers=headers()).status_code == 200


@pytest.mark.parametrize(("method", "path", "body"), ACTIONS)
def test_the_key_never_stands_in_for_a_handler(method, path, body):
    client.post("/api/claims/CLM-1001/decision", json=LARGE, headers=AGENT)
    response = client.request(method, path, json=body, headers=AGENT)
    assert response.status_code == 401
    claim = client.get("/api/claims/CLM-1001", headers=AGENT).json()
    assert claim["signed_off_by"] is None


def test_every_route_is_in_exactly_one_group():
    found = {r.operation_id: groups(r) for r in routes()}
    assert found.pop("health") == []
    assert all(len(g) == 1 for g in found.values()), found
    assert {o for o, g in found.items() if g == ["shared"]} == SHARED
    assert {o for o, g in found.items() if g == ["handler"]} == HANDLER
    assert {o for o, g in found.items() if g == ["key"]} == KEY


def test_every_route_but_health_refuses_a_request_with_neither():
    assert client.get("/api/health").status_code == 200
    for route in routes():
        if route.operation_id == "health":
            continue
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods or []:
            assert client.request(method, path).status_code == 401, route.path


def test_no_operation_has_a_key_or_token_parameter():
    document = client.get("/openapi.json").json()
    for path, operations in document["paths"].items():
        for operation in operations.values():
            names = {p["name"].lower() for p in operation.get("parameters", [])}
            assert not names & {"x-api-key", "authorization", "request"}, path
            assert all(p["in"] != "header" for p in operation.get("parameters", []))


def test_without_a_key_set_handlers_still_work_and_key_routes_refuse(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_API_KEY")
    assert client.get("/api/claims", headers=headers()).status_code == 200
    assert client.get("/api/claims/CLM-1001", headers=headers()).status_code == 200
    changed = client.put("/api/settings", json=LAB_SETTINGS, headers=headers())
    assert changed.status_code == 200
    assert client.get("/api/handlers", headers=headers(MARCUS)).status_code == 200
    empty = {"X-API-Key": ""}
    for route in routes():
        if not groups(route) or groups(route)[0] == "handler":
            continue
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods or []:
            for sent in [empty, AGENT]:
                response = client.request(method, path, headers=sent)
                assert response.status_code == 401, route.path
