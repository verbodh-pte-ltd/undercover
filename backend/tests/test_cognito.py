import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from backend import auth
from backend.main import app
from backend.tests.signing import AGENT, DANA, headers

client = TestClient(app, headers=AGENT)

POOL = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Test"
CLIENT_ID = "test-app-client"
# Cognito signs with RS256; a stand-in key, served as its JWKS.
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWKS = {
    "keys": [{**json.loads(RSAAlgorithm.to_jwk(KEY.public_key())), "kid": "cognito"}]
}


def cognito_token(**changes: object) -> str:
    """A Cognito ID token for Dana, with any claim changed or removed (None)."""
    claims: dict[str, object] = {
        "sub": "dana-sub",
        "email": DANA,
        "aud": CLIENT_ID,
        "iss": POOL,
        "token_use": "id",
        "exp": time.time() + 3600,
    }
    claims.update(changes)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "cognito"})


@pytest.fixture
def cognito(monkeypatch):
    """The backend set up for Cognito, as the AUTH_ settings would."""
    # A client of its own, since a client caches the keys it fetched.
    jwks = jwt.PyJWKClient(f"{POOL}/.well-known/jwks.json")
    monkeypatch.setattr(jwks, "fetch_data", lambda: JWKS)
    monkeypatch.setattr(auth, "jwks", jwks)
    monkeypatch.setattr(auth, "ISSUER", POOL)
    monkeypatch.setattr(auth, "AUDIENCE", CLIENT_ID)


def me(token: str):
    return client.get("/api/handlers/me", headers={"Authorization": f"Bearer {token}"})


def test_without_auth_settings_it_uses_local_supabase():
    assert auth.settings({}) == (
        "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json",
        None,
        "authenticated",
    )


def test_the_auth_settings_point_it_at_cognito():
    env = {
        "VITE_SUPABASE_URL": "http://ignored",
        "AUTH_JWKS_URL": f"{POOL}/.well-known/jwks.json",
        "AUTH_ISSUER": POOL,
        "AUTH_AUDIENCE": CLIENT_ID,
    }
    assert auth.settings(env) == (f"{POOL}/.well-known/jwks.json", POOL, CLIENT_ID)


def test_a_supabase_sign_in_still_finds_the_handler_by_email():
    response = client.get("/api/handlers/me", headers=headers())
    assert response.json()["handler_id"] == "HND-01"


@pytest.mark.usefixtures("cognito")
def test_a_cognito_id_token_finds_the_handler_by_email():
    assert me(cognito_token()).json()["handler_id"] == "HND-01"


BAD_COGNITO_TOKENS = {
    # An access token has no aud, only client_id, and no email.
    "access token": {"aud": None, "client_id": CLIENT_ID, "token_use": "access"},
    "another app client": {"aud": "another-client"},
    "another user pool": {"iss": "https://cognito-idp.us-east-1.amazonaws.com/x"},
    "expired": {"exp": time.time() - 60},
}


@pytest.mark.usefixtures("cognito")
@pytest.mark.parametrize(
    "changes", BAD_COGNITO_TOKENS.values(), ids=list(BAD_COGNITO_TOKENS)
)
def test_cognito_refuses_other_tokens(changes):
    assert me(cognito_token(**changes)).status_code == 401


@pytest.mark.usefixtures("cognito")
def test_on_cognito_a_supabase_token_is_refused():
    assert client.get("/api/handlers/me", headers=headers()).status_code == 401
