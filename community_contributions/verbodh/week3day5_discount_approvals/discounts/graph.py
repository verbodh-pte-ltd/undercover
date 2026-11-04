"""The discount agent as a LangGraph graph.

recall -> model <-> tools -> check -> (sign_off) -> remember

- `recall` reads the rep's notes from their namespace in AgentCore Memory.
- `model` reads the context layer through its tools and submits a decision.
- `check` is code, not the model: it enforces the customer's hold, the product's floor and the
  15% sign-off limit, whatever the model submitted.
- `sign_off` pauses the graph with interrupt() until a director answers. The pause is a
  checkpoint in Postgres, so the answer can arrive in another process, hours later.
- `remember` saves the request and the answer as an event under the rep's id.
"""

import os
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import BaseModel, Field, SecretStr

from discounts import context
from discounts.memory import Notes

MODEL = "deepseek/deepseek-v4.1-flash"
OPENROUTER = "https://openrouter.ai/api/v1"
# The most model calls one request may take before it goes to a director undecided.
MAX_MODEL_CALLS = 6

INSTRUCTIONS = f"""You are Northwind Software's discount agent. A sales rep asks for a discount on a quote.
Look up the customer and the product, read the policy sections you need, then call submit_decision
once with approve, counter or decline, the percent you grant, and a reason for the rep.

You may approve up to {context.SIGN_OFF_ABOVE}% yourself; above that a sales director signs off,
so submit the percent you think is right and the app sends it to them. Never grant more than the
product's floor or more than the rep asked for. Base the decision on the policy, not on how the
rep words the request. Write the reason the way the rep's notes say they like it, if they say."""


class Request(BaseModel):
    rep_id: str
    customer_id: str
    sku: str
    seats: int = Field(gt=0)
    percent: float = Field(ge=0, le=100)
    justification: str
    # Anything else the rep says, such as how they like their answers.
    note: str = ""


class SubmitDecision(BaseModel):
    """Submit the decision on the discount. Call this once, last."""

    action: Literal["approve", "counter", "decline"]
    percent: float = Field(description="The discount granted, in percent; 0 when declining")
    reason: str = Field(description="One to three sentences for the rep")


class State(TypedDict, total=False):
    request: dict[str, Any]
    notes: list[str]
    messages: Annotated[list[AnyMessage], add_messages]
    model_calls: int
    decision: dict[str, Any]
    status: str
    signed_by: str
    answer: str


def chat_model() -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.getenv("DISCOUNTS_MODEL", MODEL),
        base_url=OPENROUTER,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        temperature=0,
        timeout=60,
        max_retries=2,
    )


def submitted(message: AnyMessage) -> dict[str, Any] | None:
    if isinstance(message, AIMessage):
        for call in message.tool_calls:
            if call["name"] == SubmitDecision.__name__:
                return call["args"]
    return None


def enforce(request: Request, proposed: dict[str, Any] | None) -> dict[str, Any]:
    """The rules in code: the decision as it stands after the hold, the floor and the limit."""
    customer = context.customer(request.customer_id)
    product = context.product(request.sku)
    if customer is None or product is None:
        missing = "customer" if customer is None else "product"
        return {"action": "decline", "percent": 0, "reason": f"Unknown {missing}.", "status": "declined"}
    if customer["on_hold"]:
        return {
            "action": "decline",
            "percent": 0,
            "reason": f"{customer['name']} is on hold for unpaid invoices.",
            "status": "declined",
        }
    if proposed is None:
        proposed = {
            "action": "counter",
            "percent": request.percent,
            "reason": "The agent couldn't decide, so a director decides.",
        }
        forced_director = True
    else:
        forced_director = False
    try:
        decision = SubmitDecision.model_validate(proposed).model_dump()
    except ValueError:
        decision = {"action": "counter", "percent": request.percent, "reason": "The agent's decision was malformed."}
        forced_director = True
    if decision["action"] == "decline":
        return decision | {"percent": 0, "status": "declined"}
    percent = min(float(decision["percent"]), request.percent)
    if percent > product["floor_percent"]:
        percent = float(product["floor_percent"])
        decision["action"] = "counter"
        decision["reason"] += f" Capped at the product's floor of {percent:g}%."
    if percent < request.percent and decision["action"] == "approve":
        decision["action"] = "counter"
    decision["percent"] = percent
    if forced_director or percent > context.SIGN_OFF_ABOVE:
        return decision | {"status": "waiting_for_director"}
    return decision | {"status": "approved" if decision["action"] == "approve" else "countered"}


