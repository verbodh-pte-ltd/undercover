"""The discount agent as an AgentCore app: one entrypoint, three operations.

- {"operation": "request", ...a discount request}: decides it, or pauses for a director.
- {"operation": "sign_off", "thread_id", "person_id", "answer": "approve" | "decline"}: resumes a
  paused request from its checkpoint, in whatever session the answer arrives.
- {"operation": "status", "thread_id"}: where a request stands.

On Runtime the two credentials come from AgentCore Identity: each DISCOUNTS_CREDENTIAL_<NAME>
setting names the provider holding <NAME>. On the laptop they come from the environment.
"""

import os
import re
import threading
import uuid
from typing import Any

from bedrock_agentcore.identity.auth import requires_api_key
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

from discounts import context, graph, memory

CREDENTIAL = re.compile(r"DISCOUNTS_CREDENTIAL_(\w+)")
SCHEMA = "discounts"

app = BedrockAgentCoreApp()
_lock = threading.Lock()
_graph: Any = None


def fetch(provider: str) -> str:
    @requires_api_key(provider_name=provider)
    def value(**fetched: str) -> str:
        return fetched["api_key"]

    return value()


def saver(database_url: str) -> PostgresSaver:
    """LangGraph's checkpoints in their own schema, so they sit apart from UnderCover's."""
    with ConnectionPool(database_url, min_size=1, max_size=1, kwargs={"autocommit": True}) as setup:
        with setup.connection() as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    pool = ConnectionPool(
        database_url,
        min_size=1,
        max_size=4,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={SCHEMA}",
        },
    )
    checkpoints = PostgresSaver(pool)  # type: ignore[arg-type]
    checkpoints.setup()
    return checkpoints


def compiled() -> Any:
    """Once per process, on the first request: the credentials, then the graph."""
    global _graph
    with _lock:
        if _graph is None:
            for name in list(os.environ):
                found = CREDENTIAL.fullmatch(name)
                if found:
                    os.environ[found[1]] = fetch(os.environ[name])
            _graph = graph.build(graph.chat_model(), saver(os.environ["DATABASE_URL"]), memory.notes())
    return _graph


def where(agent: Any, thread_id: str) -> dict[str, Any]:
    snapshot = agent.get_state({"configurable": {"thread_id": thread_id}})
    values = snapshot.values
    if not values:
        return {"thread_id": thread_id, "status": "unknown"}
    waiting = [i.value for task in snapshot.tasks for i in task.interrupts]
    return {
        "thread_id": thread_id,
        "status": values.get("status", "working"),
        "decision": values.get("decision"),
        "signed_by": values.get("signed_by"),
        "answer": values.get("answer"),
        "card": waiting[0] if waiting else None,
        "model_calls": values.get("model_calls", 0),
    }


def handle(agent: Any, payload: dict[str, Any]) -> dict[str, Any]:
    operation = payload.get("operation")
    if operation == "health":
        return {"ok": True}
    if operation == "request":
        thread_id = str(uuid.uuid4())
        try:
            request = graph.Request.model_validate(payload).model_dump()
        except ValidationError as exc:
            return {"error": str(exc)}
        config = {"configurable": {"thread_id": thread_id}}
        agent.invoke({"request": request | {"thread_id": thread_id}}, config)
        return where(agent, thread_id)
    if operation == "sign_off":
        thread_id = str(payload["thread_id"])
        if where(agent, thread_id)["card"] is None:
            return {"error": f"{thread_id} isn't waiting for a director."}
        person_id = str(payload.get("person_id", ""))
        signer = context.person(person_id)
        # Checked before resuming: a resume the graph refuses would stay in the checkpoint and be
        # replayed on the next sign-off. The graph checks again, in case anything skips this.
        if signer is None or signer["role"] != "director":
            return where(agent, thread_id) | {"refused": f"{person_id} isn't a sales director, so can't sign off."}
        answer = {"person_id": person_id, "answer": payload.get("answer", "")}
        agent.invoke(Command(resume=answer), {"configurable": {"thread_id": thread_id}})
        return where(agent, thread_id)
    if operation == "status":
        return where(agent, str(payload["thread_id"]))
    return {"error": f"Unknown operation {operation!r}."}


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    return handle(compiled(), payload)
