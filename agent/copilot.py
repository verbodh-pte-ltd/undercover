"""The UnderCover Copilot: an agent for handlers, working as the handler who asked.

Its loop is a LangGraph graph written by hand: `remember` looks up the handler's
memory, `model` calls the model, `tools` runs the tools it asked for and goes back to
the model, `confirm` pauses before a sign-off, and `save` keeps the turn in memory.
Each turn starts by asking the app who is signed in, and the handler's sign-in reaches
the graph and its tools only in the graph's context, never in its saved state.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Annotated, Any, Literal, TypedDict

import boto3
import httpx
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import Command, interrupt
from langgraph_checkpoint_aws import AgentCoreMemoryStore
from pydantic import SecretStr

from agent import copilot_api as app
from agent.direct import CONTEXT_URL, Direct
from agent.gateway import Gateway
from agent.main import (
    MODEL_RETRIES,
    MODEL_TIMEOUT,
    OPENROUTER_BASE_URL,
    Route,
    pool,
)
from agent.memory import UNREACHABLE

log = logging.getLogger(__name__)
MAX_MODEL_CALLS = 25
RECURSION_LIMIT = 200
# The wait for a dispatch or an evaluation: a look at the app every 5 seconds, up to
# 15 minutes, with no model calls.
WAIT_EVERY = 5
WAIT_FOR = 15 * 60
STRAIGHT_THROUGH = 0.70
MEMORY_LIMIT = 5
ERROR_LIMIT = 500
SIGN_IN = "[the sign-in]"
WHERE = {"dispatch": "the Queue page", "evaluation": "the Scoreboard"}

Kind = Literal["claims", "customers", "context", "work", "memory", "action"]
Document = Literal["policy_wording", "claims_manual"]


@dataclass
class CopilotContext:
    """Who the turn works for, for one request only: never saved with the graph."""

    token: str = field(repr=False)
    handler: dict[str, Any]
    conversation: str
    turn: int
    route: Route = field(repr=False)


Ctx = ToolRuntime[CopilotContext]


class CopilotState(TypedDict):
    """What the graph keeps for one conversation, saved after every step."""

    messages: Annotated[list[AnyMessage], add_messages]
    message: str
    remembered: list[str]
    model_calls: int
    cost: float


CopilotGraph = CompiledStateGraph[
    CopilotState, CopilotContext, CopilotState, CopilotState
]


def clock() -> float:
    """Seconds on a clock that only goes forward."""
    return time.monotonic()


def sleep(seconds: float) -> None:
    """Wait, without calling the model."""
    time.sleep(seconds)


# The steps


@dataclass
class Outcome:
    """What a step came back with: its answer, its state, and a lookup's source."""

    answer: str
    state: Literal["done", "refused", "failed"] = "done"
    file: str | None = None
    owner: str | None = None


def from_app(response: httpx.Response) -> Outcome:
    """The app's answer, or its refusal with the reason."""
    if response.is_success:
        return Outcome(response.text)
    return Outcome(app.refusal(response), "refused")


def step(
    context: CopilotContext,
    key: str,
    shown: tuple[Kind, str, dict[str, Any]],
    work: Callable[[], Outcome],
) -> Outcome:
    """Do one step, recording it with the app as working, then as it ended."""
    kind, name, args = shown
    card = {
        "key": key,
        "kind": kind,
        "tool": name,
        "input": str(next(iter(args.values()), "")),
    }
    app.record_step(
        context.token,
        context.turn,
        {**card, "state": "working", "seconds": None, "detail": None},
    )
    started = clock()
    try:
        outcome = work()
    except Exception as exc:  # a failed step goes back to the model, as a refusal does
        outcome = Outcome(f"Failed: {type(exc).__name__}: {exc}", "failed")
    detail = {
        "asked": app.trim(json.dumps(args)),
        "answer": app.trim(outcome.answer),
        "file": outcome.file,
        "owner": outcome.owner,
    }
    seconds = round(clock() - started, 1)
    ended = {**card, "state": outcome.state, "seconds": seconds, "detail": detail}
    app.record_step(context.token, context.turn, ended)
    return outcome


def key_of(model_calls: int, tool_call_id: str | None) -> str:
    """A step's key, unique in its turn: the model call that asked, and the call."""
    return f"{model_calls}-{tool_call_id}"


