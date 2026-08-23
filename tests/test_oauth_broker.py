"""End-to-end tests for the OAuth broker (AS + RS federating to Hubstaff).

The Hubstaff upstream code exchange is stubbed so the whole downstream flow can
run offline: DCR -> authorize -> callback -> consent -> token -> Bearer /mcp ->
refresh rotation -> reuse detection -> remembered consent.

Run:  .venv/bin/python -m pytest tests/ -q
"""
import base64
import json
import os
import re
from urllib.parse import parse_qs, urlparse

import pytest

# Configure the app for a self-contained, offline test run BEFORE importing it.
os.environ.setdefault("HUBSTAFF_ORGANIZATION_ID", "542238")
os.environ["HUBSTAFF_CLIENT_ID"] = "up_client"
os.environ["HUBSTAFF_CLIENT_SECRET"] = "up_secret"
os.environ["PUBLIC_BASE_URL"] = "https://hubstaff.hbai.dev"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    os.environ["OAUTH_DB_PATH"] = str(tmp_path / "oauth.db")
    from starlette.testclient import TestClient
    from hubstaff_mcp import server
    from hubstaff_mcp.oauth import callback

    async def fake_exchange(code, verifier):
        assert code == "hubstaff_code_123"
        assert verifier  # our upstream PKCE verifier is threaded through
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "user_42"}).encode()
        ).rstrip(b"=").decode()
        return {
            "access_token": "HS_AT",
            "refresh_token": "HS_RT",
            "expires_in": 86400,
            "id_token": f"{header}.{payload}.",
        }

    monkeypatch.setattr(callback, "_exchange_code", fake_exchange)
    return TestClient(server.app)


def _pkce():
    from hubstaff_mcp.oauth.tokens import new_pkce_verifier, s256_challenge

    v = new_pkce_verifier()
    return v, s256_challenge(v)


def _register(client):
    r = client.post(
        "/register",
        json={"redirect_uris": ["https://client.example/cb"], "client_name": "Test MCP"},
    )
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _authorize(client, client_id, challenge, state):
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "https://client.example/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": "https://hubstaff.hbai.dev/mcp",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


def test_metadata_and_unauth(client):
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.json()["resource"] == "https://hubstaff.hbai.dev/mcp"
    r = client.get("/.well-known/oauth-authorization-server")
    assert r.json()["code_challenge_methods_supported"] == ["S256"]

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert "oauth-protected-resource" in r.headers.get("www-authenticate", "")


def test_full_flow(client):
    client_id = _register(client)
    verifier, challenge = _pkce()
    ustate = _authorize(client, client_id, challenge, "dstate1")

    # callback -> consent page
    r = client.get(
        "/oauth/hubstaff/callback",
        params={"state": ustate, "code": "hubstaff_code_123"},
        follow_redirects=False,
    )
    assert r.status_code == 200 and "Approve" in r.text
    consent_id = re.search(r'name="consent_id" value="([^"]+)"', r.text).group(1)

    # approve -> auth code
    r = client.post(
        "/oauth/consent",
        data={"consent_id": consent_id, "action": "approve"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    cb = parse_qs(urlparse(r.headers["location"]).query)
    assert cb["state"] == ["dstate1"]
    code = cb["code"][0]

    # token exchange
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": verifier,
            "resource": "https://hubstaff.hbai.dev/mcp",
        },
    )
    assert r.status_code == 200, r.text
    tok = r.json()
    access, refresh = tok["access_token"], tok["refresh_token"]

    # reused auth code rejected
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": verifier,
        },
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

    # Bearer works on /mcp
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200
    assert len(r.json()["result"]["tools"]) == 13


def test_pkce_mismatch_rejected(client):
    client_id = _register(client)
    verifier, challenge = _pkce()
    ustate = _authorize(client, client_id, challenge, "s")
    client.get(
        "/oauth/hubstaff/callback",
        params={"state": ustate, "code": "hubstaff_code_123"},
        follow_redirects=False,
    )
    # approve
    r = client.get(
        "/oauth/hubstaff/callback",
        params={"state": _authorize(client, client_id, challenge, "s2"), "code": "hubstaff_code_123"},
        follow_redirects=False,
    )
    consent_id = re.search(r'name="consent_id" value="([^"]+)"', r.text).group(1)
    r = client.post(
        "/oauth/consent",
        data={"consent_id": consent_id, "action": "approve"},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    # wrong verifier
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": "wrong-verifier",
        },
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_refresh_rotation_and_reuse(client):
    client_id = _register(client)
    verifier, challenge = _pkce()
    ustate = _authorize(client, client_id, challenge, "st")
    r = client.get(
        "/oauth/hubstaff/callback",
        params={"state": ustate, "code": "hubstaff_code_123"},
        follow_redirects=False,
    )
    consent_id = re.search(r'name="consent_id" value="([^"]+)"', r.text).group(1)
    r = client.post(
        "/oauth/consent",
        data={"consent_id": consent_id, "action": "approve"},
        follow_redirects=False,
    )
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": verifier,
        },
    )
    refresh = r.json()["refresh_token"]

    # rotate
    r = client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id},
    )
    assert r.status_code == 200
    new_refresh = r.json()["refresh_token"]
    assert new_refresh != refresh

    # reuse old refresh -> family revoked
    r = client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id},
    )
    assert r.status_code == 400
    # rotated refresh now dead too
    r = client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": new_refresh, "client_id": client_id},
    )
    assert r.status_code == 400


def test_bad_redirect_uri_no_redirect(client):
    # unregistered redirect_uri -> error page, never a redirect
    client_id = _register(client)
    _, challenge = _pkce()
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "https://attacker.example/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "error" in r.text.lower()


def test_dcr_rejects_non_https_redirect(client):
    r = client.post("/register", json={"redirect_uris": ["http://attacker.example/cb"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"
