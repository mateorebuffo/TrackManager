"""
Descubrir: ranking de productores y sellos que bajó el alumno, y recomendaciones.

GET /descubrir                  — los rankings (salen de la base, cargan al toque)
GET /descubrir/recomendaciones  — parcial HTML que la página pide aparte: tarda
                                  unos segundos (Cosine Club + Discogs)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.user import User
from app.services import discovery

router = APIRouter(tags=["discover"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/descubrir", response_class=HTMLResponse)
def discover_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "discover.html", {"request": request, **discovery.rankings(db, current_user.id)},
    )


@router.get("/descubrir/recomendaciones", response_class=HTMLResponse)
def recommendations(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        "discover_recs.html", {"request": request, **discovery.recommend(db, current_user.id)},
    )
