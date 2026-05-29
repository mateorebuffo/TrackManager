"""
Tests for the sp_dc Spotify sync implementation.

Sections
--------
1. _get_sp_dc_access_token helper  — unit tests with mocked httpx
2. GET /sync/spotify/access-token  — non-admin gets token; admin gets 403
3. POST /sync/spotify/import       — ingests tracks; admin blocked; edge cases
4. POST /settings/verify/spotify-cookie — valid/invalid cookie, admin rejected
5. _StaticCollector + ingestion pipeline — raw-track building + run_sync
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth_middleware import get_current_user
from app.collectors.base import RawTrack
from app.db import Base, get_db
from app.main import app
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem
from app.models.source_track import SourceTrack
from app.models.user import User
from app.models.user_settings import UserSettings
from app.services.ingestion import run_sync


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def engine():
    _engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    connection = _engine.connect()
    # Import all models so Base.metadata is complete
    from app.models import (  # noqa: F401
        app_event, download_job, normalized_track, review_item,
        source_track, track_history, user, user_report, user_settings,
    )
    Base.metadata.create_all(bind=connection)
    yield _engine
    Base.metadata.drop_all(bind=connection)
    connection.close()
    _engine.dispose()


@pytest.fixture(scope="function")
def db_session(engine) -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def non_admin_user(db_session) -> User:
    user = User(username="testuser", hashed_password="x", is_admin=False, is_active=True, api_token="tok-100")
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture(scope="function")
def admin_user(db_session) -> User:
    user = User(username="admin", hashed_password="x", is_admin=True, is_active=True, api_token="tok-admin")
    db_session.add(user)
    db_session.flush()
    return user


def _make_client(db_session: Session, current_user: User) -> TestClient:
    """Return a configured TestClient that bypasses AuthMiddleware for the given user."""
    import app.auth_middleware as _mw
    from app.services.auth import make_session_token

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: current_user

    # Patch the two auth-middleware helpers that use the module-level SessionLocal
    # (which is a separate in-memory DB and has no test users).
    _patches = [
        patch.object(_mw, "has_any_user", return_value=True),
        patch.object(_mw, "get_user_by_id", return_value=current_user),
    ]
    for p in _patches:
        p.start()

    fake_token = make_session_token(current_user.id)
    client = TestClient(
        app,
        base_url="http://localhost",
        raise_server_exceptions=True,
        cookies={"mc_session": fake_token},
    )
    client.__enter__()

    # Store cleanup info on the client object
    client._test_patches = _patches  # type: ignore[attr-defined]
    return client


@pytest.fixture(scope="function")
def non_admin_client(db_session, non_admin_user):
    c = _make_client(db_session, non_admin_user)
    yield c
    c.__exit__(None, None, None)
    for p in c._test_patches:
        p.stop()
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def admin_client(db_session, admin_user):
    c = _make_client(db_session, admin_user)
    yield c
    c.__exit__(None, None, None)
    for p in c._test_patches:
        p.stop()
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sp_dc_settings(db_session: Session, user_id: int, sp_dc: str = "valid_sp_dc_cookie") -> UserSettings:
    us = UserSettings(user_id=user_id, spotify_sp_dc=sp_dc)
    db_session.add(us)
    db_session.flush()
    return us


def _spotify_token_response(access_token: str = "test_access_token", is_anonymous: bool = False) -> MagicMock:
    """Mock httpx response for open.spotify.com/get_access_token."""
    mock = MagicMock()
    data: dict = {"isAnonymous": is_anonymous}
    if not is_anonymous:
        data["accessToken"] = access_token
    mock.json.return_value = data
    mock.status_code = 200
    return mock


def _spotify_me_response(user_id: str = "spotify_user_123") -> MagicMock:
    """Mock httpx response for api.spotify.com/v1/me."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"id": user_id}
    return mock


# Realistic Spotify track payloads as returned by the browser
_SAMPLE_TRACKS: list[dict] = [
    {
        "id": "spotify_track_001",
        "name": "Glue",
        "artists": [{"id": "artist_bicep", "name": "Bicep"}],
        "duration_ms": 480_000,
        "added_at": "2024-01-15T12:00:00Z",
        "album": {"id": "album_001", "name": "Bicep"},
        "url": "https://open.spotify.com/track/spotify_track_001",
    },
    {
        "id": "spotify_track_002",
        "name": "Baby",
        "artists": [{"id": "artist_fourtet", "name": "Four Tet"}],
        "duration_ms": 360_000,
        "added_at": "2024-02-01T08:30:00Z",
        "album": {"id": "album_002", "name": "New Album"},
        "url": "https://open.spotify.com/track/spotify_track_002",
    },
]


