"""Checking the sign-in on each request to the hosted server (renaissance.hosted).

The players sign in with an outside OAuth service (WorkOS AuthKit, Auth0, ...), which Claude's
connector settings talk to directly; this server only checks the access tokens it issues. They're
JWTs: signed with a key from the service's JWKS, from the expected issuer, not expired, and with a
subject (`sub`), which is who the player is. The JWKS address comes from the issuer's metadata
(/.well-known/oauth-authorization-server, else /.well-known/openid-configuration).

With an audience set, the token's `aud` must include it. Set it when the service puts this
server's address in `aud` (RFC 8707 resource indicators); without it, any token from the issuer
is accepted, so keep the service's tenant for rensim alone.
"""
from __future__ import annotations

import json
import urllib.request

import anyio
import jwt
from mcp.server.auth.provider import AccessToken

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


def discover_jwks_url(issuer: str) -> str:
    base = issuer.rstrip("/")
    for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        try:
            with urllib.request.urlopen(base + path, timeout=10) as r:
                meta = json.load(r)
        except OSError:
            continue
        if meta.get("jwks_uri"):
            return meta["jwks_uri"]
    raise RuntimeError(f"Couldn't find the JWKS for {issuer}: set RENSIM_AUTH_JWKS_URL")


class JWTVerifier:
    """The SDK's TokenVerifier for JWT access tokens: verify_token -> AccessToken, or None."""

    def __init__(self, issuer: str, audience: str | None = None, jwks_url: str | None = None,
                 jwks_client: jwt.PyJWKClient | None = None):
        self.issuer, self.audience = issuer, audience
        self._jwks_url, self._jwks = jwks_url, jwks_client

    def jwks(self) -> jwt.PyJWKClient:
        if self._jwks is None:   # looked up on first use, so the server starts even if the issuer is down
            self._jwks = jwt.PyJWKClient(self._jwks_url or discover_jwks_url(self.issuer), cache_keys=True)
        return self._jwks

    def decode(self, token: str) -> dict:
        key = self.jwks().get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=ALGORITHMS, issuer=self.issuer, audience=self.audience,
                          options={"require": ["exp", "iss", "sub"], "verify_aud": self.audience is not None},
                          leeway=30)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self.decode, token)
        except (jwt.PyJWTError, RuntimeError, OSError):
            return None
        scope = claims.get("scope") or claims.get("scp") or ""
        return AccessToken(token=token, client_id=str(claims.get("client_id") or claims.get("azp") or ""),
                           scopes=scope.split() if isinstance(scope, str) else list(scope),
                           expires_at=claims.get("exp"), subject=str(claims["sub"]), claims=claims)