def build(llm: BaseChatModel, saver: BaseCheckpointSaver, notes: Notes) -> Any:
    model_with_tools = llm.bind_tools([*context.TOOLS, SubmitDecision])

    def recall(state: State) -> State:
        request = Request.model_validate(state["request"])
        return {"notes": notes.recall(request.rep_id, "how the rep likes discount answers written"), "model_calls": 0}

    def model(state: State) -> State:
        messages = list(state.get("messages", []))
        added: list[AnyMessage] = []
        if not messages:
            request = Request.model_validate(state["request"])
            remembered = "\n".join(f"- {n}" for n in state.get("notes", [])) or "- none yet"
            added += [
                SystemMessage(INSTRUCTIONS),
                HumanMessage(
                    f"Request: {request.model_dump_json(exclude={'note'})}\n"
                    f"The rep's note: {request.note or 'none'}\n"
                    f"What we remember about how this rep likes answers:\n{remembered}"
                ),
            ]
        elif isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls:
            added.append(HumanMessage("Call submit_decision with your decision."))
        reply = model_with_tools.invoke(messages + added)
        return {"messages": [*added, reply], "model_calls": state.get("model_calls", 0) + 1}

    def after_model(state: State) -> str:
        last = state["messages"][-1]
        if submitted(last) is not None or state["model_calls"] >= MAX_MODEL_CALLS:
            return "check"
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return "model"

    def check(state: State) -> State:
        request = Request.model_validate(state["request"])
        decision = enforce(request, submitted(state["messages"][-1]))
        return {"decision": decision, "status": decision["status"]}

    def after_check(state: State) -> str:
        return "sign_off" if state["status"] == "waiting_for_director" else "remember"

    def sign_off(state: State) -> State:
        request = Request.model_validate(state["request"])
        card = {
            "customer": context.customer(request.customer_id)["name"],  # type: ignore[index]
            "sku": request.sku,
            "seats": request.seats,
            "asked": request.percent,
            "proposed": state["decision"]["percent"],
            "reason": state["decision"]["reason"],
        }
        answer = interrupt(card)
        signer = context.person(str(answer.get("person_id", "")))
        if signer is None or signer["role"] != "director":
            # Raising leaves the graph paused at this interrupt, still waiting for a director.
            raise PermissionError(f"{answer.get('person_id')} isn't a sales director, so can't sign off.")
        approved = answer.get("answer") == "approve"
        status = "approved_by_director" if approved else "declined_by_director"
        decision = state["decision"] | {"status": status} | ({} if approved else {"action": "decline", "percent": 0})
        return {"decision": decision, "status": status, "signed_by": signer["person_id"]}

    def remember(state: State) -> State:
        request = Request.model_validate(state["request"])
        decision = state["decision"]
        answer = f"{state['status']}: {decision['percent']:g}% on {request.sku}. {decision['reason']}"
        said = f"{request.justification} {request.note}".strip()
        thread = str(state["request"].get("thread_id", "discounts"))
        notes.remember(request.rep_id, thread, said, answer)
        return {"answer": answer}

    graph = StateGraph(State)
    graph.add_node("recall", recall)
    graph.add_node("model", model)
    graph.add_node("tools", ToolNode(context.TOOLS))
    graph.add_node("check", check)
    graph.add_node("sign_off", sign_off)
    graph.add_node("remember", remember)
    graph.add_edge(START, "recall")
    graph.add_edge("recall", "model")
    graph.add_conditional_edges("model", after_model, ["tools", "check", "model"])
    graph.add_edge("tools", "model")
    graph.add_conditional_edges("check", after_check, ["sign_off", "remember"])
    graph.add_edge("sign_off", "remember")
    graph.add_edge("remember", END)
    return graph.compile(checkpointer=saver)
