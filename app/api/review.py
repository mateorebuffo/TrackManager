from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import Annotated

from app.db import get_db
from app.models.review_item import ReviewItem, TrackStatus

router = APIRouter(prefix="/review", tags=["review"])

_QUEUE_URL = "/tracks/download-queue"


def _get_item(item_id: int, db: Session) -> ReviewItem:
    item = db.query(ReviewItem).filter(ReviewItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"ReviewItem {item_id} not found")
    return item


def _redirect(next_url: str | None, default: str) -> RedirectResponse:
    return RedirectResponse(url=next_url or default, status_code=303)


# ---------------------------------------------------------------------------
# Queue-page actions (form endpoints → redirect back to queue or next)
# ---------------------------------------------------------------------------

@router.post("/{item_id}/downloaded/form")
def downloaded_form(item_id: int, db: Session = Depends(get_db),
                    next: Annotated[str | None, Form()] = None) -> RedirectResponse:
    item = _get_item(item_id, db)
    item.mark_reviewed(TrackStatus.downloaded)
    db.commit()
    return _redirect(next, _QUEUE_URL)


@router.post("/{item_id}/not-found/form")
def not_found_form(item_id: int, db: Session = Depends(get_db),
                   next: Annotated[str | None, Form()] = None) -> RedirectResponse:
    item = _get_item(item_id, db)
    item.mark_reviewed(TrackStatus.not_found)
    db.commit()
    return _redirect(next, _QUEUE_URL)


@router.post("/{item_id}/bandcamp-only/form")
def bandcamp_only_form(item_id: int, db: Session = Depends(get_db),
                       next: Annotated[str | None, Form()] = None) -> RedirectResponse:
    item = _get_item(item_id, db)
    item.mark_reviewed(TrackStatus.bandcamp_only)
    db.commit()
    return _redirect(next, _QUEUE_URL)


@router.post("/{item_id}/vinyl-only/form")
def vinyl_only_form(item_id: int, db: Session = Depends(get_db),
                    next: Annotated[str | None, Form()] = None) -> RedirectResponse:
    item = _get_item(item_id, db)
    item.mark_reviewed(TrackStatus.vinyl_only)
    db.commit()
    return _redirect(next, _QUEUE_URL)




# ---------------------------------------------------------------------------
# Main-page actions (form endpoints → redirect back to pending)
# ---------------------------------------------------------------------------

@router.post("/{item_id}/discard/form")
def discard_form(item_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    item = _get_item(item_id, db)
    item.mark_reviewed(TrackStatus.discarded)
    db.commit()
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/{item_id}/pending/form")
def reset_to_pending_form(item_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Reset any status back to pending."""
    item = _get_item(item_id, db)
    item.status = TrackStatus.pending
    item.reviewed_at = None
    item.notes = None
    db.commit()
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/{item_id}/requeue/form")
def requeue_form(item_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Move a track from the queue back to pending."""
    item = _get_item(item_id, db)
    item.status = TrackStatus.pending
    item.reviewed_at = None
    db.commit()
    return RedirectResponse(url="/tracks/download-queue", status_code=303)


@router.post("/{item_id}/retry-queue/form")
def retry_queue_form(item_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Reset a resolved track back to queued so it can be retried."""
    item = _get_item(item_id, db)
    item.status = TrackStatus.queued
    item.reviewed_at = None
    db.commit()
    return RedirectResponse(url="/tracks/download-queue", status_code=303)
