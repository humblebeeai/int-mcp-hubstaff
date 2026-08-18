"""Resource-server Bearer token validation.

Validates the ``Authorization: Bearer <token>`` presented to ``/mcp`` against
tokens WE issued (opaque, looked up by SHA-256 hash), enforcing expiry and
audience (RFC 8707 ``resource``). Returns the internal ``grant_id`` the token
maps to. We only ever accept tokens minted by our own authorization server —
never a raw Hubstaff token (that would be token passthrough / confused deputy).
"""
import time
from typing import Optional

from ..config import config
from . import store
from .tokens import hash_token


def build_www_authenticate() -> str:
    """Value for the ``WWW-Authenticate`` header on a 401 from ``/mcp``.

    Points clients at our Protected Resource Metadata document (RFC 9728) so
    they can discover the authorization server and begin the OAuth flow.
    """
    prm_url = f"{config.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource"
    return f'Bearer resource_metadata="{prm_url}"'


def extract_bearer(authorization_header: Optional[str]) -> Optional[str]:
    """Pull the token out of an ``Authorization: Bearer <token>`` header."""
    if not authorization_header:
        return None
    parts = authorization_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


async def resolve_bearer(token: str) -> Optional[str]:
    """Return the ``grant_id`` for a valid access token, or ``None``.

    Rejects unknown, expired, or wrong-audience tokens.
    """
    row = await store.get_access_token_row(hash_token(token))
    if row is None:
        return None
    if row["expires_at"] < time.time():
        return None
    # Audience binding: the token must have been issued for THIS resource.
    if row.get("resource") and row["resource"] != config.resource_uri:
        return None
    return row["grant_id"]
