"""
Discogs presence check.

Uses the Discogs search API with targeted artist= + track= parameters to
avoid false positives from unrelated releases with the same keyword.
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.discogs.com/database/search"
_HEADERS = {"User-Agent": "MusicCollectorMVP/1.0"}
_TIMEOUT = 12


def _parse(query: str) -> tuple[str, str]:
    """Split 'Artist - Track' into (artist, track). Returns ('', query) otherwise."""
    if " - " in query:
        artist, track = query.split(" - ", 1)
        return artist.strip(), track.strip()
    return "", query.strip()


def _search(params: dict) -> int:
    """Return Discogs pagination item count, or -1 on rate-limit."""
    resp = httpx.get(_BASE, params={**params, "per_page": "1"}, headers=_HEADERS, timeout=_TIMEOUT)
    if resp.status_code == 429:
        return -1
    resp.raise_for_status()
    return resp.json().get("pagination", {}).get("items", 0)


def _with_retry(params: dict, label: str) -> int:
    count = _search(params)
    if count == -1:
        logger.warning("Discogs rate-limited for %r, retrying after 3s", label)
        time.sleep(3)
        count = _search(params)
        if count == -1:
            logger.warning("Discogs still rate-limited for %r, skipping", label)
            return 0
    return count


def exists(query: str) -> bool:
    """
    Return True only if Discogs has a release where the specific artist AND
    track name match. Falls back to a generic search only when the query
    has no 'Artist - Track' separator.

    Using separate artist= and track= params prevents false positives like
    'J6 - Biohazard' matching vinyls from the unrelated band 'Biohazard'.
    """
    artist, track = _parse(query)

    if artist:
        # Targeted: must match both artist and track title on the same release
        try:
            count = _with_retry({"artist": artist, "track": track, "type": "release"}, query)
            logger.info("Discogs targeted search artist=%r track=%r → %d results", artist, track, count)
            return count > 0
        except Exception:
            logger.exception("Discogs targeted check failed for %r", query)
            return False
    else:
        # No separator — generic search (rare, query is a single title)
        try:
            count = _with_retry({"q": query, "type": "release"}, query)
            logger.info("Discogs generic search %r → %d results", query, count)
            return count > 0
        except Exception:
            logger.exception("Discogs generic check failed for %r", query)
            return False
