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

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

_FIELDS = [
    ("soundcloud_oauth_token", "SoundCloud OAuth Token",           "password", False),
    ("spotify_client_id",      "Spotify Client ID",                "password", False),
    ("spotify_client_secret",  "Spotify Client Secret",            "password", False),
    ("spotify_sp_dc",          "Spotify Cookie (sp_dc)",           "password", False),
    ("muzpa_sess",             "Muzpa Session (SESS=...)",          "password", False),
    ("deezer_arl",             "Deezer ARL",                        "password", False),
    ("download_dir",           "Carpeta de descarga",               "text",     False),
    ("download_full_eps",      "Descargar EPs completos",           "checkbox", False),
    ("folder_organize_mode",   "Organización de carpetas",          "radio",    False),
]


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
        "spotify_sp_dc":          us.spotify_sp_dc or "",
        "muzpa_sess":             us.muzpa_sess or "",
        "deezer_arl":             us.deezer_arl or "",
        "download_dir":           us.download_dir or "",
        "download_full_eps":      us.download_full_eps,
        "folder_organize_mode":   us.folder_organize_mode or "none",
    }
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "fields": _FIELDS,
            "current": current,
            "spotify_connected": spotify_auth.is_connected(db, current_user.id),
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

    for key, _label, field_type, _required in _FIELDS:
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


@router.post("/verify/muzpa")
def verify_muzpa(
    payload: _VerifyPayload,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    sess = payload.value.strip()
    if not sess:
        return JSONResponse({"ok": False, "msg": "No hay SESS configurado."})
    try:
        resp = httpx.get(
            "https://srv.muzpa.com/a/ms/media/search",
            params={"q": "test", "limit": "1"},
            cookies={"SESS": sess},
            headers={"User-Agent": "TrackManager/1.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            return JSONResponse({"ok": True, "msg": "Credencial válida."})
        elif resp.status_code in (401, 403):
            return JSONResponse({"ok": False, "msg": "Sesión expirada o inválida. Actualizá el token."})
        else:
            return JSONResponse({"ok": False, "msg": f"Respuesta inesperada: {resp.status_code}."})
    except Exception as e:
        logger.exception("Muzpa verify error")
        return JSONResponse({"ok": False, "msg": f"Error de conexión: {e}"})


@router.post("/verify/deezer")
def verify_deezer(
    payload: _VerifyPayload,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    arl = payload.value.strip()
    if not arl:
        return JSONResponse({"ok": False, "msg": "No hay ARL configurado."})
    try:
        resp = httpx.get(
            "https://www.deezer.com/ajax/gw-light.php",
            params={"method": "deezer.getUserData", "input": "3",
                    "api_version": "1.0", "api_token": "null"},
            cookies={"arl": arl},
            headers={"User-Agent": "TrackManager/1.0"},
            timeout=10,
        )
        data = resp.json()
        user_id = data.get("results", {}).get("USER", {}).get("USER_ID", 0)
        if user_id and int(user_id) > 0:
            email = data.get("results", {}).get("USER", {}).get("EMAIL", "")
            msg = f"Credencial válida.{' (' + email + ')' if email else ''}"
            return JSONResponse({"ok": True, "msg": msg})
        else:
            return JSONResponse({"ok": False, "msg": "ARL expirado o inválido. Actualizá el ARL desde tu navegador."})
    except Exception as e:
        logger.exception("Deezer verify error")
        return JSONResponse({"ok": False, "msg": f"Error de conexión: {e}"})


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


@router.post("/verify/spotify-cookie")
def verify_spotify_cookie(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Verify the sp_dc cookie by exchanging it for a Spotify access token."""
    if current_user.is_admin:
        return JSONResponse({"ok": False, "msg": "Los administradores usan OAuth, no sp_dc."})
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    sp_dc = (us.spotify_sp_dc or "").strip() if us else ""
    if not sp_dc:
        return JSONResponse({"ok": False, "msg": "No hay cookie sp_dc guardada."})
    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://open.spotify.com/",
        "Origin": "https://open.spotify.com",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    try:
        resp = httpx.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            cookies={"sp_dc": sp_dc},
            headers=_headers,
            timeout=10,
            follow_redirects=True,
        )
        try:
            data = resp.json()
        except Exception:
            return JSONResponse({"ok": False, "msg": f"Respuesta inesperada de Spotify (HTTP {resp.status_code}). La cookie puede ser inválida."})
        if data.get("isAnonymous") is True:
            return JSONResponse({"ok": False, "msg": "Cookie sp_dc inválida o expirada. Copiala de nuevo desde tu browser."})
        token = data.get("accessToken")
        if not token:
            return JSONResponse({"ok": False, "msg": f"Respuesta inesperada de Spotify: {resp.text[:200]}"})
        # Quick validation: fetch user profile
        me = httpx.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if me.status_code == 200:
            user_id = me.json().get("id", "?")
            return JSONResponse({"ok": True, "msg": f"Cookie válida — cuenta: {user_id}"})
        return JSONResponse({"ok": True, "msg": "Cookie válida (token obtenido correctamente)"})
    except Exception as e:
        logger.exception("Error verifying sp_dc cookie")
        return JSONResponse({"ok": False, "msg": f"Error de conexión: {e}"})
