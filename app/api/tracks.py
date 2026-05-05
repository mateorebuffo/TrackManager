import logging
import re
from datetime import date, datetime
from urllib.parse import quote, quote_plus
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import asc, desc, nulls_last, or_
from sqlalchemy.orm import Session, contains_eager
from typing import Annotated

from app.auth_middleware import get_current_user
from app.db import get_db
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.models.user import User
from app.models.user_settings import UserSettings
from app.services import log_service, spotify_auth, youtube_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tracks", tags=["tracks"])
templates = Jinja2Templates(directory="app/templates")

_QUEUE_STATUSES = [
    TrackStatus.queued,
]

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

_SORT_OPTIONS = {
    "newest":           nulls_last(desc(SourceTrack.liked_at)),
    "oldest":           nulls_last(asc(SourceTrack.liked_at)),
    "imported_newest":  desc(SourceTrack.collected_at),
    "imported_oldest":  asc(SourceTrack.collected_at),
    "artist_asc":       nulls_last(asc(NormalizedTrack.normalized_artist)),
    "artist_desc":      nulls_last(desc(NormalizedTrack.normalized_artist)),
    "title_asc":        nulls_last(asc(NormalizedTrack.normalized_title)),
    "title_desc":       nulls_last(desc(NormalizedTrack.normalized_title)),
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
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    compare_ids: list[int] = []
    if compare:
        compare_ids = [int(x) for x in compare.split(",") if x.strip().isdigit()]

    if compare_ids:
        items = (
            db.query(ReviewItem)
            .join(ReviewItem.normalized_track)
            .join(NormalizedTrack.source_track)
            .filter(
                ReviewItem.id.in_(compare_ids),
                SourceTrack.user_id == current_user.id,
            )
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
            .filter(
                ReviewItem.status.in_(visible),
                SourceTrack.user_id == current_user.id,
            )
            .options(
                contains_eager(ReviewItem.normalized_track)
                .contains_eager(NormalizedTrack.source_track)
            )
        )

        if q and q.strip():
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
                display_notes = "Duplicado"
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
                "collected_at": st.collected_at.strftime("%d/%m/%Y") if st and st.collected_at else "—",
                "duration": _fmt_duration(st.duration_seconds if st else None),
                "notes": display_notes,
                "dup_compare_url": dup_compare_url,
                "raw_title": st.raw_title if st else "—",
            }
        )

    counts = _status_counts(db, current_user.id)

    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    spotify_ok  = spotify_auth.is_connected(db, current_user.id)
    youtube_ok  = youtube_auth.is_connected(db, current_user.id)
    has_source  = bool((us and us.soundcloud_oauth_token) or spotify_ok or youtube_ok)
    has_dl      = bool(us and (us.muzpa_sess or us.deezer_arl))
    needs_setup = not has_source or not has_dl

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
            "sc_configured": bool(us and us.soundcloud_oauth_token),
            "spotify_connected": spotify_ok,
            "youtube_connected": youtube_ok,
            "spotify_playlist_name": _user_playlist_name(db, current_user.id, "spotify"),
            "youtube_playlist_name": _user_playlist_name(db, current_user.id, "youtube"),
            "compare_mode": bool(compare_ids),
            "needs_setup": needs_setup,
        },
    )


