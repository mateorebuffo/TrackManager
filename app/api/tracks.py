import re
from datetime import date, datetime, timezone
from urllib.parse import quote, quote_plus
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc, nulls_last, or_
from sqlalchemy.orm import Session, contains_eager
from typing import Annotated

from app.db import get_db
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.services import spotify_auth, youtube_auth

router = APIRouter(prefix="/tracks", tags=["tracks"])
templates = Jinja2Templates(directory="app/templates")

_QUEUE_STATUSES = [
    TrackStatus.queued,
]

# Maps the ?status= param to the DB values to include
_STATUS_FILTER: dict[str, list[TrackStatus]] = {
    "pending":    [TrackStatus.pending],
    "queued":     [TrackStatus.queued],
    "downloaded": [TrackStatus.downloaded],
    "not_found":  [TrackStatus.not_found],
    "vinyl_only":    [TrackStatus.vinyl_only],
    "bandcamp_only": [TrackStatus.bandcamp_only],
    "set_mix":       [TrackStatus.set_mix],
    "discarded":     [TrackStatus.discarded],
    "all":        list(TrackStatus),
}

# Maps the ?sort= param to a SQLAlchemy order clause
_SORT_OPTIONS = {
    "newest":      nulls_last(desc(SourceTrack.liked_at)),
    "oldest":      nulls_last(asc(SourceTrack.liked_at)),
    "artist_asc":  nulls_last(asc(NormalizedTrack.normalized_artist)),
    "artist_desc": nulls_last(desc(NormalizedTrack.normalized_artist)),
    "title_asc":   nulls_last(asc(NormalizedTrack.normalized_title)),
    "title_desc":  nulls_last(desc(NormalizedTrack.normalized_title)),
}


