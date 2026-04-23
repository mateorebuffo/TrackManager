"""
Debug and internal visibility routes.

GET  /debug/events                 — recent app events (admin only)
GET  /debug/track/{review_id}      — track history + related events
GET  /debug/reports                — user reports (own; admin sees all)
POST /debug/reports                — submit a problem report
POST /debug/reports/{id}/status    — update report status (admin only)
POST /debug/cleanup                — run log retention cleanup (admin only)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.app_event import AppEvent
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem
from app.models.source_track import SourceTrack
from app.models.track_history import TrackHistory
from app.models.user import User
from app.models.user_report import REPORT_CATEGORIES, ReportStatus, UserReport
from app.services import log_service
from app.services.log_cleanup import run_cleanup

router = APIRouter(prefix="/debug", tags=["debug"])
templates = Jinja2Templates(directory="app/templates")


def _require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")


# ── App Events ────────────────────────────────────────────────────────────────

@router.get("/events", response_class=HTMLResponse)
def debug_events(
    request: Request,
    level: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    cleanup: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    _require_admin(current_user)

    q = db.query(AppEvent)
    if level:
        q = q.filter(AppEvent.level == level)
    if event_type:
        q = q.filter(AppEvent.event_type == event_type)
    events = q.order_by(AppEvent.created_at.desc()).limit(limit).all()

    event_types = [
        row[0]
        for row in db.query(AppEvent.event_type).distinct().order_by(AppEvent.event_type).all()
    ]

    return templates.TemplateResponse(
        "debug_events.html",
        {
            "request": request,
            "events": events,
            "event_types": event_types,
            "active_level": level or "",
            "active_type": event_type or "",
            "limit": limit,
            "cleanup_deleted": cleanup,
        },
    )


# ── Track History ─────────────────────────────────────────────────────────────

@router.get("/track/{review_id}", response_class=HTMLResponse)
def debug_track_history(
    review_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    item = (
        db.query(ReviewItem)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(ReviewItem.id == review_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Track not found")

    st = item.normalized_track.source_track if item.normalized_track else None
    if not current_user.is_admin and (not st or st.user_id != current_user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    history = (
        db.query(TrackHistory)
        .filter(TrackHistory.track_id == review_id)
        .order_by(TrackHistory.created_at.asc())
        .all()
    )
    events = (
        db.query(AppEvent)
        .filter(AppEvent.track_id == review_id)
        .order_by(AppEvent.created_at.asc())
        .all()
    )

    nt = item.normalized_track
    if nt:
        a = nt.normalized_artist or ""
        t = nt.normalized_title or ""
        track_label = f"{a} — {t}" if a else t
    else:
        track_label = f"Review #{review_id}"

    return templates.TemplateResponse(
        "debug_track_history.html",
        {
            "request": request,
            "review_id": review_id,
            "track_label": track_label,
            "history": history,
            "events": events,
            "item": item,
            "categories": REPORT_CATEGORIES,
        },
    )


# ── User Reports ──────────────────────────────────────────────────────────────

@router.get("/reports", response_class=HTMLResponse)
def debug_reports(
    request: Request,
    status: str | None = Query(default=None),
    submitted: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    q = db.query(UserReport)
    if not current_user.is_admin:
        q = q.filter(UserReport.user_id == current_user.id)
    if status:
        q = q.filter(UserReport.status == status)
    reports = q.order_by(UserReport.created_at.desc()).limit(200).all()

    return templates.TemplateResponse(
        "debug_reports.html",
        {
            "request": request,
            "reports": reports,
            "categories": REPORT_CATEGORIES,
            "active_status": status or "",
            "is_admin": current_user.is_admin,
            "submitted": submitted == "1",
        },
    )


@router.post("/reports")
async def submit_report(
    request: Request,
    track_id: Annotated[int | None, Form()] = None,
    category: Annotated[str, Form()] = "other",
    description: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    if not description.strip():
        return RedirectResponse(url="/debug/reports?error=empty", status_code=303)
    log_service.create_user_report(
        db,
        user_id=current_user.id,
        category=category,
        description=description.strip(),
        track_id=track_id or None,
    )
    return RedirectResponse(url="/debug/reports?submitted=1", status_code=303)


@router.post("/reports/{report_id}/status")
def update_report_status(
    report_id: int,
    status: Annotated[str, Form()],
    resolution_notes: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    _require_admin(current_user)
    report = db.query(UserReport).filter(UserReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404)
    try:
        report.status = ReportStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status!r}")
    if resolution_notes is not None:
        report.resolution_notes = resolution_notes.strip() or None
    report.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url="/debug/reports", status_code=303)


# ── Cleanup ───────────────────────────────────────────────────────────────────

@router.post("/cleanup")
def debug_cleanup(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    _require_admin(current_user)
    deleted = run_cleanup(db)
    total = sum(deleted.values())
    return RedirectResponse(url=f"/debug/events?cleanup={total}", status_code=303)
