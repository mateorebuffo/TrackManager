"""
Descubrir: ranking de productores y sellos que bajó el alumno, y recomendaciones.

GET /descubrir                  — los rankings (salen de la base, cargan al toque)
GET /descubrir/tracks           — lo que bajó de un productor (?productor=) o sello (?sello=)
GET /descubrir/recomendaciones  — parcial HTML (?nuevas=1: no repetir lo ya sugerido) que la página pide aparte: tarda
                                  unos segundos (Cosine Club + Discogs)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.user import User
from app.services import discovery
from app.utils.rate_limit import UserRateLimiter

router = APIRouter(tags=["discover"])
templates = Jinja2Templates(directory="app/templates")

# Cosine Club da 120 consultas por minuto para toda la app.
_refresh_limiter = UserRateLimiter(calls=1, window=15)


@router.get("/descubrir", response_class=HTMLResponse)
def discover_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "discover.html", {"request": request, **discovery.rankings(db, current_user.id)},
    )


@router.get("/descubrir/tracks", response_class=HTMLResponse)
def tracks_page(
    request: Request,
    productor: str | None = Query(default=None, max_length=255),
    sello: str | None = Query(default=None, max_length=255),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not (productor or sello):
        return RedirectResponse(url="/descubrir", status_code=302)
    return templates.TemplateResponse("discover_tracks.html", {
        "request": request,
        "kind": "Productor" if productor else "Sello",
        "name": productor or sello,
        "tracks": discovery.tracks_of(db, current_user.id, producer=productor,
                                      label=None if productor else sello),
    })


@router.get("/descubrir/recomendaciones", response_class=HTMLResponse)
def recommendations(
    request: Request,
    nuevas: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    wait = nuevas and not _refresh_limiter.acquire(current_user.id)
    recs = discovery.recommend(db, current_user.id, fresh=nuevas and not wait)
    return templates.TemplateResponse(
        "discover_recs.html", {"request": request, "wait": wait, **recs},
    )
