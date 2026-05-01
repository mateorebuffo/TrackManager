"""
Auth routes: login, logout, setup (first-run), user management (admin).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Annotated

# ── Login rate limiting (in-memory, per IP) ──────────────────────────────────
_failed: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()
_MAX_ATTEMPTS = 10
_WINDOW = 15 * 60  # 15 minutes


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    with _lock:
        _failed[ip] = [t for t in _failed[ip] if now - t < _WINDOW]
        return len(_failed[ip]) >= _MAX_ATTEMPTS


def _record_failure(ip: str) -> None:
    with _lock:
        _failed[ip].append(time.time())

from app.auth_middleware import get_current_user, require_admin
from app.db import get_db
from app.models.user import User
from app.services.auth import (
    authenticate,
    create_user,
    has_any_user,
    hash_password,
    get_user_by_username,
)

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")
_COOKIE = "mc_session"


def _safe_next(next_url: str) -> str:
    """Only allow same-origin relative redirects — prevents open-redirect attacks."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


# ── Login / Logout ───────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/") -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": None})


@router.post("/login", include_in_schema=False)
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
    db: Session = Depends(get_db),
) -> Response:
    ip = _client_ip(request)
    if _is_rate_limited(ip):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Demasiados intentos fallidos. Esperá 15 minutos."},
            status_code=429,
        )
    from app.services.auth import authenticate, make_session_token
    user = authenticate(username, password, db)
    if not user:
        _record_failure(ip)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Usuario o contraseña incorrectos."},
            status_code=401,
        )
    token = make_session_token(user.id)
    response = RedirectResponse(url=_safe_next(next), status_code=303)
    is_secure = request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        _COOKIE, token,
        httponly=True, samesite="lax",
        max_age=60 * 60 * 24 * 30,
        secure=is_secure,
    )
    return response


@router.post("/logout", include_in_schema=False)
def logout() -> Response:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(_COOKIE)
    return response


# ── First-run setup ──────────────────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse, include_in_schema=False)
def setup_page(request: Request, db: Session = Depends(get_db)) -> Response:
    if has_any_user(db):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("setup.html", {"request": request, "error": None})


@router.post("/setup", include_in_schema=False)
def setup(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password2: Annotated[str, Form()],
    db: Session = Depends(get_db),
) -> Response:
    if has_any_user(db):
        return RedirectResponse(url="/", status_code=302)
    if password != password2:
        return templates.TemplateResponse(
            "setup.html", {"request": request, "error": "Las contraseñas no coinciden."}, status_code=400
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            "setup.html", {"request": request, "error": "La contraseña debe tener al menos 6 caracteres."}, status_code=400
        )
    create_user(username, password, is_admin=True, db=db)
    return RedirectResponse(url="/login", status_code=303)


# ── User management (admin only) ─────────────────────────────────────────────

@router.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
def users_page(request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(require_admin)) -> HTMLResponse:
    users = db.query(User).order_by(User.created_at).all()
    return templates.TemplateResponse(
        "admin_users.html",
        {"request": request, "users": users, "current_user": current_user, "error": None, "success": None},
    )


@router.post("/admin/users/create", include_in_schema=False)
def create_user_admin(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    is_admin: Annotated[str, Form()] = "off",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Response:
    existing = get_user_by_username(username, db)
    users = db.query(User).order_by(User.created_at).all()
    if existing:
        return templates.TemplateResponse(
            "admin_users.html",
            {"request": request, "users": users, "current_user": current_user,
             "error": f"El usuario '{username}' ya existe.", "success": None},
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            "admin_users.html",
            {"request": request, "users": users, "current_user": current_user,
             "error": "La contraseña debe tener al menos 6 caracteres.", "success": None},
            status_code=400,
        )
    create_user(username, password, is_admin=(is_admin == "on"), db=db)
    return RedirectResponse(url="/admin/users?created=1", status_code=303)


@router.post("/admin/users/{user_id}/delete", include_in_schema=False)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Response:
    if user_id == current_user.id:
        return RedirectResponse(url="/admin/users?error=self", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        db.delete(user)
        db.commit()
    return RedirectResponse(url="/admin/users?deleted=1", status_code=303)


@router.post("/admin/users/{user_id}/reset-password", include_in_schema=False)
def reset_password(
    user_id: int,
    new_password: Annotated[str, Form()],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Response:
    user = db.query(User).filter(User.id == user_id).first()
    if user and len(new_password) >= 6:
        user.hashed_password = hash_password(new_password)
        db.commit()
    return RedirectResponse(url="/admin/users?reset=1", status_code=303)
