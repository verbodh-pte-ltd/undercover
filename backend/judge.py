"""The judge: Jev grades the claim agent's explanation against the handler's reason."""

import os
from typing import Any

import httpx
from openrouter import OpenRouter, errors, utils
from openrouter.components import DecisionsNoulAnswer, DecisionsScoreAnswer

JEV = "typesafe/jev-1.13"

# Jev numbers the grades by their position in the list, so F is 0 and A is 3
RUBRIC = {
    "F": "different grounds from the handler's reason, or it contradicts the"
    " handler's reason",
    "C": "partly the same as the handler's reason: one of the handler's grounds or"
    " the section is missing, or it adds a ground the handler did not give",
    "B": "the same grounds and the same section of the policy wording as the"
    " handler's reason",
    "A": "the same grounds and section as the handler's reason, and clearly better"
    " expressed: clearer for the customer, with the section and the figures,"
    " shorter, and no jargon",
}
GRADES = list(RUBRIC)

JUDGE_QUESTIONS: dict[str, Any] = {
    "grade": {
        "type": "score",
        "instructions": "An expert claims handler decided a travel insurance claim"
        " and gave `handler.reason`, resting on `handler.reference`. Grade how well"
        " `agent.rationale` explains the same decision.",
        "criteria": [f"{letter}: {text}" for letter, text in RUBRIC.items()],
    },
    "same_grounds": {
        "type": "noul",
        "instructions": "`agent.rationale` gives the same main grounds for the"
        " decision as `handler.reason`",
    },
    "same_section": {
        "type": "noul",
        "instructions": "`agent.rationale` refers to the same section of the policy"
        " wording as `handler.reference`",
    },
}

# The client ignores an address set on it, so each call passes this one. Read once,
# so the end-to-end tests can point it at their stand-in.
SERVER_URL = os.getenv("JUDGE_URL", "https://openrouter.ai")
TIMEOUT_MS = 10_000
NO_RETRIES = utils.RetryConfig("none", utils.BackoffStrategy(0, 0, 0, 0), False)

client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY"))


def grade(handler: dict[str, str], agent: dict[str, str]) -> dict[str, Any] | None:
    """Ask Jev to grade the agent's rationale against the handler's, or None."""
    try:
        response = client.alpha.decisions.create(
            model=JEV,
            state={"handler": handler, "agent": agent},
            questions=JUDGE_QUESTIONS,
            server_url=SERVER_URL,
            timeout_ms=TIMEOUT_MS,
            retries=NO_RETRIES,
        )
    except (errors.OpenRouterError, httpx.HTTPError):
        return None
    level = response.answers.get("grade")
    grounds = response.answers.get("same_grounds")
    section = response.answers.get("same_section")
    if not (
        isinstance(level, DecisionsScoreAnswer)
        and level.probabilities
        and isinstance(grounds, DecisionsNoulAnswer)
        and isinstance(section, DecisionsNoulAnswer)
    ):
        return None
    probabilities = level.probabilities
    # The letter is the most likely level, never the score rounded.
    most_likely = max(probabilities, key=lambda k: probabilities[k])
    if most_likely not in {str(i) for i in range(len(GRADES))}:
        return None
    return {
        "grade": level.score,
        "letter": GRADES[int(most_likely)],
        "same_grounds": grounds.noul,
        "same_section": section.noul,
        "judge_cost": response.usage.cost or 0.0,
    }
