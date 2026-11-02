import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from context import server

HOST = "abc123.lambda-url.us-east-1.on.aws"
SOURCES = {
    "data_dictionary.yaml": ("The data catalog", "Data governance", "2026-09-15"),
    "glossary.yaml": ("The policy wording, version 2026.1", "Legal", "2026-09-01"),
    "plans.yaml": ("Policy administration", "Product", "2026-09-01"),
    "claim_rules.yaml": (
        "Claims operations",
        "Head of claims operations",
        "2026-09-01",
    ),
    "reference_data.yaml": (
        "Outside data providers",
        "Claims operations",
        "2026-09-30",
    ),
    "policy_wording.md": ("Published policy documents", "Legal", "2026-09-01"),
    "claims_manual.md": (
        "Claims operations",
        "Head of claims operations",
        "2026-09-01",
    ),
}


def named(source):
    return source["file"], (source["system"], source["owner"], source["reviewed"])


def test_each_document_is_split_into_tagged_sections_without_its_front_matter():
    manual = server.PREPARED["claims_manual"]
    assert manual["version"] == "2026.2"
    assert server.PREPARED["policy_wording"]["version"] == "2026.1"
    numbers = [s["number"] for s in manual["sections"]]
    assert numbers == [str(n) for n in range(1, 15)]
    first = manual["sections"][0]
    assert (first["document"], first["title"]) == ("claims_manual", "Purpose and scope")
    assert first["text"].startswith("## 1. Purpose and scope\n")
    for prepared in server.PREPARED.values():
        for section in prepared["sections"]:
            assert "reviewed:" not in section["text"]
            assert "# UnderCover" not in section["text"]


def test_a_term_in_any_case():
    found = server.lookup_term("uNaTTended")
    assert found["term"] == "Unattended"
    assert found["definition"].startswith("Out of your sight")
    assert found["sections"] == [5]


def test_an_unknown_term_gets_the_terms_the_glossary_defines():
    found = server.lookup_term("Volcano")
    assert found["found"] is False
    assert "Unattended" in found["terms_defined"]
    assert len(found["terms_defined"]) == len(server.GLOSSARY["terms"])


@pytest.mark.parametrize("record", ["claim", "policy", "previous_claim"])
def test_each_records_fields(record):
    found = server.describe_fields(record)
    assert found["record"] == record
    assert found["fields"]
    assert all({"field", "meaning"} <= set(f) for f in found["fields"])


@pytest.mark.parametrize(
    ("plan", "deductible"), [("economy", 100), ("premium", 50), ("first", 0)]
)
def test_each_plan_with_its_deductible_benefits_and_the_add_ons(plan, deductible):
    found = server.get_plan(plan)
    assert (found["plan"]["plan"], found["plan"]["deductible"]) == (plan, deductible)
    assert found["deductible_applies_to"] == ["baggage", "emergency_medical"]
    assert [a["add_on"] for a in found["add_ons"]] == [
        "adventure_sports",
        "pre_existing_waiver",
        "electronics_plus",
    ]


def test_the_claim_rules():
    rules = {r["rule"]: r for r in server.get_claim_rules()["rules"]}
    assert rules["filing_window"]["days"] == 90
    assert rules["repeat_claims"]["claims"] == 3


def test_a_reference_search_needs_every_word_in_any_case():
    found = server.search_reference_data("lisbon STRIKE")["facts"]
    assert len(found) == 1
    assert found[0]["topic"] == "events"
    assert found[0]["fact"].startswith("Lisbon, June 20, 2026")
    assert server.search_reference_data("lisbon hurricane")["facts"] == []


def test_a_document_search_names_each_sections_file():
    found = server.search_documents("deductible")["sections"]
    files = {(s["document"], s["source"]["file"]) for s in found}
    assert files == {
        ("policy_wording", "policy_wording.md"),
        ("claims_manual", "claims_manual.md"),
    }
    assert {"number", "title"} <= set(found[0])


def test_a_section_that_exists_and_one_that_does_not():
    section = server.read_section("claims_manual", "9")
    assert (section["title"], section["version"]) == ("Repeat claims", "2026.2")
    assert section["text"].startswith("## 9. Repeat claims")
    listed = server.list_sections("policy_wording")["sections"]
    assert listed[2] == {"number": "3", "title": "Trip Delay"}
    with pytest.raises(ToolError, match="Section 99 of claims_manual not found"):
        server.read_section("claims_manual", "99")


def test_every_answer_names_its_file_and_its_source():
    answers = [
        server.lookup_term("Carrier"),
        server.lookup_term("Volcano"),
        server.describe_fields("claim"),
        server.get_plan("first"),
        server.get_claim_rules(),
        server.search_reference_data("strike"),
        server.list_sections("policy_wording"),
        server.read_section("claims_manual", "14"),
    ]
    seen = {named(a["source"]) for a in answers}
    seen |= {named(s["source"]) for s in server.search_documents("claim")["sections"]}
    assert seen == {(f, s) for f, s in SOURCES.items()}


@dataclass
class Context:
    function_name: str = "undercover-context"
    aws_request_id: str = "request-1"


def function_url_post(body):
    """A Lambda Function URL's event for a POST to /mcp, in the payload format 2.0."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/mcp",
        "rawQueryString": "",
        "headers": {
            "host": HOST,
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc123",
            "domainName": HOST,
            "domainPrefix": "abc123",
            "http": {
                "method": "POST",
                "path": "/mcp",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
                "userAgent": "test",
            },
            "requestId": "request-1",
            "routeKey": "$default",
            "stage": "$default",
            "time": "02/Nov/2026:10:00:00 +0000",
            "timeEpoch": 1793613600000,
        },
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


def invoke(method, params, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    response = server.handler(function_url_post(message), Context())
    assert response["statusCode"] == 200, response
    return json.loads(response["body"])


def test_the_lambda_handler_lists_then_calls_twice_in_one_process():
    for _ in range(2):
        tools = invoke("tools/list", {})["result"]["tools"]
        assert [t["name"] for t in tools] == [t.__name__ for t in server.TOOLS]
        called = invoke(
            "tools/call", {"name": "lookup_term", "arguments": {"term": "carrier"}}, 2
        )["result"]
        assert called["isError"] is False
        assert json.loads(called["content"][0]["text"])["term"] == "Carrier"


def test_every_tool_is_read_only_with_a_one_line_description_and_no_optional_input():
    for tool in invoke("tools/list", {})["result"]["tools"]:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["description"]
        assert "\n" not in tool["description"]
        schema = tool["inputSchema"]
        assert set(schema.get("required", [])) == set(schema.get("properties", {}))


def test_a_missing_section_is_an_error_result_on_lambda():
    arguments = {"document": "claims_manual", "number": "99"}
    called = invoke("tools/call", {"name": "read_section", "arguments": arguments})
    assert called["result"]["isError"] is True
    assert (
        "Section 99 of claims_manual not found"
        in (called["result"]["content"][0]["text"])
    )


def test_the_server_reads_only_its_own_files_and_no_environment():
    text = Path(server.__file__).read_text(encoding="utf-8")
    assert "getenv" not in text
    assert "environ" not in text
    assert "write_text" not in text
    assert Path(server.__file__).parent == server.HERE
