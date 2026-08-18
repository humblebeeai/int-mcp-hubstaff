"""OAuth discovery metadata endpoints.

- Protected Resource Metadata (RFC 9728) at ``/.well-known/oauth-protected-resource``
- Authorization Server Metadata (RFC 8414) at ``/.well-known/oauth-authorization-server``

Both are unauthenticated GETs and must be reachable through the proxy without
any auth in front of them, or the whole discovery handshake breaks.
"""
from starlette.responses import JSONResponse

from ..config import config

# Scopes we advertise to downstream MCP clients (mirror the upstream Hubstaff
# scopes we request).
_SCOPES = config.hubstaff_scopes.split()


async def protected_resource_metadata(request):
    """RFC 9728 — tells clients which authorization server protects this resource."""
    base = config.public_base_url.rstrip("/")
    return JSONResponse(
        {
            "resource": config.resource_uri,
            "authorization_servers": [base],
            "scopes_supported": _SCOPES,
            "bearer_methods_supported": ["header"],
        }
    )


async def authorization_server_metadata(request):
    """RFC 8414 — advertises our authorization/token/registration endpoints."""
    base = config.public_base_url.rstrip("/")
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "revocation_endpoint": f"{base}/revoke",
            "scopes_supported": _SCOPES,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )
