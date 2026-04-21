"""
YouTube (Google) OAuth token management.

Tokens are stored in youtube_token.json in the project root.
Same pattern as spotify_auth.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings

_TOKEN_FILE = Path("youtube_token.json")
_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_auth_url() -> str:
    params = {
        "client_id": settings.youtube_client_id,
        "response_type": "code",
        "redirect_uri": settings.youtube_redirect_uri,
        "scope": _SCOPE,
        "access_type": "offline",   # needed to receive a refresh_token
        "prompt": "consent",        # forces refresh_token even on re-auth
    }
    return _AUTHORIZE_URL + "?" + urlencode(params)


def exchange_code(code: str) -> None:
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.youtube_redirect_uri,
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
        },
        timeout=15,
    )
    resp.raise_for_status()
    _save_token(resp.json())


def get_valid_access_token() -> str:
    token_data = _load_token()
    if not token_data:
        raise RuntimeError("YouTube no está conectado. Autorizá primero.")
    if _is_expired(token_data):
        try:
            token_data = _refresh(token_data)
        except Exception:
            disconnect()
            raise RuntimeError("Token de YouTube expirado o inválido. Reconectá YouTube.")
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
    data["expires_at"] = int(time.time()) + data.get("expires_in", 3600) - 60
    _TOKEN_FILE.write_text(json.dumps(data, indent=2))


def _is_expired(token_data: dict) -> bool:
    return time.time() >= token_data.get("expires_at", 0)


def _refresh(token_data: dict) -> dict:
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No hay refresh token. Reconectá YouTube.")
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
        },
        timeout=15,
    )
    resp.raise_for_status()
    new_data = resp.json()
    # Google doesn't re-issue refresh_token on refresh — keep the old one
    new_data["refresh_token"] = refresh_token
    _save_token(new_data)
    return new_data
