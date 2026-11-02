import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from agent import direct, gateway, main
from agent.tests.standin import StandIn, serve
from agent.tests.test_agent import (  # noqa: F401 (check and in_process_backend are fixtures)
    REAL_BACKEND,
    StubModel,
    calls,
    check,
    claim,
    decision,
    in_process_backend,
    recording,
    steps_of,
    stub,
)
from backend import main as backend_main
from backend import store
from backend.main import app
from backend.tests.signing import AGENT, headers
from backend.tests.test_lambda import Context, function_url_event

client = TestClient(app, headers=AGENT)
NO_IDS = {"get_claim", "get_policy", "get_claim_history"}


@pytest.fixture(scope="module")
def standin_server():
    standin = StandIn()
    url, server = serve(standin.app)
    yield standin, f"{url}/mcp"
    server.should_exit = True


@pytest.fixture
def standin(standin_server, direct_context, monkeypatch):
    """The stand-in Gateway, with the agent pointed at it."""
    found, url = standin_server
    found.reset()
    found.context_url = direct_context
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", url)
    return found


def inputs(tool):
    return set(tool.tool_call_schema.model_json_schema().get("properties", {}))


def test_the_models_tools_take_no_ids_the_agent_already_knows():
    names = [t.name for t in main.MODEL_TOOLS]
    assert names == [
        "get_claim",
        "get_policy",
        "get_claim_history",
        "lookup_term",
        "describe_fields",
        "get_plan",
        "get_claim_rules",
        "search_reference_data",
        "search_documents",
        "list_sections",
        "read_section",
        "calculate_payout",
        "record_decision",
    ]
    tools = {t.name: t for t in main.MODEL_TOOLS}
    for name in NO_IDS:
        assert inputs(tools[name]) == set()
    assert inputs(tools["calculate_payout"]) == {"benefit", "lines", "paid_by_others"}
    assert inputs(tools["record_decision"]) == {
        "decision",
        "payout",
        "reason",
        "policy_section",
    }


class Binding(StubModel):
    """A stub model that keeps the tools it is given."""

    bound: list

    def bind_tools(self, tools, **kwargs):
        self.bound.extend(convert_to_openai_tool(t) for t in tools)
        return self


def bound_tools():
    model = Binding(messages=iter([]), bound=[])
    main.claim_graph(model, "Decide.", main.saver())
    return model.bound


def test_both_routes_give_the_model_the_same_tools(standin, monkeypatch):
    on_the_gateway = bound_tools()
    monkeypatch.delenv("UNDERCOVER_GATEWAY_URL")
    assert bound_tools() == on_the_gateway
    assert isinstance(main.route(), direct.Direct)


PAYOUT = {
    "benefit": "trip_delay",
    "lines": [
        {
            "description": "Hotel",
            "amount": 300,
            "quantity": 1,
            "kind": "expense",
            "nights": 0,
            "hours": 0,
        }
    ],
    "paid_by_others": 0,
}


def every_tool(payout=300.0):
    return [
        calls(("get_claim", {}), ("get_policy", {}), ("get_claim_history", {})),
        calls(
            ("list_sections", {"document": "claims_manual"}),
            ("read_section", {"document": "claims_manual", "number": "6"}),
        ),
        calls(("calculate_payout", PAYOUT)),
        calls(("record_decision", decision("CLM-1001", payout))),
        AIMessage(content=f"Approved, ${payout:,.2f}."),
    ]


