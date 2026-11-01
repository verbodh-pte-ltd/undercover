"""The rule that keeps or rolls back a new version, as in labs/week2/week2day5.ipynb."""

from typing import Any

RULE = (
    "A new version is kept when it meets the objectives, at least one measure"
    " improves by more than its noise and none gets worse by more than its tolerance."
    " Accuracy and the grade compare as differences, cost and time per claim as"
    " fractions of the old value. A version with no grade is rolled back."
)
DIFFERENCES = ["accuracy", "grade"]
FRACTIONS = ["cost_per_claim", "seconds_per_claim"]


def compare(
    new: dict[str, Any], old: dict[str, Any], noise: dict, tolerance: dict
) -> tuple[list[str], list[str]]:
    """The measures better by more than their noise, and worse by more than tolerance"""
    better, worse = [], []
    for name in DIFFERENCES:
        # An old version with no grade has nothing to compare the grade with.
        if old[name] is None:
            continue
        if new[name] > old[name] + noise[name]:
            better.append(name)
        if new[name] < old[name] - tolerance[name]:
            worse.append(name)
    for name in FRACTIONS:
        if new[name] < old[name] * (1 - noise[name]):
            better.append(name)
        if new[name] > old[name] * (1 + tolerance[name]):
            worse.append(name)
    return better, worse


def keep_or_roll_back(
    new: dict[str, Any], old: dict[str, Any], settings: dict[str, Any]
) -> tuple[bool, str]:
    """Keep a new version or roll it back, from its held-out scores and the old ones."""
    if new["grade"] is None:
        return False, "it has no grade"
    objectives = settings["objectives"].items()
    missed = [name for name, floor in objectives if new[name] < floor]
    if missed:
        return False, f"it missed the objectives on {', '.join(missed)}"
    better, worse = compare(new, old, settings["noise"], settings["tolerance"])
    if worse:
        return False, f"{', '.join(worse)} got worse"
    if not better:
        return False, "no measure improved by more than its noise"
    return True, f"{', '.join(better)} improved"
