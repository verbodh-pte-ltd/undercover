"""What the two routes share: which target serves each tool, and one MCP tool call."""

import asyncio
from typing import Any, NamedTuple

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import TextContent

TIMEOUT = 60
APP = "undercover-app"
CONTEXT = "undercover-context"
# The app's operations the claim agent uses, the model's and the graph's payment.
APP_TOOLS = {
    "get_claim",
    "get_policy",
    "get_claim_history",
    "calculate_payout",
    "record_decision",
    "pay_claim",
}
# The context layer's tools, all read-only.
CONTEXT_TOOLS = {
    "lookup_term",
    "describe_fields",
    "get_plan",
    "get_claim_rules",
    "search_reference_data",
    "search_documents",
    "list_sections",
    "read_section",
}


class Answer(NamedTuple):
    """A tool's answer, and who refused it: "app", "context", "gateway" or None."""

    text: str
    refused_by: str | None


def target(tool: str) -> str:
    """The Gateway's target that serves a tool."""
    return APP if tool in APP_TOOLS else CONTEXT


def refuser(tool: str) -> str:
    """Who answers a tool's refusal: the app or the context layer."""
    return "app" if tool in APP_TOOLS else "context"


async def call_async(
    url: str, name: str, arguments: dict[str, Any], auth: httpx.Auth | None
) -> tuple[str, bool]:
    """Call one tool in a session of its own; the text and whether it is an error.

    An MCP error, such as the Gateway's refusal, is raised as McpError.
    """
    async with (
        httpx.AsyncClient(auth=auth, timeout=TIMEOUT) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        try:
            result = await session.call_tool(name, arguments)
        except McpError as exc:
            # Raised outside the client's task group, which would wrap it in a group.
            refused = exc
        else:
            text = "\n".join(
                p.text for p in result.content if isinstance(p, TextContent)
            )
            return text, result.isError
    raise refused


def call_tool(
    url: str, name: str, arguments: dict[str, Any], auth: httpx.Auth | None = None
) -> tuple[str, bool]:
    """Call one MCP tool from synchronous code, on an event loop of its own."""
    # Not asyncio.run, which leaves the thread with no event loop, and Mangum asks
    # for the main thread's.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(call_async(url, name, arguments, auth))
    finally:
        loop.close()
