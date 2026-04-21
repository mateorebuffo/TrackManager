"""
YouTube collector.

Reads only the playlist named 'Music Collector YT' from the authenticated user's account.
Uses YouTube Data API v3.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterator

import httpx

from app.collectors.base import BaseCollector, RawTrack

logger = logging.getLogger(__name__)

_PLAYLIST_NAME = "Music Collector YT"
_BASE = "https://www.googleapis.com/youtube/v3"

# Titles that YouTube uses for unavailable/deleted/private videos
_UNAVAILABLE_TITLES = {"Deleted video", "Private video", "[Deleted video]", "[Private video]"}

_ISO8601_DURATION = re.compile(
    r"PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?"
)


def _parse_duration(iso: str) -> float | None:
    """Convert ISO 8601 duration string (e.g. 'PT3M45S') to seconds."""
    m = _ISO8601_DURATION.fullmatch(iso)
    if not m:
        return None
    h = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    s = int(m.group("s") or 0)
    total = h * 3600 + mins * 60 + s
    return float(total) if total else None


class YouTubeCollector(BaseCollector):
    """
    Collector that syncs videos from the 'Music Collector YT' YouTube playlist.
    Requires a valid access token (obtain via youtube_auth.get_valid_access_token()).
    """

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    @property
    def source_name(self) -> str:
        return "youtube"

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch_liked_tracks(self) -> Iterator[RawTrack]:
        playlist = self._find_playlist()
        if playlist is None:
            raise ValueError(
                f"No se encontró ninguna playlist llamada '{_PLAYLIST_NAME}' en tu cuenta de YouTube. "
                f"Creala y agregá videos para empezar a sincronizar."
            )

        playlist_id = playlist["id"]
        logger.info(
            "Sincronizando desde YouTube playlist '%s' (%s)",
            playlist["snippet"]["title"],
            playlist_id,
        )

        page_token: str | None = None
        while True:
            params: dict = {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token

            data = self._get(f"{_BASE}/playlistItems", **params)
            items = data.get("items", [])

            # Collect video IDs for this page, then fetch durations in one batch call
            video_ids = [
                item["snippet"]["resourceId"]["videoId"]
                for item in items
                if item.get("snippet", {}).get("resourceId", {}).get("kind") == "youtube#video"
                and item["snippet"]["resourceId"].get("videoId")
            ]
            durations = self._fetch_durations(video_ids)

            for item in items:
                raw = self._parse_item(item, durations)
                if raw:
                    yield raw

            page_token = data.get("nextPageToken")
            if not page_token:
                break

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get(self, url: str, **params) -> dict:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _find_playlist(self) -> dict | None:
        """
        Paginate through the user's playlists to find one named exactly _PLAYLIST_NAME.

        Edge cases:
        - Not found → returns None (caller raises a clear error)
        - Multiple matches → use the first one and log a warning (all are owned by the
          authenticated user since we use mine=true)
        """
        matches: list[dict] = []
        page_token: str | None = None

        while True:
            params: dict = {
                "part": "snippet",
                "mine": "true",
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token

            data = self._get(f"{_BASE}/playlists", **params)

            for pl in data.get("items", []):
                if pl.get("snippet", {}).get("title") == _PLAYLIST_NAME:
                    matches.append(pl)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        if not matches:
            return None

        if len(matches) > 1:
            logger.warning(
                "Múltiples playlists llamadas '%s' encontradas (%d). "
                "Usando la primera. IDs: %s",
                _PLAYLIST_NAME,
                len(matches),
                [pl["id"] for pl in matches],
            )

        return matches[0]

    def _fetch_durations(self, video_ids: list[str]) -> dict[str, float]:
        """
        Fetch duration for a list of video IDs via videos.list (contentDetails).
        Returns a dict mapping video_id -> duration_seconds.
        YouTube allows up to 50 IDs per request; our pages are already ≤ 50.
        """
        if not video_ids:
            return {}
        data = self._get(
            f"{_BASE}/videos",
            part="contentDetails",
            id=",".join(video_ids),
            maxResults=50,
        )
        result: dict[str, float] = {}
        for v in data.get("items", []):
            vid = v.get("id")
            iso = v.get("contentDetails", {}).get("duration", "")
            parsed = _parse_duration(iso)
            if vid and parsed is not None:
                result[vid] = parsed
        return result

    def _parse_item(self, item: dict, durations: dict[str, float] | None = None) -> RawTrack | None:
        snippet = item.get("snippet", {})

        # Video ID lives inside resourceId
        resource = snippet.get("resourceId", {})
        if resource.get("kind") != "youtube#video":
            return None  # skip non-video items (playlists within playlists, etc.)

        video_id = resource.get("videoId")
        if not video_id:
            return None

        title: str = snippet.get("title", "").strip()

        # Skip deleted / private / unavailable videos
        if not title or title in _UNAVAILABLE_TITLES:
            logger.debug("Skipping unavailable video: %s", video_id)
            return None

        # Channel that uploaded the video (not the playlist owner)
        raw_artist: str | None = snippet.get("videoOwnerChannelTitle") or None

        source_url = f"https://www.youtube.com/watch?v={video_id}"

        # publishedAt on a playlistItem = when it was added to the playlist
        added_at_raw: str | None = snippet.get("publishedAt")
        liked_at: datetime | None = None
        if added_at_raw:
            try:
                liked_at = datetime.fromisoformat(added_at_raw.replace("Z", "+00:00"))
            except ValueError:
                pass

        raw_metadata = {
            "video_id": video_id,
            "channel_title": snippet.get("videoOwnerChannelTitle"),
            "channel_id": snippet.get("videoOwnerChannelId"),
            "playlist_id": snippet.get("playlistId"),
            "description": snippet.get("description", "")[:500],  # trim for storage
        }

        return RawTrack(
            source="youtube",
            source_track_id=video_id,
            source_url=source_url,
            raw_title=title,
            raw_artist=raw_artist,
            duration_seconds=(durations or {}).get(video_id),
            liked_at=liked_at,
            raw_metadata=raw_metadata,
        )
