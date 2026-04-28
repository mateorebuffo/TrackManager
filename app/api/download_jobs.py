"""
Download Jobs API — consumed by the local Download Agent.

GET  /api/download-jobs               — fetch pending jobs (batch of 5)
POST /api/download-jobs/{id}/start    — mark in_progress
POST /api/download-jobs/{id}/complete — report result
POST /api/generate-token              — generate API token for current user
GET  /api/me/token                    — get current API token
GET  /api/download-agent              — download pre-configured agent zip
"""
from __future__ import annotations

import io
import json
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.download_job import DownloadJob, JobStatus
from app.models.review_item import ReviewItem, TrackStatus
from app.models.user import User
from app.services import log_service

router = APIRouter(tags=["download-jobs"])

BATCH_SIZE = 5


# ── Token auth for the agent ─────────────────────────────────────────────────

def get_user_by_token(token: str, db: Session) -> User | None:
    return db.query(User).filter(User.api_token == token).first()


def agent_auth(
    authorization: str | None = None,
    db: Session = Depends(get_db),
) -> User:
    from fastapi import Header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")
    return user


# Accept both session auth (browser) and token auth (agent)
def flexible_auth(
    request_obj=None,
    db: Session = Depends(get_db),
) -> User:
    """Used by token-authenticated agent endpoints."""
    from fastapi import Request
    pass


# ── Token management (browser-side) ─────────────────────────────────────────

@router.post("/api/generate-token")
def generate_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
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
        "muzpa_sess":           us.muzpa_sess          if us else "",
        "deezer_arl":           us.deezer_arl           if us else "",
        "folder_organize_mode": us.folder_organize_mode if us else "none",
        "download_full_eps":    us.download_full_eps    if us else False,
    }


@router.get("/api/download-agent")
def download_agent(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Return a zip with the pre-built agent exe + a pre-configured config.json."""
    exe_path = Path("app/static/agent/TrackManagerAgent.exe")
    if not exe_path.exists():
        raise HTTPException(
            status_code=503,
            detail="El agente aún no está compilado. Ejecutá agent/build.bat primero.",
        )

    cfg = {
        "api_url":      str(request.base_url).rstrip("/"),
        "token":        current_user.api_token or "",
        "organize":     "import_date",
        "poll_seconds": 10,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe_path, "TrackManagerAgent.exe")
        zf.writestr("config.json", json.dumps(cfg, indent=2, ensure_ascii=False))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="TrackManager-Agent.zip"'},
    )


# ── Agent endpoints ───────────────────────────────────────────────────────────

class CompletePayload(BaseModel):
    status: str  # completed | not_found | vinyl_only | failed
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
    from fastapi import Header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    user = get_user_by_token(token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Token inválido")

    jobs = (
        db.query(DownloadJob)
        .filter(
            DownloadJob.user_id == user.id,
            DownloadJob.status == JobStatus.pending,
        )
        .order_by(DownloadJob.created_at)
        .limit(BATCH_SIZE)
        .all()
    )
    return [
        {
            "id":       j.id,
            "query":    j.query,
            "review_id": j.review_id,
        }
        for j in jobs
    ]


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

    job.status = JobStatus(payload.status if payload.status != "completed" else "completed")
    job.last_error = payload.error
    job.updated_at = datetime.now(timezone.utc)
    if payload.status == "completed":
        job.downloaded_at = datetime.now(timezone.utc)

    # Update the review item status
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
