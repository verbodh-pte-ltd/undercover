"""Serve the context layer on this machine: uv run -m context."""

import uvicorn

from context.server import build_server

HOST = "127.0.0.1"
PORT = 8006

if __name__ == "__main__":
    uvicorn.run(build_server().streamable_http_app(), host=HOST, port=PORT)
