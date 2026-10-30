import pytest

FROM_DOT_ENV = ["DATABASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test its own SQLite database, seeded from data/, and no tracing."""
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "undercover.db"))
    for name in FROM_DOT_ENV:
        monkeypatch.delenv(name, raising=False)
