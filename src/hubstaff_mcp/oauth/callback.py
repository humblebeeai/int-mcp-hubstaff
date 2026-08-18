"""Upstream Hubstaff callback + per-client consent interstitial.

``/oauth/hubstaff/callback`` receives the Hubstaff authorization code, exchanges
it (HTTP Basic client creds + our upstream PKCE verifier) for the user's Hubstaff
tokens, persists them as a grant, and then either mints our downstream auth code
(if the user has already consented to this MCP client) or shows a consent page.

``/oauth/consent`` handles the consent form POST: on approval it records consent,
mints the auth code, and redirects back to the MCP client.
"""
import base64
import binascii
import json
import time
from html import escape

import httpx
from starlette.responses import HTMLResponse, RedirectResponse

from ..config import config
from . import store
from .issue import build_redirect, mint_auth_code
from .tokens import new_id


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(
        f"<html><body><h1>Login error</h1><p>{escape(message)}</p></body></html>",
        status_code=status,
    )


def _decode_id_token_sub(id_token: str) -> str | None:
    """Best-effort extraction of the ``sub`` claim from an OIDC id_token.

    The id_token arrives over our direct TLS exchange with Hubstaff, so for the
    purpose of an internal consent/user key we decode (without signature
    verification) rather than pulling in a JWT library. Returns None on failure.
    """
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        sub = payload.get("sub")
        return str(sub) if sub is not None else None
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return None


async def _exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange a Hubstaff authorization code for tokens (auth-code grant)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{config.hubstaff_account_base_url}/access_tokens",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.hubstaff_redirect_uri,
                "code_verifier": code_verifier,
            },
            auth=httpx.BasicAuth(
                config.hubstaff_client_id, config.hubstaff_client_secret
            ),
        )
    resp.raise_for_status()
    return resp.json()


async def _complete(
    *, grant_id, client_id, redirect_uri, downstream_state, code_challenge,
    resource, scope,
) -> RedirectResponse:
    """Mint our auth code and redirect back to the MCP client."""
    code = await mint_auth_code(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        resource=resource,
        grant_id=grant_id,
        scope=scope,
    )
    params = {"code": code}
    if downstream_state is not None:
        params["state"] = downstream_state
    return RedirectResponse(build_redirect(redirect_uri, params), status_code=302)


def _consent_page(consent_id: str, client_name: str, scope: str) -> HTMLResponse:
    scopes_html = "".join(f"<li>{escape(s)}</li>" for s in scope.split())
    name = escape(client_name or "An application")
    html = f"""<html><body>
    <h1>Authorize access</h1>
    <p><strong>{name}</strong> is requesting access to your Hubstaff data with:</p>
    <ul>{scopes_html}</ul>
    <form method="post" action="/oauth/consent">
      <input type="hidden" name="consent_id" value="{escape(consent_id)}"/>
      <button type="submit" name="action" value="approve">Approve</button>
      <button type="submit" name="action" value="deny">Deny</button>
    </form>
    </body></html>"""
    return HTMLResponse(html)


async def hubstaff_callback(request):
    await store.init()
    q = request.query_params

    if q.get("error"):
        return _error_page(f"Hubstaff returned an error: {q.get('error')}")

    upstream_state = q.get("state")
    code = q.get("code")
    if not upstream_state or not code:
        return _error_page("Missing code or state.")

    pending = await store.take_pending_auth(upstream_state)
    if pending is None:
        return _error_page("Login session expired or invalid. Please try again.")

    try:
        token_data = await _exchange_code(code, pending["upstream_pkce_verifier"])
    except httpx.HTTPError as e:
        return _error_page(f"Failed to exchange Hubstaff code: {e}")

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    if not access_token or not refresh_token:
        return _error_page("Hubstaff did not return the expected tokens.")
    expires_in = token_data.get("expires_in", 24 * 3600)
    user_id = _decode_id_token_sub(token_data.get("id_token", ""))

    grant_id = new_id("grant_")
    await store.create_grant(
        grant_id,
        hubstaff_refresh_token=refresh_token,
        hubstaff_access_token=access_token,
        hubstaff_access_expires_at=time.time() + float(expires_in),
        hubstaff_user_id=user_id,
        scope=pending["scope"],
        downstream_client_id=pending["downstream_client_id"],
    )

    client_id = pending["downstream_client_id"]
    scope = pending["scope"]

    # Confused-deputy mitigation: require explicit per-client consent (unless a
    # matching consent already exists for this Hubstaff user + client + scope).
    already = user_id and await store.has_consent(user_id, client_id, scope)
    if already:
        return await _complete(
            grant_id=grant_id,
            client_id=client_id,
            redirect_uri=pending["downstream_redirect_uri"],
            downstream_state=pending["downstream_state"],
            code_challenge=pending["downstream_code_challenge"],
            resource=pending["resource"],
            scope=scope,
        )

    client = await store.get_client(client_id)
    consent_id = new_id("consent_")
    await store.create_pending_consent(
        consent_id=consent_id,
        grant_id=grant_id,
        hubstaff_user_id=user_id,
        client_id=client_id,
        redirect_uri=pending["downstream_redirect_uri"],
        downstream_state=pending["downstream_state"],
        downstream_code_challenge=pending["downstream_code_challenge"],
        resource=pending["resource"],
        scope=scope,
        client_name=(client or {}).get("client_name"),
        ttl_seconds=config.pending_auth_ttl_seconds,
    )
    return _consent_page(consent_id, (client or {}).get("client_name"), scope)


async def consent(request):
    await store.init()
    form = await request.form()
    consent_id = form.get("consent_id")
    action = form.get("action")
    if not consent_id:
        return _error_page("Missing consent_id.")

    pc = await store.take_pending_consent(consent_id)
    if pc is None:
        return _error_page("Consent session expired or invalid. Please try again.")

    if action != "approve":
        params = {"error": "access_denied", "error_description": "User denied access"}
        if pc["downstream_state"] is not None:
            params["state"] = pc["downstream_state"]
        return RedirectResponse(
            build_redirect(pc["redirect_uri"], params), status_code=302
        )

    if pc["hubstaff_user_id"]:
        await store.record_consent(
            pc["hubstaff_user_id"], pc["client_id"], pc["scope"]
        )

    return await _complete(
        grant_id=pc["grant_id"],
        client_id=pc["client_id"],
        redirect_uri=pc["redirect_uri"],
        downstream_state=pc["downstream_state"],
        code_challenge=pc["downstream_code_challenge"],
        resource=pc["resource"],
        scope=pc["scope"],
    )
