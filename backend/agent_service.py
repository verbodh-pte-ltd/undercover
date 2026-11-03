"""The backend's calls to the agent's AgentCore app: over HTTP, or on AgentCore Runtime.

Each call sends one payload, which names an operation and carries its inputs. The agent
took the work, or it didn't (`NotTaken`: never reached, an error answer, or a refusal),
or nobody knows (a timeout or an answer cut off), and then the backend changes nothing.
"""

import json
import logging
import os
import uuid
from typing import Any

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.exceptions import ConnectionError as NeverConnected
from fastapi import HTTPException

AGENT_URL = "http://127.0.0.1:8080"
TIMEOUT = 10
RUNTIME_CONFIG = Config(
    retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20
)
REFUSED = 409
ERROR = 502
UNREACHABLE = 503
UNKNOWN = 504
logger = logging.getLogger(__name__)


class NotTaken(HTTPException):
    """The agent didn't take the work, so whatever the backend began can be undone."""


def url() -> str:
    """The agent's address on the laptop route: UNDERCOVER_AGENT, or port 8080 here."""
    return os.getenv("UNDERCOVER_AGENT", AGENT_URL)


def may_have_started() -> HTTPException:
    """The answer when the request went but no clear answer came back."""
    return HTTPException(
        UNKNOWN,
        "The agent didn't answer in time. Reload the page to see whether it went"
        " through",
    )


def over_http(payload: dict[str, Any]) -> Any:
    """Post the payload to the agent's /invocations with its key; return its answer."""
    key = os.getenv("UNDERCOVER_API_KEY", "")
    try:
        response = httpx.post(
            f"{url()}/invocations",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise NotTaken(UNREACHABLE, "The agent isn't running") from exc
    except httpx.HTTPError as exc:
        raise may_have_started() from exc
    if response.is_error:
        raise NotTaken(ERROR, f"The agent couldn't take it: {response.text}")
    try:
        return response.json()
    except ValueError as exc:
        raise may_have_started() from exc


def runtime_client() -> Any:
    """A client for AgentCore Runtime in the app's region: one attempt, 5 s, 20 s."""
    return boto3.client("bedrock-agentcore", config=RUNTIME_CONFIG)


def session_id() -> str:
    """A new Runtime session's id, at least 33 characters long."""
    return f"undercover-{uuid.uuid4().hex}"


def over_runtime(arn: str, payload: dict[str, Any]) -> Any:
    """Send the payload to the agent's runtime in a new session; return its answer."""
    try:
        response = runtime_client().invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session_id(),
            payload=json.dumps(payload),
        )
    except NeverConnected as exc:
        raise NotTaken(
            UNREACHABLE, "The agent on AgentCore Runtime can't be reached"
        ) from exc
    except ClientError as exc:
        raise NotTaken(ERROR, f"The agent couldn't take it: {exc}") from exc
    except BotoCoreError as exc:
        raise may_have_started() from exc
    try:
        return json.loads(response["response"].read())
    except (BotoCoreError, ValueError) as exc:
        raise may_have_started() from exc


def send(payload: dict[str, Any]) -> None:
    """Ask the agent to take some work; return once it has, or raise saying why not."""
    arn = os.getenv("UNDERCOVER_AGENT_RUNTIME")
    answer = over_runtime(arn, payload) if arn else over_http(payload)
    if isinstance(answer, dict) and answer.get("accepted") is True:
        return
    if isinstance(answer, dict) and "refused" in answer:
        raise NotTaken(REFUSED, str(answer["refused"]))
    raise may_have_started()


def start(operation: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Ask the agent to start a run or round, or answer a proposal, or say why not."""
    send({"operation": operation, **inputs})
    return {"status": "started"}


def resume_claim(claim_id: str) -> None:
    """Ask the agent to carry on a signed-off claim; a failure only logs a warning."""
    try:
        send({"operation": "resume_claim", "claim_id": claim_id})
    except HTTPException as exc:
        logger.warning("The agent didn't resume %s: %s", claim_id, exc.detail)
