"""Simple token management with JSON file caching."""
import json
import os
import time
import httpx
from .config import config


def load_tokens():
    """Load tokens from JSON file."""
    if not os.path.exists("tokens.json"):
        return {}
    try:
        with open("tokens.json", "r") as f:
            return json.load(f)
    except:
        return {}


def save_tokens(tokens):
    """Save tokens to JSON file."""
    try:
        with open("tokens.json", "w") as f:
            json.dump(tokens, f, indent=2)
    except Exception as e:
        print(f"Could not save tokens: {e}")


async def get_access_token():
    """Get valid access token, refreshing if needed."""
    tokens = load_tokens()
    
    # Check if we have a valid access token
    access_token = tokens.get("access_token")
    cached_at = tokens.get("cached_at", 0)
    current_time = time.time()
    
    # Token is valid for 6 days
    if access_token and (current_time - cached_at) < 6 * 24 * 3600:
        return access_token
    
    # Need to refresh
    refresh_token = tokens.get("refresh_token", config.hubstaff_token)
    
    print("Refreshing access token...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://account.hubstaff.com/access_tokens",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Save new tokens
                new_tokens = {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token", refresh_token),
                    "cached_at": current_time
                }
                save_tokens(new_tokens)
                print("Token refreshed successfully")
                return data["access_token"]
            else:
                print(f"Token refresh failed: {response.status_code}")
                
        except Exception as e:
            print(f"Error refreshing token: {e}")
    
    # Return cached token if refresh failed
    if access_token:
        return access_token
    
    raise Exception("No valid access token available")


def initialize_tokens():
    """Initialize tokens.json with refresh token if it doesn't exist."""
    if not os.path.exists("tokens.json") and config.hubstaff_token:
        initial_tokens = {
            "refresh_token": config.hubstaff_token,
            "created_at": time.time()
        }
        save_tokens(initial_tokens)
        print("Created tokens.json with refresh token")


# Initialize on import
initialize_tokens()