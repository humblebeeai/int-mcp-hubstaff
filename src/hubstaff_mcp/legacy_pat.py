"""Legacy ``X-MCP-API-Key`` PAT path, adapted onto the grant-id model.

Before the OAuth broker, each caller sent their Hubstaff Personal Access Token
(PAT) as ``X-MCP-API-Key`` and the PAT was used directly as the refresh token.
To keep that working during migration (``config.allow_legacy_pat``), we lazily
materialize a PAT into a synthetic *legacy grant* so the rest of the server can
uniformly deal in ``grant_id``.

The synthetic grant id is derived deterministically from the PAT (hashed) so the
same PAT always maps to the same grant row and its cached access token is reused
across requests. The reserved ``default`` key (env ``HUBSTAFF_REFRESH_TOKEN``)
is also handled here for the service-account / local-testing fallback.
"""
from .config import config
from .oauth import store
from .oauth.tokens import hash_token

_LEGACY_PREFIX = "legacy_"


async def ensure_legacy_grant(pat: str) -> str:
    """Return a grant id for a legacy PAT, creating the grant row if needed.

    The PAT is stored as the grant's Hubstaff refresh token with the
    ``is_legacy_pat`` flag set (so refreshes skip client auth).
    """
    grant_id = _LEGACY_PREFIX + hash_token(pat)
    existing = await store.get_grant(grant_id)
    if existing is None:
        await store.create_grant(
            grant_id,
            hubstaff_refresh_token=pat,
            is_legacy_pat=True,
        )
    elif existing.get("hubstaff_refresh_token") != pat:
        # Extremely unlikely hash collision or a rotated PAT reusing the row —
        # refresh the stored credential.
        await store.create_grant(
            grant_id,
            hubstaff_refresh_token=pat,
            is_legacy_pat=True,
        )
    return grant_id


async def resolve_default_grant() -> str | None:
    """Grant id for the reserved env-token service account, if configured."""
    if not config.hubstaff_token:
        return None
    return await ensure_legacy_grant(config.hubstaff_token)
