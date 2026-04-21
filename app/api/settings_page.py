"""
Settings page — read and write .env configuration via the UI.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

_ENV_PATH = Path(".env")

_FIELDS = [
    ("SOUNDCLOUD_CLIENT_ID",    "SoundCloud Client ID",     "text",     False),
    ("SOUNDCLOUD_OAUTH_TOKEN",  "SoundCloud OAuth Token",   "password", False),
    ("SPOTIFY_CLIENT_ID",       "Spotify Client ID",        "text",     False),
    ("SPOTIFY_CLIENT_SECRET",   "Spotify Client Secret",    "password", False),
    ("SPOTIFY_REDIRECT_URI",    "Spotify Redirect URI",     "text",     False),
    ("YOUTUBE_CLIENT_ID",       "YouTube Client ID",        "text",     False),
    ("YOUTUBE_CLIENT_SECRET",   "YouTube Client Secret",    "password", False),
    ("YOUTUBE_REDIRECT_URI",    "YouTube Redirect URI",     "text",     False),
    ("MUZPA_SESS",              "Muzpa Session (SESS=...)", "password", False),
    ("DEEZER_ARL",              "Deezer ARL",               "password", False),
    ("DOWNLOAD_DIR",            "Carpeta de descarga",      "text",     False),
    ("DOWNLOAD_FULL_EPS",         "Descargar EPs completos",              "checkbox", False),
    ("ORGANIZE_BY_LIKE_DATE",     "Organizar por fecha de like",          "checkbox", False),
]


def _read_env() -> dict[str, str]:
    if not _ENV_PATH.exists():
        return {}
    values: dict[str, str] = {}
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def _write_env(updates: dict[str, str]) -> None:
    lines: list[str] = []
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()

    written: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                written.add(key)
                continue
        new_lines.append(line)

    # Append keys that weren't already in the file
    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}")

    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    current = _read_env()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "fields": _FIELDS, "current": current},
    )


@router.post("", response_class=HTMLResponse)
async def save_settings(request: Request) -> RedirectResponse:
    form = await request.form()
    updates: dict[str, str] = {}

    for key, _label, field_type, _required in _FIELDS:
        if field_type == "checkbox":
            updates[key] = "true" if form.get(key) == "on" else "false"
        else:
            val = form.get(key, "")
            if val:  # only write non-empty values to avoid blanking secrets
                updates[key] = str(val).strip()

    _write_env(updates)
    return RedirectResponse(url="/settings?saved=1", status_code=303)
