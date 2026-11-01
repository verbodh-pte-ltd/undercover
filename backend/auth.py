"""Checks a handler's Supabase Auth sign-in against the project's public keys."""

import os

import jwt

# Read once, so the end-to-end tests can point it at their stand-in. The default is
# the local Supabase stack.
SUPABASE_URL = os.getenv("VITE_SUPABASE_URL", "http://127.0.0.1:54321")
jwks = jwt.PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")


def email(token: str) -> str:
    """The email address in a valid access token; raise jwt.PyJWTError if it isn't."""
    key = jwks.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token, key, algorithms=[key.algorithm_name], audience="authenticated"
    )
    return claims["email"]
