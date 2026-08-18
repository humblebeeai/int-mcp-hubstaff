"""Token endpoint (downstream leg) + token revocation.

``/token`` handles the ``authorization_code`` grant (verifying downstream PKCE)
and the ``refresh_token`` grant (with rotation + reuse detection). It issues
*our* opaque tokens, which map to an internal grant; it never returns Hubstaff
tokens to the client.
"""
import time

from starlette.responses import JSONResponse

from ..config import config
from . import store
from .issue import issue_token_pair
from .tokens import hash_token, verify_pkce_s256


def _error(code: str, desc: str = "", status: int = 400):
    body = {"error": code}
    if desc:
        body["error_description"] = desc
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


async def _authorization_code_grant(form):
    code = form.get("code")
    client_id = form.get("client_id")
    redirect_uri = form.get("redirect_uri")
    code_verifier = form.get("code_verifier")
    resource = form.get("resource")

    if not code or not client_id or not code_verifier:
        return _error("invalid_request", "code, client_id, code_verifier required")

    row = await store.take_auth_code(hash_token(code))
    if row is None:
        return _error("invalid_grant", "authorization code invalid, used, or expired")

    if row["client_id"] != client_id:
        return _error("invalid_grant", "client_id mismatch")
    if row["redirect_uri"] != redirect_uri:
        return _error("invalid_grant", "redirect_uri mismatch")
    if not verify_pkce_s256(code_verifier, row["code_challenge"]):
        return _error("invalid_grant", "PKCE verification failed")
    if resource and resource.rstrip("/") != (row["resource"] or "").rstrip("/"):
        return _error("invalid_target", "resource mismatch")

    body = await issue_token_pair(
        grant_id=row["grant_id"],
        client_id=client_id,
        resource=row["resource"] or config.resource_uri,
        scope=row["scope"] or config.hubstaff_scopes,
    )
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


async def _refresh_token_grant(form):
    refresh_token = form.get("refresh_token")
    client_id = form.get("client_id")
    if not refresh_token:
        return _error("invalid_request", "refresh_token required")

    row = await store.get_refresh_token_row(hash_token(refresh_token))
    if row is None:
        return _error("invalid_grant", "unknown refresh token")

    # Reuse detection: a revoked (already-rotated) token being presented again
    # means the family may be compromised — revoke the whole family.
    if row["revoked"]:
        await store.revoke_refresh_family(row["refresh_family_id"])
        return _error("invalid_grant", "refresh token reuse detected; re-authorize")
    if row["expires_at"] < time.time():
        return _error("invalid_grant", "refresh token expired")
    if client_id and row["client_id"] != client_id:
        return _error("invalid_grant", "client_id mismatch")

    # Rotate: revoke the presented token, issue a new pair in the same family.
    await store.mark_refresh_used(row["token_hash"])
    body = await issue_token_pair(
        grant_id=row["grant_id"],
        client_id=row["client_id"],
        resource=config.resource_uri,
        scope=config.hubstaff_scopes,
        refresh_family_id=row["refresh_family_id"],
        rotated_from=row["token_hash"],
    )
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


async def token(request):
    await store.init()
    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        return await _authorization_code_grant(form)
    if grant_type == "refresh_token":
        return await _refresh_token_grant(form)
    return _error("unsupported_grant_type", f"grant_type={grant_type!r}")


async def revoke(request):
    """RFC 7009 token revocation. Always returns 200 per the spec."""
    await store.init()
    form = await request.form()
    tok = form.get("token")
    if tok:
        h = hash_token(tok)
        # Try both token types; unknown tokens are a no-op.
        await store.delete_access_token(h)
        rt = await store.get_refresh_token_row(h)
        if rt is not None:
            await store.revoke_refresh_family(rt["refresh_family_id"])
    return JSONResponse({}, status_code=200)
