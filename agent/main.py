"""The UnderCover Agent."""

import json
import os
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

load_dotenv(override=True)

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
RESULT_PREVIEW = 300


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=30)


def call(method: str, path: str, body: dict[str, Any] | None = None) -> str:
    """Call the backend and return its reply as text, refusals included."""
    with backend() as client:
        response = client.request(method, path, json=body)
    if response.is_success:
        return response.text
    return f"Refused ({response.status_code}): {response.text}"


@tool
def get_claim(claim_id: str) -> str:
    """Get one claim in full: the story, the items, the documents and its decision."""
    return call("GET", f"/api/claims/{claim_id}")


@tool
def get_policy(policy_id: str) -> str:
    """Get a policy: the plan, the add-ons, the trip and the travelers."""
    return call("GET", f"/api/policies/{policy_id}")


@tool
def get_claim_history(customer_id: str) -> str:
    """Get the customer's earlier claims, with the outcome and amount paid."""
    return call("GET", f"/api/customers/{customer_id}/claims")


@tool
def get_policy_wording() -> str:
    """Get the policy wording, the contract that decisions follow."""
    return call("GET", "/api/policy-wording")


@tool
def record_decision(
    claim_id: str,
    decision: Literal["approve", "decline", "refer"],
    payout: float,
    reason: str,
    policy_section: str,
) -> str:
    """Approve, decline or refer a claim, with payout, reasons and policy section."""
    body = {
        "decision": decision,
        "payout": payout,
        "reason": reason,
        "policy_section": policy_section,
    }
    return call("POST", f"/api/claims/{claim_id}/decision", body)


@tool
def pay_claim(claim_id: str, amount: float) -> str:
    """Pay an approved claim its payout. A payout over $1,000 needs sign-off first."""
    return call("POST", f"/api/claims/{claim_id}/payment", {"amount": amount})


TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    get_policy_wording,
    record_decision,
    pay_claim,
]


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or GPT-6 Luna by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
    )


def steps(message: BaseMessage) -> list[dict[str, str]]:
    """Describe one message from the agent as steps for the dashboard."""
    if isinstance(message, ToolMessage):
        text = f"{message.name}: {message.text[:RESULT_PREVIEW]}"
        return [{"kind": "tool_result", "text": text}]
    found = []
    if isinstance(message, AIMessage):
        for tool_call in message.tool_calls:
            text = f"{tool_call['name']}({json.dumps(tool_call['args'])})"
            found.append({"kind": "tool_call", "text": text})
    if message.text:
        found.append({"kind": "reply", "text": message.text})
    return found


def run(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Run the agent on one claim, record each step, and return its final reply."""
    with backend() as client:
        run_id = client.post("/api/runs", json={"cap": 1}).json()["run_id"]
        start = client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})
        start.raise_for_status()
        instructions = client.get("/api/instructions").json()["claim"]
        agent = create_agent(llm or model(), tools=TOOLS, system_prompt=instructions)
        reply = ""
        for update in agent.stream(
            {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            for node in update.values():
                for message in node["messages"]:
                    for step in steps(message):
                        client.post(f"/api/claims/{claim_id}/steps", json=step)
                    reply = message.text
    return reply
