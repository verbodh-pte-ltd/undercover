import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mangum.types import LambdaCognitoIdentity, LambdaMobileClientContext
from sqlalchemy import NullPool, QueuePool, event, inspect
from starlette.routing import Mount

from backend import main, store
from backend.main import app
from backend.tests.signing import AGENT, headers

INDEX = "<!doctype html><title>UnderCover</title>"
SCRIPT = "console.log('dashboard');"
BACKEND = Path(main.__file__).parent
ROOT = BACKEND.parent
DECLINE = {
    "decision": "decline",
    "payout": 0,
    "reason": "Not covered.",
    "policy_section": "9. Exclusions",
}


@pytest.fixture
def built(tmp_path):
    """A stand-in dashboard build served by the app, in place of any real one."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "assets" / "app.js").write_text(SCRIPT)
    routes = app.router.routes
    real = [r for r in routes if isinstance(r, Mount) and r.name == "dashboard"]
    for mount in real:
        routes.remove(mount)
    assert main.mount_dashboard(app, tmp_path)
    yield TestClient(app, headers=AGENT)
    routes[:] = [r for r in routes if not isinstance(r, Mount)] + real


def test_pages_get_index_html_and_files_get_themselves(built):
    for page in ["/", "/claims", "/claims/CLM-1001", "/assets/missing.js"]:
        response = built.get(page)
        assert response.status_code == 200
        assert response.text == INDEX
        assert response.headers["content-type"].startswith("text/html")
    script = built.get("/assets/app.js")
    assert script.text == SCRIPT
    assert "javascript" in script.headers["content-type"]


def test_api_routes_work_as_before_with_the_dashboard_served(built):
    assert built.get("/api/health").json() == {"status": "ok"}
    assert built.get("/api/claims/CLM-1001").json()["claim_id"] == "CLM-1001"
    assert built.get("/api/claims", headers=headers()).status_code == 200
    decided = built.post("/api/claims/CLM-1001/decision", json=DECLINE)
    assert decided.status_code == 200
    assert TestClient(app).get("/api/claims/CLM-1001").status_code == 401


@pytest.mark.parametrize("path", ["/api/nothing-here", "/api/claims/x/y/z", "/api"])
def test_an_unknown_api_path_gets_a_json_404(built, path):
    response = built.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_nothing_is_mounted_without_a_build(tmp_path):
    bare = FastAPI()
    assert not main.mount_dashboard(bare, tmp_path / "dist")
    assert not [r for r in bare.routes if isinstance(r, Mount)]
    assert TestClient(bare).get("/").status_code == 404


def test_the_dashboard_build_is_dashboard_dist():
    assert main.DASHBOARD == ROOT / "dashboard" / "dist"


@dataclass
class Context:
    """The context Lambda gives the function with each event."""

    function_name: str = "undercover-app"
    function_version: str = "$LATEST"
    invoked_function_arn: str = "arn:aws:lambda:eu-west-2:0:function:undercover-app"
    memory_limit_in_mb: int = 1024
    aws_request_id: str = "request-1"
    log_group_name: str = "/aws/lambda/undercover-app"
    log_stream_name: str = "stream"
    identity: LambdaCognitoIdentity | None = None
    client_context: LambdaMobileClientContext | None = None

    def get_remaining_time_in_millis(self) -> int:
        """The time left before Lambda stops the function."""
        return 30_000


def function_url_event(path: str) -> dict:
    """A Lambda Function URL's event for a GET, in the payload format 2.0."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "abc.lambda-url.eu-west-2.on.aws", "accept": "*/*"},
        "requestContext": {
            "accountId": "anonymous",
            "apiId": "abc",
            "domainName": "abc.lambda-url.eu-west-2.on.aws",
            "domainPrefix": "abc",
            "http": {
                "method": "GET",
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
                "userAgent": "test",
            },
            "requestId": "request-1",
            "routeKey": "$default",
            "stage": "$default",
            "time": "02/Nov/2026:10:00:00 +0000",
            "timeEpoch": 1793613600000,
        },
        "isBase64Encoded": False,
    }


def test_the_lambda_handler_answers_a_function_url_event_for_health():
    assert main.handler.app is app
    assert main.handler.lifespan == "off"
    response = main.handler(function_url_event("/api/health"), Context())
    assert response["statusCode"] == 200
    assert response["body"] == '{"status":"ok"}'


@pytest.mark.usefixtures("built")
@pytest.mark.parametrize("page", ["/", "/claims"])
def test_the_lambda_handler_serves_the_dashboard(page):
    response = main.handler(function_url_event(page), Context())
    assert response["statusCode"] == 200
    assert response["body"] == INDEX