def _build_raw_tracks(tracks: list[dict]) -> list[RawTrack]:
    """Mirror the _build_raw_tracks logic inside the import endpoint."""
    result = []
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
        result.append(
            RawTrack(
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
        )
    return result


# ===========================================================================
# 1.  _get_sp_dc_access_token  (unit tests, no HTTP)
# ===========================================================================


class TestGetSpDcAccessToken:
    """Directly test the helper that exchanges sp_dc for an access token."""

    def _call(self, db_session: Session, user_id: int):
        from app.api.sync import _get_sp_dc_access_token
        return _get_sp_dc_access_token(db_session, user_id)

    def test_no_settings_row_returns_400(self, db_session, non_admin_user):
        result = self._call(db_session, non_admin_user.id)
        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 400
        assert "error" in json.loads(result.body)

    def test_empty_sp_dc_returns_400(self, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="")
        result = self._call(db_session, non_admin_user.id)
        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 400

    def test_valid_sp_dc_returns_token_string(self, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="valid_cookie")
        with patch("httpx.get", return_value=_spotify_token_response("my_token")):
            result = self._call(db_session, non_admin_user.id)
        assert result == "my_token"

    def test_anonymous_response_returns_401(self, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="expired_cookie")
        with patch("httpx.get", return_value=_spotify_token_response(is_anonymous=True)):
            result = self._call(db_session, non_admin_user.id)
        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 401

    def test_missing_access_token_key_returns_502(self, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="weird_cookie")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"isAnonymous": False}  # no accessToken key
        with patch("httpx.get", return_value=mock_resp):
            result = self._call(db_session, non_admin_user.id)
        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 502

    def test_connection_error_returns_502(self, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="valid_cookie")
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            result = self._call(db_session, non_admin_user.id)
        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 502

    def test_whitespace_sp_dc_treated_as_empty(self, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="   ")
        result = self._call(db_session, non_admin_user.id)
        from fastapi.responses import JSONResponse
        assert isinstance(result, JSONResponse)
        assert result.status_code == 400


# ===========================================================================
# 2.  GET /sync/spotify/access-token  (endpoint tests)
# ===========================================================================


class TestSpotifyAccessTokenEndpoint:
    def test_non_admin_with_valid_sp_dc_returns_token(self, non_admin_client, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="valid_cookie")
        with patch("httpx.get", return_value=_spotify_token_response("returned_token")):
            resp = non_admin_client.get("/sync/spotify/access-token")
        assert resp.status_code == 200
        assert resp.json()["access_token"] == "returned_token"

    def test_non_admin_without_sp_dc_returns_400(self, non_admin_client):
        resp = non_admin_client.get("/sync/spotify/access-token")
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_admin_returns_403(self, admin_client):
        resp = admin_client.get("/sync/spotify/access-token")
        assert resp.status_code == 403

    def test_expired_sp_dc_returns_401(self, non_admin_client, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="expired_cookie")
        with patch("httpx.get", return_value=_spotify_token_response(is_anonymous=True)):
            resp = non_admin_client.get("/sync/spotify/access-token")
        assert resp.status_code == 401
        assert "error" in resp.json()

    def test_response_contains_only_access_token_key(self, non_admin_client, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="valid_cookie")
        with patch("httpx.get", return_value=_spotify_token_response("tok123")):
            resp = non_admin_client.get("/sync/spotify/access-token")
        assert set(resp.json().keys()) == {"access_token"}


# ===========================================================================
# 3.  POST /sync/spotify/import  (endpoint tests)
# ===========================================================================


