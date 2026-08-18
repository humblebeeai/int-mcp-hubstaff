"""SQLite-backed persistence for the OAuth broker.

Replaces the whole-file-rewrite ``tokens.json`` with a relational store that
supports the several record types the broker needs (DCR clients, pending auth
requests, our auth codes, per-user Hubstaff grants, and our issued
access/refresh tokens).

Design notes:
- One DB file (``config.oauth_db_path``) with WAL mode for safe concurrent
  reads and a busy timeout so brief write contention doesn't error.
- All access goes through ``asyncio.to_thread`` so the event loop is never
  blocked on disk I/O. Connections are opened per-call (cheap for SQLite) with
  ``check_same_thread=False`` disabled by using a fresh connection each time.
- Only *hashes* of issued tokens/codes are stored. Hubstaff refresh tokens are
  stored in the clear (they are the credential we must replay) — protect the DB
  file at rest (see deployment notes / .gitignore).
- Timestamps are stored as unix epoch seconds (REAL) so TTL cleanup is a simple
  ``DELETE WHERE expires_at < ?``.
"""
import asyncio
import json
import os
import sqlite3
import time
from typing import Any, Optional

from ..config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    client_secret_hash TEXT,
    redirect_uris TEXT NOT NULL,            -- JSON array
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
    grant_types TEXT,                        -- JSON array
    client_name TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_auth (
    upstream_state TEXT PRIMARY KEY,
    downstream_client_id TEXT NOT NULL,
    downstream_redirect_uri TEXT NOT NULL,
    downstream_state TEXT,
    downstream_code_challenge TEXT NOT NULL,
    scope TEXT,
    resource TEXT,
    upstream_pkce_verifier TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    resource TEXT,
    grant_id TEXT NOT NULL,
    scope TEXT,
    used INTEGER NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    grant_id TEXT PRIMARY KEY,
    hubstaff_user_id TEXT,
    hubstaff_refresh_token TEXT,
    hubstaff_access_token TEXT,
    hubstaff_access_expires_at REAL,
    scope TEXT,
    downstream_client_id TEXT,
    is_legacy_pat INTEGER NOT NULL DEFAULT 0,
    needs_reauth INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    resource TEXT,
    scope TEXT,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    refresh_family_id TEXT NOT NULL,
    rotated_from TEXT,
    revoked INTEGER NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
    hubstaff_user_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (hubstaff_user_id, client_id, scope)
);