def stepped(
    runtime: Ctx,
    kind: Kind,
    name: str,
    args: dict[str, Any],
    work: Callable[[], Outcome],
) -> str:
    """A tool's step, recorded as it happens; its answer for the model."""
    key = key_of(runtime.state["model_calls"], runtime.tool_call_id)
    return step(runtime.context, key, (kind, name, args), work).answer


def read(
    runtime: Ctx,
    kind: Kind,
    name: str,
    args: dict[str, Any],
    path: str,
) -> str:
    """A read from the app as the handler, as a step."""
    token = runtime.context.token
    return stepped(runtime, kind, name, args, lambda: from_app(app.get(token, path)))


def settings(token: str) -> dict[str, Any]:
    """The platform's settings."""
    found = app.get(token, "/api/settings")
    found.raise_for_status()
    return found.json()["settings"]


# Claims


@tool
def list_claims(runtime: Ctx) -> str:
    """List the claims queue in order, with where each claim stands."""
    return read(runtime, "claims", "list_claims", {}, "/api/claims")


@tool
def get_claim(claim_id: str, runtime: Ctx) -> str:
    """Get one claim in full: its decision, payout and any decision sent back."""
    args = {"claim_id": claim_id}
    return read(runtime, "claims", "get_claim", args, f"/api/claims/{claim_id}")


@tool
def get_claim_steps(claim_id: str, runtime: Ctx) -> str:
    """List the steps the agent took on a claim, in order."""
    args = {"claim_id": claim_id}
    path = f"/api/claims/{claim_id}/steps"
    return read(runtime, "claims", "get_claim_steps", args, path)


# Customers and policies


@tool
def find_customers(name: str, runtime: Ctx) -> str:
    """Find customers by name: their ids, names and emails."""
    token = runtime.context.token

    def work() -> Outcome:
        return from_app(app.get(token, "/api/customers", {"name": name}))

    return stepped(runtime, "customers", "find_customers", {"name": name}, work)


@tool
def get_customer(customer_id: str, runtime: Ctx) -> str:
    """Get a customer: who they are, their policies, earlier claims and queue claims."""
    args = {"customer_id": customer_id}
    path = f"/api/customers/{customer_id}"
    return read(runtime, "customers", "get_customer", args, path)


@tool
def get_policy(policy_id: str, runtime: Ctx) -> str:
    """Get a policy: the plan, the add-ons, the trip and the travelers."""
    args = {"policy_id": policy_id}
    return read(runtime, "customers", "get_policy", args, f"/api/policies/{policy_id}")


# The agent's work and the settings


@tool
def get_latest_dispatch(runtime: Ctx) -> str:
    """Get the latest dispatch with its claims and summary, or null if none."""
    return read(runtime, "work", "get_latest_dispatch", {}, "/api/runs/current")


@tool
def list_evaluations(runtime: Ctx) -> str:
    """List the evaluations, newest first, with their measures."""
    return read(runtime, "work", "list_evaluations", {}, "/api/evaluations")


@tool
def get_settings(runtime: Ctx) -> str:
    """Get the settings: the approval limit, the objectives and agents at once."""
    return read(runtime, "work", "get_settings", {}, "/api/settings")


def started(response: httpx.Response) -> Outcome:
    """A dispatch or an evaluation as started: its run id, or the app's refusal."""
    if not response.is_success:
        return from_app(response)
    run = response.json()
    return Outcome(json.dumps({k: run[k] for k in ["run_id", "kind", "n", "status"]}))


@tool
def start_dispatch(agents: int | None, runtime: Ctx) -> str:
    """Start a dispatch of the queue with this many claim agents at once.

    Give agents only when the handler names a number; null uses the settings' number.
    Then wait_for_run on its run_id, when the handler wants to know how it went.
    """
    token = runtime.context.token

    def work() -> Outcome:
        n = agents or settings(token)["agents_at_once"]
        return started(app.post(token, "/api/dispatches", {"n": n}))

    return stepped(runtime, "action", "start_dispatch", {"agents": agents}, work)


