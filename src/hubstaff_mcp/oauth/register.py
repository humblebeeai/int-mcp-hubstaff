"""Dynamic Client Registration endpoint (RFC 7591).

Lets MCP clients obtain a ``client_id`` without manual setup. We register public
clients only (PKCE, ``token_endpoint_auth_method: none``) — no client secret is
issued, since MCP clients are public and rely on PKCE.
"""
from starlette.responses import JSONResponse

from ..config import config
from . import store
from .issue import is_allowed_redirect_uri
from .tokens import new_id


def _error(msg: str, code: str = "invalid_client_metadata", status: int = 400):
    return JSONResponse({"error": code, "error_description": msg}, status_code=status)


async def register(request):
    await store.init()
    try:
        body = await request.json()
    except Exception:
        return _error("Request body must be JSON")
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object")

    redirect_uris = body.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _error("redirect_uris is required and must be a non-empty array")
    for uri in redirect_uris:
        if not isinstance(uri, str) or not is_allowed_redirect_uri(uri):
            return _error(
                f"redirect_uri must be https or loopback http: {uri!r}",
                code="invalid_redirect_uri",
            )

    client_id = new_id("mcp_")
    grant_types = body.get("grant_types") or ["authorization_code", "refresh_token"]
    client_name = body.get("client_name")

    await store.create_client(
        client_id=client_id,
        redirect_uris=redirect_uris,
        token_endpoint_auth_method="none",
        grant_types=grant_types,
        client_name=client_name,
        client_secret_hash=None,
    )

    return JSONResponse(
        {
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": grant_types,
            "response_types": ["code"],
            "client_name": client_name,
            "scope": config.hubstaff_scopes,
        },
        status_code=201,
    )
