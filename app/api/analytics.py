"""
Analítica del admin: salud del sistema y alumnos con problemas.

GET  /analitica         — el panel (prueba los servicios si la última prueba es vieja)
POST /analitica/probar  — "Probar ahora", como mucho una vez cada 5 minutos
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.user import User
from app.services import health
from app.utils.rate_limit import UserRateLimiter

router = APIRouter(tags=["analytics"])
templates = Jinja2Templates(directory="app/templates")

_probe_limiter = UserRateLimiter(calls=1, window=300)


def _require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")


@router.get("/analitica", response_class=HTMLResponse)
def analytics_page(
    request: Request,
    probado: int | None = Query(default=None),
    espera: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    _require_admin(current_user)
    # Sin tarea programada: la prueba automática corre al abrir el panel, así
    # quedan una o dos por día y nunca un golpe constante a los servicios.
    if health.is_stale(db):
        health.run_checks(db)
    return templates.TemplateResponse(
        "analytics.html",
        {"request": request, "probado": probado, "espera": espera, **health.dashboard(db)},
    )


@router.post("/analitica/probar")
def probe_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    _require_admin(current_user)
    if not _probe_limiter.acquire(current_user.id):
        return RedirectResponse(url="/analitica?espera=1", status_code=303)
    health.run_checks(db)
    return RedirectResponse(url="/analitica?probado=1", status_code=303)
