"""The Gateway route: both targets through the AgentCore Gateway, signed with SigV4."""

from collections.abc import Generator
from typing import Any

import boto3
import httpx
from botocore.auth import SigV4Auth as BotocoreSigV4Auth
from botocore.awsrequest import AWSRequest
from mcp.shared.exceptions import McpError

from agent.mcp_client import (
    APP_TOOLS,
    CONTEXT_TOOLS,
    Answer,
    call_tool,
    refuser,
    target,
)

SERVICE = "bedrock-agentcore"


class SigV4(httpx.Auth):
    """Sign each request with AWS Signature Version 4, as boto3 signs its own."""

    requires_request_body = True

    def __init__(self, session: boto3.Session, service: str = SERVICE) -> None:
        self.credentials = session.get_credentials()
        self.region = session.region_name
        self.service = service

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        """Add the signature's headers to the request."""
        headers = {
            "Host": request.url.netloc.decode(),
            "Content-Type": request.headers.get("content-type", ""),
        }
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=headers,
        )
        frozen = self.credentials.get_frozen_credentials()
        BotocoreSigV4Auth(frozen, self.service, self.region).add_auth(signed)
        for name in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
            if name in signed.headers:
                request.headers[name] = signed.headers[name]
        yield request


class Gateway:
    """The app's and the context layer's tools as the Gateway's MCP tools."""

    def __init__(self, url: str) -> None:
        self.url = url
        # The AWS CLI's credentials and region, read when the route is made.
        self.auth = SigV4(boto3.Session())

    def name(self, tool: str) -> str:
        """A tool's name on the Gateway: its target, three underscores, its name.

        The agent's own tools, such as search_memory, keep theirs."""
        if tool not in APP_TOOLS | CONTEXT_TOOLS:
            return tool
        return f"{target(tool)}___{tool}"

    def use(self, tool: str, arguments: dict[str, Any]) -> Answer:
        """Call one tool; the Gateway's own refusal is an MCP error, not a result."""
        try:
            text, error = call_tool(self.url, self.name(tool), arguments, self.auth)
        except McpError as exc:
            return Answer(f"Refused by the Gateway: {exc.error.message}", "gateway")
        if error:
            return Answer(f"Refused: {text}", refuser(tool))
        return Answer(text, None)
