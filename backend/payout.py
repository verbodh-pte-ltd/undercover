"""UnderCover's payout rules for an approved claim.

The rules come from section 10 of the policy wording, the plan limits seeded from
context/plans.yaml into the plans table, and sections 6, 7 and 10 of the claims manual.
"""

from math import ceil, inf
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

CASH_LIMIT = 100.0
HOTEL_NIGHT_LIMIT = 350.0
MEALS_PER_TRAVELER_PER_DAY = 35.0

Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]

BENEFIT_MAXIMUM = {
    "trip_cancellation": "trip_cancellation_max",
    "trip_interruption": "trip_cancellation_max",
    "trip_delay": "trip_delay_max",
    "baggage_delay": "baggage_delay_max",
    "baggage": "baggage_max",
    "passport": "passport_max",
    "emergency_medical": "emergency_medical_max",
}


class Line(BaseModel):
    """One covered item or expense on a claim."""

    description: str = Field(description="What the item or expense is")
    amount: float = Field(
        description="The total claimed for the line in US dollars, before any limits."
        " For several items, the total for all of them"
    )
    quantity: int = Field(
        ge=1,
        description="How many separate items the line holds, such as 3 for three"
        " shirts. A pair or a set is one item. 1 for an expense",
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


class Step(TypedDict):
    """One line of the calculation."""

    step: str
    amount: float


class Calculation(TypedDict):
    """The payout on a claim and the calculation behind it."""

    calculation: list[Step]
    payout: float


class Payout(Calculation):
    """The calculation, and whether its payout needs a handler's sign-off."""

    needs_sign_off: bool


def step(description: str, amount: float) -> Step:
    """Make one line of the calculation, rounded to the cent."""
    return {"step": description, "amount": round(amount, 2)}


def electronics_plus_limit(plan: dict[str, Any]) -> float:
    """The per-item limit for electronics with the Electronics Plus add-on."""
    add_on = next(a for a in plan["add_ons"] if a["add_on"] == "electronics_plus")
    return add_on["electronics_per_item_limit"]


def limit_for(line: Line, policy: dict[str, Any], plan: dict[str, Any]) -> float:
    """Return the most paid for one line, or infinity when it has no limit."""
    travelers = len(policy["travelers"])
    match line.kind:
        case "item":
            return plan["per_item_limit"] * line.quantity
        case "electronics":
            per_item = (
                electronics_plus_limit(plan)
                if "electronics_plus" in policy["add_ons"]
                else plan["per_item_limit"]
            )
            return per_item * line.quantity
        case "hotel":
            return HOTEL_NIGHT_LIMIT * ceil(travelers / 2) * line.nights
        case "meals_without_receipts":
            return MEALS_PER_TRAVELER_PER_DAY * travelers * ceil(line.hours / 24)
        case _:
            return inf


def eligible_amount(line: Line, policy: dict[str, Any], plan: dict[str, Any]) -> Step:
    """Return the line's step: the amount claimed, or its limit if that is lower."""
    limit = limit_for(line, policy, plan)
    if line.amount > limit:
        return step(f"{line.description}, limited to ${limit:,.2f}", limit)
    return step(line.description, line.amount)


def benefit_maximum(
    policy: dict[str, Any], plan: dict[str, Any], benefit: Benefit
) -> float:
    """Return the plan's maximum for a benefit, capped at the trip cost if cancelled."""
    maximum = plan[BENEFIT_MAXIMUM[benefit]]
    if benefit in ("trip_cancellation", "trip_interruption"):
        return min(maximum, policy["trip"]["trip_cost"])
    return maximum


def work_out_payout(
    policy: dict[str, Any],
    plan: dict[str, Any],
    benefit: Benefit,
    lines: list[Line],
    paid_by_others: float,
) -> Calculation:
    """Work out the payout on an approved claim, following section 10 of the wording,
    with the policy's plan as the plans table holds it."""
    calculation = [
        eligible_amount(line, policy, plan) for line in lines if line.kind != "cash"
    ]
    cash = sum(line.amount for line in lines if line.kind == "cash")
    if cash:
        calculation.append(
            step(f"Cash, up to ${CASH_LIMIT:,.0f} in all", min(cash, CASH_LIMIT))
        )
    total = sum(entry["amount"] for entry in calculation)

    if paid_by_others:
        calculation.append(
            step("Less what others have paid or refunded", -paid_by_others)
        )
        total -= paid_by_others

    if benefit in plan["deductible_applies_to"] and total > 0:
        deductible = min(plan["deductible"], total)
        if deductible:
            calculation.append(step("Less the deductible", -deductible))
            total -= deductible

    maximum = benefit_maximum(policy, plan, benefit)
    if total > maximum:
        calculation.append(
            step(
                f"Less the amount over the maximum of ${maximum:,.0f}", maximum - total
            )
        )
        total = maximum

    if total < 0:
        calculation.append(step("Nothing is left to pay", -total))
        total = 0.0

    return {"calculation": calculation, "payout": round(total, 2)}
