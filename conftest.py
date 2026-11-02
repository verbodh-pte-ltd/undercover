import pytest

from backend import auth, judge
from backend.tests import signing

FROM_DOT_ENV = ["DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test its own SQLite database, seeded from data/, and no tracing."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "undercover.db"))
    # Nothing listens on port 9, so a sign-off never reaches a real agent service.
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://127.0.0.1:9")
    monkeypatch.setenv("UNDERCOVER_API_KEY", signing.API_KEY)
    for name in FROM_DOT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)


@pytest.fixture(autouse=True)
def no_judge(monkeypatch):
    """Never call the judge: every claim is left not graded unless a test says."""
    monkeypatch.setattr(judge, "grade", lambda handler, agent: None)


@pytest.fixture(autouse=True)
def stub_jwks(monkeypatch):
    """Serve the test key's JWKS in place of Supabase Auth's."""
    monkeypatch.setattr(auth.jwks, "fetch_data", lambda: signing.JWKS)
