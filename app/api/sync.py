import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.collectors.soundcloud import SoundCloudCollector
from app.collectors.spotify import SpotifyCollector
from app.collectors.youtube import YouTubeCollector
from app.db import get_db
from app.services.ingestion import SyncResult, run_sync
from app.services import spotify_auth, youtube_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# SoundCloud
# ---------------------------------------------------------------------------

@router.post("/soundcloud")
def sync_soundcloud(request: Request, db: Session = Depends(get_db)):
    """Trigger a SoundCloud likes sync. Redirects to /tracks/pending for browsers."""
    collector = SoundCloudCollector()
    result: SyncResult = run_sync(collector, db)

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
def spotify_connect():
    from app.config import settings
    if not settings.spotify_client_id:
        return HTMLResponse(
            "<h3>Error: SPOTIFY_CLIENT_ID no configurado en .env</h3>",
            status_code=500,
        )
    return RedirectResponse(url=spotify_auth.get_auth_url())


@router.get("/spotify/callback")
def spotify_callback(request: Request, code: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(
            f"<h3>Error al conectar Spotify: {error or 'no code received'}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    try:
        spotify_auth.exchange_code(code)
    except Exception as exc:
        logger.exception("Spotify token exchange failed")
        return HTMLResponse(
            f"<h3>Error al obtener token de Spotify: {exc}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=500,
        )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/spotify/disconnect")
def spotify_disconnect():
    spotify_auth.disconnect()
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/spotify")
def sync_spotify(request: Request, db: Session = Depends(get_db)):
    try:
        access_token = spotify_auth.get_valid_access_token()
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
        result: SyncResult = run_sync(collector, db)
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
def youtube_connect():
    from app.config import settings
    if not settings.youtube_client_id:
        return HTMLResponse(
            "<h3>Error: YOUTUBE_CLIENT_ID no configurado en .env</h3>",
            status_code=500,
        )
    return RedirectResponse(url=youtube_auth.get_auth_url())


@router.get("/youtube/callback")
def youtube_callback(request: Request, code: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(
            f"<h3>Error al conectar YouTube: {error or 'no code received'}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    try:
        youtube_auth.exchange_code(code)
    except Exception as exc:
        logger.exception("YouTube token exchange failed")
        return HTMLResponse(
            f"<h3>Error al obtener token de YouTube: {exc}</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=500,
        )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/youtube/disconnect")
def youtube_disconnect():
    youtube_auth.disconnect()
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/youtube")
def sync_youtube(request: Request, db: Session = Depends(get_db)):
    try:
        access_token = youtube_auth.get_valid_access_token()
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
        result: SyncResult = run_sync(collector, db)
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
