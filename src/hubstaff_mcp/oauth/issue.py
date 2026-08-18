"""Shared helpers for issuing OAuth artifacts and building redirects.

Used by the authorize/callback/consent/token endpoints so code-minting,
token-minting, redirect construction and redirect-URI validation live in one
place.
"""
from urllib.parse import urlencode, urlparse

from ..config import config
from . import store
from .tokens import hash_token, new_id, new_token


def is_allowed_redirect_uri(uri: str) -> bool:
    """Allow only HTTPS or loopback HTTP redirect URIs (OAuth 2.1 / MCP).

    Prevents open-redirect abuse and matches the spec requirement that redirect
    URIs be ``localhost`` or HTTPS.
    """
    try:
        p = urlparse(uri)
    except Exception:
        return False
    if p.scheme == "https":
        return bool(p.netloc)
    if p.scheme == "http":
        host = (p.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1")
    return False


def build_redirect(redirect_uri: str, params: dict) -> str:
    """Append query params to a redirect URI (preserving any existing query)."""
    sep = "&" if urlparse(redirect_uri).query else "?"
    return f"{redirect_uri}{sep}{urlencode(params)}"


async def mint_auth_code(
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    resource: str,
    grant_id: str,
    scope: str,
) -> str:
    """Create a single-use authorization code and return its plaintext value."""
    code = new_token()
    await store.create_auth_code(
        code_hash=hash_token(code),
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        resource=resource,
        grant_id=grant_id,
        scope=scope,
        ttl_seconds=config.auth_code_ttl_seconds,
    )
    return code


async def issue_token_pair(
    *,
    grant_id: str,
    client_id: str,
    resource: str,
    scope: str,
    refresh_family_id: str | None = None,
    rotated_from: str | None = None,
) -> dict:
    """Issue an access token (+ rotating refresh token) and persist both.

    Returns the RFC 6749 token response body.
    """
    access = new_token()
    refresh = new_token()
    family = refresh_family_id or new_id("fam_")

    await store.create_access_token(
        token_hash=hash_token(access),
        grant_id=grant_id,
        client_id=client_id,
        resource=resource,
        scope=scope,
        ttl_seconds=config.access_token_ttl_seconds,
    )
    await store.create_refresh_token(
        token_hash=hash_token(refresh),
        grant_id=grant_id,
        client_id=client_id,
        refresh_family_id=family,
        ttl_seconds=config.refresh_token_ttl_seconds,
        rotated_from=rotated_from,
    )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": config.access_token_ttl_seconds,
        "refresh_token": refresh,
        "scope": scope,
    }
