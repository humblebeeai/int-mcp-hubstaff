"""Cryptographic helpers for the OAuth broker: PKCE, random tokens, hashing.

Kept dependency-free (stdlib only). Tokens we issue are high-entropy random
strings; we persist only their SHA-256 hash so a store leak does not yield live
credentials.
"""
import base64
import hashlib
import secrets


def new_token(nbytes: int = 32) -> str:
    """Generate a high-entropy URL-safe token (client secrets, codes, tokens)."""
    return secrets.token_urlsafe(nbytes)


def new_id(prefix: str = "") -> str:
    """Generate an opaque identifier, optionally prefixed (e.g. ``grant_``)."""
    return f"{prefix}{secrets.token_urlsafe(24)}"


def hash_token(token: str) -> str:
    """SHA-256 hex digest used as the storage key for issued tokens/codes."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _b64url_nopad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def s256_challenge(verifier: str) -> str:
    """Compute the S256 PKCE code_challenge for a given code_verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url_nopad(digest)


def verify_pkce_s256(verifier: str, challenge: str) -> bool:
    """Constant-time check that ``verifier`` matches an S256 ``challenge``."""
    if not verifier or not challenge:
        return False
    return secrets.compare_digest(s256_challenge(verifier), challenge)


def new_pkce_verifier() -> str:
    """Generate a PKCE code_verifier for our *upstream* leg to Hubstaff.

    RFC 7636 allows 43-128 chars from the unreserved set; token_urlsafe(64)
    yields ~86 chars, comfortably within range.
    """
    return secrets.token_urlsafe(64)