def engine_arguments(monkeypatch) -> dict:
    """What the store passes to create_engine for a Postgres address."""
    seen = {}

    def create_engine(url, **kwargs):
        seen.update(kwargs, url=url)

    monkeypatch.setattr(store, "create_engine", create_engine)
    store.postgres_engine("postgresql+psycopg://u:p@127.0.0.1:6543/postgres")
    return seen


def test_on_lambda_postgres_keeps_no_connections_and_prepares_nothing(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "undercover-app")
    seen = engine_arguments(monkeypatch)
    assert seen["poolclass"] is NullPool
    assert "pool_size" not in seen
    assert seen["connect_args"] == {
        "options": "-c search_path=undercover",
        "prepare_threshold": None,
    }


def test_off_lambda_postgres_keeps_its_pool_of_four(monkeypatch):
    seen = engine_arguments(monkeypatch)
    assert (seen["pool_size"], seen["max_overflow"]) == (store.POOL_SIZE, 0) == (4, 0)
    assert "prepare_threshold" not in seen["connect_args"]


def connections_over_two_requests() -> int:
    """How many connections the store opens for two requests."""
    store.engines.clear()
    opened = []
    event.listen(store.engine(), "connect", lambda *_: opened.append(1))
    client = TestClient(app, headers=AGENT)
    for _ in range(2):
        assert client.get("/api/claims/CLM-1001").status_code == 200
    return len(opened)


def test_on_lambda_each_request_opens_its_own_connection(monkeypatch):
    store.connect().close()  # seeds the database, as `npm run seed` would
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "undercover-app")
    assert connections_over_two_requests() == 2
    assert isinstance(store.engine().pool, NullPool)


def test_off_lambda_the_requests_reuse_the_pooled_connection():
    # The seeding opened it, before the count started.
    assert connections_over_two_requests() == 0
    assert isinstance(store.engine().pool, QueuePool)


def test_on_lambda_the_store_makes_no_tables_and_seeds_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("UNDERCOVER_DB", str(tmp_path / "empty.db"))
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "undercover-app")
    with store.engine().connect() as conn:
        assert inspect(conn).get_table_names() == []


def source_files() -> list[Path]:
    """The backend's own modules, without its tests."""
    return sorted(BACKEND.glob("*.py"))


def test_the_backend_imports_nothing_from_the_agent():
    imported = set()
    for path in source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    outside = imported - set(sys.stdlib_module_names) - {"backend"}
    assert "agent" not in imported
    assert outside == {
        "fastapi",
        "httpx",
        "jwt",
        "mangum",
        "openrouter",
        "pydantic",
        "sqlalchemy",
        "starlette",
        "yaml",
    }


def functions_naming(name: str) -> set[str]:
    """The backend's functions whose code names a module-level name."""
    found = set()
    for path in source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(n, ast.Name | ast.Attribute)
                and (getattr(n, "id", None) == name or getattr(n, "attr", None) == name)
                for n in ast.walk(node)
            ):
                found.add(node.name)
    return found


def test_the_backend_reads_context_only_while_seeding():
    assert store.CONTEXT == ROOT / "context"
    assert functions_naming("CONTEXT") == {"seed_plans", "seed_documents"}
    assert functions_naming("seed_plans") == {"seed"}
    assert functions_naming("seed_documents") == {"seed"}


def test_the_backend_reads_files_only_from_backend_data_and_the_build():
    allowed = [BACKEND, ROOT / "data", ROOT / "dashboard" / "dist"]
    folders = [store.DATA, store.INSTRUCTIONS, store.DEFAULT_DB, main.DASHBOARD]
    for folder in folders:
        assert any(folder.resolve().is_relative_to(a.resolve()) for a in allowed)
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        assert "write_text" not in text
        assert "write_bytes" not in text
        assert "mkdir" not in text
        assert "open(" not in text.replace("_open(", "")


def test_the_backend_reads_only_these_environment_variables():
    found = set()
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r"getenv\(\s*['\"](\w+)['\"]", text))
        found |= set(re.findall(r"environ\[['\"](\w+)['\"]\]", text))
    # The three a Lambda function sets, Lambda's own name, and the rest, each with a
    # default or needed only by work that can't start on Lambda (the agent's service
    # and the judge, which an evaluation needs).
    assert found == {
        "DATABASE_URL",
        "VITE_SUPABASE_URL",
        "UNDERCOVER_API_KEY",
        "AWS_LAMBDA_FUNCTION_NAME",
        "UNDERCOVER_DB",
        "UNDERCOVER_AGENT",
        "MODEL_NAME",
        "JUDGE_URL",
        "OPENROUTER_API_KEY",
    }
