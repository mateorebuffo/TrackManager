"""
SoundCloud collector.

Real API path:
  GET https://api.soundcloud.com/me/likes/tracks
  Headers: Authorization: OAuth <token>
  Params:  client_id=<id>, limit=200, offset=<n>

This module ships with a MockSoundCloudCollector used when USE_MOCK_COLLECTOR=true,
and a real SoundCloudCollector that hits the actual API.
Switch by setting USE_MOCK_COLLECTOR=false in .env and providing credentials.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import httpx

from app.collectors.base import BaseCollector, RawTrack
from app.config import settings

logger = logging.getLogger(__name__)

_SC_BASE = "https://api-v2.soundcloud.com"
_PAGE_SIZE = 200


class SoundCloudCollector(BaseCollector):
    """
    Fetches liked tracks from the SoundCloud API.
    Requires SOUNDCLOUD_OAUTH_TOKEN in environment.
    """

    @property
    def source_name(self) -> str:
        return "soundcloud"

    def fetch_liked_tracks(self) -> Iterator[RawTrack]:
        if settings.use_mock_collector:
            logger.info("Using mock SoundCloud collector")
            yield from _MockSoundCloudCollector().fetch_liked_tracks()
            return

        if not settings.soundcloud_oauth_token:
            raise RuntimeError(
                "SOUNDCLOUD_OAUTH_TOKEN is not set. "
                "Set USE_MOCK_COLLECTOR=true to use mock data."
            )

        yield from self._fetch_real()

    def _fetch_real(self) -> Iterator[RawTrack]:
        headers = {"Authorization": f"OAuth {settings.soundcloud_oauth_token}"}
        base_params = {"client_id": settings.soundcloud_client_id}

        with httpx.Client(timeout=30) as client:
            # Step 1: get user ID
            me_resp = client.get(f"{_SC_BASE}/me", headers=headers, params=base_params)
            me_resp.raise_for_status()
            user_id = me_resp.json()["id"]
            logger.info("SoundCloud user_id=%s", user_id)

            # Step 2: paginate likes
            # First request uses explicit params; subsequent requests embed all params
            # in the URL directly because httpx params={} would wipe existing query string.
            next_url: str | None = _inject_params(
                f"{_SC_BASE}/users/{user_id}/likes",
                limit=_PAGE_SIZE,
                linked_partitioning=1,
                **base_params,
            )
            seen_urls: set[str] = set()

            while next_url:
                if next_url in seen_urls:
                    logger.warning("Pagination loop detected — stopping")
                    break
                seen_urls.add(next_url)

                # No params= argument — all params are already in the URL
                resp = client.get(next_url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                next_href = data.get("next_href")
                collection = data.get("collection", [])
                logger.info("SC: %d items, next_href=%s", len(collection), next_href)

                if not collection:
                    break

                for item in collection:
                    track = item.get("track")
                    if track is None:
                        continue  # skip playlists
                    # item["created_at"] = when the like was made (not track upload date)
                    yield _parse_sc_track(track, liked_at_raw=item.get("created_at"))

                if next_href:
                    next_url = _inject_params(next_href, **base_params, linked_partitioning=1)
                else:
                    next_url = None


def _inject_params(url: str, **kwargs) -> str:
    """Add/override query params in a URL without losing existing ones."""
    parsed = urlparse(url)
    existing = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    existing.update({k: str(v) for k, v in kwargs.items()})
    return urlunparse(parsed._replace(query=urlencode(existing)))


def _parse_sc_track(track: dict, liked_at_raw: str | None = None) -> RawTrack:
    """Convert a raw SoundCloud API track dict into a RawTrack."""
    user = track.get("user", {})
    artist = user.get("username") or user.get("full_name")

    # liked_at_raw comes from the item level (when the like was made)
    # fallback to track fields for mock/legacy data
    if not liked_at_raw:
        liked_at_raw = track.get("liked_at") or track.get("created_at")
    liked_at: datetime | None = None
    if liked_at_raw:
        try:
            liked_at = datetime.fromisoformat(liked_at_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    publisher_metadata = track.get("publisher_metadata") or {}
    publisher_artist = publisher_metadata.get("artist") or None

    return RawTrack(
        source="soundcloud",
        source_track_id=str(track["id"]),
        source_url=track.get("permalink_url", ""),
        raw_title=track.get("title", "Unknown"),
        raw_artist=artist,
        duration_seconds=(track.get("duration") or 0) / 1000,  # ms → s
        liked_at=liked_at,
        raw_metadata={
            "genre": track.get("genre"),
            "tag_list": track.get("tag_list"),
            "playback_count": track.get("playback_count"),
            "likes_count": track.get("likes_count"),
            "waveform_url": track.get("waveform_url"),
            "publisher_artist": publisher_artist,
        },
    )


# ---------------------------------------------------------------------------
# Mock collector — realistic fake data for development / testing
# ---------------------------------------------------------------------------

_MOCK_TRACKS = [
    {
        "id": "1001",
        "title": "Bicep - Glue (Extended Mix) [FREE DOWNLOAD]",
        "artist": "Bicep",
        "url": "https://soundcloud.com/bicep/glue-extended",
        "duration_ms": 480000,
        "liked_at": "2024-03-15T10:22:00Z",
    },
    {
        "id": "1002",
        "title": "Four Tet - Baby (Original Mix) 🎵",
        "artist": "Four Tet",
        "url": "https://soundcloud.com/four-tet/baby",
        "duration_ms": 360000,
        "liked_at": "2024-03-14T08:00:00Z",
    },
    {
        "id": "1003",
        "title": "Bonobo - Kiara (Radio Edit) [OUT NOW]",
        "artist": "Bonobo",
        "url": "https://soundcloud.com/bonobo/kiara-radio",
        "duration_ms": 210000,
        "liked_at": "2024-03-13T18:45:00Z",
    },
    {
        "id": "1004",
        "title": "Jamie xx - Loud Places (Official Video) #freemusic",
        "artist": "Jamie xx",
        "url": "https://soundcloud.com/jamiexx/loud-places",
        "duration_ms": 293000,
        "liked_at": "2024-03-12T14:30:00Z",
    },
    {
        "id": "1005",
        "title": "Floating Points - LesAlpx",
        "artist": "Floating Points",
        "url": "https://soundcloud.com/floating-points/lesalpx",
        "duration_ms": 430000,
        "liked_at": "2024-03-11T09:15:00Z",
    },
    {
        "id": "1006",
        "title": "Mount Kimbie - Before I Move Off (VIP Mix)",
        "artist": "Mount Kimbie",
        "url": "https://soundcloud.com/mountkimbie/before-i-move-off-vip",
        "duration_ms": 320000,
        "liked_at": "2024-03-10T20:00:00Z",
    },
    {
        "id": "1007",
        "title": "Jon Hopkins - Emerald Rush [Official Audio] OUT NOW",
        "artist": "Jon Hopkins",
        "url": "https://soundcloud.com/jonhopkins/emerald-rush",
        "duration_ms": 395000,
        "liked_at": "2024-03-09T16:00:00Z",
    },
    {
        "id": "1008",
        "title": "Moderat - Bad Kingdom (Club Mix) [Lyrics Video]",
        "artist": "Moderat",
        "url": "https://soundcloud.com/moderat/bad-kingdom-club",
        "duration_ms": 345000,
        "liked_at": "2024-03-08T12:00:00Z",
    },
    # Deliberate near-duplicate of 1001 to test deduplication
    {
        "id": "1009",
        "title": "Bicep - Glue (Radio Edit)",
        "artist": "Bicep",
        "url": "https://soundcloud.com/bicep/glue-radio",
        "duration_ms": 220000,
        "liked_at": "2024-03-07T10:00:00Z",
    },
    {
        "id": "1010",
        "title": "Objekt - Dogma (Extended) 🔥 FREE DL",
        "artist": "Objekt",
        "url": "https://soundcloud.com/objekt/dogma",
        "duration_ms": 510000,
        "liked_at": "2024-03-06T08:30:00Z",
    },
]


class _MockSoundCloudCollector(BaseCollector):
    @property
    def source_name(self) -> str:
        return "soundcloud"

    def fetch_liked_tracks(self) -> Iterator[RawTrack]:
        for t in _MOCK_TRACKS:
            liked_at = datetime.fromisoformat(t["liked_at"].replace("Z", "+00:00"))
            yield RawTrack(
                source="soundcloud",
                source_track_id=t["id"],
                source_url=t["url"],
                raw_title=t["title"],
                raw_artist=t["artist"],
                duration_seconds=t["duration_ms"] / 1000,
                liked_at=liked_at,
                raw_metadata={"mock": True},
            )
