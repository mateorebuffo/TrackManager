"""
Download Jobs API — consumed by the local Download Agent.

GET  /api/download-jobs               — fetch pending jobs (batch of 5)
POST /api/download-jobs/{id}/start    — mark in_progress
POST /api/download-jobs/{id}/complete — report result
POST /api/generate-token              — generate API token for current user
GET  /api/me/token                    — get current API token
GET  /api/me/settings                 — get download credentials for the agent
GET  /api/download-agent              — download pre-configured agent zip
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
    }


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


@router.get("/api/check-bandcamp")
def check_bandcamp(
    q: str,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> dict:
    """Proxy Bandcamp presence check via Brave Search API."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido")
    token = authorization.removeprefix("Bearer ").strip()
    if not get_user_by_token(token, db):
        raise HTTPException(status_code=401, detail="Token inválido")

    from app.config import settings
    if not settings.brave_api_key:
        return {"found": False}

    import re as _re
    import httpx as _httpx
    try:
        parts = q.split(" - ", 1)
        artist = parts[0].strip() if len(parts) == 2 else q
        title  = parts[1].strip() if len(parts) == 2 else q
        # Drop trailing EP/LP/Album/Single so "Lucid Interval EP" → "Lucid Interval"
        clean_title = _re.sub(r"\s+(EP|LP|Album|Single)\s*$", "", title, flags=_re.IGNORECASE).strip()

        resp = _httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": f"site:bandcamp.com {artist} {clean_title}", "count": 5},
            headers={
                "X-Subscription-Token": settings.brave_api_key,
                "Accept": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])

        # Words > 3 chars to skip noise like "the", "dj", "by"
        artist_words = [w for w in _re.sub(r"[^\w\s]", " ", artist).lower().split() if len(w) > 3]
        title_words  = [w for w in _re.sub(r"[^\w\s]", " ", clean_title).lower().split() if len(w) > 3]

        for r in results:
            url = r.get("url", "")
            if "bandcamp.com" not in url or ("/track/" not in url and "/album/" not in url):
                continue
            # Check title only — Bandcamp page titles are "Track, by Artist" or "Artist - Track"
            # Checking description too causes false positives when an artist has other Bandcamp content
            page_title = r.get("title", "").lower()
            if (not artist_words or any(w in page_title for w in artist_words)) and \
               (not title_words  or any(w in page_title for w in title_words)):
                return {"found": True}

        return {"found": False}
    except Exception:
        return {"found": False}


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