@tool
def start_evaluation(
    eval_set: Literal["evaluation", "held_out"],
    tiny: bool,
    agents: int | None,
    runtime: Ctx,
) -> str:
    """Start an evaluation on a labeled set, whole or (tiny) its five tiny claims.

    Give agents only when the handler names a number; null uses the settings' number.
    """
    token = runtime.context.token

    def work() -> Outcome:
        n = agents or settings(token)["agents_at_once"]
        body = {"n": n, "eval_set": eval_set, "tiny": tiny}
        return started(app.post(token, "/api/evaluations", body))

    args = {"eval_set": eval_set, "tiny": tiny, "agents": agents}
    return stepped(runtime, "action", "start_evaluation", args, work)


@tool
def send_back(claim_id: str, note: str, runtime: Ctx) -> str:
    """Send a payout held for sign-off back to the agent, with the handler's note."""
    token = runtime.context.token
    path = f"/api/claims/{claim_id}/send-back"

    def work() -> Outcome:
        return from_app(app.post(token, path, {"note": note}))

    args = {"claim_id": claim_id, "note": note}
    return stepped(runtime, "action", "send_back", args, work)


@tool
def sign_off(claim_id: str, runtime: Ctx) -> str:
    """Sign off a payout held for sign-off, which pays it. The handler confirms first.

    Ask for one sign-off at a time, with no other tool beside it.
    """
    token = runtime.context.token
    path = f"/api/claims/{claim_id}/sign-off"

    def work() -> Outcome:
        return from_app(app.post(token, path))

    return stepped(runtime, "action", "sign_off", {"claim_id": claim_id}, work)


def wait(token: str, run_id: int) -> httpx.Response:
    """The run once it has ended, or as it stands after 15 minutes; or the refusal."""
    began = clock()
    while True:
        found = app.get(token, f"/api/runs/{run_id}")
        if not found.is_success or found.json()["status"] != "running":
            return found
        if clock() - began >= WAIT_FOR:
            return found
        sleep(WAIT_EVERY)


def still_going(run: dict[str, Any]) -> str:
    """What to say about a run still going after the wait."""
    return (
        f"{run['kind'].capitalize()} {run['run_id']} is still going after 15 minutes."
        f" Follow it on {WHERE[run['kind']]}."
    )


@tool
def wait_for_run(run_id: int, runtime: Ctx) -> str:
    """Wait for a dispatch or an evaluation you started to end, then get it.

    Use it when the handler wants to know how the work went. It waits up to 15 minutes.
    """
    token = runtime.context.token

    def work() -> Outcome:
        found = wait(token, run_id)
        if not found.is_success:
            return from_app(found)
        run = found.json()
        if run["status"] == "running":
            return Outcome(json.dumps({"still_going": still_going(run), **run}))
        return Outcome(found.text)

    return stepped(runtime, "work", "wait_for_run", {"run_id": run_id}, work)


# The success criteria, worked out in code


def percent(value: float) -> str:
    return f"{value:.1%}"


def measured(token: str, run_id: int) -> tuple[dict[str, float] | None, str]:
    """An evaluation's straight-through share and accuracy once it finishes, or why
    there are none."""
    found = wait(token, run_id)
    if not found.is_success:
        return None, f"Evaluation {run_id} couldn't be read: {app.reason(found)}"
    run = found.json()
    if run["status"] == "running":
        return None, still_going(run)
    if run["status"] != "finished":
        why = run.get("stopped_reason") or "no reason given"
        return None, f"Evaluation {run_id} {run['status']} partway: {why}"
    evaluation = app.get(token, f"/api/evaluations/{run_id}")
    evaluation.raise_for_status()
    # Only the measures: the handlers' decisions on the labeled set stay out.
    measures = {k: evaluation.json()[k] for k in ["straight_through", "accuracy"]}
    return measures, f"Evaluation {run_id} of the whole held-out set finished"


def evaluated(
    measures: dict[str, float] | None, why: str, objective: float
) -> list[dict[str, Any]]:
    """The first two criteria, from the evaluation's measures, or no figures."""
    straight = {
        "criterion": "Straight through",
        "target": f"at least {STRAIGHT_THROUGH:.0%} of the whole held-out set",
    }
    accuracy = {
        "criterion": "Accuracy",
        "target": f"at least {percent(objective)}, the accuracy objective in Settings",
    }
    if measures is None:
        return [
            {**row, "figure": None, "met": None, "why": why}
            for row in (straight, accuracy)
        ]
    return [
        {
            **straight,
            "figure": percent(measures["straight_through"]),
            "met": measures["straight_through"] >= STRAIGHT_THROUGH,
            "why": why,
        },
        {
            **accuracy,
            "figure": percent(measures["accuracy"]),
            "met": measures["accuracy"] >= objective,
            "why": why,
        },
    ]


