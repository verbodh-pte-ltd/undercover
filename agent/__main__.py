"""Work the claims queue: uv run -m agent [--cap N | CLAIM_ID ...]."""

import sys

from dotenv import load_dotenv

from agent.main import backend, run_queue, work_claim


def main(args: list[str]) -> None:
    """Run the queue agent up to a cap (default: all waiting), or the named claims."""
    if args and args[0] != "--cap":
        with backend() as client:
            run = client.post("/api/runs", json={"cap": len(args)}).json()
        for claim_id in args:
            print(f"{claim_id}: {work_claim(claim_id, run['run_id'])}")
        return
    if args:
        cap = int(args[1])
    else:
        with backend() as client:
            cap = len(client.get("/api/claims/waiting").json())
    print(run_queue(cap) if cap else "No claims are waiting.")


if __name__ == "__main__":
    load_dotenv(override=True)
    main(sys.argv[1:])
