import pytest

from backend import auth, judge
from backend.tests import signing

FROM_DOT_ENV = [
    "DATABASE_URL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "UNDERCOVER_API",
    "UNDERCOVER_GATEWAY_URL",
    "UNDERCOVER_CONTEXT",
    "UNDERCOVER_MEMORY_ID",
    # The agent's runtime on AgentCore Runtime: never called from a test.
    "UNDERCOVER_AGENT_RUNTIME",
    "AWS_PROFILE",
    "AWS_SESSION_TOKEN",
]
# The agent's MCP calls run each on an event loop of their own, never asyncio.run,
# which would leave the main thread with no loop for Mangum's get_event_loop() in the
# Lambda tests (agent/tests/test_routes.py checks the handler after a Gateway call).
# Fake AWS credentials and a region, and no AWS files, so no test reads ~/.aws or
# reaches AWS: the Gateway and the memory are stand-ins.
FAKE_AWS = {
    "AWS_ACCESS_KEY_ID": "AKIDTESTONLY",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test its own SQLite database, seeded from data/, and no tracing."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "undercover.db"))
    # Nothing listens on port 9, so a sign-off never reaches a real agent.
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://127.0.0.1:9")
    monkeypatch.setenv("UNDERCOVER_API_KEY", signing.API_KEY)
    for name in FROM_DOT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    for name, value in FAKE_AWS.items():
        monkeypatch.setenv(name, value)
    for name in ["AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"]:
        monkeypatch.setenv(name, str(tmp_path / "no-aws-file"))


@pytest.fixture(autouse=True)
def no_judge(monkeypatch):
    """Never call the judge: every claim is left not graded unless a test says."""
    monkeypatch.setattr(judge, "grade", lambda handler, agent: None)


@pytest.fixture(autouse=True)
def stub_jwks(monkeypatch):
    """Serve the test key's JWKS in place of Supabase Auth's."""
    monkeypatch.setattr(auth.jwks, "fetch_data", lambda: signing.JWKS)