def test_the_models_tools_and_the_payment_go_through_the_gateway_signed(
    standin, monkeypatch
):
    sent = recording(monkeypatch)
    assert main.work("CLM-1001", None, stub(*every_tool())) == "Approved, $300.00."
    # The tools one reply asks for run at once, in any order.
    assert sorted(name for name, _ in standin.calls) == sorted(
        [
            "undercover-app___get_claim",
            "undercover-app___get_policy",
            "undercover-app___get_claim_history",
            "undercover-context___list_sections",
            "undercover-context___read_section",
            "undercover-app___calculate_payout",
            "undercover-app___record_decision",
            "undercover-app___pay_claim",
        ]
    )
    assert dict(standin.calls)["undercover-app___get_claim"] == {"claim_id": "CLM-1001"}
    assert standin.calls[-1][1] == {"claim_id": "CLM-1001", "amount": 300.0}
    for sent_headers in standin.headers:
        signature = sent_headers["authorization"]
        assert signature.startswith("AWS4-HMAC-SHA256 Credential=AKIDTESTONLY/")
        assert "/us-east-1/bedrock-agentcore/aws4_request" in signature
    # The bookkeeping and the check's read go straight to the app with the key.
    for done in ["/start", "/steps", "/model-calls", "/work-times"]:
        assert any(r.endswith(done) for r in sent), done
    assert "GET /api/claims/CLM-1001" in sent
    assert not [r for r in sent if r.endswith(("/payment", "/decision", "/payout"))]
    result = claim("CLM-1001")
    assert (result["status"], result["paid_amount"]) == ("decided", 300.0)
    texts = steps_of("CLM-1001")
    assert "undercover-app___get_claim({})" in texts
    assert "undercover-app___pay_claim: Paid $300.00" in texts


def test_get_claim_gives_the_same_text_on_both_routes_as_the_check_reads(standin):
    on_the_gateway = main.route().use("get_claim", {"claim_id": "CLM-1001"})
    direct_route = direct.Direct(main.backend, direct.CONTEXT_URL)
    straight = direct_route.use("get_claim", {"claim_id": "CLM-1001"})
    assert on_the_gateway == straight
    assert straight.text == main.call("GET", "/api/claims/CLM-1001")


@pytest.mark.parametrize("on_the_gateway", [False, True])
def test_a_claim_is_decided_the_same_on_each_route(
    on_the_gateway, standin, monkeypatch
):
    if not on_the_gateway:
        monkeypatch.delenv("UNDERCOVER_GATEWAY_URL")
    main.work("CLM-1001", None, stub(*every_tool()))
    result = claim("CLM-1001")
    assert (result["decision"], result["payout"], result["paid_amount"]) == (
        "approve",
        300.0,
        300.0,
    )
    assert bool(standin.calls) == on_the_gateway


@pytest.mark.parametrize("on_the_gateway", [False, True])
def test_an_app_refusal_reaches_the_model_as_a_refusal_on_each_route(
    on_the_gateway, standin, monkeypatch
):
    if not on_the_gateway:
        monkeypatch.delenv("UNDERCOVER_GATEWAY_URL")
    replies = [
        calls(("record_decision", decision("CLM-1001", 0, "decline"))),
        calls(("record_decision", decision("CLM-1001", 300.0))),
        AIMessage(content="Declined."),
    ]
    main.work("CLM-1001", None, stub(*replies))
    name = main.route().name("record_decision")
    refused = [t for t in steps_of("CLM-1001") if t.startswith(f"{name}: Refused")]
    assert len(refused) == 1
    assert "has already been decided" in refused[0]
    assert claim("CLM-1001")["decision"] == "decline"


def test_a_gateway_refusal_is_recorded_as_the_gateways_and_work_goes_on(standin):
    standin.refuse.add("undercover-app___get_policy")
    replies = [
        calls(("get_policy", {})),
        calls(("record_decision", decision("CLM-1001", 0, "refer"))),
        AIMessage(content="Referred."),
    ]
    assert main.work("CLM-1001", None, stub(*replies)) == "Referred."
    texts = steps_of("CLM-1001")
    assert (
        "undercover-app___get_policy: Refused by the Gateway: Tool Execution Denied"
        " by policy" in texts
    )
    assert claim("CLM-1001")["decision"] == "refer"


def raise_the_limit(to=2000):
    with store.connect() as conn:
        conn.execute(
            store.settings.update()
            .where(store.settings.c.name == "approval_limit")
            .values(value=str(to))
        )
        conn.commit()


