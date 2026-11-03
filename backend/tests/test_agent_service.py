import json

import httpx
import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    IncompleteReadError,
    ReadTimeoutError,
)
from fastapi.testclient import TestClient

from backend import agent_service
from backend.main import app
from backend.tests.signing import AGENT, API_KEY, headers

client = TestClient(app, headers=AGENT)
ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/agent-abc"
DISPATCH = {"operation": "start_dispatch", "n": 3}


def answering(status, body):
    return httpx.Response(status, json=body)


@pytest.fixture
def laptop(monkeypatch):
    """The laptop route, with a stand-in for httpx.post that answers as scripted."""
    sent = []
    script = {"answer": answering(200, {"accepted": True})}

    def post(url, json, headers, timeout):
        sent.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        answer = script["answer"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setenv("UNDERCOVER_AGENT", "http://agent.test")
    monkeypatch.setattr(agent_service.httpx, "post", post)
    return sent, script


class Body:
    """The answer's stream, as botocore gives it, or an error while reading it."""

    def __init__(self, content):
        self.content = content

    def read(self):
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


class FakeRuntime:
    """A stand-in for the bedrock-agentcore client, answering as scripted."""

    def __init__(self):
        self.sent = []
        self.answer = Body(b'{"accepted": true}')

    def invoke_agent_runtime(self, **kwargs):
        self.sent.append(kwargs)
        if isinstance(self.answer, Exception):
            raise self.answer
        return {"response": self.answer, "contentType": "application/json"}


@pytest.fixture
def runtime(monkeypatch):
    found = FakeRuntime()
    monkeypatch.setenv("UNDERCOVER_AGENT_RUNTIME", ARN)
    monkeypatch.setattr(agent_service, "runtime_client", lambda: found)
    return found


def refused_with(status):
    with pytest.raises(agent_service.NotTaken) as caught:
        agent_service.send(DISPATCH)
    assert caught.value.status_code == status
    return caught.value.detail


def unknown():
    with pytest.raises(agent_service.HTTPException) as caught:
        agent_service.send(DISPATCH)
    assert not isinstance(caught.value, agent_service.NotTaken)
    assert caught.value.status_code == 504
    assert "Reload the page" in caught.value.detail


def test_the_laptop_route_posts_the_payload_with_the_key(laptop):
    sent, _ = laptop
    agent_service.send(DISPATCH)
    assert sent == [
        {
            "url": "http://agent.test/invocations",
            "json": DISPATCH,
            "headers": {"Authorization": f"Bearer {API_KEY}"},
            "timeout": 10,
        }
    ]


def test_the_laptop_route_defaults_to_port_8080(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_AGENT")
    assert agent_service.url() == "http://127.0.0.1:8080"


def test_on_the_laptop_route_a_refusal_is_not_taken_with_its_reason(laptop):
    _, script = laptop
    script["answer"] = answering(200, {"refused": "A dispatch is already running"})
    assert refused_with(409) == "A dispatch is already running"


def test_on_the_laptop_route_a_failed_connection_is_not_taken(laptop):
    _, script = laptop
    script["answer"] = httpx.ConnectError("refused")
    assert refused_with(503) == "The agent isn't running"
    script["answer"] = httpx.ConnectTimeout("no route")
    assert refused_with(503) == "The agent isn't running"


def test_on_the_laptop_route_an_error_answer_is_not_taken(laptop):
    _, script = laptop
    script["answer"] = answering(401, {"error": "The agent's key is missing or wrong"})
    assert "The agent's key is missing or wrong" in refused_with(502)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ReadTimeout("no answer"),
        httpx.RemoteProtocolError("cut off"),
        httpx.Response(200, content=b'{"accep'),
        answering(200, {"something": "else"}),
    ],
)
def test_on_the_laptop_route_a_timeout_or_a_cut_off_answer_may_have_started(
    laptop, answer
):
    _, script = laptop
    script["answer"] = answer
    unknown()


def test_the_runtime_route_sends_the_payload_in_a_new_session(runtime, laptop):
    sent_over_http, _ = laptop
    agent_service.send(DISPATCH)
    agent_service.send(DISPATCH)
    first, second = runtime.sent
    assert first["agentRuntimeArn"] == ARN
    assert json.loads(first["payload"]) == DISPATCH
    assert len(first["runtimeSessionId"]) >= 33
    assert first["runtimeSessionId"] != second["runtimeSessionId"]
    assert sent_over_http == []


def test_the_runtime_client_makes_one_attempt_in_the_apps_region():
    found = agent_service.runtime_client()
    config = found.meta.config
    assert (config.connect_timeout, config.read_timeout) == (5, 20)
    assert config.retries["total_max_attempts"] == 1
    assert found.meta.region_name == "us-east-1"


def test_on_the_runtime_route_a_refusal_is_not_taken_with_its_reason(runtime):
    runtime.answer = Body(b'{"refused": "Round 3 is already running"}')
    assert refused_with(409) == "Round 3 is already running"


def test_on_the_runtime_route_a_failed_connection_is_not_taken(runtime):
    runtime.answer = EndpointConnectionError(endpoint_url="https://runtime.test")
    assert refused_with(503) == "The agent on AgentCore Runtime can't be reached"


def test_on_the_runtime_route_an_error_answer_is_not_taken(runtime):
    error = {"Error": {"Code": "AccessDeniedException", "Message": "Not allowed"}}
    runtime.answer = ClientError(error, "InvokeAgentRuntime")
    assert "AccessDeniedException" in refused_with(502)


@pytest.mark.parametrize(
    "answer",
    [
        ReadTimeoutError(endpoint_url="https://runtime.test"),
        Body(IncompleteReadError(actual_bytes=3, expected_bytes=20)),
        Body(b'{"accep'),
    ],
)
def test_on_the_runtime_route_a_timeout_or_a_cut_off_answer_may_have_started(
    runtime, answer
):
    runtime.answer = answer
    unknown()


def test_a_dispatch_and_an_evaluation_ask_the_agent_to_work_their_runs(laptop):
    sent, _ = laptop
    dispatch = client.post("/api/dispatches", json={"n": 3}, headers=headers())
    client.post(f"/api/runs/{dispatch.json()['run_id']}/finish")
    body = {"n": 5, "eval_set": "held_out", "tiny": True}
    evaluation = client.post("/api/evaluations", json=body, headers=headers())
    assert [s["json"] for s in sent] == [
        {"operation": "work_run", "run_id": dispatch.json()["run_id"]},
        {"operation": "work_run", "run_id": evaluation.json()["run_id"]},
    ]


@pytest.mark.parametrize(
    ("answer", "status", "detail"),
    [
        (
            answering(200, {"refused": "A dispatch is already running"}),
            409,
            "A dispatch is already running",
        ),
        (httpx.ConnectError("refused"), 503, "The agent isn't running"),
        (httpx.ReadTimeout("no answer"), 504, "Reload the page"),
    ],
)
def test_a_start_passes_on_what_the_agent_said(laptop, answer, status, detail):
    _, script = laptop
    script["answer"] = answer
    response = client.post("/api/dispatches", json={"n": 3}, headers=headers())
    assert response.status_code == status
    assert detail in response.json()["detail"]


def test_a_resume_that_fails_only_logs_a_warning(runtime, caplog):
    runtime.answer = Body(b"")
    agent_service.resume_claim("CLM-1001")
    assert json.loads(runtime.sent[0]["payload"]) == {
        "operation": "resume_claim",
        "claim_id": "CLM-1001",
    }
    [warning] = caplog.records
    assert warning.levelname == "WARNING"
    assert warning.getMessage().startswith("The agent didn't resume CLM-1001: ")
