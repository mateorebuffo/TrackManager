"""
Discogs presence check.

Uses the public Discogs search API to check if a release exists.
No authentication required for basic searches (25 req/min unauthenticated).
Returns True if results are found — used to classify tracks as discogs_only.
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.discogs.com/database/search"
_HEADERS = {
    "User-Agent": "MusicCollectorMVP/1.0",
}
_TIMEOUT = 10


def exists(query: str) -> bool:
    """Return True if Discogs has at least one release result for `query`."""
    params = {"q": query, "type": "release", "per_page": "1"}
    try:
        resp = httpx.get(_BASE, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        count = data.get("pagination", {}).get("items", 0)
        found = count > 0
        logger.debug("Discogs search %r → %d results", query, count)
        return found
    except Exception:
        logger.exception("Discogs check failed for %r", query)
        return False
