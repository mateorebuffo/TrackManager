"""
Spotify OAuth token management — per-user, stored in UserSettings.spotify_token_json.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import settings

_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SCOPES = "user-library-read playlist-read-private playlist-read-collaborative"


def get_auth_url() -> str:
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": _SCOPES,
    }
    return _AUTHORIZE_URL + "?" + urlencode(params)


def exchange_code(code: str, db: Session, user_id: int) -> None:
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
    _save_token(resp.json(), db, user_id)


def get_valid_access_token(db: Session, user_id: int) -> str:
    token_data = _load_token(db, user_id)
    if not token_data:
        raise RuntimeError("Spotify no está conectado. Autorizá primero.")
    if _is_expired(token_data):
        token_data = _refresh(token_data, db, user_id)
    return token_data["access_token"]


def is_connected(db: Session, user_id: int) -> bool:
    return bool(_load_token(db, user_id))


def disconnect(db: Session, user_id: int) -> None:
    us = _get_user_settings(db, user_id)
    us.spotify_token_json = None
    db.commit()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_user_settings(db: Session, user_id: int):
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=user_id).first()
    if not us:
        us = UserSettings(user_id=user_id)
        db.add(us)
        db.flush()
    return us


def _load_token(db: Session, user_id: int) -> dict | None:
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=user_id).first()
    if not us or not us.spotify_token_json:
        return None
    try:
        return json.loads(us.spotify_token_json)
    except (json.JSONDecodeError, TypeError):
        return None


def _save_token(data: dict, db: Session, user_id: int) -> None:
    data["expires_at"] = int(time.time()) + data.get("expires_in", 3600) - 60
    us = _get_user_settings(db, user_id)
    us.spotify_token_json = json.dumps(data)
    db.commit()


def _is_expired(token_data: dict) -> bool:
    return time.time() >= token_data.get("expires_at", 0)


def _refresh(token_data: dict, db: Session, user_id: int) -> dict:
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
    if "refresh_token" not in new_data:
        new_data["refresh_token"] = refresh_token
    _save_token(new_data, db, user_id)
    return new_data
