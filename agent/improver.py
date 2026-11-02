"""The improver: follows its recipe to propose better versions of the claim agent.

A LangChain agent, as in labs/week2/week2day5.ipynb. It pauses for a handler on each
proposal, and the agent's service carries a round on from the handler's answer.
"""

import contextvars
import json
import os
from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import SecretStr

from agent import dispatcher, improver_api, main

MODEL = "openai/gpt-6-luna"
EFFORT = "medium"
MODEL_TIMEOUT = 300
MODEL_RETRIES = 5
MAX_MODEL_CALLS = 30
# A version's evaluations in a round: once on the evaluation set, twice on the held-out.
EVALUATIONS = ["evaluation", "held_out", "held_out"]
START = "Please carry out one round of your recipe."

INSTRUCTIONS = """You improve the configuration of UnderCover's claim agent, an AI \
agent that decides travel insurance claims the way UnderCover's claims handlers would. \
Your aim is a version that agrees with the handlers more often and gives their \
reasons, at a lower cost and in less time, while it meets the business objectives on \
the held-out set.

The configuration is the claim agent's instructions, its model and its reasoning \
effort. The claim agent works from its instructions; the claim, the customer's policy \
and the customer's earlier claims; the context layer's tools for the terms the wording \
defines, the meaning of each field, the plans, the claim rules, outside reference \
data, and searching, listing and reading the sections of the policy wording and the \
claims manual; its memory of the customer from earlier claims, when memory is on; and \
its tools for working out a payout and recording a decision.

A change must help every claim like the ones it fixes, so it never names a claim, a \
customer or a policy. Nothing you propose takes effect until a handler approves it, \
and a handler who rejects it gives you the reason.

Your recipe says how to go about a round.

# Your recipe

{recipe}
"""


class RoundStoppedError(Exception):
    """The round was stopped, so the improver makes no more model calls."""


def round_of(config: RunnableConfig) -> int:
    """The round the improver is working."""
    return config.get("configurable", {})["round_id"]


@tool
def read_results(config: RunnableConfig) -> str:
    """Read the current version of the claim agent's configuration, its scores, its
    mistakes and weakest explanations on the evaluation set, the earlier rounds, the
    objectives, what's left of the round's spending limit and the models allowed."""
    return improver_api.text(improver_api.read_results(round_of(config)))


@tool(parse_docstring=True)
def propose_change(
    instructions: str,
    model: str,
    reasoning_effort: str,
    reason: str,
    config: RunnableConfig,
) -> str:
    """Propose a new version of the claim agent's configuration. A handler reviews it
    before it's saved.

    Args:
        instructions: The claim agent's complete instructions
        model: One of the models allowed
        reasoning_effort: A reasoning effort allowed for that model
        reason: Why this change should improve the scores
    """
    # This runs only once a handler has approved it, and the service has recorded it.
    version = improver_api.get_round(round_of(config))["version"]
    return f"A handler approved it as version {version}. Run the evaluation next."


@tool
def run_evaluation(config: RunnableConfig) -> str:
    """Run the new version once on the evaluation set and twice on the held-out set,
    then keep it or roll it back."""
    round_id = round_of(config)
    found = improver_api.get_round(round_id)
    if found["version"] is None or found["kept"] is not None:
        return "There's no new version to evaluate. Propose a change first."
    improver_api.record_stage(round_id, "evaluations")
    refused = ""
    for eval_set in EVALUATIONS:
        refused = evaluate(round_id, eval_set, found["version"])
        if refused:
            break
    response = improver_api.apply_verdict(round_id)
    if not response.is_success:
        return improver_api.text(response)
    judged = response.json()
    verdict = "Kept" if judged["kept"] else "Rolled back"
    scores = {k: judged[k] for k in ["held_out_before", "held_out_after"]}
    return (
        f"{refused}\n{verdict} version {judged['version']}: {judged['why']}.\n"
        f"{json.dumps(scores, indent=2)}"
    ).strip()


@tool(parse_docstring=True)
def propose_recipe(recipe: str, reason: str) -> str:
    """Propose a new version of your recipe for improving the claim agent. A handler
    reviews it before it's saved.

    Args:
        recipe: The complete new recipe
        reason: What this round showed, and why the new recipe should lead to better
            changes
    """
    return "A handler approved it as your next recipe."


TOOLS = [read_results, propose_change, run_evaluation, propose_recipe]


def evaluate(round_id: int, eval_set: str, version: int) -> str:
    """Run one of the round's evaluations by the dispatcher, or say why it's refused."""
    response = improver_api.create_evaluation(round_id, eval_set, version)
    if not response.is_success:
        return improver_api.text(response)
    run = response.json()
    # A fresh context, as the harness runs each claim graph: the dispatcher, compiled
    # without a checkpointer, would otherwise save into the improver's thread.
    contextvars.Context().run(dispatcher.dispatch, run["run_id"], run["n"])
    return ""


