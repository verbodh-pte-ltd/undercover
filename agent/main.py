"""The UnderCover Agent: a queue agent that hands each claim to a fresh claim agent."""

import json
import os
import uuid
from typing import Any, Literal, TypedDict

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

load_dotenv(override=True)

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
MODEL_TIMEOUT = 60
MODEL_RETRIES = 2
RETRY_DELAY = 1.0
RESULT_PREVIEW = 300
RECURSION_LIMIT = 1000

Document = Literal["policy_wording", "claims_manual"]


class Todo(TypedDict):
    claim_id: str
    text: str
    state: Literal["to_do", "in_progress", "done", "left_for_handler"]


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=30)


def call(method: str, path: str, body: Any = None) -> str:
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
def list_sections(document: Document) -> str:
    """List the numbered sections of policy_wording or claims_manual."""
    return call("GET", f"/api/documents/{document}/sections")


@tool
def read_section(document: Document, number: str) -> str:
    """Read one numbered section of policy_wording or claims_manual."""
    return call("GET", f"/api/documents/{document}/sections/{number}")


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


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    list_sections,
    read_section,
    record_decision,
    pay_claim,
]


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or DeepSeek V4.1 Flash by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
        timeout=MODEL_TIMEOUT,
    )


def retry() -> ModelRetryMiddleware:
    """Retry a failed model call with backoff, then raise the error."""
    return ModelRetryMiddleware(
        max_retries=MODEL_RETRIES, initial_delay=RETRY_DELAY, on_failure="error"
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


def run(claim_id: str, llm: BaseChatModel | None = None) -> str:
    """Run a fresh claim agent on a started claim and record each step."""
    with backend() as client:
        instructions = client.get("/api/instructions").json()["claim"]
        agent = create_agent(
            llm or model(),
            tools=CLAIM_TOOLS,
            system_prompt=instructions,
            middleware=[retry()],
        )
        reply = ""
        for update in agent.stream(
            {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
            config={"configurable": {"thread_id": f"{claim_id}-{uuid.uuid4().hex}"}},
            stream_mode="updates",
        ):
            for node in update.values():
                for message in node["messages"]:
                    for step in steps(message):
                        client.post(f"/api/claims/{claim_id}/steps", json=step)
                    reply = message.text
    return reply


def work_claim(claim_id: str, run_id: int, llm: BaseChatModel | None = None) -> str:
    """Work one claim on a fresh claim agent and say in one line how it went."""
    with backend() as client:
        start = client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})
    if not start.is_success:
        return f"not started: {start.text}"
    error = "the claim agent stopped without a decision"
    reply = ""
    try:
        reply = run(claim_id, llm)
    except Exception as exc:  # one bad claim must never stop the run
        error = f"{type(exc).__name__}: {exc}"[:RESULT_PREVIEW]
    with backend() as client:
        claim = client.get(f"/api/claims/{claim_id}").json()
        if claim["status"] == "in_progress":
            release = {"error": error}
            claim = client.post(f"/api/claims/{claim_id}/release", json=release).json()
    if claim["status"] == "waiting":
        return f"failed: {error}. {claim_id} is back in the queue."
    if claim["status"] == "needs_handler":
        return f"failed twice: {error}. {claim_id} is left for a handler."
    return reply or f"{claim_id} is {claim['status']}: {claim['decision']}"


def queue_tools(run_id: int, claim_llm: BaseChatModel | None) -> list[BaseTool]:
    """The queue agent's tools, bound to its run."""

    @tool
    def list_waiting_claims() -> str:
        """List the waiting claims in queue order, with their failed attempts."""
        return call("GET", "/api/claims/waiting")

    @tool
    def set_todos(todos: list[Todo]) -> str:
        """Replace the run's to-do list with the whole list as it now stands."""
        return call("PUT", f"/api/runs/{run_id}/todos", todos)

    @tool("work_claim")
    def work_claim_tool(claim_id: str) -> str:
        """Hand one waiting claim to a fresh claim agent; it replies in one line."""
        return work_claim(claim_id, run_id, claim_llm)

    @tool
    def finish_run(summary: str) -> str:
        """Finish the run with a summary for the handlers, once no claim is missed."""
        return call("POST", f"/api/runs/{run_id}/finish", {"summary": summary})

    return [list_waiting_claims, set_todos, work_claim_tool, finish_run]


def run_queue(
    cap: int, llm: BaseChatModel | None = None, claim_llm: BaseChatModel | None = None
) -> str:
    """Start a run of up to cap claims and let the queue agent work it."""
    with backend() as client:
        run_id = client.post("/api/runs", json={"cap": cap}).json()["run_id"]
        instructions = client.get("/api/instructions").json()["queue"]
    agent = create_agent(
        llm or model(),
        tools=queue_tools(run_id, claim_llm),
        system_prompt=instructions,
        middleware=[retry()],
    )
    prompt = f"Please work the queue. This run is capped at {cap} claims."
    reply = ""
    for update in agent.stream(
        {"messages": [{"role": "user", "content": prompt}]},
        config={"recursion_limit": RECURSION_LIMIT},
        stream_mode="updates",
    ):
        for node in update.values():
            for message in node["messages"]:
                for step in steps(message):
                    print(f"[{step['kind']}] {step['text']}")
                reply = message.text
    return reply
