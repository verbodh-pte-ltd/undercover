import pytest

from agent.tests.standin import serve
from context.server import build_server


@pytest.fixture(scope="session")
def context_layer():
    """The context layer, served on a free port for the session."""
    url, server = serve(build_server().streamable_http_app())
    yield f"{url}/mcp"
    server.should_exit = True


@pytest.fixture(autouse=True)
def direct_context(context_layer, monkeypatch):
    """Point the direct route at the test's context layer."""
    monkeypatch.setenv("UNDERCOVER_CONTEXT", context_layer)
    return context_layer
