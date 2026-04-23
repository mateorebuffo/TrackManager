"""
Spotify collector.

Reads only the playlist named 'Track Manager' from the authenticated user's account.
Uses Spotify's structured metadata directly — no noisy title parsing needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator

import httpx

from app.collectors.base import BaseCollector, RawTrack

logger = logging.getLogger(__name__)

_PLAYLIST_NAME = "Track Manager"
_BASE = "https://api.spotify.com/v1"


class SpotifyCollector(BaseCollector):
    """
    Collector that syncs tracks from a Spotify playlist.
    Requires a valid access token (obtain via spotify_auth.get_valid_access_token()).
    If playlist_id is provided it is used directly; otherwise falls back to searching
    for a playlist named _PLAYLIST_NAME.
    """

    def __init__(self, access_token: str, playlist_id: str | None = None) -> None:
        self._token = access_token
        self._playlist_id = playlist_id

    @property
    def source_name(self) -> str:
        return "spotify"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def list_playlists(self) -> list[dict]:
        """Return all user playlists as [{"id": ..., "name": ...}]."""
        me = self._get(f"{_BASE}/me")
        user_id = me["id"]
        playlists: list[dict] = []
        url = f"{_BASE}/me/playlists"
        while url:
            data = self._get(url, limit=50)
            for pl in data.get("items", []):
                if pl and pl.get("id") and pl.get("name"):
                    owned = pl.get("owner", {}).get("id") == user_id
                    playlists.append({
                        "id": pl["id"],
                        "name": pl["name"],
                        "owned": owned,
                        "tracks": pl.get("tracks", {}).get("total", 0),
                    })
            url = data.get("next")
        return playlists

    def fetch_liked_tracks(self) -> Iterator[RawTrack]:
        if self._playlist_id:
            playlist_id = self._playlist_id
            logger.info("Sincronizando desde Spotify playlist id=%s", playlist_id)
        else:
            playlist = self._find_playlist()
            if playlist is None:
                raise ValueError(
                    f"No se encontró ninguna playlist llamada '{_PLAYLIST_NAME}' en tu cuenta de Spotify. "
                    f"Elegí una playlist desde el menú de sincronización."
                )
            playlist_id = playlist["id"]
            logger.info(
                "Sincronizando desde Spotify playlist '%s' (%s)",
                playlist["name"],
                playlist_id,
            )

        url = f"{_BASE}/playlists/{playlist_id}/tracks"
        params: dict = {
            "limit": 100,
            "fields": "items(added_at,track(id,name,artists,external_urls,duration_ms,album(id,name))),next",
        }

        while url:
            data = self._get(url, **params)
            params = {}

            for item in data.get("items", []):
                track = item.get("track")
                # Skip local files, unavailable tracks, null entries
                if not track or not track.get("id"):
                    continue
                raw = self._parse_track(track, added_at=item.get("added_at"))
                if raw:
                    yield raw

            url = data.get("next")

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get(self, url: str, **params) -> dict:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            params=params or None,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _find_playlist(self) -> dict | None:
        """
        Paginate through the user's playlists to find one named exactly _PLAYLIST_NAME.

        Edge cases:
        - Not found → returns None (caller raises a clear error)
        - Multiple matches → prefer playlist owned by the current user;
          if still ambiguous, use the first one and log a warning
        """
        me = self._get(f"{_BASE}/me")
        user_id = me["id"]

        matches: list[dict] = []
        url = f"{_BASE}/me/playlists"
        while url:
            data = self._get(url, limit=50)
            for pl in data.get("items", []):
                if pl and pl.get("name") == _PLAYLIST_NAME:
                    matches.append(pl)
            url = data.get("next")

        if not matches:
            return None

        if len(matches) == 1:
            return matches[0]

        # Multiple matches — prefer ones owned by the current user
        owned = [pl for pl in matches if pl.get("owner", {}).get("id") == user_id]
        if len(owned) == 1:
            return owned[0]

        logger.warning(
            "Múltiples playlists llamadas '%s' encontradas (%d). "
            "Usando la primera. IDs: %s",
            _PLAYLIST_NAME,
            len(matches),
            [pl["id"] for pl in matches],
        )
        return matches[0]

    def _parse_track(self, track: dict, added_at: str | None) -> RawTrack | None:
        track_id: str = track["id"]
        name: str = track["name"]

        artists = track.get("artists") or []
        raw_artist = ", ".join(a["name"] for a in artists) if artists else None

        source_url = (
            track.get("external_urls", {}).get("spotify")
            or f"https://open.spotify.com/track/{track_id}"
        )

        duration_ms = track.get("duration_ms")
        duration_seconds = duration_ms / 1000.0 if duration_ms else None

        liked_at: datetime | None = None
        if added_at:
            try:
                liked_at = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
            except ValueError:
                pass

        album = track.get("album") or {}
        raw_metadata = {
            "artists": [{"id": a.get("id"), "name": a["name"]} for a in artists],
            "album_id": album.get("id"),
            "album_name": album.get("name"),
        }

        return RawTrack(
            source="spotify",
            source_track_id=track_id,
            source_url=source_url,
            raw_title=name,
            raw_artist=raw_artist,
            duration_seconds=duration_seconds,
            liked_at=liked_at,
            raw_metadata=raw_metadata,
        )
