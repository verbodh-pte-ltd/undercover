"""The context layer: the discount policy, the customers, the products and the people.

The agent reaches it only through the three tools at the bottom. The graph's own checks
(the floor, the sign-off limit, who may sign) read the same files, so the rules the agent is
told and the rules the code enforces come from one place.
"""

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

FOLDER = Path(__file__).resolve().parent.parent / "context"
# The largest discount, in percent, the agent may approve without a director.
SIGN_OFF_ABOVE = 15


@cache
def _records(name: str, key: str) -> dict[str, dict[str, Any]]:
    rows = json.loads((FOLDER / f"{name}.json").read_text(encoding="utf-8"))
    return {row[key]: row for row in rows}


def customer(customer_id: str) -> dict[str, Any] | None:
    return _records("customers", "customer_id").get(customer_id.upper())


def product(sku: str) -> dict[str, Any] | None:
    return _records("products", "sku").get(sku.upper())


def person(person_id: str) -> dict[str, Any] | None:
    return _records("people", "person_id").get(person_id.upper())


@cache
def sections() -> dict[str, str]:
    """The policy's sections by heading, lower case."""
    text = (FOLDER / "pricing_policy.md").read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    return {p.split("\n", 1)[0].strip().lower(): p.split("\n", 1)[1].strip() for p in parts}


@tool
def get_customer(customer_id: str) -> str:
    """A customer by id: annual revenue, tier, whether they are on hold, and notes."""
    found = customer(customer_id)
    return json.dumps(found) if found else f"No customer {customer_id}."


@tool
def get_product(sku: str) -> str:
    """A product by SKU: its list price per seat and its floor, the largest discount allowed."""
    found = product(sku)
    return json.dumps(found) if found else f"No product {sku}."


@tool
def read_policy(section: str) -> str:
    """One section of the discount policy by its heading."""
    found = sections().get(section.strip().lower())
    return found or f"No section {section!r}. The sections are: {', '.join(sections())}."


TOOLS = [get_customer, get_product, read_policy]
