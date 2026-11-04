"""The Copilot as an AgentCore app: each payload names an operation and its inputs.

npm run dev serves `app` on 127.0.0.1:8081, and runtime/copilot.py runs it on AgentCore
Runtime. Nothing happens at import: it gets ready on its first request, once per
process. It asks for no key, since every call it makes carries the handler's sign-in,
which the app checks.
"""

import os
import random
import threading
from collections.abc import Callable
from typing import Annotated, Any, Literal

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)
from starlette.exceptions import HTTPException

from agent import copilot, entrypoint, main

# The only credentials the Copilot fetches on Runtime.
NEEDED = {"OPENROUTER_API_KEY", "DATABASE_URL"}
ACCEPTED = {"accepted": True}

app = BedrockAgentCoreApp()
getting_ready = threading.Lock()
ready = threading.Event()

# A conversation's id, which is also its Runtime session and its Memory session.
ConversationId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{33,100}$")]


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(Operation):
    operation: Literal["health"]


class StartTurn(Operation):
    operation: Literal["start_turn"]
    conversation_id: ConversationId
    turn_id: int
    message: str
    token: str = Field(repr=False)


class AnswerConfirmation(Operation):
    operation: Literal["answer_confirmation"]
    conversation_id: ConversationId
    turn_id: int
    answer: Literal["sign_off", "cancel"]
    token: str = Field(repr=False)


Payload = Annotated[
    Health | StartTurn | AnswerConfirmation, Field(discriminator="operation")
]
PAYLOAD: TypeAdapter[Payload] = TypeAdapter(Payload)


def get_ready() -> None:
    """Once per process, on the first request: its two credentials, and the pool."""
    with getting_ready:
        if ready.is_set():
            return
        if entrypoint.on_runtime():
            # Every session on Runtime starts from one snapshot, random numbers and all.
            random.seed()
            for setting, provider in entrypoint.credentials().items():
                if setting in NEEDED:
                    os.environ[setting] = entrypoint.fetch(provider)
        if os.getenv("DATABASE_URL"):
            main.pool()
        ready.set()


def in_background(work: Callable[..., None], *args: Any) -> dict:
    """Work the turn on a thread of its own, registered with the app until it ends."""
    task = app.add_async_task(work.__name__)

    def go() -> None:
        try:
            work(*args)
        finally:
            app.complete_async_task(task)

    threading.Thread(target=go, daemon=True).start()
    return ACCEPTED


def operate(operation: Payload) -> dict:
    """Do what the operation asks: answer at once, and work the turn in the
    background."""
    match operation:
        case Health():
            return {"healthy": True}
        case StartTurn():
            return in_background(
                copilot.start_turn,
                operation.conversation_id,
                operation.turn_id,
                operation.message,
                operation.token,
            )
        case AnswerConfirmation():
            return in_background(
                copilot.answer_confirmation,
                operation.conversation_id,
                operation.turn_id,
                operation.answer,
                operation.token,
            )


@app.entrypoint
def invoke(payload: Any) -> dict:
    """Take one operation's payload, check it, and answer."""
    get_ready()
    try:
        operation = PAYLOAD.validate_python(payload)
    except ValidationError as exc:
        raise HTTPException(400, entrypoint.problems(exc)) from exc
    return operate(operation)
