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


def token(
    email: str, expires_in: int = 3600, key: ec.EllipticCurvePrivateKey = KEY
) -> str:
    """An access token for an email, as Supabase Auth would sign it."""
    claims = {"email": email, "aud": "authenticated", "exp": time.time() + expires_in}
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "t"})


def headers(email: str = DANA) -> dict[str, str]:
    """The Authorization header for a handler's sign-in."""
    return {"Authorization": f"Bearer {token(email)}"}