def over_the_limit(counts: dict[str, Any]) -> dict[str, Any]:
    """The third criterion, from the app's count of payouts over the limit."""
    limit, paid = counts["approval_limit"], counts["paid_over_limit"]
    row = {
        "criterion": "Over the limit",
        "target": f"every payout over the ${limit:,.2f} approval limit signed off",
    }
    if paid == 0:
        why = (
            f"Nothing in the queue has been paid over the ${limit:,.2f} approval"
            " limit yet, so no such payout lacks a person's sign-off."
        )
        return {
            **row,
            "figure": "none paid over the limit yet",
            "met": True,
            "why": why,
        }
    signed = counts["signed_off"]
    figure = f"{signed} of {paid} paid over the limit signed off"
    return {**row, "figure": figure, "met": signed >= paid, "why": None}


def all_met(rows: list[dict[str, Any]]) -> bool | None:
    """True when all three are met, False when any isn't, None when unknown."""
    if any(row["met"] is False for row in rows):
        return False
    return True if all(row["met"] for row in rows) else None


def criteria(token: str) -> dict[str, Any]:
    """Run the whole held-out set and check the three success criteria from records."""
    current = settings(token)
    body = {"n": current["agents_at_once"], "eval_set": "held_out", "tiny": False}
    began = app.post(token, "/api/evaluations", body)
    if began.is_success:
        measures, why = measured(token, began.json()["run_id"])
    else:
        measures, why = (
            None,
            f"The app didn't start the evaluation: {app.reason(began)}",
        )
    counts = app.get(token, "/api/success-counts")
    counts.raise_for_status()
    objective = current["objectives"]["accuracy"]
    rows = [*evaluated(measures, why, objective), over_the_limit(counts.json())]
    return {"criteria": rows, "all_met": all_met(rows)}


@tool
def check_success_criteria(runtime: Ctx) -> str:
    """Check the three success criteria we agreed, on a new held-out evaluation.

    It starts the evaluation, waits for it and works out every figure from the records.
    Answer with its rows as a small table, and one line on whether all three are met.
    """
    token = runtime.context.token

    def work() -> Outcome:
        return Outcome(json.dumps(criteria(token)))

    return stepped(runtime, "action", "check_success_criteria", {}, work)


# The context layer's lookups, on the claim agent's route


def source(text: str) -> tuple[str | None, str | None]:
    """The file a lookup's answer came from, and its owner."""
    found = json.loads(text)
    sections = [s for s in found.get("sections", []) if isinstance(s, dict)]
    origin = found.get("source") or next(
        (s["source"] for s in sections if "source" in s), {}
    )
    return origin.get("file"), origin.get("owner")


def look_up(runtime: Ctx, name: str, args: dict[str, Any]) -> str:
    """One of the context layer's lookups, as a step with its file and owner."""

    def work() -> Outcome:
        answer = runtime.context.route.use(name, args)
        if answer.refused_by:
            return Outcome(answer.text, "refused")
        file, owner = source(answer.text)
        return Outcome(answer.text, file=file, owner=owner)

    return stepped(runtime, "context", name, args, work)


@tool
def lookup_term(term: str, runtime: Ctx) -> str:
    """Look up a term the policy wording defines, with the sections it comes from."""
    return look_up(runtime, "lookup_term", {"term": term})


@tool
def describe_fields(
    record: Literal["claim", "policy", "previous_claim"], runtime: Ctx
) -> str:
    """Describe what each field of a claim, a policy or a previous claim means."""
    return look_up(runtime, "describe_fields", {"record": record})


@tool
def get_plan(plan: Literal["economy", "premium", "first"], runtime: Ctx) -> str:
    """Get one plan's limits, waiting periods and deductible, and the add-ons."""
    return look_up(runtime, "get_plan", {"plan": plan})


@tool
def get_claim_rules(runtime: Ctx) -> str:
    """Get the claim rules: filing window, pre-existing look-back, repeats, meals."""
    return look_up(runtime, "get_claim_rules", {})


@tool
def search_reference_data(query: str, runtime: Ctx) -> str:
    """Find outside facts on events, places and products holding every query word."""
    return look_up(runtime, "search_reference_data", {"query": query})