@router.get("/pending/json")
def pending_tracks_json(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    items = (
        db.query(ReviewItem)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.status == TrackStatus.pending,
            SourceTrack.user_id == current_user.id,
        )
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
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    nt = (
        db.query(NormalizedTrack)
        .join(NormalizedTrack.source_track)
        .filter(
            NormalizedTrack.id == nt_id,
            SourceTrack.user_id == current_user.id,
        )
        .first()
    )
    if nt:
        before = {
            "artist": nt.normalized_artist,
            "title": nt.normalized_title,
            "version": nt.version_info,
        }
        new_artist  = artist.strip() or None
        new_title   = title.strip() or None
        new_version = version.strip() or None

        # Query ReviewItem directly — avoids ORM lazy-load issues after commit
        review_id_val = (
            db.query(ReviewItem.id)
            .filter(ReviewItem.normalized_track_id_fk == nt.id)
            .scalar()
        )
        nt.normalized_artist = new_artist
        nt.normalized_title  = new_title
        nt.version_info      = new_version
        db.commit()

        if review_id_val:
            log_service.add_track_history(
                db, track_id=review_id_val, action="manually_edited",
                user_id=current_user.id,
                details={"before": before, "after": {
                    "artist": new_artist,
                    "title": new_title,
                    "version": new_version,
                }},
                commit=True,
            )
    referer = request.headers.get("referer", "")
    url = referer if referer.startswith("/") and not referer.startswith("//") else "/tracks/pending"
    return RedirectResponse(url=url, status_code=303)


@router.post("/download-queue", response_class=HTMLResponse)
def download_queue_post(
    request: Request,
    review_ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    if not review_ids:
        return RedirectResponse(url="/tracks/pending", status_code=303)
    valid = _user_review_ids(db, current_user.id, review_ids)
    if valid:
        db.query(ReviewItem).filter(ReviewItem.id.in_(valid)).update(
            {"status": TrackStatus.queued}, synchronize_session=False
        )
        db.commit()
        for rid in valid:
            log_service.add_track_history(
                db, track_id=rid, action="added_to_queue",
                user_id=current_user.id, commit=True,
            )
    return _render_queue(request, db, current_user.id)


@router.post("/bulk-to-pending")
def bulk_to_pending(
    request: Request,
    review_ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    valid = _user_review_ids(db, current_user.id, review_ids)
    if valid:
        db.query(ReviewItem).filter(ReviewItem.id.in_(valid)).update(
            {"status": TrackStatus.pending, "reviewed_at": None}, synchronize_session=False
        )
        _cancel_jobs(db, valid)
        db.commit()
    referer = request.headers.get("referer", "")
    url = referer if referer.startswith("/") and not referer.startswith("//") else "/tracks/pending"
    return RedirectResponse(url=url, status_code=303)


@router.post("/bulk-discard")
def bulk_discard(
    review_ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    valid = _user_review_ids(db, current_user.id, review_ids)
    if valid:
        db.query(ReviewItem).filter(ReviewItem.id.in_(valid)).update(
            {"status": TrackStatus.discarded}, synchronize_session=False
        )
        db.commit()
        for rid in valid:
            log_service.add_track_history(
                db, track_id=rid, action="discarded",
                user_id=current_user.id, commit=True,
            )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/bulk-to-queue")
def bulk_to_queue(
    review_ids: Annotated[list[int], Form()] = [],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    valid = _user_review_ids(db, current_user.id, review_ids)
    if valid:
        db.query(ReviewItem).filter(ReviewItem.id.in_(valid)).update(
            {"status": TrackStatus.queued, "reviewed_at": None}, synchronize_session=False
        )
        db.commit()
    return RedirectResponse(url="/tracks/download-queue", status_code=303)


@router.post("/download-queue/reset-all")
def download_queue_reset_all(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    valid = (
        db.query(ReviewItem.id)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.status.in_(_QUEUE_STATUSES),
            SourceTrack.user_id == current_user.id,
        )
        .all()
    )
    valid = [row[0] for row in valid]
    if valid:
        db.query(ReviewItem).filter(ReviewItem.id.in_(valid)).update(
            {"status": TrackStatus.pending, "reviewed_at": None}, synchronize_session=False
        )
        _cancel_jobs(db, valid)
        db.commit()
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.get("/download-queue", response_class=HTMLResponse)
def download_queue_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    return _render_queue(request, db, current_user.id)


def _render_queue(request: Request, db: Session, user_id: int) -> HTMLResponse:
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=user_id).first()
    has_muzpa  = bool(us and us.muzpa_sess)
    has_deezer = bool(us and us.deezer_arl)

    items = (
        db.query(ReviewItem)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.status.in_(_QUEUE_STATUSES),
            SourceTrack.user_id == user_id,
        )
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
        {"request": request, "rows": rows, "queued_count": queued_count,
         "has_muzpa": has_muzpa, "has_deezer": has_deezer},
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


def _user_review_ids(db: Session, user_id: int, candidate_ids: list[int]) -> list[int]:
    """Return the subset of candidate_ids that belong to user_id."""
    if not candidate_ids:
        return []
    rows = (
        db.query(ReviewItem.id)
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(
            ReviewItem.id.in_(candidate_ids),
            SourceTrack.user_id == user_id,
        )
        .all()
    )
    return [row[0] for row in rows]


def _status_counts(db: Session, user_id: int) -> dict:
    from sqlalchemy import func
    rows = (
        db.query(ReviewItem.status, func.count(ReviewItem.id))
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(SourceTrack.user_id == user_id)
        .group_by(ReviewItem.status)
        .all()
    )
    return {status.value: count for status, count in rows}


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _user_playlist_name(db: Session, user_id: int, platform: str) -> str | None:
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=user_id).first()
    if not us:
        return None
    if platform == "spotify":
        return us.spotify_playlist_name or None
    if platform == "youtube":
        return us.youtube_playlist_name or None
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _cancel_jobs(db: Session, review_ids: list[int]) -> None:
    from app.models.download_job import DownloadJob, JobStatus
    from datetime import datetime, timezone
    if not review_ids:
        return
    db.query(DownloadJob).filter(
        DownloadJob.review_id.in_(review_ids),
        DownloadJob.status.in_([JobStatus.pending, JobStatus.in_progress]),
    ).update(
        {"status": JobStatus.cancelled, "updated_at": datetime.now(timezone.utc)},
        synchronize_session=False,
    )