CREATE TABLE IF NOT EXISTS pending_consent (
    consent_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    hubstaff_user_id TEXT,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    downstream_state TEXT,
    downstream_code_challenge TEXT NOT NULL,
    resource TEXT,
    scope TEXT,
    client_name TEXT,
    expires_at REAL NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    db_path = config.oauth_db_path
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_sync() -> None:
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


_initialized = False
_init_lock = asyncio.Lock()


async def init() -> None:
    """Create the schema if needed. Idempotent; safe to call on startup."""
    global _initialized
    async with _init_lock:
        if _initialized:
            return
        await asyncio.to_thread(_init_sync)
        _initialized = True


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


# --- generic thread wrapper -------------------------------------------------

async def _run(fn, *args) -> Any:
    return await asyncio.to_thread(fn, *args)


# --- clients (DCR) ----------------------------------------------------------

def _create_client_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO clients (client_id, client_secret_hash, redirect_uris, "
            "token_endpoint_auth_method, grant_types, client_name, created_at) "
            "VALUES (:client_id, :client_secret_hash, :redirect_uris, "
            ":token_endpoint_auth_method, :grant_types, :client_name, :created_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def create_client(
    client_id: str,
    redirect_uris: list[str],
    token_endpoint_auth_method: str = "none",
    grant_types: Optional[list[str]] = None,
    client_name: Optional[str] = None,
    client_secret_hash: Optional[str] = None,
) -> None:
    await _run(
        _create_client_sync,
        {
            "client_id": client_id,
            "client_secret_hash": client_secret_hash,
            "redirect_uris": json.dumps(redirect_uris),
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "grant_types": json.dumps(grant_types or ["authorization_code", "refresh_token"]),
            "client_name": client_name,
            "created_at": time.time(),
        },
    )


def _get_client_sync(client_id: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        d = _row_to_dict(row)
        if d is not None:
            d["redirect_uris"] = json.loads(d["redirect_uris"])
            d["grant_types"] = json.loads(d["grant_types"]) if d["grant_types"] else []
        return d
    finally:
        conn.close()


async def get_client(client_id: str) -> Optional[dict]:
    return await _run(_get_client_sync, client_id)


# --- pending_auth (upstream leg state) --------------------------------------

def _create_pending_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO pending_auth (upstream_state, downstream_client_id, "
            "downstream_redirect_uri, downstream_state, downstream_code_challenge, "
            "scope, resource, upstream_pkce_verifier, created_at, expires_at) "
            "VALUES (:upstream_state, :downstream_client_id, :downstream_redirect_uri, "
            ":downstream_state, :downstream_code_challenge, :scope, :resource, "
            ":upstream_pkce_verifier, :created_at, :expires_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def create_pending_auth(
    upstream_state: str,
    downstream_client_id: str,
    downstream_redirect_uri: str,
    downstream_state: Optional[str],
    downstream_code_challenge: str,
    scope: Optional[str],
    resource: Optional[str],
    upstream_pkce_verifier: str,
    ttl_seconds: int,
) -> None:
    now = time.time()
    await _run(
        _create_pending_sync,
        {
            "upstream_state": upstream_state,
            "downstream_client_id": downstream_client_id,
            "downstream_redirect_uri": downstream_redirect_uri,
            "downstream_state": downstream_state,
            "downstream_code_challenge": downstream_code_challenge,
            "scope": scope,
            "resource": resource,
            "upstream_pkce_verifier": upstream_pkce_verifier,
            "created_at": now,
            "expires_at": now + ttl_seconds,
        },
    )


def _take_pending_sync(upstream_state: str) -> Optional[dict]:
    """Fetch and delete (single-use) a pending row if unexpired."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM pending_auth WHERE upstream_state = ?", (upstream_state,)
        ).fetchone()
        conn.execute(
            "DELETE FROM pending_auth WHERE upstream_state = ?", (upstream_state,)
        )
        conn.commit()
        d = _row_to_dict(row)
        if d is None:
            return None
        if d["expires_at"] < time.time():
            return None
        return d
    finally:
        conn.close()


async def take_pending_auth(upstream_state: str) -> Optional[dict]:
    """Atomically consume a pending auth row (single-use, expiry-checked)."""
    return await _run(_take_pending_sync, upstream_state)


# --- grants (per-user Hubstaff tokens) --------------------------------------

def _create_grant_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO grants (grant_id, hubstaff_user_id, "
            "hubstaff_refresh_token, hubstaff_access_token, hubstaff_access_expires_at, "
            "scope, downstream_client_id, is_legacy_pat, needs_reauth, created_at) "
            "VALUES (:grant_id, :hubstaff_user_id, :hubstaff_refresh_token, "
            ":hubstaff_access_token, :hubstaff_access_expires_at, :scope, "
            ":downstream_client_id, :is_legacy_pat, 0, :created_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def create_grant(
    grant_id: str,
    hubstaff_refresh_token: str,
    hubstaff_access_token: Optional[str] = None,
    hubstaff_access_expires_at: Optional[float] = None,
    hubstaff_user_id: Optional[str] = None,
    scope: Optional[str] = None,
    downstream_client_id: Optional[str] = None,
    is_legacy_pat: bool = False,
) -> None:
    await _run(
        _create_grant_sync,
        {
            "grant_id": grant_id,
            "hubstaff_user_id": hubstaff_user_id,
            "hubstaff_refresh_token": hubstaff_refresh_token,
            "hubstaff_access_token": hubstaff_access_token,
            "hubstaff_access_expires_at": hubstaff_access_expires_at,
            "scope": scope,
            "downstream_client_id": downstream_client_id,
            "is_legacy_pat": 1 if is_legacy_pat else 0,
            "created_at": time.time(),
        },
    )


def _get_grant_sync(grant_id: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


async def get_grant(grant_id: str) -> Optional[dict]:
    return await _run(_get_grant_sync, grant_id)


def _update_grant_tokens_sync(row: dict) -> None:
    """Persist a refreshed Hubstaff token pair (incl. the rotated refresh token).

    This is the fix for the historical bug where the rotated refresh_token was
    discarded. Done in a single statement so the rotation is atomic.
    """
    conn = _connect()
    try:
        conn.execute(
            "UPDATE grants SET hubstaff_access_token = :access, "
            "hubstaff_access_expires_at = :expires_at, "
            "hubstaff_refresh_token = :refresh, needs_reauth = 0 "
            "WHERE grant_id = :grant_id",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def update_grant_tokens(
    grant_id: str,
    access_token: str,
    access_expires_at: float,
    refresh_token: str,
) -> None:
    await _run(
        _update_grant_tokens_sync,
        {
            "grant_id": grant_id,
            "access": access_token,
            "expires_at": access_expires_at,
            "refresh": refresh_token,
        },
    )


def _mark_needs_reauth_sync(grant_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE grants SET needs_reauth = 1 WHERE grant_id = ?", (grant_id,)
        )
        conn.commit()
    finally:
        conn.close()


async def mark_needs_reauth(grant_id: str) -> None:
    await _run(_mark_needs_reauth_sync, grant_id)


# --- auth codes (our downstream codes) --------------------------------------

def _create_auth_code_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO auth_codes (code_hash, client_id, redirect_uri, "
            "code_challenge, resource, grant_id, scope, used, expires_at) "
            "VALUES (:code_hash, :client_id, :redirect_uri, :code_challenge, "
            ":resource, :grant_id, :scope, 0, :expires_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def create_auth_code(
    code_hash: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    resource: Optional[str],
    grant_id: str,
    scope: Optional[str],
    ttl_seconds: int,
) -> None:
    await _run(
        _create_auth_code_sync,
        {
            "code_hash": code_hash,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "resource": resource,
            "grant_id": grant_id,
            "scope": scope,
            "expires_at": time.time() + ttl_seconds,
        },
    )


def _take_auth_code_sync(code_hash: str) -> Optional[dict]:
    """Atomically consume an auth code (single-use, expiry-checked)."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM auth_codes WHERE code_hash = ?", (code_hash,)
        ).fetchone()
        d = _row_to_dict(row)
        if d is None:
            conn.commit()
            return None
        # Delete regardless so a reused code can never succeed twice.
        conn.execute("DELETE FROM auth_codes WHERE code_hash = ?", (code_hash,))
        conn.commit()
        if d["used"] or d["expires_at"] < time.time():
            return None
        return d
    finally:
        conn.close()


async def take_auth_code(code_hash: str) -> Optional[dict]:
    return await _run(_take_auth_code_sync, code_hash)


# --- issued access tokens ---------------------------------------------------

def _create_access_token_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO access_tokens (token_hash, grant_id, client_id, resource, "
            "scope, expires_at) VALUES (:token_hash, :grant_id, :client_id, "
            ":resource, :scope, :expires_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def create_access_token(
    token_hash: str,
    grant_id: str,
    client_id: str,
    resource: Optional[str],
    scope: Optional[str],
    ttl_seconds: int,
) -> None:
    await _run(
        _create_access_token_sync,
        {
            "token_hash": token_hash,
            "grant_id": grant_id,
            "client_id": client_id,
            "resource": resource,
            "scope": scope,
            "expires_at": time.time() + ttl_seconds,
        },
    )


def _get_access_token_sync(token_hash: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM access_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


async def get_access_token_row(token_hash: str) -> Optional[dict]:
    """Look up an issued access token by hash. Caller checks expiry + resource."""
    return await _run(_get_access_token_sync, token_hash)


# --- issued refresh tokens (rotation + family revocation) -------------------

def _create_refresh_token_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, grant_id, client_id, "
            "refresh_family_id, rotated_from, revoked, expires_at) "
            "VALUES (:token_hash, :grant_id, :client_id, :refresh_family_id, "
            ":rotated_from, 0, :expires_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def create_refresh_token(
    token_hash: str,
    grant_id: str,
    client_id: str,
    refresh_family_id: str,
    ttl_seconds: int,
    rotated_from: Optional[str] = None,
) -> None:
    await _run(
        _create_refresh_token_sync,
        {
            "token_hash": token_hash,
            "grant_id": grant_id,
            "client_id": client_id,
            "refresh_family_id": refresh_family_id,
            "rotated_from": rotated_from,
            "expires_at": time.time() + ttl_seconds,
        },
    )


def _get_refresh_token_sync(token_hash: str) -> Optional[dict]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


async def get_refresh_token_row(token_hash: str) -> Optional[dict]:
    return await _run(_get_refresh_token_sync, token_hash)


def _revoke_refresh_family_sync(family_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE refresh_family_id = ?",
            (family_id,),
        )
        conn.commit()
    finally:
        conn.close()


async def revoke_refresh_family(family_id: str) -> None:
    """Revoke every refresh token in a family (breach response on reuse)."""
    await _run(_revoke_refresh_family_sync, family_id)


def _mark_refresh_used_sync(token_hash: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
            (token_hash,),
        )
        conn.commit()
    finally:
        conn.close()


async def mark_refresh_used(token_hash: str) -> None:
    """Revoke a single (just-rotated) refresh token."""
    await _run(_mark_refresh_used_sync, token_hash)


# --- consent records (confused-deputy mitigation) ---------------------------

def _has_consent_sync(hubstaff_user_id: str, client_id: str, scope: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM consents WHERE hubstaff_user_id = ? AND client_id = ? "
            "AND scope = ?",
            (hubstaff_user_id, client_id, scope),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


async def has_consent(hubstaff_user_id: str, client_id: str, scope: str) -> bool:
    return await _run(_has_consent_sync, hubstaff_user_id, client_id, scope)


def _record_consent_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO consents (hubstaff_user_id, client_id, scope, "
            "created_at) VALUES (:hubstaff_user_id, :client_id, :scope, :created_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def record_consent(hubstaff_user_id: str, client_id: str, scope: str) -> None:
    await _run(
        _record_consent_sync,
        {
            "hubstaff_user_id": hubstaff_user_id,
            "client_id": client_id,
            "scope": scope,
            "created_at": time.time(),
        },
    )


# --- pending consent (per-client consent interstitial) ----------------------

def _create_pending_consent_sync(row: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO pending_consent (consent_id, grant_id, hubstaff_user_id, "
            "client_id, redirect_uri, downstream_state, downstream_code_challenge, "
            "resource, scope, client_name, expires_at) VALUES (:consent_id, "
            ":grant_id, :hubstaff_user_id, :client_id, :redirect_uri, "
            ":downstream_state, :downstream_code_challenge, :resource, :scope, "
            ":client_name, :expires_at)",
            row,
        )
        conn.commit()
    finally:
        conn.close()


async def create_pending_consent(
    consent_id: str,
    grant_id: str,
    hubstaff_user_id: Optional[str],
    client_id: str,
    redirect_uri: str,
    downstream_state: Optional[str],
    downstream_code_challenge: str,
    resource: Optional[str],
    scope: Optional[str],
    client_name: Optional[str],
    ttl_seconds: int,
) -> None:
    await _run(
        _create_pending_consent_sync,
        {
            "consent_id": consent_id,
            "grant_id": grant_id,
            "hubstaff_user_id": hubstaff_user_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "downstream_state": downstream_state,
            "downstream_code_challenge": downstream_code_challenge,
            "resource": resource,
            "scope": scope,
            "client_name": client_name,
            "expires_at": time.time() + ttl_seconds,
        },
    )


def _take_pending_consent_sync(consent_id: str) -> Optional[dict]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM pending_consent WHERE consent_id = ?", (consent_id,)
        ).fetchone()
        conn.execute(
            "DELETE FROM pending_consent WHERE consent_id = ?", (consent_id,)
        )
        conn.commit()
        d = _row_to_dict(row)
        if d is None or d["expires_at"] < time.time():
            return None
        return d
    finally:
        conn.close()


async def take_pending_consent(consent_id: str) -> Optional[dict]:
    return await _run(_take_pending_consent_sync, consent_id)


# --- token revocation -------------------------------------------------------

def _delete_access_token_sync(token_hash: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM access_tokens WHERE token_hash = ?", (token_hash,))
        conn.commit()
    finally:
        conn.close()


async def delete_access_token(token_hash: str) -> None:
    await _run(_delete_access_token_sync, token_hash)


# --- TTL cleanup ------------------------------------------------------------

def _cleanup_sync() -> None:
    now = time.time()
    conn = _connect()
    try:
        conn.execute("DELETE FROM pending_auth WHERE expires_at < ?", (now,))
        conn.execute("DELETE FROM pending_consent WHERE expires_at < ?", (now,))
        conn.execute("DELETE FROM auth_codes WHERE expires_at < ?", (now,))
        conn.execute("DELETE FROM access_tokens WHERE expires_at < ?", (now,))
        conn.execute("DELETE FROM refresh_tokens WHERE expires_at < ?", (now,))
        conn.commit()
    finally:
        conn.close()


async def cleanup_expired() -> None:
    """Best-effort purge of expired short-lived rows."""
    await _run(_cleanup_sync)
