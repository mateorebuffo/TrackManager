"""
Authentication middleware and FastAPI dependency.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.db import SessionLocal, get_db
from app.models.user import User
from app.services.auth import decode_session_token, get_user_by_id, has_any_user


def get_user_by_api_token(token: str, db: Session) -> Optional[User]:
    """Vive acá y no en download_jobs.py para no importar una API desde el middleware."""
    return db.query(User).filter(User.api_token == token).first()

_COOKIE = "mc_session"
_PUBLIC = {"/login", "/setup", "/favicon.ico", "/health", "/para-que-sirve"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Always allow public paths and static assets
        if path in _PUBLIC or path.startswith("/static"):
            return await call_next(request)

        # Bearer token auth on API routes (used by the Download Agent).
        # El token se valida ACÁ, no sólo en cada handler: antes alcanzaba con que
        # el header empezara con "Bearer " para pasar, así que un endpoint /api/*
        # que se olvidara de chequearlo quedaba abierto. Ahora falla cerrado.
        authorization = request.headers.get("authorization", "")
        if path.startswith("/api/") and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()
            db = SessionLocal()
            try:
                user = get_user_by_api_token(token, db) if token else None
                if not user:
                    return JSONResponse({"detail": "Token inválido"}, status_code=401)
                request.state.user = user
            finally:
                db.close()
            return await call_next(request)

        db = SessionLocal()
        try:
            # First-run: no users yet → redirect to setup
            if not has_any_user(db):
                if path != "/setup":
                    return RedirectResponse(url="/setup", status_code=302)
                return await call_next(request)

            # Check session cookie
            token = request.cookies.get(_COOKIE)
            user_id = decode_session_token(token) if token else None
            user = get_user_by_id(user_id, db) if user_id else None

            if not user:
                return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)

            # Attach user to request state for downstream use
            request.state.user = user
        finally:
            db.close()

        return await call_next(request)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency — returns the authenticated user (middleware guarantees it exists)."""
    user = getattr(request.state, "user", None)
    if user:
        # Re-attach to this session to avoid DetachedInstanceError
        return db.merge(user)
    # Fallback: read from cookie (shouldn't normally be needed)
    token = request.cookies.get(_COOKIE)
    user_id = decode_session_token(token) if token else None
    if user_id:
        u = get_user_by_id(user_id, db)
        if u:
            return u
    raise RuntimeError("Unauthenticated request reached endpoint")


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin required")
    return user
