"""
Asistente de primeros pasos — guía al usuario nuevo a instalar y configurar el agente.

GET  /primeros-pasos        — las 4 diapositivas
POST /primeros-pasos/listo  — marcar como visto (Finalizar y Saltar usan el mismo)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.user import User

router = APIRouter(tags=["onboarding"])
templates = Jinja2Templates(directory="app/templates")


def is_pending(db: Session, user_id: int) -> bool:
    """¿Todavía no vio el asistente? Lo consulta la raíz para decidir el redirect."""
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=user_id).first()
    return not (us and us.onboarding_done)


@router.get("/primeros-pasos", response_class=HTMLResponse)
def onboarding_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    # Sigue accesible una vez completado: es el link "Ver primeros pasos" de Ajustes.
    from app.api.settings_page import _agent_is_available
    return templates.TemplateResponse(
        "onboarding.html",
        {
            "request": request,
            "api_token": current_user.api_token,
            "agent_available": _agent_is_available(),
        },
    )


@router.post("/primeros-pasos/listo")
def onboarding_done(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    from app.api.settings_page import _get_or_create_settings
    us = _get_or_create_settings(db, current_user.id)
    us.onboarding_done = True
    db.commit()
    return RedirectResponse(url="/tracks/pending", status_code=303)
