"""The direct route: the app with the agent's key, and the context layer unsigned."""

import string
from collections.abc import Callable
from typing import Any

import httpx

from agent.mcp_client import APP_TOOLS, Answer, call_tool

CONTEXT_URL = "http://127.0.0.1:8006/mcp"
# Each of the app's operations as the claim agent calls it.
OPERATIONS = {
    "get_claim": ("GET", "/api/claims/{claim_id}"),
    "get_policy": ("GET", "/api/policies/{policy_id}"),
    "get_claim_history": ("GET", "/api/customers/{customer_id}/claims"),
    "list_sections": ("GET", "/api/documents/{document}/sections"),
    "read_section": ("GET", "/api/documents/{document}/sections/{number}"),
    "calculate_payout": ("POST", "/api/claims/{claim_id}/payout"),
    "record_decision": ("POST", "/api/claims/{claim_id}/decision"),
    "pay_claim": ("POST", "/api/claims/{claim_id}/payment"),
}


class Direct:
    """The app through the agent's own client, and the context layer at its address."""

    def __init__(self, backend: Callable[[], httpx.Client], context_url: str) -> None:
        self.backend = backend
        self.context_url = context_url

    def name(self, tool: str) -> str:
        """A tool's name on this route: its own."""
        return tool

    def use(self, tool: str, arguments: dict[str, Any]) -> Answer:
        """Call one tool; a refusal comes back as text with its reason."""
        if tool not in APP_TOOLS:
            text, error = call_tool(self.context_url, tool, arguments)
            return Answer(
                f"Refused: {text}" if error else text, "context" if error else None
            )
        method, template = OPERATIONS[tool]
        in_path = {f for _, f, _, _ in string.Formatter().parse(template) if f}
        path = template.format(**{k: arguments[k] for k in in_path})
        body = {k: v for k, v in arguments.items() if k not in in_path}
        with self.backend() as client:
            response = client.request(
                method, path, json=body if method == "POST" else None
            )
        if response.is_success:
            return Answer(response.text, None)
        return Answer(f"Refused ({response.status_code}): {response.text}", "app")
