"""
Download Jobs API — consumed by the local Download Agent.

GET  /api/download-jobs               — fetch pending jobs (batch of 5)
POST /api/download-jobs/{id}/start    — mark in_progress
POST /api/download-jobs/{id}/complete — report result
POST /api/generate-token              — generate API token for current user
GET  /api/me/token                    — get current API token
GET  /api/me/settings                 — get download credentials for the agent
GET  /api/me/credentials              — validity status of the 3 service credentials
POST /api/me/credentials              — validate + store a credential captured by the agent
GET  /api/download-agent              — redirect to the agent download URL
"""
from __future__ import annotations

import secrets

from app.utils.rate_limit import UserRateLimiter

_token_limiter = UserRateLimiter(calls=5, window=3600)  # 5 rotaciones por hora
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.download_job import DownloadJob, JobStatus
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.models.user import User
from app.services import log_service

router = APIRouter(tags=["download-jobs"])

BATCH_SIZE = 5


# ── Token auth for the agent ─────────────────────────────────────────────────

def get_user_by_token(token: str, db: Session) -> User | None:
    return db.query(User).filter(User.api_token == token).first()


def agent_auth(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    return user


# ── Token management (browser-side) ─────────────────────────────────────────

@router.post("/api/generate-token")
def generate_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    if not _token_limiter.acquire(current_user.id):
        raise HTTPException(status_code=429, detail="Límite de rotaciones alcanzado. Intentá en una hora.")
    current_user.api_token = secrets.token_hex(32)
    db.commit()
    return {"token": current_user.api_token}


@router.get("/api/me/token")
def get_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return {"token": current_user.api_token}


@router.get("/api/me/settings")
def get_agent_settings(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> dict:
    """Return the user's download credentials for the local agent."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")

    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=user.id).first()
    return {
        "muzpa_sess":        us.muzpa_sess        if us else "",
        "deezer_arl":        us.deezer_arl         if us else "",
        "download_full_eps": us.download_full_eps  if us else False,
        # El agente no usa el token de SoundCloud (lo consume /sync/soundcloud en el
        # server), pero sí necesita saber si está conectado para pintar el estado.
        "soundcloud_connected": bool(us.soundcloud_oauth_token) if us else False,
    }


# ── Credenciales capturadas por el agente ────────────────────────────────────

class CredentialPayload(BaseModel):
    service: str  # muzpa | deezer | soundcloud
    value: str


@router.get("/api/me/credentials")
def get_credential_status(
    user: User = Depends(agent_auth),
    db: Session = Depends(get_db),
) -> dict:
    """Estado de las 3 credenciales. Corre los checks contra cada servicio."""
    from app.models.user_settings import UserSettings
    from app.services import credential_check

    us = db.query(UserSettings).filter_by(user_id=user.id).first()
    out = {}
    for service, (column, _fn) in credential_check.SERVICES.items():
        value = getattr(us, column, "") or "" if us else ""
        if not value:
            out[service] = {"ok": False, "connected": False, "msg": "Sin conectar."}
            continue
        ok, msg = credential_check.check(service, value)
        out[service] = {"ok": ok, "connected": True, "msg": msg}

    # YouTube es OAuth, no una cookie: no hay valor que validar, se pregunta al
    # servicio de tokens (que además refresca solo si hace falta).
    from app.services import youtube_auth
    if not youtube_auth.is_connected(db, user.id):
        out["youtube"] = {"ok": False, "connected": False, "msg": "Sin conectar."}
    else:
        try:
            youtube_auth.get_valid_access_token(db, user.id)
            out["youtube"] = {"ok": True, "connected": True, "msg": credential_check.CONNECTED}
        except RuntimeError as e:
            out["youtube"] = {"ok": False, "connected": True, "msg": str(e)}
    return out


@router.post("/api/me/credentials")
def save_credential(
    payload: CredentialPayload,
    user: User = Depends(agent_auth),
    db: Session = Depends(get_db),
) -> dict:
    """Validar una credencial capturada por el agente y guardarla solo si pasa."""
    from app.services import credential_check

    if payload.service == "youtube":
        # OAuth: el token lo guarda el callback del server, no hay valor que postear.
        raise HTTPException(status_code=400, detail="YouTube se conecta por OAuth, no por cookie")
    if payload.service not in credential_check.SERVICES:
        raise HTTPException(status_code=400, detail="Servicio desconocido")

    value = credential_check.normalize(payload.service, payload.value)
    ok, msg = credential_check.check(payload.service, value)
    if not ok:
        # No pisar una credencial buena con una que no valida.
        return {"ok": False, "msg": msg}

    from app.api.settings_page import _get_or_create_settings
    us = _get_or_create_settings(db, user.id)
    column, _fn = credential_check.SERVICES[payload.service]
    setattr(us, column, value)
    db.commit()

    log_service.log_event(
        db, "settings_changed", f"Credencial de {payload.service} conectada desde el agente",
        user_id=user.id, commit=True,
    )
    return {"ok": True, "msg": msg}


@router.get("/api/download-agent", response_model=None)
def download_agent(
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    from app.config import settings
    if not settings.agent_download_url:
        raise HTTPException(status_code=503, detail="AGENT_DOWNLOAD_URL no configurado.")
    return RedirectResponse(url=settings.agent_download_url, status_code=302)


# ── Agent endpoints ───────────────────────────────────────────────────────────

class CompletePayload(BaseModel):
    status: str  # completed | not_found | vinyl_only | bandcamp_only | failed
    error: str | None = None


def _get_job_for_agent(job_id: int, db: Session, authorization: str | None) -> tuple[DownloadJob, User]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    job = db.query(DownloadJob).filter(
        DownloadJob.id == job_id,
        DownloadJob.user_id == user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job, user


@router.get("/api/download-jobs")
def get_pending_jobs(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return pending jobs for the authenticated agent."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")

    from app.api.auto_download import _cancel_stale_jobs
    _cancel_stale_jobs(db, user.id)

    jobs = (
        db.query(DownloadJob)
        .filter(
            DownloadJob.user_id == user.id,
            DownloadJob.status == JobStatus.pending,
        )
        .options(
            joinedload(DownloadJob.review_item)
            .joinedload(ReviewItem.normalized_track)
            .joinedload(NormalizedTrack.source_track)
        )
        .order_by(DownloadJob.created_at)
        .limit(BATCH_SIZE)
        .all()
    )

    result = []
    for j in jobs:
        liked_at = None
        collected_at = None
        try:
            st = j.review_item.normalized_track.source_track
            if st:
                if st.liked_at:
                    liked_at = st.liked_at.strftime("%Y-%m-%d")
                if st.collected_at:
                    collected_at = st.collected_at.strftime("%Y-%m-%d")
        except Exception:
            pass
        result.append({
            "id":           j.id,
            "query":        j.query,
            "review_id":    j.review_id,
            "liked_at":     liked_at,
            "collected_at": collected_at,
        })
    return result


@router.get("/api/download-jobs/stats")
def get_jobs_stats(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> dict:
    """Return pending + in_progress counts for the authenticated agent."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")

    counts = dict(
        db.query(DownloadJob.status, func.count())
        .filter(DownloadJob.user_id == user.id)
        .filter(DownloadJob.status.in_([JobStatus.pending, JobStatus.in_progress]))
        .group_by(DownloadJob.status)
        .all()
    )
    return {
        "pending":     counts.get(JobStatus.pending, 0),
        "in_progress": counts.get(JobStatus.in_progress, 0),
    }


@router.post("/api/download-jobs/cancel-all-pending")
def cancel_all_pending(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Cancel all pending and in-progress jobs for the current user."""
    count = (
        db.query(DownloadJob)
        .filter(
            DownloadJob.user_id == current_user.id,
            DownloadJob.status.in_([JobStatus.pending, JobStatus.in_progress]),
        )
        .update(
            {"status": JobStatus.cancelled, "updated_at": datetime.now(timezone.utc)},
            synchronize_session=False,
        )
    )
    db.commit()
    return {"cancelled": count}


@router.post("/api/download-jobs/reset-stuck")
def reset_stuck_jobs(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> dict:
    """Reset in_progress jobs back to pending (called by agent on startup)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")

    count = (
        db.query(DownloadJob)
        .filter(DownloadJob.user_id == user.id, DownloadJob.status == JobStatus.in_progress)
        .update({"status": JobStatus.pending, "updated_at": datetime.now(timezone.utc)})
    )
    db.commit()
    return {"reset": count}


@router.post("/api/download-jobs/{job_id}/start")
def start_job(
    job_id: int,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> dict:
    job, user = _get_job_for_agent(job_id, db, authorization)
    job.status = JobStatus.in_progress
    job.attempt_count += 1
    job.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


# Bandcamp detection disabled — no reliable API found yet.
# To re-enable: uncomment this endpoint and restore bandcamp_check in agent/download/orchestrator.py
#
# @router.get("/api/check-bandcamp")
# def check_bandcamp(q, authorization, db) -> dict:
#     ... (full implementation in git history)


@router.post("/api/download-jobs/{job_id}/complete")
def complete_job(
    job_id: int,
    payload: CompletePayload,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> dict:
    job, user = _get_job_for_agent(job_id, db, authorization)

    valid = {"completed", "not_found", "vinyl_only", "bandcamp_only", "failed"}
    if payload.status not in valid:
        raise HTTPException(status_code=422, detail=f"status debe ser uno de {valid}")

    job.status = JobStatus(payload.status)
    job.last_error = payload.error
    job.updated_at = datetime.now(timezone.utc)
    if payload.status == "completed":
        job.downloaded_at = datetime.now(timezone.utc)

    status_map = {
        "completed":     TrackStatus.downloaded,
        "not_found":     TrackStatus.not_found,
        "vinyl_only":    TrackStatus.vinyl_only,
        "bandcamp_only": TrackStatus.bandcamp_only,
        "failed":        TrackStatus.not_found,
    }
    item = db.query(ReviewItem).filter(ReviewItem.id == job.review_id).first()
    if item:
        item.status = status_map[payload.status]

    db.commit()

    log_service.log_event(
        db,
        "download_completed" if payload.status == "completed" else f"download_{payload.status}",
        f"Agent reported: {payload.status} for job #{job_id}",
        user_id=user.id,
        track_id=job.review_id,
        context={"query": job.query, "error": payload.error},
        commit=True,
    )

    return {"ok": True}
