from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload
from typing import Annotated

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.models.user import User
from app.services import log_service

router = APIRouter(prefix="/review", tags=["review"])

_QUEUE_URL = "/tracks/download-queue"


def _get_item(item_id: int, user_id: int, db: Session) -> ReviewItem:
    item = (
        db.query(ReviewItem)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.id == item_id,
            SourceTrack.user_id == user_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail=f"ReviewItem {item_id} not found")
    return item


def _redirect(next_url: str | None, default: str) -> RedirectResponse:
    return RedirectResponse(url=next_url or default, status_code=303)


@router.post("/{item_id}/downloaded/form")
def downloaded_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    item = _get_item(item_id, current_user.id, db)
    item.mark_reviewed(TrackStatus.downloaded)
    db.commit()
    log_service.add_track_history(
        db, track_id=item.id, action="marked_downloaded",
        user_id=current_user.id, commit=True,
    )
    return _redirect(next, _QUEUE_URL)


@router.post("/{item_id}/not-found/form")
def not_found_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    item = _get_item(item_id, current_user.id, db)
    item.mark_reviewed(TrackStatus.not_found)
    db.commit()
    log_service.add_track_history(
        db, track_id=item.id, action="marked_not_found",
        user_id=current_user.id, commit=True,
    )
    return _redirect(next, _QUEUE_URL)


@router.post("/{item_id}/bandcamp-only/form")
def bandcamp_only_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    item = _get_item(item_id, current_user.id, db)
    item.mark_reviewed(TrackStatus.bandcamp_only)
    db.commit()
    log_service.add_track_history(
        db, track_id=item.id, action="marked_bandcamp_only",
        user_id=current_user.id, commit=True,
    )
    return _redirect(next, _QUEUE_URL)


@router.post("/{item_id}/vinyl-only/form")
def vinyl_only_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    next: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    item = _get_item(item_id, current_user.id, db)
    item.mark_reviewed(TrackStatus.vinyl_only)
    db.commit()
    log_service.add_track_history(
        db, track_id=item.id, action="marked_vinyl_only",
        user_id=current_user.id, commit=True,
    )
    return _redirect(next, _QUEUE_URL)


@router.post("/{item_id}/discard/form")
def discard_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    item = _get_item(item_id, current_user.id, db)
    item.mark_reviewed(TrackStatus.discarded)
    db.commit()
    log_service.add_track_history(
        db, track_id=item.id, action="discarded",
        user_id=current_user.id, commit=True,
    )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/{item_id}/pending/form")
def reset_to_pending_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    from app.models.download_job import DownloadJob, JobStatus
    from datetime import datetime, timezone
    item = _get_item(item_id, current_user.id, db)
    item.status = TrackStatus.pending
    item.reviewed_at = None
    item.notes = None
    db.query(DownloadJob).filter(
        DownloadJob.review_id == item_id,
        DownloadJob.status.in_([JobStatus.pending, JobStatus.in_progress]),
    ).update(
        {"status": JobStatus.cancelled, "updated_at": datetime.now(timezone.utc)},
        synchronize_session=False,
    )
    db.commit()
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/{item_id}/requeue/form")
def requeue_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    item = _get_item(item_id, current_user.id, db)
    item.status = TrackStatus.pending
    item.reviewed_at = None
    db.commit()
    return RedirectResponse(url="/tracks/download-queue", status_code=303)


@router.post("/{item_id}/retry-queue/form")
def retry_queue_form(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    item = _get_item(item_id, current_user.id, db)
    item.status = TrackStatus.queued
    item.reviewed_at = None
    db.commit()
    return RedirectResponse(url="/tracks/download-queue", status_code=303)
