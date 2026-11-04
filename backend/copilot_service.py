"""The backend's calls to the Copilot's AgentCore app: over HTTP or on Runtime.

Each call sends one payload, which names an operation and carries its inputs, the
handler's sign-in among them. The Copilot took the turn, or it didn't (`NotTaken`), or
nobody knows (a timeout or an answer cut off). Neither the payload nor the sign-in is
ever logged or put in an error.
"""

import json
import os
import time
from typing import Any

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.exceptions import ConnectionError as NeverConnected
from fastapi import HTTPException

from backend.agent_service import ERROR, REFUSED, UNKNOWN, UNREACHABLE

COPILOT_URL = "http://127.0.0.1:8081"
TIMEOUT = 10
RUNTIME_CONFIG = Config(
    retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20
)
# While Runtime starts or stops a session, a call gets a conflict that never reached the
# Copilot, so it is safe to send again.
CONFLICT_RETRIES = 3
CONFLICT_WAIT = 1.0


class NotTaken(HTTPException):
    """The Copilot didn't take the turn, so the app marks it failed."""


def url() -> str:
    """The Copilot's address on the laptop route: UNDERCOVER_COPILOT, or port 8081."""
    return os.getenv("UNDERCOVER_COPILOT", COPILOT_URL)


def may_have_started() -> HTTPException:
    """The answer when the request went but no clear answer came back."""
    return HTTPException(
        UNKNOWN, "The Copilot didn't answer in time. It may still be working on it"
    )


def over_http(payload: dict[str, Any]) -> Any:
    """Post the payload to the Copilot's /invocations; return its answer."""
    try:
        response = httpx.post(f"{url()}/invocations", json=payload, timeout=TIMEOUT)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise NotTaken(UNREACHABLE, "The Copilot isn't running") from exc
    except httpx.HTTPError as exc:
        raise may_have_started() from exc
    if response.is_error:
        raise NotTaken(
            ERROR, f"The Copilot couldn't take it (HTTP {response.status_code})"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise may_have_started() from exc


def runtime_client() -> Any:
    """A client for AgentCore Runtime in the app's region: one attempt, 5 s, 20 s."""
    return boto3.client("bedrock-agentcore", config=RUNTIME_CONFIG)


def conflict(exc: ClientError) -> bool:
    """Whether Runtime refused the call because the session is starting or stopping."""
    return exc.response.get("Error", {}).get("Code") == "ConflictException"


def invoke(arn: str, session: str, payload: dict[str, Any], retries: int) -> Any:
    """Invoke the Copilot's runtime in the session, sending a conflict again."""
    try:
        return runtime_client().invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session,
            payload=json.dumps(payload),
        )
    except NeverConnected as exc:
        raise NotTaken(
            UNREACHABLE, "The Copilot on AgentCore Runtime can't be reached"
        ) from exc
    except ClientError as exc:
        if conflict(exc) and retries > 0:
            time.sleep(CONFLICT_WAIT)
            return invoke(arn, session, payload, retries - 1)
        code = exc.response.get("Error", {}).get("Code", "an error")
        raise NotTaken(ERROR, f"The Copilot couldn't take it ({code})") from exc
    except BotoCoreError as exc:
        raise may_have_started() from exc


def over_runtime(arn: str, session: str, payload: dict[str, Any]) -> Any:
    """Send the payload to the Copilot's runtime in the conversation's session.

    A conflict is sent again, up to three times, a second apart.
    """
    response = invoke(arn, session, payload, CONFLICT_RETRIES)
    try:
        return json.loads(response["response"].read())
    except (BotoCoreError, ValueError) as exc:
        raise may_have_started() from exc


def send(conversation_id: str, payload: dict[str, Any]) -> None:
    """Ask the Copilot to take a turn; return once it has, or raise saying why not."""
    arn = os.getenv("UNDERCOVER_COPILOT_RUNTIME")
    answer = over_runtime(arn, conversation_id, payload) if arn else over_http(payload)
    if isinstance(answer, dict) and answer.get("accepted") is True:
        return
    if isinstance(answer, dict) and "refused" in answer:
        raise NotTaken(REFUSED, str(answer["refused"]))
    raise may_have_started()


def start_turn(conversation_id: str, turn_id: int, message: str, token: str) -> None:
    """Ask the Copilot to work a handler's message, in the conversation's session."""
    send(
        conversation_id,
        {
            "operation": "start_turn",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "message": message,
            "token": token,
        },
    )


def answer_confirmation(
    conversation_id: str, turn_id: int, answer: str, token: str
) -> None:
    """Ask the Copilot to carry a paused turn on with the handler's answer."""
    send(
        conversation_id,
        {
            "operation": "answer_confirmation",
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "answer": answer,
            "token": token,
        },
    )
