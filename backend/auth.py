"""Checks a handler's sign-in against Supabase Auth's or Cognito's public keys."""

import os
from collections.abc import Mapping

import jwt


def settings(env: Mapping[str, str]) -> tuple[str, str | None, str]:
    """The JWKS URL, issuer and audience: from AUTH_ if set, else local Supabase's."""
    supabase = env.get("VITE_SUPABASE_URL", "http://127.0.0.1:54321")
    return (
        env.get("AUTH_JWKS_URL", f"{supabase}/auth/v1/.well-known/jwks.json"),
        env.get("AUTH_ISSUER"),
        env.get("AUTH_AUDIENCE", "authenticated"),
    )


# Read once, so the end-to-end tests can point it at their stand-in. On Cognito the
# audience is the app client's id, which only its ID tokens carry, so its access
# tokens are refused.
JWKS_URL, ISSUER, AUDIENCE = settings(os.environ)
jwks = jwt.PyJWKClient(JWKS_URL)


def email(token: str) -> str:
    """The email address in a valid token; raise jwt.PyJWTError if it isn't."""
    key = jwks.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key,
        algorithms=[key.algorithm_name],
        audience=AUDIENCE,
        issuer=ISSUER,
    )
    return claims["email"]
