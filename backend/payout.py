"""UnderCover's payout rules for an approved claim.

The rules come from section 10 of the policy wording, the plan limits in data/plans.json
and sections 6, 7 and 10 of the claims manual.
"""

from math import ceil, inf
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from backend import store

PLAN_DATA = store.load("plans.json")
PLANS = {plan["plan"]: plan for plan in PLAN_DATA["plans"]}
ADD_ONS = {add_on["add_on"]: add_on for add_on in PLAN_DATA["add_ons"]}
DEDUCTIBLE_BENEFITS = PLAN_DATA["deductible_applies_to"]

ELECTRONICS_PLUS_LIMIT = ADD_ONS["electronics_plus"]["electronics_per_item_limit"]
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


class Payout(TypedDict):
    """The payout on a claim and the calculation behind it."""

    calculation: list[Step]
    payout: float
    needs_sign_off: bool


def step(description: str, amount: float) -> Step:
    """Make one line of the calculation, rounded to the cent."""
    return {"step": description, "amount": round(amount, 2)}


def limit_for(line: Line, policy: dict[str, Any]) -> float:
    """Return the most paid for one line, or infinity when it has no limit."""
    plan = PLANS[policy["plan"]]
    travelers = len(policy["travelers"])
    match line.kind:
        case "item":
            return plan["per_item_limit"] * line.quantity
        case "electronics":
            per_item = (
                ELECTRONICS_PLUS_LIMIT
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


def eligible_amount(line: Line, policy: dict[str, Any]) -> Step:
    """Return the line's step: the amount claimed, or its limit if that is lower."""
    limit = limit_for(line, policy)
    if line.amount > limit:
        return step(f"{line.description}, limited to ${limit:,.2f}", limit)
    return step(line.description, line.amount)


def benefit_maximum(policy: dict[str, Any], benefit: Benefit) -> float:
    """Return the plan's maximum for a benefit, capped at the trip cost if cancelled."""
    maximum = PLANS[policy["plan"]][BENEFIT_MAXIMUM[benefit]]
    if benefit in ("trip_cancellation", "trip_interruption"):
        return min(maximum, policy["trip"]["trip_cost"])
    return maximum


def work_out_payout(
    policy: dict[str, Any], benefit: Benefit, lines: list[Line], paid_by_others: float
) -> Payout:
    """Work out the payout on an approved claim, following section 10 of the wording."""
    calculation = [
        eligible_amount(line, policy) for line in lines if line.kind != "cash"
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

    if benefit in DEDUCTIBLE_BENEFITS and total > 0:
        deductible = min(PLANS[policy["plan"]]["deductible"], total)
        if deductible:
            calculation.append(step("Less the deductible", -deductible))
            total -= deductible

    maximum = benefit_maximum(policy, benefit)
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

    payout = round(total, 2)
    limit = store.load("company.json")["payout_approval_limit"]
    return {
        "calculation": calculation,
        "payout": payout,
        "needs_sign_off": payout > limit,
    }
