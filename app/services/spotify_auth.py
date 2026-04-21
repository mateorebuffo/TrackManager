"""
Spotify OAuth token management.

Tokens are stored in spotify_token.json in the project root.
This is intentionally simple for an MVP — no DB table needed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings

_TOKEN_FILE = Path("spotify_token.json")
_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SCOPES = "playlist-read-private playlist-read-collaborative"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_auth_url() -> str:
    """Build the Spotify authorization URL to redirect the user to."""
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": _SCOPES,
    }
    return _AUTHORIZE_URL + "?" + urlencode(params)


def exchange_code(code: str) -> None:
    """Exchange an authorization code for access + refresh tokens and persist them."""
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.spotify_redirect_uri,
        },
        auth=(settings.spotify_client_id, settings.spotify_client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    _save_token(resp.json())


def get_valid_access_token() -> str:
    """
    Return a valid access token.
    Automatically refreshes if the stored token is expired.
    Raises RuntimeError if not connected.
    """
    token_data = _load_token()
    if not token_data:
        raise RuntimeError("Spotify no está conectado. Autorizá primero.")
    if _is_expired(token_data):
        token_data = _refresh(token_data)
    return token_data["access_token"]


def is_connected() -> bool:
    return _TOKEN_FILE.exists() and bool(_load_token())


def disconnect() -> None:
    if _TOKEN_FILE.exists():
        _TOKEN_FILE.unlink()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _load_token() -> dict | None:
    if not _TOKEN_FILE.exists():
        return None
    try:
        return json.loads(_TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_token(data: dict) -> None:
    # Compute absolute expiry timestamp (with 60s safety margin)
    data["expires_at"] = int(time.time()) + data.get("expires_in", 3600) - 60
    _TOKEN_FILE.write_text(json.dumps(data, indent=2))


def _is_expired(token_data: dict) -> bool:
    return time.time() >= token_data.get("expires_at", 0)


def _refresh(token_data: dict) -> dict:
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No hay refresh token. Reconectá Spotify.")
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(settings.spotify_client_id, settings.spotify_client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    new_data = resp.json()
    # Spotify may or may not return a new refresh_token; keep the old one if not
    if "refresh_token" not in new_data:
        new_data["refresh_token"] = refresh_token
    _save_token(new_data)
    return new_data