def test_a_payment_the_gateway_refuses_waits_for_a_sign_off_that_pays_it(
    standin, monkeypatch
):
    raise_the_limit()
    replies = [
        calls(("record_decision", decision("CLM-1001", 1500.0))),
        AIMessage(content="Approved, $1,500.00."),
    ]
    reply = main.work("CLM-1001", None, stub(*replies))
    assert reply == "CLM-1001 is waiting for a handler's sign-off."
    held = claim("CLM-1001")
    assert (held["status"], held["held_by_gateway"], held["paid_amount"]) == (
        "waiting_for_sign_off",
        True,
        None,
    )
    assert any(
        t.startswith("undercover-app___pay_claim: Refused by the Gateway")
        for t in steps_of("CLM-1001")
    )
    signed = client.post("/api/claims/CLM-1001/sign-off", headers=headers()).json()
    assert (signed["status"], signed["paid_amount"]) == ("decided", 1500.0)
    sent = recording(monkeypatch)
    main.run("CLM-1001", "CLM-1001-1", stub())
    assert "Signed off by HND-01" in steps_of("CLM-1001")
    assert [n for n, _ in standin.calls].count("undercover-app___pay_claim") == 1
    assert not [r for r in sent if r.endswith("/payment")]


def test_the_routes_and_the_backend_are_read_when_built(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", "https://gw.example/mcp")
    found = main.route()
    assert isinstance(found, gateway.Gateway)
    assert found.url == "https://gw.example/mcp"
    assert found.name("get_claim") == "undercover-app___get_claim"
    monkeypatch.delenv("UNDERCOVER_GATEWAY_URL")
    monkeypatch.setenv("UNDERCOVER_CONTEXT", "http://context.example/mcp")
    straight = main.route()
    assert isinstance(straight, direct.Direct)
    assert straight.context_url == "http://context.example/mcp"
    monkeypatch.setenv("UNDERCOVER_API", "https://app.example")
    with REAL_BACKEND() as backend:
        assert str(backend.base_url) == "https://app.example"


def test_the_lambda_handler_still_answers_after_a_gateway_call(standin):
    # A call through the Gateway runs on an event loop of its own and never takes
    # the main thread's away, which Mangum asks for.
    main.route().use("get_claim", {"claim_id": "CLM-1001"})
    response = backend_main.handler(function_url_event("/api/health"), Context())
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok"}


CONTEXT_CALLS = [
    ("lookup_term", {"term": "unattended"}, "Out of your sight"),
    ("describe_fields", {"record": "policy"}, "purchased_on"),
    ("get_plan", {"plan": "first"}, "UnderCover First"),
    ("get_claim_rules", {}, "filing_window"),
    ("search_reference_data", {"query": "lisbon strike"}, "air traffic controllers"),
    ("search_documents", {"query": "wild animals"}, "Interpretation notes"),
    ("list_sections", {"document": "claims_manual"}, "Repeat claims"),
    ("read_section", {"document": "claims_manual", "number": "9"}, "third claim"),
]


@pytest.mark.parametrize("on_the_gateway", [False, True])
def test_the_context_layers_tools_answer_on_each_route(
    on_the_gateway, standin, monkeypatch
):
    if not on_the_gateway:
        monkeypatch.delenv("UNDERCOVER_GATEWAY_URL")
    route = main.route()
    for tool, arguments, expected in CONTEXT_CALLS:
        answer = route.use(tool, arguments)
        assert answer.refused_by is None, tool
        assert expected in answer.text, tool
        assert '"file":' in answer.text, tool
    names = [n for n, _ in standin.calls]
    expected_calls = [f"undercover-context___{t}" for t, _, _ in CONTEXT_CALLS]
    assert names == (expected_calls if on_the_gateway else [])


@pytest.mark.parametrize("on_the_gateway", [False, True])
def test_a_context_refusal_reaches_the_model_as_a_refusal(
    on_the_gateway, standin, monkeypatch
):
    if not on_the_gateway:
        monkeypatch.delenv("UNDERCOVER_GATEWAY_URL")
    replies = [
        calls(("read_section", {"document": "claims_manual", "number": "99"})),
        calls(("record_decision", decision("CLM-1001", 0, "refer"))),
        AIMessage(content="Referred."),
    ]
    main.work("CLM-1001", None, stub(*replies))
    name = main.route().name("read_section")
    refused = [t for t in steps_of("CLM-1001") if t.startswith(f"{name}: Refused")]
    assert len(refused) == 1
    assert "Section 99 of claims_manual not found" in refused[0]
