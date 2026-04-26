"""
Auto-download API endpoints.

POST /auto-download/{review_id}/form  — download a single track
POST /auto-download/all/form          — start bulk download, shows progress page
GET  /auto-download/stream/{job_id}   — SSE stream with per-track progress
GET  /auto-download/result/{job_id}   — summary page after bulk download
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from urllib.parse import quote, quote_plus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, contains_eager, joinedload

from pathlib import Path

from app.auth_middleware import get_current_user
from app.db import SessionLocal, get_db
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.models.user import User
from app.services import auto_download, log_service
from app.utils.fs import resolve_download_folder

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-download", tags=["auto-download"])
templates = Jinja2Templates(directory="app/templates")

# In-memory job store: job_id -> {"review_ids": [...], "user_id": N, "summary": {...}, "total": N}
_jobs: dict[str, dict] = {}


# ── Bulk download ────────────────────────────────────────────────────────────

@router.post("/all/form")
def auto_download_all_start(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Create a job and show the progress page."""
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
        .order_by(SourceTrack.liked_at.desc().nulls_last())
        .all()
    )

    if not items:
        return RedirectResponse(url="/tracks/download-queue?empty=1", status_code=303)

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "user_id": current_user.id,
        "review_ids": [item.id for item in items],
        "labels": {
            item.id: _make_label(item.normalized_track)
            for item in items
            if item.normalized_track
        },
        "liked_at": {
            item.id: item.normalized_track.source_track.liked_at
            for item in items
            if item.normalized_track and item.normalized_track.source_track
        },
        "collected_at": {
            item.id: item.normalized_track.source_track.collected_at
            for item in items
            if item.normalized_track and item.normalized_track.source_track
        },
        "summary": None,
        "total": len(items),
        "cancelled": False,
    }

    return templates.TemplateResponse(
        "download_progress.html",
        {"request": request, "job_id": job_id, "total": len(items)},
    )


@router.post("/cancel/{job_id}")
def auto_download_cancel(job_id: str) -> dict:
    """Mark a job as cancelled — the stream will stop after the current track."""
    job = _jobs.get(job_id)
    if job:
        job["cancelled"] = True
    return {"ok": True}


