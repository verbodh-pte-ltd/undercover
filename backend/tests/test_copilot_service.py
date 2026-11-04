import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend import copilot_service

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/copilot-abc"
SESSION = "6f1c2a7e-3b0d-4c55-9a51-0d6a3c9e2f10"
SIGN_IN = "the-handlers-sign-in"


class Body:
    def __init__(self, content):
        self.content = content

    def read(self):
        return self.content


def conflict():
    return ClientError(
        {"Error": {"Code": "ConflictException", "Message": "Session is starting"}},
        "InvokeAgentRuntime",
    )


class FakeRuntime:
    """A stand-in for the bedrock-agentcore client, answering as scripted in turn."""

    def __init__(self, answers):
        self.sent = []
        self.answers = answers

    def invoke_agent_runtime(self, **kwargs):
        self.sent.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"response": Body(answer)}


@pytest.fixture
def runtime(monkeypatch):
    found = FakeRuntime([b'{"accepted": true}'])
    waits = []
    monkeypatch.setenv("UNDERCOVER_COPILOT_RUNTIME", ARN)
    monkeypatch.setattr(copilot_service, "runtime_client", lambda: found)
    monkeypatch.setattr(copilot_service.time, "sleep", waits.append)
    return found, waits


@pytest.fixture
def laptop(monkeypatch):
    sent = []
    script = {"answer": httpx.Response(200, json={"accepted": True})}

    def post(url, json, timeout):
        sent.append({"url": url, "json": json, "timeout": timeout})
        if isinstance(script["answer"], Exception):
            raise script["answer"]
        return script["answer"]

    monkeypatch.delenv("UNDERCOVER_COPILOT_RUNTIME", raising=False)
    monkeypatch.setenv("UNDERCOVER_COPILOT", "http://copilot.test")
    monkeypatch.setattr(copilot_service.httpx, "post", post)
    return sent, script


def not_taken(status):
    with pytest.raises(copilot_service.NotTaken) as caught:
        copilot_service.start_turn(SESSION, 7, "Hello", SIGN_IN)
    assert caught.value.status_code == status
    assert SIGN_IN not in str(caught.value.detail)
    return caught.value.detail


def test_the_laptop_route_posts_the_turn_with_no_key(laptop):
    sent, _ = laptop
    copilot_service.answer_confirmation(SESSION, 7, "sign_off", SIGN_IN)
    assert sent == [
        {
            "url": "http://copilot.test/invocations",
            "json": {
                "operation": "answer_confirmation",
                "conversation_id": SESSION,
                "turn_id": 7,
                "answer": "sign_off",
                "token": SIGN_IN,
            },
            "timeout": 10,
        }
    ]


def test_the_laptop_route_defaults_to_port_8081(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_COPILOT", raising=False)
    assert copilot_service.url() == "http://127.0.0.1:8081"


def test_on_the_laptop_a_refusal_or_no_copilot_is_not_taken(laptop):
    _, script = laptop
    script["answer"] = httpx.Response(200, json={"refused": "Busy"})
    assert not_taken(409) == "Busy"
    script["answer"] = httpx.ConnectError("refused")
    assert not_taken(503) == "The Copilot isn't running"
    script["answer"] = httpx.Response(500, text=f"failed with {SIGN_IN}")
    assert not_taken(502) == "The Copilot couldn't take it (HTTP 500)"


def test_on_the_laptop_a_timeout_or_an_odd_answer_is_unknown(laptop):
    _, script = laptop
    for answer in [httpx.ReadTimeout("slow"), httpx.Response(200, text="not json")]:
        script["answer"] = answer
        with pytest.raises(copilot_service.HTTPException) as caught:
            copilot_service.start_turn(SESSION, 7, "Hello", SIGN_IN)
        assert not isinstance(caught.value, copilot_service.NotTaken)
        assert caught.value.status_code == 504


def test_runtime_gets_the_conversation_as_its_session(runtime):
    found, waits = runtime
    copilot_service.start_turn(SESSION, 7, "Hello", SIGN_IN)
    assert len(found.sent) == 1
    assert found.sent[0]["agentRuntimeArn"] == ARN
    assert found.sent[0]["runtimeSessionId"] == SESSION
    assert '"operation": "start_turn"' in found.sent[0]["payload"]
    assert waits == []


def test_runtime_retries_a_conflict_up_to_three_times(runtime):
    found, waits = runtime
    found.answers = [conflict(), conflict(), b'{"accepted": true}']
    copilot_service.start_turn(SESSION, 7, "Hello", SIGN_IN)
    assert len(found.sent) == 3
    assert {s["runtimeSessionId"] for s in found.sent} == {SESSION}
    assert waits == [1.0, 1.0]
    found.sent.clear()
    found.answers = [conflict(), conflict(), conflict(), conflict()]
    assert not_taken(502) == "The Copilot couldn't take it (ConflictException)"
    assert len(found.sent) == 4


def test_runtime_unreachable_or_refusing_is_not_taken(runtime):
    found, _ = runtime
    found.answers = [EndpointConnectionError(endpoint_url="https://runtime.test")]
    assert not_taken(503) == "The Copilot on AgentCore Runtime can't be reached"
    found.answers = [b'{"refused": "No"}']
    assert not_taken(409) == "No"
    found.answers = [b"not json"]
    with pytest.raises(copilot_service.HTTPException) as caught:
        copilot_service.start_turn(SESSION, 7, "Hello", SIGN_IN)
    assert caught.value.status_code == 504