class TestImportSpotifyTracksEndpoint:
    def _payload(self, tracks=None, playlist_id="pl_abc", playlist_name="My Playlist"):
        return {"tracks": tracks if tracks is not None else _SAMPLE_TRACKS,
                "playlist_id": playlist_id, "playlist_name": playlist_name}

    def test_admin_returns_403(self, admin_client):
        resp = admin_client.post("/sync/spotify/import", json=self._payload())
        assert resp.status_code == 403

    def test_empty_tracks_returns_400(self, non_admin_client):
        resp = non_admin_client.post("/sync/spotify/import", json=self._payload(tracks=[]))
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_valid_tracks_returns_200_with_counts(self, non_admin_client):
        resp = non_admin_client.post("/sync/spotify/import", json=self._payload())
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_tracks"] == 2
        assert data["total_fetched"] == 2
        assert data["errors"] == 0

    def test_creates_review_items_in_db(self, non_admin_client, db_session):
        non_admin_client.post("/sync/spotify/import", json=self._payload())
        assert db_session.query(ReviewItem).count() == 2

    def test_source_track_source_is_spotify(self, non_admin_client, db_session):
        non_admin_client.post("/sync/spotify/import", json=self._payload())
        for st in db_session.query(SourceTrack).all():
            assert st.source == "spotify"

    def test_duration_stored_in_seconds(self, non_admin_client, db_session):
        resp = non_admin_client.post("/sync/spotify/import", json=self._payload(tracks=_SAMPLE_TRACKS[:1]))
        assert resp.status_code == 200
        st = db_session.query(SourceTrack).first()
        assert st.duration_seconds == pytest.approx(480.0)

    def test_saves_playlist_selection(self, non_admin_client, db_session, non_admin_user):
        non_admin_client.post("/sync/spotify/import", json=self._payload(playlist_id="pl_test", playlist_name="Test PL"))
        db_session.expire_all()
        us = db_session.query(UserSettings).filter_by(user_id=non_admin_user.id).first()
        assert us is not None
        assert us.spotify_playlist_id == "pl_test"
        assert us.spotify_playlist_name == "Test PL"

    def test_idempotent_second_import_skips(self, non_admin_client):
        non_admin_client.post("/sync/spotify/import", json=self._payload())
        resp2 = non_admin_client.post("/sync/spotify/import", json=self._payload())
        data = resp2.json()
        assert data["new_tracks"] == 0
        assert data["skipped_existing"] == 2

    def test_tracks_without_id_are_skipped(self, non_admin_client):
        no_id_track = [{"name": "No ID", "artists": [{"name": "Artist"}], "duration_ms": 100_000}]
        # raw_tracks list is non-empty but _build_raw_tracks yields nothing → run_sync sees 0
        resp = non_admin_client.post("/sync/spotify/import", json=self._payload(tracks=no_id_track))
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_tracks"] == 0
        assert data["total_fetched"] == 0

    def test_response_contains_expected_keys(self, non_admin_client):
        resp = non_admin_client.post("/sync/spotify/import", json=self._payload())
        assert {"new_tracks", "skipped_existing", "total_fetched", "errors"}.issubset(resp.json().keys())


# ===========================================================================
# 4.  POST /settings/verify/spotify-cookie  (endpoint tests)
# ===========================================================================


