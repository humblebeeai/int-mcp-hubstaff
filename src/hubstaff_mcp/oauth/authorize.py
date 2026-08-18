"""Authorization endpoint (downstream leg) — starts the brokered login.

Validates the downstream MCP client's request, then redirects the user's browser
to Hubstaff to log in. We keep the downstream request in a single-use
``pending_auth`` row keyed by *our* upstream ``state`` so the callback can
resume it. PKCE runs on both legs: the client's challenge is stored for
verification at ``/token``; we generate our own verifier/challenge for the
upstream leg.
"""
from urllib.parse import urlencode

from starlette.responses import HTMLResponse, RedirectResponse

from ..config import config
from . import store
from .issue import build_redirect
from .tokens import new_pkce_verifier, new_token, s256_challenge


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    """Rendered only when we cannot safely redirect (bad client/redirect_uri)."""
    return HTMLResponse(
        f"<html><body><h1>Authorization error</h1><p>{message}</p></body></html>",
        status_code=status,
    )


async def authorize(request):
    await store.init()
    q = request.query_params

    client_id = q.get("client_id")
    redirect_uri = q.get("redirect_uri")

    # Validate the client and redirect_uri BEFORE trusting the redirect target.
    if not client_id:
        return _error_page("Missing client_id.")
    client = await store.get_client(client_id)
    if client is None:
        return _error_page("Unknown client_id. Register via /register first.")
    if not redirect_uri or redirect_uri not in client["redirect_uris"]:
        return _error_page("redirect_uri does not match a registered value.")

    # From here we can safely redirect errors back to the client.
    state = q.get("state")
    scope = q.get("scope") or config.hubstaff_scopes
    resource = q.get("resource")
    response_type = q.get("response_type")
    code_challenge = q.get("code_challenge")
    code_challenge_method = q.get("code_challenge_method")

    def redirect_error(code: str, desc: str):
        params = {"error": code, "error_description": desc}
        if state is not None:
            params["state"] = state
        return RedirectResponse(build_redirect(redirect_uri, params), status_code=302)

    if response_type != "code":
        return redirect_error("unsupported_response_type", "response_type must be code")
    if not code_challenge or code_challenge_method != "S256":
        return redirect_error("invalid_request", "PKCE S256 code_challenge is required")
    # RFC 8707: resource must identify this MCP server when provided.
    if resource and resource.rstrip("/") != config.resource_uri.rstrip("/"):
        return redirect_error("invalid_target", "resource does not match this server")

    # Create the pending row keyed by our upstream state, with our own PKCE.
    upstream_state = new_token()
    upstream_verifier = new_pkce_verifier()
    await store.create_pending_auth(
        upstream_state=upstream_state,
        downstream_client_id=client_id,
        downstream_redirect_uri=redirect_uri,
        downstream_state=state,
        downstream_code_challenge=code_challenge,
        scope=scope,
        resource=config.resource_uri,
        upstream_pkce_verifier=upstream_verifier,
        ttl_seconds=config.pending_auth_ttl_seconds,
    )

    # Redirect the browser to Hubstaff's login/consent.
    upstream_params = {
        "response_type": "code",
        "client_id": config.hubstaff_client_id,
        "redirect_uri": config.hubstaff_redirect_uri,
        "scope": config.hubstaff_scopes,
        "state": upstream_state,
        "nonce": new_token(16),
        "code_challenge": s256_challenge(upstream_verifier),
        "code_challenge_method": "S256",
    }
    upstream_url = f"{config.hubstaff_account_base_url}/authorizations/new?{urlencode(upstream_params)}"
    return RedirectResponse(upstream_url, status_code=302)
