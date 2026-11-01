import pytest

from backend import auth
from backend.tests import signing

FROM_DOT_ENV = ["DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test its own SQLite database, seeded from data/, and no tracing."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "undercover.db"))
    # Nothing listens on port 9, so a sign-off never reaches a real agent service.
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://127.0.0.1:9")
    for name in FROM_DOT_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def stub_jwks(monkeypatch):
    """Serve the test key's JWKS in place of Supabase Auth's."""
    monkeypatch.setattr(auth.jwks, "fetch_data", lambda: signing.JWKS)
