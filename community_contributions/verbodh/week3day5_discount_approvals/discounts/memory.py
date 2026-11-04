"""AgentCore Memory with a namespace for each sales rep.

Each request is saved as an event under the rep's id. The Memory's user-preference strategy
turns the events into notes in /reps/{actorId}/preferences/, so one rep's notes are never read
for another. Without DISCOUNTS_MEMORY_ID the agent works with no memory.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Protocol

import boto3

log = logging.getLogger(__name__)
NAMESPACE = "/reps/{actorId}/preferences/"


class Notes(Protocol):
    def recall(self, rep_id: str, query: str) -> list[str]: ...

    def remember(self, rep_id: str, session_id: str, said: str, answered: str) -> None: ...


class NoMemory:
    def recall(self, rep_id: str, query: str) -> list[str]:
        return []

    def remember(self, rep_id: str, session_id: str, said: str, answered: str) -> None:
        return None


class AgentCoreNotes:
    def __init__(self, memory_id: str, region: str) -> None:
        self.memory_id = memory_id
        self.client = boto3.client("bedrock-agentcore", region_name=region)

    def recall(self, rep_id: str, query: str) -> list[str]:
        try:
            found = self.client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespace=NAMESPACE.replace("{actorId}", rep_id),
                searchCriteria={"searchQuery": query, "topK": 5},
            )
        except Exception as exc:  # A memory that can't be read never stops a decision
            log.warning("Couldn't read %s's notes: %s", rep_id, exc)
            return []
        return [r["content"]["text"] for r in found.get("memoryRecordSummaries", [])]

    def remember(self, rep_id: str, session_id: str, said: str, answered: str) -> None:
        try:
            self.client.create_event(
                memoryId=self.memory_id,
                actorId=rep_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(UTC),
                payload=[
                    {"conversational": {"content": {"text": said}, "role": "USER"}},
                    {"conversational": {"content": {"text": answered}, "role": "ASSISTANT"}},
                ],
            )
        except Exception as exc:
            log.warning("Couldn't save %s's request to memory: %s", rep_id, exc)


def notes() -> Notes:
    memory_id = os.getenv("DISCOUNTS_MEMORY_ID")
    if not memory_id:
        return NoMemory()
    return AgentCoreNotes(memory_id, os.getenv("AWS_REGION", "us-east-1"))
