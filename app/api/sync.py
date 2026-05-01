import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
from app.utils.rate_limit import UserRateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])
templates = Jinja2Templates(directory="app/templates")

_sync_limiter = UserRateLimiter(calls=1, window=60)  # 1 sync per 60s per user


def _sync_rate_limited(user_id: int, request: Request):
    if not _sync_limiter.acquire(user_id):
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(
                "<h3>Sincronización en progreso.</h3>"
                "<p>Esperá 60 segundos antes de volver a sincronizar.</p>"
                "<p><a href='/tracks/pending'>Volver</a></p>",
                status_code=429,
            )
        return JSONResponse({"status": "error", "detail": "Rate limit: esperá 60s entre sincronizaciones."}, status_code=429)
    return None


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
    if (limited := _sync_rate_limited(current_user.id, request)):
        return limited
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
            "<h3>Error: SPOTIFY_CLIENT_ID no configurado.</h3>"
            "<p>Añadí <code>SPOTIFY_CLIENT_ID</code> y <code>SPOTIFY_CLIENT_SECRET</code> "
            "en las variables de entorno.</p>",
            status_code=500,
        )
    if not settings.spotify_redirect_uri:
        return HTMLResponse(
            "<h3>Error: SPOTIFY_REDIRECT_URI no configurado.</h3>"
            "<p>Añadí <code>SPOTIFY_REDIRECT_URI=https://tu-app.railway.app/sync/spotify/callback</code> "
            "en las variables de entorno y registralo en el Spotify Developer Console.</p>",
            status_code=500,
        )
    return RedirectResponse(url=spotify_auth.get_auth_url(current_user.id))


@router.get("/spotify/callback")
def spotify_callback(
    request: Request,
    code: str = "",
    error: str = "",
    state: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not spotify_auth.verify_state(state, current_user.id):
        return HTMLResponse(
            "<h3>Error de seguridad: estado OAuth inválido.</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    if error or not code:
        return HTMLResponse(
            "<h3>Error al conectar Spotify.</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    try:
        spotify_auth.exchange_code(code, db, current_user.id)
    except Exception:
        logger.exception("Spotify token exchange failed")
        return HTMLResponse(
            "<h3>Error al conectar Spotify. Intentá de nuevo.</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=500,
        )
    from app.services import log_service
    log_service.log_event(
        db, "spotify_connected", "Spotify OAuth connected",
        user_id=current_user.id, source="spotify", commit=True,
    )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/spotify/disconnect")
def spotify_disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    spotify_auth.disconnect(db, current_user.id)
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.get("/spotify/playlists")
def spotify_playlists(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the user's Spotify playlists as JSON."""
    try:
        access_token = spotify_auth.get_valid_access_token(db, current_user.id)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        collector = SpotifyCollector(access_token)
        return JSONResponse(collector.list_playlists())
    except Exception as exc:
        logger.exception("Error listing Spotify playlists")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/spotify/select-playlist")
def spotify_select_playlist(
    playlist_id: str = Form(...),
    playlist_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    if not us:
        us = UserSettings(user_id=current_user.id)
        db.add(us)
    us.spotify_playlist_id = playlist_id
    us.spotify_playlist_name = playlist_name
    db.commit()

    try:
        access_token = spotify_auth.get_valid_access_token(db, current_user.id)
        collector = SpotifyCollector(access_token, playlist_id=playlist_id)
        run_sync(collector, db, user_id=current_user.id)
    except Exception:
        logger.exception("Spotify sync after playlist selection failed")

    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/spotify")
def sync_spotify(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if (limited := _sync_rate_limited(current_user.id, request)):
        return limited
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

    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    playlist_id = us.spotify_playlist_id if us else None

    try:
        collector = SpotifyCollector(access_token, playlist_id=playlist_id)
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
            "<h3>Error: YOUTUBE_CLIENT_ID no configurado.</h3>"
            "<p>Añadí <code>YOUTUBE_CLIENT_ID</code> y <code>YOUTUBE_CLIENT_SECRET</code> "
            "en las variables de entorno.</p>",
            status_code=500,
        )
    if not settings.youtube_redirect_uri:
        return HTMLResponse(
            "<h3>Error: YOUTUBE_REDIRECT_URI no configurado.</h3>"
            "<p>Añadí <code>YOUTUBE_REDIRECT_URI=https://tu-app.railway.app/sync/youtube/callback</code> "
            "en las variables de entorno y registralo en Google Cloud Console.</p>",
            status_code=500,
        )
    return RedirectResponse(url=youtube_auth.get_auth_url(current_user.id))


@router.get("/youtube/callback")
def youtube_callback(
    request: Request,
    code: str = "",
    error: str = "",
    state: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not youtube_auth.verify_state(state, current_user.id):
        return HTMLResponse(
            "<h3>Error de seguridad: estado OAuth inválido.</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    if error or not code:
        return HTMLResponse(
            "<h3>Error al conectar YouTube.</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=400,
        )
    try:
        youtube_auth.exchange_code(code, db, current_user.id)
    except Exception:
        logger.exception("YouTube token exchange failed")
        return HTMLResponse(
            "<h3>Error al conectar YouTube. Intentá de nuevo.</h3>"
            "<p><a href='/tracks/pending'>Volver</a></p>",
            status_code=500,
        )
    from app.services import log_service
    log_service.log_event(
        db, "youtube_connected", "YouTube OAuth connected",
        user_id=current_user.id, source="youtube", commit=True,
    )
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/youtube/disconnect")
def youtube_disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    youtube_auth.disconnect(db, current_user.id)
    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.get("/youtube/playlists")
def youtube_playlists(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the user's YouTube playlists as JSON."""
    try:
        access_token = youtube_auth.get_valid_access_token(db, current_user.id)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    try:
        collector = YouTubeCollector(access_token)
        return JSONResponse(collector.list_playlists())
    except Exception as exc:
        logger.exception("Error listing YouTube playlists")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/youtube/select-playlist")
def youtube_select_playlist(
    playlist_id: str = Form(...),
    playlist_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    if not us:
        us = UserSettings(user_id=current_user.id)
        db.add(us)
    us.youtube_playlist_id = playlist_id
    us.youtube_playlist_name = playlist_name
    db.commit()

    try:
        access_token = youtube_auth.get_valid_access_token(db, current_user.id)
        collector = YouTubeCollector(access_token, playlist_id=playlist_id)
        run_sync(collector, db, user_id=current_user.id)
    except Exception:
        logger.exception("YouTube sync after playlist selection failed")

    return RedirectResponse(url="/tracks/pending", status_code=303)


@router.post("/youtube")
def sync_youtube(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if (limited := _sync_rate_limited(current_user.id, request)):
        return limited
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

    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    playlist_id = us.youtube_playlist_id if us else None

    try:
        collector = YouTubeCollector(access_token, playlist_id=playlist_id)
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
