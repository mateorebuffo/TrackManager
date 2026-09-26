"""
Settings page — per-user configuration stored in UserSettings table.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel


class _VerifyPayload(BaseModel):
    value: str = ""

logger = logging.getLogger(__name__)
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Annotated

from app.auth_middleware import get_current_user
from app.config import settings
from app.db import get_db
from app.models.user import User
from app.models.user_settings import UserSettings
from app.services import credential_check

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

_FIELDS = [
    ("soundcloud_oauth_token", "SoundCloud OAuth Token",           "password", False),
    ("spotify_client_id",      "Spotify Client ID",                "password", False),
    ("spotify_client_secret",  "Spotify Client Secret",            "password", False),
    ("muzpa_sess",             "Muzpa Session (SESS=...)",          "password", False),
    ("deezer_arl",             "Deezer ARL",                        "password", False),
    ("download_full_eps",      "Descargar EPs completos",           "checkbox", False),
]

# Ahora se conectan desde el agente. Se ocultan con SHOW_LEGACY_SETTINGS en vez de
# borrarse, por si hiciera falta volver a configurarlas desde la web.
_LEGACY_FIELDS = {"soundcloud_oauth_token", "muzpa_sess", "deezer_arl"}


def _active_fields() -> list[tuple]:
    """
    Los campos que se muestran Y se guardan — la misma lista para las dos cosas.

    Si difirieran, guardar pisaría con vacío los campos que no se renderizan: es
    exactamente lo que venía pasando con download_dir y folder_organize_mode, que
    estaban en _FIELDS pero en ninguna plantilla, así que cada "Guardar cambios"
    los borraba. Nadie los leía (el agente usa su propio agent.json), por eso se
    fueron de la lista.
    """
    if settings.show_legacy_settings:
        return _FIELDS
    return [f for f in _FIELDS if f[0] not in _LEGACY_FIELDS]


def _get_or_create_settings(db: Session, user_id: int) -> UserSettings:
    us = db.query(UserSettings).filter_by(user_id=user_id).first()
    if not us:
        us = UserSettings(user_id=user_id)
        db.add(us)
        db.commit()
        db.refresh(us)
    return us


def _agent_is_available() -> bool:
    """Return True if the agent can be downloaded (external URL or local exe present)."""
    if settings.agent_download_url:
        return True
    return Path("app/static/agent/TrackManagerAgent.exe").exists()


@router.get("", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    from app.services import spotify_auth, youtube_auth
    us = _get_or_create_settings(db, current_user.id)
    current = {
        "soundcloud_oauth_token": us.soundcloud_oauth_token or "",
        "spotify_client_id":      us.spotify_client_id or "",
        "spotify_client_secret":  us.spotify_client_secret or "",
        "muzpa_sess":             us.muzpa_sess or "",
        "deezer_arl":             us.deezer_arl or "",
        "download_full_eps":      us.download_full_eps,
    }
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "fields": _active_fields(),
            "show_legacy_settings": settings.show_legacy_settings,
            "current": current,
            "spotify_connected": current_user.is_admin and spotify_auth.is_connected(db, current_user.id),
            "youtube_connected": youtube_auth.is_connected(db, current_user.id),
            "api_token": current_user.api_token,
            "base_url": str(request.base_url).rstrip("/"),
            "agent_available": _agent_is_available(),
            "is_admin": current_user.is_admin,
        },
    )


@router.post("", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    form = await request.form()
    us = _get_or_create_settings(db, current_user.id)

    for key, _label, field_type, _required in _active_fields():
        if field_type == "checkbox":
            setattr(us, key, form.get(key) == "on")
        elif field_type == "radio":
            val = str(form.get(key, "none")).strip()
            setattr(us, key, val)
        elif field_type == "password":
            val = str(form.get(key, "")).strip()
            if val:  # never blank-out a password field accidentally
                setattr(us, key, val)
        else:
            setattr(us, key, str(form.get(key, "")).strip())

    db.commit()

    from app.services import log_service
    log_service.log_event(
        db, "settings_changed", "User settings updated",
        user_id=current_user.id, commit=True,
    )

    next_url = str(form.get("_next", "")).strip()
    allowed = {"/sync/spotify/connect", "/sync/youtube/connect"}
    redirect_to = next_url if next_url in allowed else "/settings?saved=1"
    return RedirectResponse(url=redirect_to, status_code=303)


# Rutas explícitas a propósito: un /verify/{service} dinámico taparía /verify/spotify,
# que se registra más abajo. La lógica vive en services/credential_check.

@router.post("/verify/muzpa")
def verify_muzpa(
    payload: _VerifyPayload,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    ok, msg = credential_check.check_muzpa(payload.value)
    return JSONResponse({"ok": ok, "msg": msg})


@router.post("/verify/deezer")
def verify_deezer(
    payload: _VerifyPayload,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    ok, msg = credential_check.check_deezer(payload.value)
    return JSONResponse({"ok": ok, "msg": msg})


@router.post("/verify/soundcloud")
def verify_soundcloud(
    payload: _VerifyPayload,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    ok, msg = credential_check.check_soundcloud(payload.value)
    return JSONResponse({"ok": ok, "msg": msg})


@router.post("/verify/spotify")
def verify_spotify(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    if not current_user.is_admin:
        return JSONResponse({"ok": False, "msg": "Solo administradores."}, status_code=403)
    from app.services import spotify_auth as sa
    lines: list[str] = []

    # 1. Credenciales configuradas
    try:
        client_id, _ = sa.get_credentials(db, current_user.id)
        lines.append(f"✓ Client ID configurado: {client_id[:8]}…")
    except RuntimeError as e:
        return JSONResponse({"ok": False, "msg": f"✗ Credenciales: {e}"})

    # 2. Token en DB
    try:
        access_token = sa.get_valid_access_token(db, current_user.id)
        lines.append("✓ Token OAuth válido")
    except RuntimeError as e:
        return JSONResponse({"ok": False, "msg": "\n".join(lines) + f"\n✗ Token: {e}"})

    headers = {"Authorization": f"Bearer {access_token}"}

    # 3. GET /v1/me
    try:
        r = httpx.get("https://api.spotify.com/v1/me", headers=headers, timeout=10)
        if r.status_code == 200:
            me = r.json()
            lines.append(f"✓ /v1/me OK — user: {me.get('id')}, plan: {me.get('product', '?')}")
        else:
            lines.append(f"✗ /v1/me → {r.status_code}: {r.text[:200]}")
            return JSONResponse({"ok": False, "msg": "\n".join(lines)})
    except Exception as e:
        lines.append(f"✗ /v1/me → excepción: {e}")
        return JSONResponse({"ok": False, "msg": "\n".join(lines)})

    # 4. GET /v1/me/playlists — tomamos el primero para testear tracks
    first_playlist_id = None
    first_playlist_name = None
    try:
        r = httpx.get("https://api.spotify.com/v1/me/playlists", headers=headers,
                      params={"limit": 5}, timeout=10)
        if r.status_code == 200:
            items = r.json().get("items", [])
            lines.append(f"✓ /v1/me/playlists OK — primeras {len(items)}: {[p['name'] for p in items if p]}")
            first_pl = next((p for p in items if p), None)
            if first_pl:
                first_playlist_id = first_pl["id"]
                first_playlist_name = first_pl["name"]
        else:
            lines.append(f"✗ /v1/me/playlists → {r.status_code}: {r.text[:200]}")
            return JSONResponse({"ok": False, "msg": "\n".join(lines)})
    except Exception as e:
        lines.append(f"✗ /v1/me/playlists → excepción: {e}")
        return JSONResponse({"ok": False, "msg": "\n".join(lines)})

    # 5. GET tracks de la primera playlist de la lista (sin fields, request simple)
    if first_playlist_id:
        try:
            r = httpx.get(
                f"https://api.spotify.com/v1/playlists/{first_playlist_id}/tracks",
                headers=headers, params={"limit": 1}, timeout=10,
            )
            if r.status_code == 200:
                total = r.json().get("total", "?")
                lines.append(f"✓ tracks de '{first_playlist_name}' ({first_playlist_id}) — total: {total}")
            else:
                lines.append(f"✗ tracks de '{first_playlist_name}' ({first_playlist_id}) → {r.status_code}: {r.text[:300]}")
        except Exception as e:
            lines.append(f"✗ tracks de '{first_playlist_name}' → excepción: {e}")

    # 6. GET tracks de playlist conocida pública (para comparar)
    test_public_id = "2lsR6oi8AyGYH4M6jDXBnG"
    try:
        r = httpx.get(
            f"https://api.spotify.com/v1/playlists/{test_public_id}/tracks",
            headers=headers, params={"limit": 1}, timeout=10,
        )
        if r.status_code == 200:
            total = r.json().get("total", "?")
            lines.append(f"✓ tracks de playlist pública de prueba ({test_public_id}) — total: {total}")
        else:
            lines.append(f"✗ tracks de playlist pública de prueba ({test_public_id}) → {r.status_code}: {r.text[:300]}")
    except Exception as e:
        lines.append(f"✗ tracks playlist pública de prueba → excepción: {e}")

    # 7. GET playlist sin token (acceso anónimo, para ver si es problema del app)
    try:
        r = httpx.get(
            f"https://api.spotify.com/v1/playlists/{test_public_id}",
            headers=headers, timeout=10,
        )
        lines.append(f"— GET /v1/playlists/{test_public_id} (metadata) → {r.status_code}")
    except Exception as e:
        lines.append(f"✗ metadata playlist → excepción: {e}")

    return JSONResponse({"ok": True, "msg": "\n".join(lines)})
