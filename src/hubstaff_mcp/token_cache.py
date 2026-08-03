"""Token cache keyed by Hubstaff PAT.

Each caller's Hubstaff Personal Access Token (PAT) is sent as the
``X-MCP-API-Key`` header. The PAT doubles as the long-lived refresh token
Hubstaff uses to mint short-lived access tokens, so we key the access-token
cache by the PAT itself.
"""
import json
import os
import time
import httpx
from .config import config


def load_tokens():
    """Load cached access tokens from JSON file."""
    if not os.path.exists("tokens.json"):
        return {}
    try:
        with open("tokens.json", "r") as f:
            return json.load(f)
    except:
        return {}


def save_tokens(tokens):
    """Save cached access tokens to JSON file."""
    try:
        with open("tokens.json", "w") as f:
            json.dump(tokens, f, indent=2)
    except Exception as e:
        print(f"Could not save tokens: {e}")


def get_user_tokens(api_key: str) -> dict:
    """Get cached access token for a given PAT."""
    all_tokens = load_tokens()
    # Backward compat: legacy single-user flat format
    if "access_token" in all_tokens or "refresh_token" in all_tokens:
        return all_tokens
    return all_tokens.get(api_key, {})


def save_user_tokens(api_key: str, user_tokens: dict):
    """Save access token for a given PAT."""
    all_tokens = load_tokens()
    # Backward compat: legacy single-user flat format
    if "access_token" in all_tokens or "refresh_token" in all_tokens:
        all_tokens = {}
    all_tokens[api_key] = user_tokens
    save_tokens(all_tokens)


async def get_access_token(api_key: str) -> str:
    """Get a valid access token for a PAT, refreshing if needed.

    The PAT is used directly as the refresh token (Hubstaff treats Personal
    Access Tokens as refresh tokens). Access tokens are valid for 6 days.
    """
    user_tokens = get_user_tokens(api_key)

    access_token = user_tokens.get("access_token")
    cached_at = user_tokens.get("cached_at", 0)
    current_time = time.time()

    # Token is valid for 6 days
    if access_token and (current_time - cached_at) < 6 * 24 * 3600:
        return access_token

    # For the reserved "default" key (no header sent), use the env token.
    # Otherwise the PAT itself is the refresh token.
    refresh_token = config.hubstaff_token if api_key == "default" else api_key

    if not refresh_token:
        raise Exception(
            "No Hubstaff credential configured. Send a valid Hubstaff PAT in "
            "the X-MCP-API-Key header."
        )

    print(f"Refreshing access token for PAT: {refresh_token[:8]}...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://account.hubstaff.com/access_tokens",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )

            if response.status_code == 200:
                data = response.json()

                new_tokens = {
                    "access_token": data["access_token"],
                    "cached_at": current_time,
                }
                save_user_tokens(api_key, new_tokens)
                print(f"Token refreshed successfully for PAT: {refresh_token[:8]}...")
                return data["access_token"]
            else:
                print(f"Token refresh failed: {response.status_code}")
                if response.status_code in (401, 403):
                    raise Exception(
                        "Invalid or expired Hubstaff PAT. Ask the user to create a "
                        "valid Personal Access Token at account.hubstaff.com."
                    )

        except httpx.HTTPError as e:
            print(f"Error refreshing token: {e}")

    raise Exception(f"No valid access token available for PAT: {api_key[:8]}...")


def initialize_tokens():
    """Migrate any legacy single-user tokens.json to the PAT-keyed format.

    The legacy format is stored under a reserved ``default`` key so existing
    setups keep working, and the key is reused as the PAT for compatibility.
    """
    if not os.path.exists("tokens.json"):
        return
    all_tokens = load_tokens()
    if "access_token" in all_tokens or "refresh_token" in all_tokens:
        print("Migrating legacy tokens.json to PAT-keyed format...")
        # Keep under "default"; callers without a header will use the env token
        save_tokens({"default": all_tokens})


# Initialize on import
initialize_tokens()
