from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.main import app
from backend.tests.signing import AGENT, headers

PATH = "/api/agent/openapi.json"
SIX = {
    "get_claim",
    "get_policy",
    "get_claim_history",
    "calculate_payout",
    "record_decision",
    "pay_claim",
}

client = TestClient(app)


def document():
    return client.get(PATH, headers=AGENT).json()


def operations(found):
    return {
        operation["operationId"]: operation
        for path in found["paths"].values()
        for operation in path.values()
    }


def test_the_document_has_exactly_the_claim_agents_six_operations_and_no_servers():
    found = document()
    assert set(operations(found)) == SIX
    assert len(operations(found)) == len(SIX)
    assert "servers" not in found


def test_each_operation_keeps_its_id_and_one_line_docstring():
    endpoints = {
        r.operation_id: r.endpoint for r in app.routes if isinstance(r, APIRoute)
    }
    for operation_id, operation in operations(document()).items():
        docstring = endpoints[operation_id].__doc__
        assert docstring
        assert "\n" not in docstring
        assert operation["description"] == docstring


def test_the_payment_still_takes_amount():
    found = document()
    body = operations(found)["pay_claim"]["requestBody"]["content"]
    name = body["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    assert set(found["components"]["schemas"][name]["properties"]) == {"amount"}


def test_no_operation_has_a_key_or_token_input():
    found = document()
    assert "securitySchemes" not in found.get("components", {})
    for operation in operations(found).values():
        assert "security" not in operation
        for parameter in operation.get("parameters", []):
            assert parameter["in"] != "header"
            assert parameter["name"].lower() not in {"x-api-key", "authorization"}


def test_the_document_needs_the_key_or_a_handler():
    assert client.get(PATH, headers=AGENT).status_code == 200
    assert client.get(PATH, headers=headers()).status_code == 200
    assert client.get(PATH).status_code == 401
