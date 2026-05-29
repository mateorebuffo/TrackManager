import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
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


def _require_admin(current_user: User) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Solo administradores pueden usar Spotify.")


_SP_DC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://open.spotify.com/",
    "Origin": "https://open.spotify.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def _get_sp_dc_access_token(db: Session, user_id: int) -> str | JSONResponse:
    """Exchange the stored sp_dc cookie for a short-lived Spotify access token.
    Returns the token string on success, or a JSONResponse with error on failure.
    """
    import httpx as _httpx
    from app.models.user_settings import UserSettings as _US
    us = db.query(_US).filter_by(user_id=user_id).first()
    sp_dc = (us.spotify_sp_dc or "").strip() if us else ""
    if not sp_dc:
        return JSONResponse({"error": "No hay cookie sp_dc configurada. Configurala en Ajustes."}, status_code=400)
    try:
        resp = _httpx.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            cookies={"sp_dc": sp_dc},
            headers=_SP_DC_HEADERS,
            timeout=10,
            follow_redirects=True,
        )
        try:
            data = resp.json()
        except Exception:
            logger.error("Spotify devolvió respuesta no-JSON (HTTP %s): %s", resp.status_code, resp.text[:300])
            return JSONResponse(
                {"error": f"Spotify devolvió una respuesta inesperada (HTTP {resp.status_code}). "
                          "Verificá que tu cookie sp_dc sea válida y esté actualizada."},
                status_code=502,
            )
        if data.get("isAnonymous") is True:
            return JSONResponse({"error": "Cookie sp_dc inválida o expirada. Actualizala en Ajustes."}, status_code=401)
        token = data.get("accessToken")
        if not token:
            return JSONResponse({"error": "No se pudo obtener el token de Spotify."}, status_code=502)
        return token
    except Exception as exc:
        logger.exception("Error exchanging sp_dc for access token")
        return JSONResponse({"error": f"Error conectando con Spotify: {exc}"}, status_code=502)

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
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    sc_token = (us.soundcloud_oauth_token or "") if us else ""
    if not sc_token:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/settings?missing=soundcloud", status_code=303)
        return {"status": "error", "detail": "SoundCloud OAuth token no configurado."}
    if (limited := _sync_rate_limited(current_user.id, request)):
        return limited
    collector = SoundCloudCollector(oauth_token=sc_token)
    result: SyncResult = run_sync(collector, db, user_id=current_user.id)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(
            url=f"/tracks/pending?synced={result.new_tracks}&source=soundcloud",
            status_code=303,
        )

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
def spotify_connect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.config import settings
    if not settings.spotify_redirect_uri:
        return HTMLResponse(
            "<h3>Error: SPOTIFY_REDIRECT_URI no configurado.</h3>"
            "<p>Añadí <code>SPOTIFY_REDIRECT_URI=https://tu-app.railway.app/sync/spotify/callback</code> "
            "en las variables de entorno y registralo en el Spotify Developer Console.</p>",
            status_code=500,
        )
    try:
        url = spotify_auth.get_auth_url(current_user.id, db)
    except RuntimeError:
        return RedirectResponse(url="/settings?missing=spotify_credentials", status_code=303)
    return RedirectResponse(url=url)


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


