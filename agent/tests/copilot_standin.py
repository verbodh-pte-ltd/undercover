"""A stand-in for the app's routes the Copilot calls, as the plan's contract gives them.

It checks the handler's sign-in on every request, refuses any request that carries the
agent's key, and keeps every request it gets, so tests can look at what was sent.
"""

from typing import Any

from fastapi import FastAPI, HTTPException, Request

DANA = "dana-sign-in-0123456789abcdefghijklmnopqrstuvwxyz"
MARCUS = "marcus-sign-in-0123456789abcdefghijklmnopqrstuvwxyz"
HANDLERS = {
    DANA: {
        "handler_id": "HND-01",
        "name": "Dana Whitfield",
        "role": "Senior Claims Handler",
        "email": "dana.whitfield@undercover.example",
        "can_approve_over_limit": True,
    },
    MARCUS: {
        "handler_id": "HND-02",
        "name": "Marcus Oyelaran",
        "role": "Claims Handler",
        "email": "marcus.oyelaran@undercover.example",
        "can_approve_over_limit": False,
    },
}
CONFIGURATION = {
    "model": "openai/gpt-6-luna",
    "reasoning_effort": "low",
    "instructions": "You are the UnderCover Copilot.",
}
SETTINGS = {
    "approval_limit": 1000.0,
    "objectives": {"accuracy": 0.8, "grade": 2.0},
    "agents_at_once": 4,
}


def claim(claim_id: str, status: str, payout: float) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "customer_id": "CUS-1001",
        "customer_name": "Ana Lopez",
        "policy_id": "POL-1001",
        "status": status,
        "decision": "approve",
        "payout": payout,
        "reason": "The flight was canceled for a crew problem. The costs are covered.",
        "run_id": None,
        "paid_amount": payout if status == "decided" else None,
        "sent_back": [],
    }


