"""A stand-in for the AgentCore Gateway, and a way to serve an app on a free port."""

import socket
import threading
import time
from typing import Any

import anyio
import uvicorn
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agent import direct
from agent.mcp_client import APP_TOOLS, CONTEXT_TOOLS, target
from backend.main import app
from backend.tests.signing import AGENT

DENIED = "Tool Execution Denied"
POLICY_LIMIT = 1000


def agents_client() -> Any:
    """The app in process, with the agent's key."""
    return TestClient(app, headers=AGENT)


def serve(asgi: Any) -> tuple[str, uvicorn.Server]:
    """Run an ASGI app on a free 127.0.0.1 port in a thread; its address and server."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(asgi, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]})
    thread.daemon = True
    thread.start()
    while not server.started:
        time.sleep(0.01)
    return f"http://127.0.0.1:{sock.getsockname()[1]}", server


class StandIn:
    """The Gateway's MCP endpoint: it names each tool `<target>___<tool>`, passes the
    app's to the app in process with the key, answers an app refusal with isError,
    and refuses a payment over $1,000, or any tool in `refuse`, as its Policy does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.headers: list[dict[str, str]] = []
        self.refuse: set[str] = set()
        self.context_url = ""
        self.app = Starlette(routes=[Route("/mcp", self.mcp, methods=["POST"])])

    def reset(self) -> None:
        self.calls.clear()
        self.headers.clear()
        self.refuse.clear()

    def denied(self, name: str, arguments: dict[str, Any]) -> bool:
        over = name.endswith("___pay_claim") and arguments["amount"] > POLICY_LIMIT
        return over or name in self.refuse

    def answer(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """What the target behind the Gateway answers."""
        target, _, tool = name.partition("___")
        if target == "undercover-app":
            route = direct.Direct(agents_client, "")
            found = route.use(tool, arguments)
            if found.refused_by:
                # The app's own answer, without the direct route's "Refused (409): ".
                return found.text.split(": ", 1)[1], True
            return found.text, False
        found = direct.Direct(agents_client, self.context_url).use(tool, arguments)
        return found.text.removeprefix("Refused: "), found.refused_by is not None

    async def mcp(self, request: Request) -> Response:
        self.headers.append(dict(request.headers))
        message = await request.json()
        if "id" not in message:
            return Response(status_code=202)
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        params = message.get("params", {})
        if message["method"] == "initialize":
            reply["result"] = {
                "protocolVersion": params["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stand-in gateway", "version": "1"},
            }
        elif message["method"] == "tools/list":
            # The MCP client lists the tools to check a call's result against them.
            names = sorted(f"{target(t)}___{t}" for t in APP_TOOLS | CONTEXT_TOOLS)
            schema = {"type": "object"}
            reply["result"] = {
                "tools": [{"name": n, "inputSchema": schema} for n in names]
            }
        elif message["method"] == "tools/call":
            name, arguments = params["name"], params["arguments"]
            self.calls.append((name, arguments))
            if self.denied(name, arguments):
                reply["error"] = {"code": -32002, "message": f"{DENIED} by policy"}
            else:
                text, error = await anyio.to_thread.run_sync(
                    self.answer, name, arguments
                )
                content = [{"type": "text", "text": text}]
                reply["result"] = {"content": content, "isError": error}
        else:
            reply["error"] = {"code": -32601, "message": "Method not found"}
        return JSONResponse(reply)
