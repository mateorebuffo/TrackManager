"""
Settings page — per-user configuration stored in UserSettings table.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Annotated

from app.auth_middleware import get_current_user
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
    ("organize_by_like_date",  "Organizar por fecha de like",       "checkbox", False),
]


def _get_or_create_settings(db: Session, user_id: int) -> UserSettings:
    us = db.query(UserSettings).filter_by(user_id=user_id).first()
    if not us:
        us = UserSettings(user_id=user_id)
        db.add(us)
        db.commit()
        db.refresh(us)
    return us


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
        "organize_by_like_date":  us.organize_by_like_date,
    }
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "fields": _FIELDS,
            "current": current,
            "spotify_connected": spotify_auth.is_connected(db, current_user.id),
            "youtube_connected": youtube_auth.is_connected(db, current_user.id),
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
        elif field_type == "password":
            val = str(form.get(key, "")).strip()
            if val:  # never blank-out a password field accidentally
                setattr(us, key, val)
        else:
            setattr(us, key, str(form.get(key, "")).strip())

    db.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=303)