@router.get("/pending", response_class=HTMLResponse)
def pending_tracks_page(
    request: Request,
    status: str = Query(default="pending"),
    q: str | None = Query(default=None),
    sort: str = Query(default="newest"),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    source: str | None = Query(default=None),
    compare: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # ── Compare mode: show only two specific review items ──────────────────
    compare_ids: list[int] = []
    if compare:
        compare_ids = [int(x) for x in compare.split(",") if x.strip().isdigit()]

    if compare_ids:
        items = (
            db.query(ReviewItem)
            .join(ReviewItem.normalized_track)
            .join(NormalizedTrack.source_track)
            .filter(ReviewItem.id.in_(compare_ids))
            .options(
                contains_eager(ReviewItem.normalized_track)
                .contains_eager(NormalizedTrack.source_track)
            )
            .order_by(nulls_last(desc(SourceTrack.liked_at)))
            .all()
        )
    else:
        visible = _STATUS_FILTER.get(status, [TrackStatus.pending])
        order   = _SORT_OPTIONS.get(sort, nulls_last(desc(SourceTrack.liked_at)))

        query = (
            db.query(ReviewItem)
            .join(ReviewItem.normalized_track)
            .join(NormalizedTrack.source_track)
            .filter(ReviewItem.status.in_(visible))
            .options(
                contains_eager(ReviewItem.normalized_track)
                .contains_eager(NormalizedTrack.source_track)
            )
        )

        if q and q.strip():
            # Normalize search term: strip ' - ' so "Artist - Title" matches search_query "Artist Title"
            q_normalized = re.sub(r"\s*-\s*", " ", q.strip()).strip()
            pattern_raw = f"%{q.strip()}%"
            pattern_norm = f"%{q_normalized}%"
            query = query.filter(
                or_(
                    NormalizedTrack.normalized_artist.ilike(pattern_raw),
                    NormalizedTrack.normalized_title.ilike(pattern_raw),
                    NormalizedTrack.search_query.ilike(pattern_norm),
                    SourceTrack.raw_title.ilike(pattern_raw),
                )
            )

        if source and source in ("soundcloud", "spotify", "youtube"):
            query = query.filter(SourceTrack.source == source)

        parsed_from = _parse_date(date_from)
        parsed_to   = _parse_date(date_to)
        if parsed_from:
            query = query.filter(SourceTrack.liked_at >= datetime(parsed_from.year, parsed_from.month, parsed_from.day))
        if parsed_to:
            query = query.filter(SourceTrack.liked_at <= datetime(parsed_to.year, parsed_to.month, parsed_to.day, 23, 59, 59))

        items = query.order_by(order).all()

    # Defaults shown in the UI when date filter is opened
    today = date.today()
    first_of_prev = date(today.year if today.month > 1 else today.year - 1, today.month - 1 if today.month > 1 else 12, 1)
    default_date_from = first_of_prev.isoformat()
    default_date_to   = today.isoformat()
    date_filter_active = False if compare_ids else bool(_parse_date(date_from) or _parse_date(date_to))

    rows = []
    for item in items:
        nt = item.normalized_track
        st: SourceTrack = nt.source_track if nt else None

        raw_notes = item.notes or ""
        dup_compare_url = None
        display_notes = raw_notes
        if raw_notes.startswith("dup:"):
            try:
                matched_review_id = int(raw_notes[4:])
                display_notes = "Posible duplicado"
                dup_compare_url = f"/tracks/pending?compare={item.id},{matched_review_id}"
            except ValueError:
                pass

        rows.append(
            {
                "review_id": item.id,
                "nt_id": nt.id if nt else None,
                "status": item.status.value,
                "artist": nt.normalized_artist if nt else "—",
                "channel": st.raw_artist if st and st.raw_artist else "—",
                "title": nt.normalized_title if nt else "—",
                "version": nt.version_info if nt else "",
                "source": st.source if st else "—",
                "source_url": st.source_url if st else "#",
                "liked_at": st.liked_at.strftime("%d/%m/%Y") if st and st.liked_at else "—",
                "duration": _fmt_duration(st.duration_seconds if st else None),
                "notes": display_notes,
                "dup_compare_url": dup_compare_url,
                "raw_title": st.raw_title if st else "—",
            }
        )

    counts = _status_counts(db)

    return templates.TemplateResponse(
        "pending_tracks.html",
        {
            "request": request,
            "rows": rows,
            "count": len(rows),
            "active_status": status,
            "q": q or "",
            "sort": sort,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "date_filter_active": date_filter_active,
            "default_date_from": default_date_from,
            "default_date_to": default_date_to,
            "counts": counts,
            "queued_count": counts.get("queued", 0),
            "source": source or "",
            "spotify_connected": spotify_auth.is_connected(),
            "youtube_connected": youtube_auth.is_connected(),
            "compare_mode": bool(compare_ids),
        },
    )


@router.get("/pending/json")
def pending_tracks_json(db: Session = Depends(get_db)) -> list[dict]:
    """JSON list of pending tracks (API consumers)."""
    items = (
        db.query(ReviewItem)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(ReviewItem.status == TrackStatus.pending)
        .options(
            contains_eager(ReviewItem.normalized_track)
            .contains_eager(NormalizedTrack.source_track)
        )
        .order_by(nulls_last(desc(SourceTrack.liked_at)))
        .all()
    )

    result = []
    for item in items:
        nt = item.normalized_track
        st = nt.source_track if nt else None
        result.append(
            {
                "review_id": item.id,
                "normalized_artist": nt.normalized_artist if nt else None,
                "normalized_title": nt.normalized_title if nt else None,
                "version_info": nt.version_info if nt else None,
                "search_query": nt.search_query if nt else None,
                "source": st.source if st else None,
                "source_url": st.source_url if st else None,
                "liked_at": st.liked_at.isoformat() if st and st.liked_at else None,
                "duration_seconds": st.duration_seconds if st else None,
                "notes": item.notes,
            }
        )
    return result


@router.post("/{nt_id}/edit-metadata")
def edit_metadata(
    request: Request,
    nt_id: int,
    artist: Annotated[str, Form()],
    title: Annotated[str, Form()],
    version: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    nt = db.query(NormalizedTrack).filter(NormalizedTrack.id == nt_id).first()
    if nt:
        nt.normalized_artist = artist.strip() or None
        nt.normalized_title = title.strip() or None
        nt.version_info = version.strip() or None
        db.commit()
    referer = request.headers.get("referer", "/tracks/pending")
    return RedirectResponse(url=referer, status_code=303)


@router.post("/download-queue", response_class=HTMLResponse)
def download_queue_post(
    request: Request,
    review_ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_db),
) -> Response:
    """Transition selected tracks from pending → queued and show the queue."""
    if not review_ids:
        return RedirectResponse(url="/tracks/pending", status_code=303)
    db.query(ReviewItem).filter(ReviewItem.id.in_(review_ids)).update(
        {"status": TrackStatus.queued}, synchronize_session=False
    )
    db.commit()
    return _render_queue(request, db)


@router.post("/bulk-to-pending")
def bulk_to_pending(
    request: Request,
    review_ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Move selected tracks back to pending."""
    if review_ids:
        db.query(ReviewItem).filter(ReviewItem.id.in_(review_ids)).update(
            {"status": TrackStatus.pending, "reviewed_at": None},
            synchronize_session=False,
        )
        db.commit()
    referer = request.headers.get("referer", "/tracks/pending")
    return RedirectResponse(url=referer, status_code=303)


@router.post("/bulk-to-queue")
def bulk_to_queue(
    review_ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Move selected resolved tracks back to queued for retry."""
    if review_ids:
        db.query(ReviewItem).filter(ReviewItem.id.in_(review_ids)).update(
            {"status": TrackStatus.queued, "reviewed_at": None},
            synchronize_session=False,
        )
        db.commit()
    return RedirectResponse(url="/tracks/download-queue", status_code=303)


@router.post("/download-queue/reset-all")
def download_queue_reset_all(db: Session = Depends(get_db)) -> RedirectResponse:
    """Move all queue tracks back to pending."""
    db.query(ReviewItem).filter(ReviewItem.status.in_(_QUEUE_STATUSES)).update(
        {"status": TrackStatus.pending, "reviewed_at": None}, synchronize_session=False
    )
    db.commit()
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.get("/download-queue", response_class=HTMLResponse)
def download_queue_get(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return _render_queue(request, db)


def _render_queue(request: Request, db: Session) -> HTMLResponse:
    items = (
        db.query(ReviewItem)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(ReviewItem.status.in_(_QUEUE_STATUSES))
        .options(
            contains_eager(ReviewItem.normalized_track)
            .contains_eager(NormalizedTrack.source_track)
        )
        .order_by(nulls_last(desc(SourceTrack.liked_at)))
        .all()
    )
    rows = _build_queue_rows(items)
    queued_count = sum(1 for r in rows if r["status"] == "queued")
    return templates.TemplateResponse(
        "download_queue.html",
        {"request": request, "rows": rows, "queued_count": queued_count},
    )


def _build_queue_rows(items: list) -> list[dict]:
    rows = []
    for item in items:
        nt = item.normalized_track
        st: SourceTrack = nt.source_track if nt else None
        artist = nt.normalized_artist if nt else ""
        title = nt.normalized_title if nt else ""
        version = nt.version_info if nt else ""
        full_title = f"{title} ({version})" if version else title
        label = f"{artist} - {full_title}" if artist else full_title
        q = quote_plus(label)
        rows.append({
            "review_id": item.id,
            "nt_id": nt.id if nt else None,
            "status": item.status.value,
            "artist": artist,
            "title": full_title,
            "title_only": title,
            "raw_title": st.raw_title if st else "",
            "channel": st.raw_artist if st and st.raw_artist else "",
            "version": version,
            "source_url": st.source_url if st else "#",
            "label": label,
            "search_query": label,
            "url_muzpa":    f"https://srv.muzpa.com/#/search?text={quote(label)}",
            "url_deemix":   f"http://localhost:6595/search?term={q}",
            "url_bandcamp": f"https://bandcamp.com/search?q={q}",
            "url_discogs":  f"https://www.discogs.com/search/?q={q}&type=release",
        })
    return rows


def _status_counts(db: Session) -> dict:
    from sqlalchemy import func
    rows = db.query(ReviewItem.status, func.count(ReviewItem.id)).group_by(ReviewItem.status).all()
    return {status.value: count for status, count in rows}


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