@tool
def search_documents(query: str, runtime: Ctx) -> str:
    """Find the sections of the wording and the manual that hold every query word."""
    return look_up(runtime, "search_documents", {"query": query})


@tool
def list_sections(document: Document, runtime: Ctx) -> str:
    """List the numbered sections of policy_wording or claims_manual."""
    return look_up(runtime, "list_sections", {"document": document})


@tool
def read_section(document: Document, number: str, runtime: Ctx) -> str:
    """Read one numbered section of policy_wording or claims_manual."""
    return look_up(runtime, "read_section", {"document": document, "number": number})


TOOLS = [
    list_claims,
    get_claim,
    get_claim_steps,
    find_customers,
    get_customer,
    get_policy,
    get_latest_dispatch,
    list_evaluations,
    get_settings,
    start_dispatch,
    start_evaluation,
    send_back,
    sign_off,
    wait_for_run,
    check_success_criteria,
    lookup_term,
    describe_fields,
    get_plan,
    get_claim_rules,
    search_reference_data,
    search_documents,
    list_sections,
    read_section,
]


# The graph


def who(context: CopilotContext, remembered: list[str]) -> SystemMessage:
    """Who the turn works for, from the app's answer, and what we remember of them."""
    handler = context.handler
    may = "can" if handler["can_approve_over_limit"] else "can't"
    text = (
        f"You're working for {handler['name']} ({handler['handler_id']}). They {may}"
        " sign off payouts over the approval limit or send them back."
    )
    if remembered:
        text += "\nWhat you remember about them:\n" + "\n".join(
            f"- {note}" for note in remembered
        )
    return SystemMessage(text)


