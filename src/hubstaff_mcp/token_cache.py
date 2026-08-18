"""Access-token resolution for Hubstaff, keyed by internal grant id.

Each caller is represented by an internal ``grant_id`` that maps (in the OAuth
store) to that user's Hubstaff refresh/access token pair. This module mints and
refreshes the short-lived Hubstaff *access* token from the stored refresh token.

The refresh happens against the Hubstaff token endpoint using HTTP Basic client
credentials (our one upstream OAuth app). Hubstaff rotates the refresh token on
every grant, so we persist the rotated ``refresh_token`` back into the grant row
— dropping it would brick the grant.

A per-``grant_id`` asyncio lock serializes refreshes so concurrent requests for
the same grant don't race (Hubstaff rejects overlapping refresh_token grants
with a 400).
"""
import asyncio
import time

import httpx

from .config import config
from .oauth import store

# Refresh a little before the real expiry to absorb clock skew / latency.
_EXPIRY_SKEW_SECONDS = 60

# Per-grant locks serialize token refresh so concurrent requests for the same
# grant don't race (Hubstaff rejects overlapping refresh_token grants with 400).
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(grant_id: str) -> asyncio.Lock:
    if grant_id not in _locks:
        _locks[grant_id] = asyncio.Lock()
    return _locks[grant_id]


class NeedsReauth(Exception):
    """Raised when a grant's refresh token is invalid/expired.

    Surfaced up to the transport layer as a 401 so the client restarts the
    browser OAuth flow (or the user re-issues a PAT).
    """


async def refresh_with_refresh_token(refresh_token: str) -> dict:
    """Exchange a Hubstaff refresh token for a fresh access token.

    Authenticates as our upstream OAuth app (HTTP Basic client credentials).
    Returns the raw Hubstaff token response (contains ``access_token``,
    ``refresh_token``, ``expires_in``). Raises :class:`NeedsReauth` on
    invalid_grant, or ``httpx.HTTPError`` on transport failure.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{config.hubstaff_account_base_url}/access_tokens",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=httpx.BasicAuth(
                config.hubstaff_client_id, config.hubstaff_client_secret
            ),
        )

    if response.status_code == 200:
        return response.json()
    if response.status_code in (400, 401, 403):
        # invalid_grant / expired / revoked refresh token — unrecoverable.
        raise NeedsReauth(
            f"Hubstaff refused the refresh token (HTTP {response.status_code})"
        )
    response.raise_for_status()
    raise NeedsReauth(f"Unexpected token endpoint status {response.status_code}")


async def get_access_token(grant_id: str) -> str:
    """Return a valid Hubstaff access token for ``grant_id``, refreshing if stale.

    A per-grant lock serializes concurrent refreshes for the same grant.
    """
    async with _get_lock(grant_id):
        grant = await store.get_grant(grant_id)
        if grant is None:
            raise NeedsReauth(f"Unknown grant: {grant_id[:8]}...")
        if grant.get("needs_reauth"):
            raise NeedsReauth(f"Grant needs re-authorization: {grant_id[:8]}...")

        access_token = grant.get("hubstaff_access_token")
        expires_at = grant.get("hubstaff_access_expires_at") or 0
        now = time.time()

        if access_token and now < (expires_at - _EXPIRY_SKEW_SECONDS):
            return access_token

        refresh_token = grant.get("hubstaff_refresh_token")
        if not refresh_token:
            await store.mark_needs_reauth(grant_id)
            raise NeedsReauth(f"Grant has no refresh token: {grant_id[:8]}...")

        print(f"Refreshing Hubstaff access token for grant {grant_id[:8]}...")
        try:
            token_data = await refresh_with_refresh_token(refresh_token)
        except NeedsReauth:
            await store.mark_needs_reauth(grant_id)
            raise
        except httpx.HTTPError as e:
            # Transient transport error — don't brick the grant, just fail this call.
            raise Exception(f"Error refreshing Hubstaff token: {e}")

        new_access = token_data["access_token"]
        # Hubstaff rotates the refresh token; persist it or the grant bricks.
        new_refresh = token_data.get("refresh_token", refresh_token)
        expires_in = token_data.get("expires_in", 24 * 3600)
        new_expires_at = time.time() + float(expires_in)

        await store.update_grant_tokens(
            grant_id,
            access_token=new_access,
            access_expires_at=new_expires_at,
            refresh_token=new_refresh,
        )
        print(f"Hubstaff token refreshed for grant {grant_id[:8]}...")
        return new_access