class TestVerifySpotifyCookieEndpoint:
    _URL = "/settings/verify/spotify-cookie"

    def test_admin_returns_not_ok(self, admin_client):
        resp = admin_client.post(self._URL)
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_no_sp_dc_saved_returns_not_ok(self, non_admin_client):
        resp = non_admin_client.post(self._URL)
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_valid_cookie_returns_ok_with_user_id(self, non_admin_client, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="valid_cookie")
        token_resp = _spotify_token_response("access_tok_xyz")
        me_resp = _spotify_me_response("my_spotify_user")
        with patch("httpx.get", side_effect=[token_resp, me_resp]):
            resp = non_admin_client.post(self._URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "my_spotify_user" in data["msg"]

    def test_expired_cookie_isanonymous_returns_not_ok(self, non_admin_client, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="expired_cookie")
        with patch("httpx.get", return_value=_spotify_token_response(is_anonymous=True)):
            resp = non_admin_client.post(self._URL)
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_connection_error_returns_not_ok(self, non_admin_client, db_session, non_admin_user):
        _make_sp_dc_settings(db_session, non_admin_user.id, sp_dc="valid_cookie")
        with patch("httpx.get", side_effect=Exception("timeout")):
            resp = non_admin_client.post(self._URL)
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


# ===========================================================================
# 5.  _build_raw_tracks  +  run_sync ingestion pipeline (unit tests)
# ===========================================================================


class TestBuildRawTracksFromSpotify:
    def test_builds_correct_count(self):
        tracks = _build_raw_tracks(_SAMPLE_TRACKS)
        assert len(tracks) == 2

    def test_all_items_are_rawtrack_instances(self):
        for t in _build_raw_tracks(_SAMPLE_TRACKS):
            assert isinstance(t, RawTrack)

    def test_source_is_spotify(self):
        for t in _build_raw_tracks(_SAMPLE_TRACKS):
            assert t.source == "spotify"

    def test_track_ids_are_strings(self):
        for t in _build_raw_tracks(_SAMPLE_TRACKS):
            assert isinstance(t.source_track_id, str)

    def test_artist_extracted(self):
        tracks = _build_raw_tracks(_SAMPLE_TRACKS)
        assert tracks[0].raw_artist == "Bicep"
        assert tracks[1].raw_artist == "Four Tet"

    def test_title_extracted(self):
        tracks = _build_raw_tracks(_SAMPLE_TRACKS)
        assert tracks[0].raw_title == "Glue"
        assert tracks[1].raw_title == "Baby"

    def test_duration_converted_from_ms_to_seconds(self):
        tracks = _build_raw_tracks(_SAMPLE_TRACKS)
        assert tracks[0].duration_seconds == pytest.approx(480.0)
        assert tracks[1].duration_seconds == pytest.approx(360.0)

    def test_liked_at_is_aware_datetime(self):
        for t in _build_raw_tracks(_SAMPLE_TRACKS):
            assert t.liked_at is not None
            assert t.liked_at.tzinfo is not None

    def test_source_url_from_track(self):
        tracks = _build_raw_tracks(_SAMPLE_TRACKS)
        assert tracks[0].source_url == "https://open.spotify.com/track/spotify_track_001"

    def test_source_url_fallback_when_missing(self):
        no_url = [{
            "id": "abc123", "name": "Track", "artists": [{"name": "X"}], "duration_ms": 1000,
        }]
        tracks = _build_raw_tracks(no_url)
        assert tracks[0].source_url == "https://open.spotify.com/track/abc123"

    def test_track_without_id_is_skipped(self):
        no_id = [{"name": "No ID", "artists": [{"name": "X"}], "duration_ms": 1000}]
        assert _build_raw_tracks(no_id) == []

    def test_multiple_artists_joined_with_comma(self):
        multi = [{
            "id": "multi_001", "name": "Collab",
            "artists": [{"id": "a1", "name": "Artist A"}, {"id": "a2", "name": "Artist B"}],
            "duration_ms": 200_000,
        }]
        tracks = _build_raw_tracks(multi)
        assert tracks[0].raw_artist == "Artist A, Artist B"

    def test_no_duration_gives_none(self):
        no_dur = [{"id": "x", "name": "Track", "artists": [{"name": "X"}]}]
        tracks = _build_raw_tracks(no_dur)
        assert tracks[0].duration_seconds is None

    def test_invalid_added_at_silently_ignored(self):
        bad_date = [{
            "id": "d001", "name": "Track", "artists": [{"name": "X"}],
            "duration_ms": 1000, "added_at": "NOT_A_DATE",
        }]
        tracks = _build_raw_tracks(bad_date)
        assert tracks[0].liked_at is None

    def test_raw_metadata_contains_album_info(self):
        tracks = _build_raw_tracks(_SAMPLE_TRACKS)
        assert tracks[0].raw_metadata["album_name"] == "Bicep"
        assert tracks[0].raw_metadata["album_id"] == "album_001"

    def test_raw_metadata_contains_artist_list(self):
        tracks = _build_raw_tracks(_SAMPLE_TRACKS)
        assert isinstance(tracks[0].raw_metadata["artists"], list)
        assert tracks[0].raw_metadata["artists"][0]["name"] == "Bicep"


class _SpotifyStaticCollector:
    source_name = "spotify"

    def __init__(self, tracks: list[RawTrack]):
        self._tracks = tracks

    def fetch_liked_tracks(self):
        return iter(self._tracks)


class TestSpotifyIngestionPipeline:
    def test_run_sync_ingests_all_tracks(self, db_session):
        raw = _build_raw_tracks(_SAMPLE_TRACKS)
        result = run_sync(_SpotifyStaticCollector(raw), db_session)
        assert result.new_tracks == 2
        assert result.total_fetched == 2
        assert result.errors == 0

    def test_run_sync_creates_db_rows(self, db_session):
        raw = _build_raw_tracks(_SAMPLE_TRACKS)
        run_sync(_SpotifyStaticCollector(raw), db_session)
        assert db_session.query(SourceTrack).count() == 2
        assert db_session.query(NormalizedTrack).count() == 2
        assert db_session.query(ReviewItem).count() == 2

    def test_run_sync_idempotent(self, db_session):
        raw = _build_raw_tracks(_SAMPLE_TRACKS)
        run_sync(_SpotifyStaticCollector(raw), db_session)
        result2 = run_sync(_SpotifyStaticCollector(raw), db_session)
        assert result2.new_tracks == 0
        assert result2.skipped_existing == 2
        assert db_session.query(SourceTrack).count() == 2

    def test_source_track_source_field(self, db_session):
        raw = _build_raw_tracks(_SAMPLE_TRACKS)
        run_sync(_SpotifyStaticCollector(raw), db_session)
        for st in db_session.query(SourceTrack).all():
            assert st.source == "spotify"

    def test_normalized_artist_extracted(self, db_session):
        raw = _build_raw_tracks(_SAMPLE_TRACKS[:1])
        run_sync(_SpotifyStaticCollector(raw), db_session)
        nt = db_session.query(NormalizedTrack).first()
        assert nt.normalized_artist.lower() == "bicep"

    def test_empty_tracks_produces_zero_results(self, db_session):
        result = run_sync(_SpotifyStaticCollector([]), db_session)
        assert result.new_tracks == 0
        assert result.total_fetched == 0
        assert result.errors == 0
