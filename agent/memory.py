"""The claim agent's memory of each customer, kept in AgentCore Memory.

Each decided claim is saved as two events, with the customer as the actor and the
claim as the session. AgentCore turns them into memories in the background: facts
under /customers/{actorId}/facts/ and a summary of each claim under
/customers/{actorId}/claims/{sessionId}/.
"""

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.base import BaseStore
from langgraph_checkpoint_aws import AgentCoreMemoryStore

log = logging.getLogger(__name__)
# What "AgentCore can't be reached" means.
UNREACHABLE = (BotoCoreError, ClientError)
SEARCH_LIMIT = 5


def memory_store() -> BaseStore | None:
    """The memory in UNDERCOVER_MEMORY_ID, in the AWS CLI's region, or None."""
    memory_id = os.getenv("UNDERCOVER_MEMORY_ID")
    if not memory_id:
        return None
    region = boto3.Session().region_name
    return AgentCoreMemoryStore(memory_id=memory_id, region_name=region)


def the_claim(claim: dict[str, Any]) -> str:
    """The claim as the customer's message: never its story or its documents."""
    items = "; ".join(
        f"{i['description']} (${i['amount']:,.2f})" for i in claim["items"]
    )
    return (
        f"Claim {claim['claim_id']}: {claim['summary']}. Benefit claimed:"
        f" {claim['claim_type']}. Incident on {claim['incident_date']} in"
        f" {claim['incident_location']}. Items claimed: {items}."
    )


def the_decision(claim: dict[str, Any]) -> str:
    """The decision as the agent's message: decision, payout, section and reason."""
    return (
        f"Decision on {claim['claim_id']}: {claim['decision']}, payout"
        f" ${claim['payout']:,.2f}, section {claim['policy_section']}."
        f" {claim['reason']}"
    )


def save(store: BaseStore, claim: dict[str, Any]) -> list[str]:
    """Save a decided claim's two events; what was saved, or nothing if AgentCore
    can't be reached, which only logs a warning."""
    namespace = (claim["customer_id"], claim["claim_id"])
    events = [HumanMessage(the_claim(claim)), AIMessage(the_decision(claim))]
    try:
        for key, event in zip(["claim", "decision"], events, strict=True):
            store.put(namespace, key, {"message": event})
    except UNREACHABLE as exc:
        log.warning("Couldn't save %s to memory: %s", claim["claim_id"], exc)
        return []
    return [event.text for event in events]


def search(store: BaseStore, customer_id: str, claim_id: str, query: str) -> list[str]:
    """What we remember about one customer, without the summary of this claim."""
    found = store.search(("customers", customer_id), query=query, limit=SEARCH_LIMIT)
    return [
        str(item.value.get("content", item.value))
        for item in found
        if claim_id not in item.namespace
    ]
