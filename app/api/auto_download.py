"""
Auto-download API endpoints.

POST /auto-download/all/form     — enqueue all queued tracks as download jobs
POST /auto-download/{id}/form    — enqueue a single track as a download job
GET  /auto-download/jobs         — show current job queue status (HTML)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.download_job import DownloadJob, JobStatus
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.models.user import User
from app.services import log_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-download", tags=["auto-download"])
templates = Jinja2Templates(directory="app/templates")


def _make_query(nt: NormalizedTrack) -> str:
    return nt.search_query or f"{nt.normalized_artist or ''} {nt.normalized_title or ''}".strip()


def _enqueue(db: Session, review_id: int, query: str, user_id: int) -> DownloadJob:
    """Create a pending download job (idempotent — skip if one already exists)."""
    existing = db.query(DownloadJob).filter(
        DownloadJob.review_id == review_id,
        DownloadJob.status.in_([JobStatus.pending, JobStatus.in_progress]),
    ).first()
    if existing:
        return existing

    job = DownloadJob(user_id=user_id, review_id=review_id, query=query)
    db.add(job)
    db.commit()
    db.refresh(job)
    log_service.log_event(
        db, "download_queued", f"Enqueued: {query}",
        user_id=user_id, track_id=review_id,
        context={"query": query}, commit=True,
    )
    return job


# ── Bulk enqueue ──────────────────────────────────────────────────────────────

@router.post("/all/form")
def auto_download_all_start(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    """Enqueue all 'queued' tracks as download jobs."""
    items = (
        db.query(ReviewItem)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.status == TrackStatus.queued,
            SourceTrack.user_id == current_user.id,
        )
        .options(
            contains_eager(ReviewItem.normalized_track)
            .contains_eager(NormalizedTrack.source_track)
        )
        .all()
    )

    if not items:
        return RedirectResponse(url="/tracks/download-queue?empty=1", status_code=303)

    count = 0
    for item in items:
        if item.normalized_track:
            _enqueue(db, item.id, _make_query(item.normalized_track), current_user.id)
            count += 1

    return RedirectResponse(url=f"/auto-download/jobs?enqueued={count}", status_code=303)


# ── Single track enqueue ──────────────────────────────────────────────────────

@router.post("/{review_id}/form")
def auto_download_one(
    review_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    item = (
        db.query(ReviewItem)
        .options(joinedload(ReviewItem.normalized_track))
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.id == review_id,
            SourceTrack.user_id == current_user.id,
        )
        .first()
    )
    if item and item.normalized_track:
        _enqueue(db, item.id, _make_query(item.normalized_track), current_user.id)

    referer = request.headers.get("referer", "/tracks/download-queue")
    return RedirectResponse(url=referer, status_code=303)


# ── Job queue status page ─────────────────────────────────────────────────────

@router.get("/jobs", response_class=HTMLResponse)
def jobs_status(
    request: Request,
    enqueued: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    from app.models.download_job import JobStatus as JS
    from sqlalchemy import func

    counts = dict(
        db.query(DownloadJob.status, func.count())
        .filter(DownloadJob.user_id == current_user.id)
        .group_by(DownloadJob.status)
        .all()
    )
    pending_count  = counts.get(JS.pending,     0)
    progress_count = counts.get(JS.in_progress, 0)

    jobs = (
        db.query(DownloadJob)
        .filter(DownloadJob.user_id == current_user.id)
        .order_by(DownloadJob.created_at.desc())
        .limit(100)
        .all()
    )
    return templates.TemplateResponse(
        "download_jobs.html",
        {
            "request": request,
            "jobs": jobs,
            "enqueued": enqueued,
            "token": current_user.api_token,
            "pending_count":      pending_count,
            "progress_count":     progress_count,
            "completed_count":    counts.get(JS.completed,     0),
            "not_found_count":    counts.get(JS.not_found,     0),
            "vinyl_only_count":   counts.get(JS.vinyl_only,    0),
            "bandcamp_only_count": counts.get(JS.bandcamp_only, 0),
            "failed_count":       counts.get(JS.failed,        0),
        },
    )
