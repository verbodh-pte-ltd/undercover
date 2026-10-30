"""The UnderCover Agent: a queue agent that hands each claim to a claim agent graph."""

import json
import os
from functools import cache
from typing import Annotated, Any, Literal, TypedDict

import httpx
import psycopg
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, SecretStr

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
MODEL_TIMEOUT = 60
MODEL_RETRIES = 2
RETRY_DELAY = 1.0
RESULT_PREVIEW = 300
RECURSION_LIMIT = 1000
MAX_MODEL_CALLS = 30
SCHEMA = "undercover"
POOL_SIZE = 2

Document = Literal["policy_wording", "claims_manual"]
Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]


class Line(BaseModel):
    """One covered item or expense on a claim."""

    description: str = Field(description="What the item or expense is")
    amount: float = Field(
        description="The total claimed for the line in US dollars, before any limits."
        " For several items, the total for all of them"
    )
    quantity: int = Field(
        description="How many separate items the line holds, such as 3 for three"
        " shirts. A pair or a set is one item. 1 for an expense"
    )
    kind: Literal[
        "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
    ] = Field(
        description=(
            "item: a belonging, or a pair or set that counts as one item. "
            "electronics: only a phone, tablet, laptop, camera or its lens, e-reader"
            " or smartwatch; any other device is an item. "
            "cash: cash that was lost or stolen. "
            "expense: any other cost with a receipt, such as a medical bill, a meal,"
            " a taxi, a fee or a prepaid trip cost. "
            "hotel: a hotel during a trip delay. "
            "meals_without_receipts: meals during a trip delay with no receipts."
        )
    )
    nights: int = Field(description="For a hotel, the number of nights; otherwise 0")
    hours: float = Field(
        description="For meals without receipts, the length of the delay in hours;"
        " otherwise 0"
    )


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
def calculate_payout(
    policy_id: str, benefit: Benefit, lines: list[Line], paid_by_others: float
) -> str:
    """Work out the payout on an approved claim with UnderCover's payout rules.

    Give one line for each covered item or expense, with excluded ones left out, and
    anything a carrier, hotel or other party has paid or refunded in paid_by_others.
    """
    body = {
        "policy_id": policy_id,
        "benefit": benefit,
        "lines": [line.model_dump() for line in lines],
        "paid_by_others": paid_by_others,
    }
    return call("POST", "/api/payouts", body)


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


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    list_sections,
    read_section,
    calculate_payout,
    record_decision,
]


class ClaimState(TypedDict):
    """What the graph keeps for one claim."""

    messages: Annotated[list[AnyMessage], add_messages]
    claim_id: str
    model_calls: int


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


@cache
def saver() -> BaseCheckpointSaver:
    """Postgres in DATABASE_URL saves each claim's progress, or memory without it."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return InMemorySaver()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=POOL_SIZE,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={SCHEMA}",
        },
    )
    checkpointer = PostgresSaver(pool)  # pyright: ignore[reportArgumentType]
    checkpointer.setup()
    return checkpointer


def record_step(claim_id: str, kind: str, text: str) -> None:
    """Record one step on the claim."""
    call("POST", f"/api/claims/{claim_id}/steps", {"kind": kind, "text": text})


def claim_graph(llm: BaseChatModel, instructions: str) -> CompiledStateGraph:
    """The claim agent: the model and its tools, then sign-off and payment."""
    model_with_tools = llm.bind_tools(CLAIM_TOOLS).with_retry(
        stop_after_attempt=MODEL_RETRIES + 1,
        exponential_jitter_params={"initial": RETRY_DELAY},
    )
    system = SystemMessage(instructions)

    def agent(state: ClaimState) -> dict[str, Any]:
        """Call the model with the conversation so far."""
        if state["model_calls"] >= MAX_MODEL_CALLS:
            raise RuntimeError(f"the claim agent reached {MAX_MODEL_CALLS} model calls")
        reply = model_with_tools.invoke([system, *state["messages"]])
        return {"messages": [reply], "model_calls": state["model_calls"] + 1}

    def after_agent(state: ClaimState) -> Literal["tools", "settle"]:
        """Run the tools the model asked for, or settle the claim."""
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "settle"

    def settle(state: ClaimState) -> Command[Literal["sign_off", "pay", "__end__"]]:
        """Follow the backend: wait for a sign-off, pay, or stop."""
        claim = json.loads(call("GET", f"/api/claims/{state['claim_id']}"))
        if claim["decision"] != "approve" or claim["paid_amount"] is not None:
            return Command(goto="__end__")
        if claim["status"] == "waiting_for_sign_off":
            return Command(goto="sign_off")
        return Command(goto="pay")

    def sign_off(state: ClaimState) -> dict[str, Any]:
        """Wait for a handler to sign off the payout."""
        answer = interrupt({"claim_id": state["claim_id"]})
        record_step(state["claim_id"], "reply", f"Signed off by {answer['handler_id']}")
        return {}

    def pay(state: ClaimState) -> dict[str, Any]:
        """Pay the approved payout; the backend treats a repeat as done."""
        claim_id = state["claim_id"]
        payout = json.loads(call("GET", f"/api/claims/{claim_id}"))["payout"]
        result = call("POST", f"/api/claims/{claim_id}/payment", {"amount": payout})
        paid = f"Paid ${payout:,.2f}"
        text = result[:RESULT_PREVIEW] if result.startswith("Refused") else paid
        record_step(claim_id, "tool_result", f"pay_claim: {text}")
        return {}

    builder = StateGraph(ClaimState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(CLAIM_TOOLS))
    builder.add_node("settle", settle)
    builder.add_node("sign_off", sign_off)
    builder.add_node("pay", pay)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, ["tools", "settle"])
    builder.add_edge("tools", "agent")
    builder.add_edge("sign_off", "pay")
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=saver())


def traced(thread_id: str) -> RunnableConfig:
    """The run config for a claim's thread, traced in Langfuse when its keys are set."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {"langfuse_session_id": thread_id}
    return config


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
    """Run the claim agent on its thread: start, carry on, or resume after sign-off."""
    with backend() as client:
        instructions = client.get("/api/instructions").json()["claim"]
        claim = client.get(f"/api/claims/{claim_id}").json()
    graph = claim_graph(llm or model(), instructions)
    config = traced(thread_id)
    config["recursion_limit"] = RECURSION_LIMIT
    saved = graph.get_state(config)
    inputs: Any = {
        "messages": [HumanMessage(f"Decide claim {claim_id}.")],
        "claim_id": claim_id,
        "model_calls": 0,
    }
    if saved.interrupts:
        if claim["signed_off_by"] is None:
            return f"{claim_id} is waiting for a handler's sign-off."
        inputs = Command(resume={"handler_id": claim["signed_off_by"]})
    elif saved.next:
        inputs = None  # carry on from the last saved step
    elif saved.values:
        return f"{claim_id} is {claim['status']}: {claim['decision']}"
    reply = ""
    for update in graph.stream(
        inputs, config=config, stream_mode="updates", durability="sync"
    ):
        if "__interrupt__" in update:
            reply = f"{claim_id} is waiting for a handler's sign-off."
            record_step(claim_id, "reply", reply)
            continue
        for node in update.values():
            for message in (node or {}).get("messages", []):
                for step in steps(message):
                    record_step(claim_id, step["kind"], step["text"])
                reply = message.text or reply
    return reply


