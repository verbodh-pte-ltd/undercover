"""Sign-ins for the tests: tokens signed with a test key, served by a stub JWKS."""

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

KEY = ec.generate_private_key(ec.SECP256R1())
JWKS = {"keys": [{**json.loads(ECAlgorithm.to_jwk(KEY.public_key())), "kid": "t"}]}
DANA = "dana.whitfield@undercover.example"
MARCUS = "marcus.oyelaran@undercover.example"
# The agent's API key in every test, and the header the agent's side sends it in.
API_KEY = "test-agent-key-of-at-least-32-characters"
AGENT = {"X-API-Key": API_KEY}


def token(
    email: str, expires_in: int = 3600, key: ec.EllipticCurvePrivateKey = KEY
) -> str:
    """An access token for an email, as Supabase Auth would sign it."""
    claims = {"email": email, "aud": "authenticated", "exp": time.time() + expires_in}
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "t"})


def headers(email: str = DANA) -> dict[str, str]:
    """A handler's sign-in, with no key: the blank key overrides a client's default
    key, since the app refuses a handler's request that carries the agent's key."""
    return {"Authorization": f"Bearer {token(email)}", "X-API-Key": ""}