class StandIn:
    """The app's routes for the Copilot, with what each test needs to change."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.claims = {
            "CLM-1001": claim("CLM-1001", "waiting_for_sign_off", 2500.0),
            "CLM-1002": claim("CLM-1002", "decided", 300.0),
        }
        self.turns: dict[int, dict[str, Any]] = {}
        self.paid: list[str] = []
        self.sent_back: list[tuple[str, str]] = []
        self.started: list[tuple[str, dict[str, Any]]] = []
        self.refuse_start: str | None = None
        # Each read of a run takes the next status; the last one stays.
        self.run_statuses = ["finished"]
        self.stopped_reason: str | None = None
        self.measures = {"straight_through": 0.75, "accuracy": 0.85}
        self.counts = {"approval_limit": 1000.0, "paid_over_limit": 2, "signed_off": 2}
        self.app = FastAPI()
        self.routes()

    def turn(self, turn_id: int) -> None:
        """A turn the app has taken, working."""
        self.turns[turn_id] = {"state": "working", "steps": {}, "card": None}

    def answered(self, turn_id: int) -> None:
        """The handler answered the turn's confirmation, so it's working again."""
        self.turns[turn_id]["state"] = "working"

    def handler(self, request: Request) -> dict[str, Any]:
        """The signed-in handler; never a request with the agent's key."""
        if "x-api-key" in request.headers:
            raise HTTPException(401, "A request with the agent's key is the agent's")
        sent = request.headers.get("authorization", "").removeprefix("Bearer ")
        if sent not in HANDLERS:
            raise HTTPException(401, "Your sign-in has expired. Sign in again.")
        return HANDLERS[sent]

    def working(self, turn_id: int) -> dict[str, Any]:
        found = self.turns.get(turn_id)
        if found is None or found["state"] != "working":
            raise HTTPException(409, f"Turn {turn_id} isn't working")
        return found

    def run(self, run_id: int) -> dict[str, Any]:
        statuses = self.run_statuses
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        kind = "dispatch" if run_id == 7 else "evaluation"
        return {
            "run_id": run_id,
            "kind": kind,
            "n": 4,
            "status": status,
            "stopped_reason": self.stopped_reason,
            "summary": {"waiting_for_sign_off": [{"claim_id": "CLM-1001"}]},
            "claims": [],
            "measures": None if kind == "dispatch" else self.measures,
        }

    def routes(self) -> None:  # noqa: C901, PLR0915 (one route each, as the contract lists)
        app = self.app

        @app.middleware("http")
        async def keep(request: Request, call_next: Any) -> Any:
            body = await request.body()
            self.requests.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "headers": dict(request.headers),
                    "body": body.decode(),
                }
            )
            return await call_next(request)

        @app.get("/api/handlers/me")
        def me(request: Request) -> dict[str, Any]:
            return self.handler(request)

        @app.get("/api/copilot/configuration")
        def configuration(request: Request) -> dict[str, Any]:
            self.handler(request)
            return CONFIGURATION

        @app.get("/api/settings")
        def settings(request: Request) -> dict[str, Any]:
            self.handler(request)
            return {"settings": SETTINGS}

        @app.get("/api/claims")
        def claims(request: Request) -> list[dict[str, Any]]:
            self.handler(request)
            return list(self.claims.values())

        @app.get("/api/claims/{claim_id}")
        def get_claim(claim_id: str, request: Request) -> dict[str, Any]:
            self.handler(request)
            if claim_id not in self.claims:
                raise HTTPException(404, f"Claim {claim_id} not found")
            return self.claims[claim_id]

        @app.post("/api/claims/{claim_id}/sign-off")
        def sign_off(claim_id: str, request: Request) -> dict[str, Any]:
            handler = self.handler(request)
            if not handler["can_approve_over_limit"]:
                raise HTTPException(403, "can't sign off over the approval limit")
            found = self.claims[claim_id]
            if found["status"] != "waiting_for_sign_off":
                raise HTTPException(
                    409, f"Claim {claim_id} is not waiting for sign-off"
                )
            found.update(status="decided", paid_amount=found["payout"])
            self.paid.append(claim_id)
            return found

        @app.post("/api/claims/{claim_id}/send-back")
        async def send_back(claim_id: str, request: Request) -> dict[str, Any]:
            self.handler(request)
            note = (await request.json())["note"]
            self.sent_back.append((claim_id, note))
            self.claims[claim_id]["status"] = "sent_back"
            return self.claims[claim_id]

        @app.get("/api/customers")
        def customers(name: str, request: Request) -> list[dict[str, Any]]:
            self.handler(request)
            return [{"customer_id": "CUS-1001", "name": name, "email": "a@b.example"}]

        async def start(kind: str, run_id: int, request: Request) -> dict[str, Any]:
            self.handler(request)
            self.started.append((kind, await request.json()))
            if self.refuse_start:
                raise HTTPException(409, self.refuse_start)
            return {**self.run(run_id), "status": "running"}

        @app.post("/api/dispatches")
        async def dispatch(request: Request) -> dict[str, Any]:
            return await start("dispatch", 7, request)

        @app.post("/api/evaluations")
        async def evaluation(request: Request) -> dict[str, Any]:
            return await start("evaluation", 8, request)

        @app.get("/api/runs/{run_id}")
        def get_run(run_id: int, request: Request) -> dict[str, Any]:
            self.handler(request)
            if run_id not in {7, 8}:
                raise HTTPException(404, f"Run {run_id} not found")
            return self.run(run_id)

        @app.get("/api/evaluations/{run_id}")
        def get_evaluation(run_id: int, request: Request) -> dict[str, Any]:
            self.handler(request)
            # The handlers' decisions, which the Copilot must never fetch.
            labeled = [{"claim_id": "EVL-1", "handler": "approve", "handler_payout": 1}]
            return {"run_id": run_id, **self.measures, "claims": labeled}

        @app.get("/api/success-counts")
        def success_counts(request: Request) -> dict[str, Any]:
            self.handler(request)
            return self.counts

        @app.post("/api/copilot/turns/{turn_id}/steps")
        async def record_step(turn_id: int, request: Request) -> dict[str, Any]:
            self.handler(request)
            step = await request.json()
            self.working(turn_id)["steps"][step["key"]] = step
            return step

        @app.post("/api/copilot/turns/{turn_id}/pause")
        async def pause(turn_id: int, request: Request) -> dict[str, Any]:
            self.handler(request)
            turn = self.working(turn_id)
            found = self.claims[(await request.json())["claim_id"]]
            # Filled from the records, whatever the Copilot sends.
            turn["card"] = {
                "claim_id": found["claim_id"],
                "customer_name": found["customer_name"],
                "amount": found["payout"],
                "reason": found["reason"],
            }
            turn["state"] = "waiting_for_confirmation"
            return turn

        @app.post("/api/copilot/turns/{turn_id}/finish")
        async def finish(turn_id: int, request: Request) -> dict[str, Any]:
            self.handler(request)
            turn = self.working(turn_id)
            turn.update(await request.json(), state="done")
            return turn

        @app.post("/api/copilot/turns/{turn_id}/fail")
        async def fail(turn_id: int, request: Request) -> dict[str, Any]:
            self.handler(request)
            turn = self.working(turn_id)
            turn.update(await request.json(), state="failed")
            return turn

    def sent(self, path: str) -> list[dict[str, Any]]:
        """The requests to one path, in order."""
        return [r for r in self.requests if r["path"] == path]
