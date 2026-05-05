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

    return RedirectResponse(url="/settings?saved=1", status_code=303)


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