def work_claim(claim_id: str, run_id: int, llm: BaseChatModel | None = None) -> str:
    """Work one claim on its thread and say in one line how it went."""
    with backend() as client:
        start = client.post(f"/api/claims/{claim_id}/start", json={"run_id": run_id})
    if not start.is_success:
        return f"not started: {start.text}"
    return carry_on(claim_id, start.json()["thread_id"], llm)


def carry_on(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Run the claim agent on a started claim; release it if it fails undecided."""
    error = "the claim agent stopped without a decision"
    reply = ""
    try:
        reply = run(claim_id, thread_id, llm)
    except Exception as exc:  # one bad claim must never stop the run
        error = f"{type(exc).__name__}: {exc}"[:RESULT_PREVIEW]
    with backend() as client:
        claim = client.get(f"/api/claims/{claim_id}").json()
        if claim["status"] == "in_progress" and claim["decision"] is None:
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


def queue_agent(
    run_id: int, llm: BaseChatModel | None, claim_llm: BaseChatModel | None
) -> CompiledStateGraph:
    """The queue agent for one run, saving its progress like the claim agent."""
    with backend() as client:
        instructions = client.get("/api/instructions").json()["queue"]
    return create_agent(
        llm or model(),
        tools=queue_tools(run_id, claim_llm),
        system_prompt=instructions,
        middleware=[retry()],
        checkpointer=saver(),
    )


def stream_queue(agent: CompiledStateGraph, inputs: Any, run_id: int) -> str:
    """Run the queue agent on its run's thread, printing each step."""
    config: RunnableConfig = {
        "configurable": {"thread_id": f"run-{run_id}"},
        "recursion_limit": RECURSION_LIMIT,
    }
    reply = ""
    for update in agent.stream(
        inputs, config=config, stream_mode="updates", durability="sync"
    ):
        for node in update.values():
            for message in (node or {}).get("messages", []):
                for step in steps(message):
                    print(f"[{step['kind']}] {step['text']}")
                reply = message.text
    return reply


def run_queue(
    cap: int, llm: BaseChatModel | None = None, claim_llm: BaseChatModel | None = None
) -> str:
    """Start a run of up to cap claims and let the queue agent work it."""
    with backend() as client:
        run_id = client.post("/api/runs", json={"cap": cap}).json()["run_id"]
    prompt = f"Please work the queue. This run is capped at {cap} claims."
    inputs = {"messages": [{"role": "user", "content": prompt}]}
    return stream_queue(queue_agent(run_id, llm, claim_llm), inputs, run_id)


def resume_queue(
    llm: BaseChatModel | None = None, claim_llm: BaseChatModel | None = None
) -> str | None:
    """Finish the latest run if its queue agent was stopped partway, or return None."""
    with backend() as client:
        run = client.get("/api/runs/current").json()
    if run is None or run["summary"] is not None:
        return None
    agent = queue_agent(run["run_id"], llm, claim_llm)
    config: RunnableConfig = {"configurable": {"thread_id": f"run-{run['run_id']}"}}
    if not agent.get_state(config).next:
        return None
    return stream_queue(agent, None, run["run_id"])


def carry_on_unfinished(llm: BaseChatModel | None = None) -> list[str]:
    """Carry on every claim that was stopped or signed off while the agent was away."""
    with backend() as client:
        unfinished = client.get("/api/claims/unfinished").json()
    lines = []
    for claim in unfinished:
        claim_id = claim["claim_id"]
        thread_id = claim["thread_id"] or f"{claim_id}-{claim['attempts'] + 1}"
        lines.append(f"{claim_id}: {carry_on(claim_id, thread_id, llm)}")
    return lines
