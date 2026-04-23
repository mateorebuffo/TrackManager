import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth_middleware import get_current_user
from app.collectors.soundcloud import SoundCloudCollector
from app.collectors.spotify import SpotifyCollector
from app.collectors.youtube import YouTubeCollector
from app.db import get_db
from app.models.user import User
from app.services.ingestion import SyncResult, run_sync
from app.services import spotify_auth, youtube_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# SoundCloud
# ---------------------------------------------------------------------------

@router.post("/soundcloud")
def sync_soundcloud(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger a SoundCloud likes sync."""
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    sc_token = (us.soundcloud_oauth_token or "") if us else ""
    collector = SoundCloudCollector(oauth_token=sc_token or None)
    result: SyncResult = run_sync(collector, db, user_id=current_user.id)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/tracks/pending", status_code=303)

    return {
        "status": "ok",
        "total_fetched": result.total_fetched,
        "new_tracks": result.new_tracks,
        "skipped_existing": result.skipped_existing,
        "strong_duplicates_flagged": result.strong_duplicates_flagged,
        "weak_duplicates_flagged": result.weak_duplicates_flagged,
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# Spotify OAuth + sync
# ---------------------------------------------------------------------------

@router.get("/spotify/connect")
def spotify_connect(current_user: User = Depends(get_current_user)):
    from app.config import settings
    if not settings.spotify_client_id:
        return HTMLResponse(
            "<h3>Error: SPOTIFY_CLIENT_ID no configurado en .env</h3>",
            status_code=500,
        )
    return RedirectResponse(url=spotify_auth.get_auth_url())


@router.get("/spotify/callback")
def spotify_callback(
    request: Request,
    code: str = "",
    error: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if error or not code:
        return HTMLResponse(
            f"<h3>Error al conectar Spotify: {error or 'no code received'}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    try:
        spotify_auth.exchange_code(code, db, current_user.id)
    except Exception as exc:
        logger.exception("Spotify token exchange failed")
        return HTMLResponse(
            f"<h3>Error al obtener token de Spotify: {exc}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=500,
        )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/spotify/disconnect")
def spotify_disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    spotify_auth.disconnect(db, current_user.id)
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/spotify")
def sync_spotify(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        access_token = spotify_auth.get_valid_access_token(db, current_user.id)
    except RuntimeError as exc:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                f"<h3>Spotify no conectado</h3><p>{exc}</p>"
                "<p><a href='/sync/spotify/connect'>Conectar Spotify</a></p>",
                status_code=400,
            )
        return {"status": "error", "detail": str(exc)}

    try:
        collector = SpotifyCollector(access_token)
        result: SyncResult = run_sync(collector, db, user_id=current_user.id)
    except ValueError as exc:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                f"<h3>Error de sincronización</h3><p>{exc}</p>"
                "<p><a href='/tracks/pending'>Volver</a></p>",
                status_code=404,
            )
        return {"status": "error", "detail": str(exc)}

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/tracks/pending", status_code=303)

    return {
        "status": "ok",
        "total_fetched": result.total_fetched,
        "new_tracks": result.new_tracks,
        "skipped_existing": result.skipped_existing,
        "strong_duplicates_flagged": result.strong_duplicates_flagged,
        "weak_duplicates_flagged": result.weak_duplicates_flagged,
        "errors": result.errors,
    }


# ---------------------------------------------------------------------------
# YouTube OAuth + sync
# ---------------------------------------------------------------------------

@router.get("/youtube/connect")
def youtube_connect(current_user: User = Depends(get_current_user)):
    from app.config import settings
    if not settings.youtube_client_id:
        return HTMLResponse(
            "<h3>Error: YOUTUBE_CLIENT_ID no configurado en .env</h3>",
            status_code=500,
        )
    return RedirectResponse(url=youtube_auth.get_auth_url())


@router.get("/youtube/callback")
def youtube_callback(
    request: Request,
    code: str = "",
    error: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if error or not code:
        return HTMLResponse(
            f"<h3>Error al conectar YouTube: {error or 'no code received'}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    try:
        youtube_auth.exchange_code(code, db, current_user.id)
    except Exception as exc:
        logger.exception("YouTube token exchange failed")
        return HTMLResponse(
            f"<h3>Error al obtener token de YouTube: {exc}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=500,
        )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/youtube/disconnect")
def youtube_disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    youtube_auth.disconnect(db, current_user.id)
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/youtube")
def sync_youtube(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        access_token = youtube_auth.get_valid_access_token(db, current_user.id)
    except RuntimeError as exc:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                f"<h3>YouTube no conectado</h3><p>{exc}</p>"
                "<p><a href='/sync/youtube/connect'>Conectar YouTube</a></p>",
                status_code=400,
            )
        return {"status": "error", "detail": str(exc)}

    try:
        collector = YouTubeCollector(access_token)
        result: SyncResult = run_sync(collector, db, user_id=current_user.id)
    except ValueError as exc:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                f"<h3>Error de sincronización</h3><p>{exc}</p>"
                "<p><a href='/tracks/pending'>Volver</a></p>",
                status_code=404,
            )
        return {"status": "error", "detail": str(exc)}

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/tracks/pending", status_code=303)

    return {
        "status": "ok",
        "total_fetched": result.total_fetched,
        "new_tracks": result.new_tracks,
        "skipped_existing": result.skipped_existing,
        "strong_duplicates_flagged": result.strong_duplicates_flagged,
        "weak_duplicates_flagged": result.weak_duplicates_flagged,
        "errors": result.errors,
    }