class RoundGuard(AgentMiddleware):
    """Stops at a stopped round, turns off parallel tool calls and records each call."""

    def __init__(self, round_id: int) -> None:
        super().__init__()
        self.round_id = round_id

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Call the model for one tool call at most; record its cost with the round."""
        if improver_api.get_round(self.round_id)["stage"] == "stopped":
            raise RoundStoppedError(f"Round {self.round_id} was stopped")
        # One tool call a reply, so a reply never holds two proposals.
        settings = {**request.model_settings, "parallel_tool_calls": False}
        response = handler(request.override(model_settings=settings))
        for message in response.result:
            if isinstance(message, AIMessage):
                improver_api.record_model_call(self.round_id, message)
        return response


def improver_model() -> ChatOpenAI:
    """GPT-6 Luna at medium effort, or the model in MODEL_NAME at its default."""
    free = main.one_model()
    return ChatOpenAI(
        model=free or MODEL,
        base_url=main.OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if free else EFFORT,
        timeout=MODEL_TIMEOUT,
        max_retries=MODEL_RETRIES,
    )


def make_improver(round_id: int, recipe: str) -> CompiledStateGraph:
    """Build the improver for a round, with the round's recipe in its instructions."""
    approval = HumanInTheLoopMiddleware(
        interrupt_on={
            "propose_change": {"allowed_decisions": ["approve", "reject"]},
            "propose_recipe": {"allowed_decisions": ["approve", "reject"]},
        }
    )
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        RoundGuard(round_id),
        # Per round, not per resume: each round has a thread of its own
        ModelCallLimitMiddleware(thread_limit=MAX_MODEL_CALLS),
        approval,
    ]
    return create_agent(
        model=improver_model(),
        tools=TOOLS,
        system_prompt=INSTRUCTIONS.format(recipe=recipe),
        middleware=middleware,
        checkpointer=main.saver(),
    )


def traced(round_id: int, thread_id: str) -> RunnableConfig:
    """The improver's run config, in one Langfuse session for the round."""
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "round_id": round_id},
        "recursion_limit": main.RECURSION_LIMIT,
    }
    if os.getenv("LANGFUSE_PUBLIC_KEY"):
        config["callbacks"] = [CallbackHandler()]
        config["run_name"] = f"Improver round {round_id}"
        config["metadata"] = {"langfuse_session_id": f"Round {round_id}"}
    return config


def first_evaluations(round_id: int, version: int) -> None:
    """Run what the current version lacks of 1 evaluation-set and 2 held-out runs."""
    scores = improver_api.read_results(round_id).json()["scores"]
    for eval_set in ["evaluation", "held_out"]:
        done = (scores[eval_set] or {}).get("runs", 0)
        for _ in range(EVALUATIONS.count(eval_set) - done):
            if evaluate(round_id, eval_set, version):
                return


def work_round(round_id: int) -> None:
    """Work a new round: its first evaluations, then the improver until it pauses."""
    first_evaluations(round_id, improver_api.get_round(round_id)["version_before"])
    improver_api.record_stage(round_id, "improving")
    carry_on(round_id, {"messages": [HumanMessage(START)]})


def answer(round_id: int, decision: str, reason: str) -> None:
    """Carry the improver on from a handler's answer, which the service has recorded."""
    answered: dict[str, str] = {"type": decision}
    if decision == "reject":
        answered["message"] = reason
    carry_on(round_id, Command(resume={"decisions": [answered]}))


def carry_on(round_id: int, inputs: Any) -> None:
    """Run the improver until it waits for a handler or finishes.

    A proposal the backend refuses, such as a model not allowed, goes back to the
    improver as a rejection without reaching a handler.
    """
    found = improver_api.get_round(round_id)
    recipe = improver_api.recipe(found["recipe_version"])
    improver = make_improver(round_id, recipe)
    config = traced(round_id, found["thread_id"])
    try:
        while True:
            result = improver.invoke(inputs, config, durability="sync")
            if "__interrupt__" not in result:
                improver_api.record_stage(round_id, "finished")
                return
            request = result["__interrupt__"][0].value["action_requests"][0]
            response = improver_api.propose(round_id, request["name"], request["args"])
            if response.is_success:
                return
            refused = {"type": "reject", "message": improver_api.text(response)}
            inputs = Command(resume={"decisions": [refused]})
    except RoundStoppedError:
        return
    except Exception as exc:  # a round that failed must not stay open and block
        improver_api.stop_working_rounds()
        print(f"Round {round_id} stopped: {type(exc).__name__}: {exc}")