@router.get("/spotify/access-token")
def spotify_access_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a Spotify access token to the browser so it can call the Spotify API directly."""
    try:
        import json as _json
        from app.models.user_settings import UserSettings as _US
        token = spotify_auth.get_valid_access_token(db, current_user.id)
        us = db.query(_US).filter_by(user_id=current_user.id).first()
        token_data = _json.loads(us.spotify_token_json) if us and us.spotify_token_json else {}
        return JSONResponse({"access_token": token, "scope": token_data.get("scope", "")})
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/spotify/select-playlist")
def spotify_select_playlist(
    playlist_id: str = Form(...),
    playlist_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    if not us:
        us = UserSettings(user_id=current_user.id)
        db.add(us)
    us.spotify_playlist_id = playlist_id
    us.spotify_playlist_name = playlist_name
    db.commit()

    new_tracks = 0
    sync_error = False
    try:
        access_token = spotify_auth.get_valid_access_token(db, current_user.id)
        collector = SpotifyCollector(access_token, playlist_id=playlist_id)
        result = run_sync(collector, db, user_id=current_user.id)
        new_tracks = result.new_tracks
    except Exception:
        logger.exception("Spotify sync after playlist selection failed")
        sync_error = True

    if sync_error:
        return RedirectResponse(
            url="/tracks/pending?sync_error=1&source=spotify",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/tracks/pending?synced={new_tracks}&source=spotify",
        status_code=303,
    )


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
    except Exception as exc:
        # If the saved playlist is inaccessible, clear it so the user is prompted to reselect
        if playlist_id and us and ("403" in str(exc) or "Forbidden" in str(exc)):
            us.spotify_playlist_id = None
            us.spotify_playlist_name = None
            db.commit()
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            error_param = "spotify_playlist_forbidden" if playlist_id and ("403" in str(exc) or "Forbidden" in str(exc)) else "1"
            return RedirectResponse(
                url=f"/tracks/pending?sync_error={error_param}&source=spotify",
                status_code=303,
            )
        return {"status": "error", "detail": str(exc)}

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(
            url=f"/tracks/pending?synced={result.new_tracks}&source=spotify",
            status_code=303,
        )

    return {
        "status": "ok",
        "total_fetched": result.total_fetched,
        "new_tracks": result.new_tracks,
        "skipped_existing": result.skipped_existing,
        "strong_duplicates_flagged": result.strong_duplicates_flagged,
        "weak_duplicates_flagged": result.weak_duplicates_flagged,
        "errors": result.errors,
    }


@router.post("/spotify/import")
async def import_spotify_tracks(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Receive pre-fetched Spotify tracks from the browser and run them through ingestion.
    Only for non-admin users (who fetch tracks client-side to avoid server IP exposure).
    """
    if current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin usa el flujo OAuth.")

    body = await request.json()
    raw_tracks: list[dict] = body.get("tracks", [])
    playlist_id: str = body.get("playlist_id", "")
    playlist_name: str = body.get("playlist_name", "")

    if not raw_tracks:
        return JSONResponse({"error": "No se recibieron tracks."}, status_code=400)

    # Save playlist selection
    from app.models.user_settings import UserSettings
    us = db.query(UserSettings).filter_by(user_id=current_user.id).first()
    if not us:
        us = UserSettings(user_id=current_user.id)
        db.add(us)
    if playlist_id:
        us.spotify_playlist_id = playlist_id
        us.spotify_playlist_name = playlist_name
        db.commit()

    from app.collectors.base import RawTrack
    from datetime import datetime

    def _build_raw_tracks(tracks: list[dict]):
        for t in tracks:
            track_id = t.get("id")
            if not track_id:
                continue
            artists = t.get("artists") or []
            raw_artist = ", ".join(a["name"] for a in artists if a.get("name")) or None
            duration_ms = t.get("duration_ms")
            liked_at = None
            if t.get("added_at"):
                try:
                    liked_at = datetime.fromisoformat(t["added_at"].replace("Z", "+00:00"))
                except ValueError:
                    pass
            album = t.get("album") or {}
            yield RawTrack(
                source="spotify",
                source_track_id=track_id,
                source_url=t.get("url") or f"https://open.spotify.com/track/{track_id}",
                raw_title=t.get("name") or "",
                raw_artist=raw_artist,
                duration_seconds=duration_ms / 1000.0 if duration_ms else None,
                liked_at=liked_at,
                raw_metadata={
                    "artists": [{"id": a.get("id"), "name": a.get("name")} for a in artists],
                    "album_id": album.get("id"),
                    "album_name": album.get("name"),
                },
            )

    class _StaticCollector:
        source_name = "spotify"
        def __init__(self, tracks): self._tracks = tracks
        def fetch_liked_tracks(self): return _build_raw_tracks(self._tracks)

    try:
        result: SyncResult = run_sync(_StaticCollector(raw_tracks), db, user_id=current_user.id)
    except Exception as exc:
        logger.exception("Spotify client-side import failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    return JSONResponse({
        "new_tracks": result.new_tracks,
        "skipped_existing": result.skipped_existing,
        "total_fetched": result.total_fetched,
        "errors": result.errors,
    })


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

    new_tracks = 0
    sync_error = False
    try:
        access_token = youtube_auth.get_valid_access_token(db, current_user.id)
        collector = YouTubeCollector(access_token, playlist_id=playlist_id)
        result = run_sync(collector, db, user_id=current_user.id)
        new_tracks = result.new_tracks
    except Exception:
        logger.exception("YouTube sync after playlist selection failed")
        sync_error = True

    if sync_error:
        return RedirectResponse(
            url="/tracks/pending?sync_error=1&source=youtube",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/tracks/pending?synced={new_tracks}&source=youtube",
        status_code=303,
    )


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
        return RedirectResponse(
            url=f"/tracks/pending?synced={result.new_tracks}&source=youtube",
            status_code=303,
        )

    return {
        "status": "ok",
        "total_fetched": result.total_fetched,
        "new_tracks": result.new_tracks,
        "skipped_existing": result.skipped_existing,
        "strong_duplicates_flagged": result.strong_duplicates_flagged,
        "weak_duplicates_flagged": result.weak_duplicates_flagged,
        "errors": result.errors,
    }