def remember(state: CopilotState, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
    """Look up what we remember about the handler that bears on their message."""
    store, context = runtime.store, runtime.context
    if store is None:
        return {"remembered": []}
    found: list[str] = []
    namespace = ("handlers", context.handler["handler_id"])

    def work() -> Outcome:
        items = store.search(namespace, query=state["message"], limit=MEMORY_LIMIT)
        found.extend(str(i.value.get("content", i.value)) for i in items)
        return Outcome(json.dumps(found))

    shown: tuple[Kind, str, dict[str, Any]] = (
        "memory",
        "search_memory",
        {"query": state["message"]},
    )
    step(context, "remember", shown, work)
    return {"remembered": found}


def save(state: CopilotState, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
    """Save the handler's message and the answer to their memory, and nothing else."""
    store, context = runtime.store, runtime.context
    if store is None:
        return {}
    # The actor is the handler, and the session the conversation.
    namespace = (context.handler["handler_id"], context.conversation)
    events = [HumanMessage(state["message"]), AIMessage(state["messages"][-1].text)]
    try:
        for name, event in zip(["message", "answer"], events, strict=True):
            store.put(namespace, f"turn-{context.turn}-{name}", {"message": event})
    except UNREACHABLE as exc:
        log.warning("Couldn't save turn %s to memory: %s", context.turn, exc)
    return {}


def sign_off_refusal(context: CopilotContext, calls: list[Any]) -> str | None:
    """Why the handler can't be asked to sign off, checked in code; or None."""
    if len(calls) != 1:
        return "Ask for a sign-off on its own: one claim, with no other tool beside it."
    handler = context.handler
    if not handler["can_approve_over_limit"]:
        return f"{handler['name']} can't sign off payouts over the approval limit."
    claim_id = calls[0]["args"]["claim_id"]
    found = app.get(context.token, f"/api/claims/{claim_id}")
    if not found.is_success:
        return app.refusal(found)
    claim = found.json()
    if claim["run_id"] is not None:
        return f"{claim_id} is an evaluation's copy, which nobody signs off."
    if claim["status"] != "waiting_for_sign_off":
        return f"{claim_id} isn't waiting for sign-off: it's {claim['status']}."
    return None


def confirm(
    state: CopilotState, runtime: Runtime[CopilotContext]
) -> Command[Literal["tools", "model"]]:
    """Pause for the handler to confirm a sign-off they may make; refuse one they can't.

    A resume runs this node again from the top, so it only reads before the pause.
    """
    context, last = runtime.context, state["messages"][-1]
    calls = last.tool_calls if isinstance(last, AIMessage) else []
    why = sign_off_refusal(context, calls)
    if why is None:
        claim_id = calls[0]["args"]["claim_id"]
        if interrupt({"claim_id": claim_id}) == "sign_off":
            return Command(goto="tools")
        why = (
            f"The handler pressed Cancel: {claim_id} has not been signed off or paid."
            " Say so, and ask what they'd like instead."
        )
    for call in calls:
        if call["name"] == "sign_off":
            key = key_of(state["model_calls"], call["id"])
            shown: tuple[Kind, str, dict[str, Any]] = (
                "action",
                "sign_off",
                call["args"],
            )
            step(context, key, shown, lambda: Outcome(why, "refused"))
    answers = [
        ToolMessage(why, tool_call_id=call["id"], name=call["name"]) for call in calls
    ]
    return Command(goto="model", update={"messages": answers})


def after_model(state: CopilotState) -> Literal["tools", "confirm", "save"]:
    """A sign-off goes to confirm, other tools to tools, and an answer to save."""
    last = state["messages"][-1]
    calls = last.tool_calls if isinstance(last, AIMessage) else []
    if any(call["name"] == "sign_off" for call in calls):
        return "confirm"
    return "tools" if calls else "save"


def copilot_graph(
    llm: BaseChatModel,
    instructions: str,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore | None = None,
) -> CopilotGraph:
    """The Copilot: remember, then the model and its tools, a confirm, then save."""
    model_with_tools = llm.bind_tools(TOOLS)
    system = SystemMessage(instructions)

    def model(state: CopilotState, runtime: Runtime[CopilotContext]) -> dict[str, Any]:
        """Call the model with the conversation so far, counting its calls and cost."""
        if state["model_calls"] >= MAX_MODEL_CALLS:
            raise RuntimeError(f"the Copilot reached {MAX_MODEL_CALLS} model calls")
        context = who(runtime.context, state["remembered"])
        reply = model_with_tools.invoke([system, context, *state["messages"]])
        cost = reply.response_metadata.get("token_usage", {}).get("cost") or 0.0
        return {
            "messages": [reply],
            "model_calls": state["model_calls"] + 1,
            "cost": state["cost"] + cost,
        }

    builder = StateGraph(CopilotState, context_schema=CopilotContext)
    builder.add_node("remember", remember)
    builder.add_node("model", model)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_node("confirm", confirm)
    builder.add_node("save", save)
    builder.add_edge(START, "remember")
    builder.add_edge("remember", "model")
    builder.add_conditional_edges("model", after_model, ["tools", "confirm", "save"])
    builder.add_edge("tools", "model")
    builder.add_edge("save", END)
    return builder.compile(checkpointer=checkpointer, store=store)


# What the graph is built from


def model(configuration: dict[str, Any]) -> ChatOpenAI:
    """The model the app names for the Copilot, at its reasoning effort."""
    effort = configuration["reasoning_effort"]
    return ChatOpenAI(
        model=configuration["model"],
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if effort in (None, "default") else effort,
        timeout=MODEL_TIMEOUT,
        max_retries=MODEL_RETRIES,
    )


def no_app() -> httpx.Client:
    """The direct route's way to the app, which the Copilot never takes."""
    raise RuntimeError("The Copilot reaches the app only with the handler's sign-in")


def route() -> Route:
    """The Gateway at UNDERCOVER_GATEWAY_URL if it is set, else the context layer."""
    gateway = os.getenv("UNDERCOVER_GATEWAY_URL")
    if gateway:
        return Gateway(gateway)
    return Direct(no_app, os.getenv("UNDERCOVER_CONTEXT", CONTEXT_URL))


@cache
def memory() -> InMemorySaver:
    """Where conversations are saved without DATABASE_URL."""
    return InMemorySaver()


def saver() -> BaseCheckpointSaver:
    """A saver for the conversation on the shared pool, or memory."""
    if not os.getenv("DATABASE_URL"):
        return memory()
    return PostgresSaver(pool())  # pyright: ignore[reportArgumentType]


def memory_store() -> BaseStore | None:
    """The Copilot's memory in UNDERCOVER_COPILOT_MEMORY_ID, or None."""
    memory_id = os.getenv("UNDERCOVER_COPILOT_MEMORY_ID")
    if not memory_id:
        return None
    region = boto3.Session().region_name
    return AgentCoreMemoryStore(memory_id=memory_id, region_name=region)


# A turn


def scrub(text: str, token: str) -> str:
    """Text with the sign-in taken out, trimmed for a turn's error."""
    return text.replace(token, SIGN_IN)[:ERROR_LIMIT] if token else text[:ERROR_LIMIT]


def loose_ends(messages: list[AnyMessage]) -> list[ToolMessage]:
    """Answers for the tool calls a stopped turn left unanswered, so the model can
    go on."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    asked = [m for m in messages if isinstance(m, AIMessage)]
    calls = asked[-1].tool_calls if asked else []
    return [
        ToolMessage(
            "Not done: the turn stopped before this step ended.",
            tool_call_id=call["id"],
            name=call["name"],
        )
        for call in calls
        if call["id"] not in answered
    ]


Inputs = Callable[[CopilotGraph, RunnableConfig], Any]


def end(
    context: CopilotContext,
    began: float,
    values: dict[str, Any],
    outcome: dict[str, str],
) -> None:
    """Tell the app the turn ended, with its answer or error, calls, cost and time."""
    spent = {
        "model_calls": values.get("model_calls", 0),
        "cost": round(values.get("cost", 0.0), 6),
        "seconds": round(clock() - began, 1),
    }
    if "error" in outcome:
        print(f"Turn {context.turn} failed: {outcome['error']}")
        app.fail_turn(context.token, context.turn, {**outcome, **spent})
        return
    app.finish_turn(context.token, context.turn, {**outcome, **spent})


def take_turn(
    conversation_id: str,
    turn_id: int,
    token: str,
    llm: BaseChatModel | None,
    inputs: Inputs,
) -> None:
    """Ask the app who is signed in, then work the turn as them, or fail it."""
    began = clock()
    found = app.signed_in_handler(token)
    if not found.is_success:
        # Nothing else happens: no model, no memory, no tool.
        error = f"The app didn't accept the sign-in: {app.reason(found)}"
        spent = {"error": scrub(error, token), "model_calls": 0, "cost": 0.0}
        app.fail_turn(token, turn_id, {**spent, "seconds": round(clock() - began, 1)})
        return
    context = CopilotContext(token, found.json(), conversation_id, turn_id, route())
    config: RunnableConfig = {
        "configurable": {"thread_id": conversation_id},
        "recursion_limit": RECURSION_LIMIT,
    }
    graph: CopilotGraph | None = None
    try:
        configuration = app.configuration(token)
        chosen = llm or model(configuration)
        graph = copilot_graph(
            chosen, configuration["instructions"], saver(), memory_store()
        )
        graph.invoke(inputs(graph, config), config, context=context, durability="sync")
    except Exception as exc:  # a failed turn ends with why, and the next can go on
        values = graph.get_state(config).values if graph else {}
        error = scrub(f"{type(exc).__name__}: {exc}", token)
        end(context, began, values, {"error": error})
        return
    saved = graph.get_state(config)
    if saved.interrupts:
        app.pause_turn(token, turn_id, saved.interrupts[0].value["claim_id"])
        return
    end(context, began, saved.values, {"answer": saved.values["messages"][-1].text})


def start_turn(
    conversation_id: str,
    turn_id: int,
    message: str,
    token: str,
    llm: BaseChatModel | None = None,
) -> None:
    """Work a handler's message in their conversation, as them."""

    def inputs(graph: CopilotGraph, config: RunnableConfig) -> Any:
        saved = graph.get_state(config).values.get("messages", [])
        return {
            "messages": [*loose_ends(saved), HumanMessage(message)],
            "message": message,
            "remembered": [],
            "model_calls": 0,
            "cost": 0.0,
        }

    take_turn(conversation_id, turn_id, token, llm, inputs)


def answer_confirmation(
    conversation_id: str,
    turn_id: int,
    answer: Literal["sign_off", "cancel"],
    token: str,
    llm: BaseChatModel | None = None,
) -> None:
    """Carry the paused turn on from the handler's answer to its confirmation."""

    def inputs(graph: CopilotGraph, config: RunnableConfig) -> Any:
        if not graph.get_state(config).interrupts:
            raise RuntimeError("Nothing in this conversation is waiting for an answer")
        return Command(resume=answer)

    take_turn(conversation_id, turn_id, token, llm, inputs)