@router.get("/stream/{job_id}")
async def auto_download_stream(job_id: str) -> StreamingResponse:
    """SSE endpoint — processes downloads and streams progress events."""
    if job_id not in _jobs:
        async def empty():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Job no encontrado'})}\n\n"
        return StreamingResponse(empty(), media_type="text/event-stream")

    return StreamingResponse(
        _run_downloads(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_CONCURRENCY = 4


async def _run_downloads(job_id: str):
    """Async generator — processes downloads concurrently and yields SSE events."""
    job = _jobs[job_id]
    review_ids: list[int] = job["review_ids"]
    labels: dict[int, str] = job["labels"]
    liked_at_map: dict[int, object] = job.get("liked_at", {})
    total = job["total"]
    user_id: int = job["user_id"]

    # Load user settings once for this job
    from app.models.user_settings import UserSettings
    _settings_db = SessionLocal()
    try:
        user_settings = _settings_db.query(UserSettings).filter_by(user_id=user_id).first()
        base_dest = Path(user_settings.download_dir) if (user_settings and user_settings.download_dir) else None
        organize_mode = user_settings.folder_organize_mode if user_settings else "none"
    finally:
        _settings_db.close()

    summary: dict[str, list[dict]] = {
        "downloaded": [],
        "low_quality": [],
        "vinyl_only": [],
        "bandcamp_only": [],
        "not_found": [],
    }

    loop = asyncio.get_event_loop()
    event_queue: asyncio.Queue = asyncio.Queue()
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _process_one(i: int, review_id: int) -> None:
        label = labels.get(review_id, f"Track #{review_id}")
        async with sem:
            if job.get("cancelled"):
                await event_queue.put(
                    {"type": "result", "current": i, "total": total, "label": label, "result": "cancelled"}
                )
                return
            await event_queue.put(
                {"type": "processing", "current": i, "total": total, "label": label}
            )
            db = SessionLocal()
            result = "not_found"
            try:
                item = (
                    db.query(ReviewItem)
                    .options(
                        joinedload(ReviewItem.normalized_track)
                        .joinedload(NormalizedTrack.source_track)
                    )
                    .filter(ReviewItem.id == review_id)
                    .first()
                )
                if not item or not item.normalized_track:
                    result = "not_found"
                else:
                    nt = item.normalized_track
                    st = nt.source_track
                    query = nt.search_query or f"{nt.normalized_artist or ''} {nt.normalized_title or ''}".strip()

                    dest_folder = None
                    if base_dest:
                        dest_folder = resolve_download_folder(
                            base=base_dest,
                            liked_at=job.get("liked_at", {}).get(review_id),
                            collected_at=job.get("collected_at", {}).get(review_id),
                            mode=organize_mode,
                        )

                    # Load user settings fresh per worker (thread-safe)
                    us = db.query(UserSettings).filter_by(user_id=user_id).first()

                    log_service.log_event(
                        db, "download_started", f"Download started: {label}",
                        user_id=user_id, track_id=review_id,
                        context={"query": query}, commit=True,
                    )

                    result = await loop.run_in_executor(
                        None, auto_download.try_download, query, dest_folder, us
                    )

                    db_status = result if result in ("downloaded", "vinyl_only", "bandcamp_only") else "not_found"
                    item.status = TrackStatus(db_status)
                    dl_action = "auto_download_succeeded" if result == "downloaded" else f"auto_download_{result}"
                    log_service.add_track_history(
                        db, track_id=review_id, action=dl_action,
                        user_id=user_id,
                        details={"result": result, "query": query},
                    )
                    db.commit()

                    row = _build_summary_row(item, nt, st, query)
                    summary.setdefault(result, []).append(row)
            except Exception:
                logger.exception("Error processing review #%d", review_id)
                result = "not_found"
            finally:
                db.close()

            await event_queue.put(
                {"type": "result", "current": i, "total": total, "label": label, "result": result}
            )

    tasks = [
        asyncio.create_task(_process_one(i, rid))
        for i, rid in enumerate(review_ids, 1)
    ]

    completed = 0
    while completed < total:
        event = await asyncio.wait_for(event_queue.get(), timeout=600)
        yield f"data: {json.dumps(event)}\n\n"
        if event["type"] == "result":
            completed += 1
            if job.get("cancelled") and event.get("result") == "cancelled":
                # Drain remaining tasks and stop
                await asyncio.gather(*tasks, return_exceptions=True)
                job["summary"] = summary
                yield f"data: {json.dumps({'type': 'cancelled', 'job_id': job_id})}\n\n"
                return

    await asyncio.gather(*tasks, return_exceptions=True)

    job["summary"] = summary
    yield f"data: {json.dumps({'type': 'done', 'job_id': job_id})}\n\n"


@router.get("/result/{job_id}", response_class=HTMLResponse)
def auto_download_result(job_id: str, request: Request) -> HTMLResponse:
    job = _jobs.get(job_id)
    if not job or job["summary"] is None:
        return RedirectResponse(url="/tracks/download-queue", status_code=303)

    summary = job["summary"]
    return templates.TemplateResponse(
        "download_summary.html",
        {
            "request": request,
            "summary": summary,
            "total": job["total"],
            "downloaded_count": len(summary["downloaded"]),
        },
    )


# ── Single track ─────────────────────────────────────────────────────────────

@router.post("/{review_id}/form")
def auto_download_one(
    review_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    item = (
        db.query(ReviewItem)
        .options(
            joinedload(ReviewItem.normalized_track)
            .joinedload(NormalizedTrack.source_track)
        )
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.id == review_id,
            SourceTrack.user_id == current_user.id,
        )
        .first()
    )
    if item and item.normalized_track:
        from app.models.user_settings import UserSettings
        user_settings = db.query(UserSettings).filter_by(user_id=current_user.id).first()

        nt: NormalizedTrack = item.normalized_track
        query = nt.search_query or f"{nt.normalized_artist or ''} {nt.normalized_title or ''}".strip()

        dest_folder = None
        if user_settings and user_settings.download_dir:
            st = nt.source_track
            from app.utils.fs import resolve_download_folder
            dest_folder = resolve_download_folder(
                base=Path(user_settings.download_dir),
                liked_at=st.liked_at if st else None,
                collected_at=st.collected_at if st else None,
                mode=user_settings.folder_organize_mode or "none",
            )

        log_service.log_event(
            db, "download_started", f"Download started: {query}",
            user_id=current_user.id, track_id=review_id,
            context={"query": query}, commit=True,
        )
        try:
            result = auto_download.try_download(query, dest_folder, user_settings)
        except Exception:
            logger.exception("Auto-download failed for review #%d", review_id)
            result = "not_found"

        db_status = result if result in ("downloaded", "vinyl_only", "bandcamp_only") else "not_found"
        item.status = TrackStatus(db_status)
        dl_action = "auto_download_succeeded" if result == "downloaded" else f"auto_download_{result}"
        log_service.add_track_history(
            db, track_id=review_id, action=dl_action,
            user_id=current_user.id,
            details={"result": result, "query": query},
        )
        db.commit()

    referer = request.headers.get("referer", "/tracks/download-queue")
    return RedirectResponse(url=referer, status_code=303)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_label(nt: NormalizedTrack | None) -> str:
    if not nt:
        return "—"
    artist = nt.normalized_artist or ""
    title = nt.normalized_title or ""
    version = nt.version_info or ""
    full_title = f"{title} ({version})" if version else title
    return f"{artist} - {full_title}" if artist else full_title


def _build_summary_row(item: ReviewItem, nt: NormalizedTrack, st: SourceTrack | None, search_query: str) -> dict:
    label = _make_label(nt)
    q = quote_plus(label)
    return {
        "review_id": item.id,
        "label": label,
        "source_url": st.source_url if st else "#",
        "url_muzpa":    f"https://srv.muzpa.com/#/search?text={quote(label)}",
        "url_deemix":   f"http://localhost:6595/search?term={q}",
        "url_bandcamp": f"https://bandcamp.com/search?q={q}",
        "url_discogs":  f"https://www.discogs.com/search/?q={q}&type=release",
        "search_query": label,
    }
