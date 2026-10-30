"""Work named claims with no run: uv run -m agent CLAIM_ID [CLAIM_ID ...]."""

import sys

from dotenv import load_dotenv

from agent.main import work

USAGE = (
    "Usage: uv run -m agent CLAIM_ID [CLAIM_ID ...]."
    " Start dispatches from the dashboard."
)


def main(args: list[str]) -> None:
    """Work each named claim through the harness, outside any dispatch."""
    if not args:
        print(USAGE)
        return
    for claim_id in args:
        print(f"{claim_id}: {work(claim_id, None)}")


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
