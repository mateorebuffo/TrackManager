"""
Discogs presence check.

Uses the public Discogs search API to check if a release exists.
No authentication required for basic searches (25 req/min unauthenticated).
Returns True if results are found — used to classify tracks as vinyl_only.
"""
from __future__ import annotations

import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.discogs.com/database/search"
_HEADERS = {
    "User-Agent": "MusicCollectorMVP/1.0",
}
_TIMEOUT = 12

# Strip common suffixes that confuse Discogs search (e.g. "72" as a year suffix)
_SUFFIX_RE = re.compile(r"\s+\d{2,4}$")


def _clean_query(query: str) -> str:
    """Remove trailing standalone numbers (often years) that trip up Discogs."""
    return _SUFFIX_RE.sub("", query.strip())


def _search(query: str) -> int:
    """Return the number of Discogs results for query, or -1 on HTTP error."""
    params = {"q": query, "type": "release", "per_page": "1"}
    resp = httpx.get(_BASE, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    if resp.status_code == 429:
        return -1  # rate-limited
    resp.raise_for_status()
    return resp.json().get("pagination", {}).get("items", 0)


def exists(query: str) -> bool:
    """Return True if Discogs has at least one release result for `query`."""
    queries = [query]
    cleaned = _clean_query(query)
    if cleaned != query:
        queries.append(cleaned)

    for attempt, q in enumerate(queries):
        try:
            count = _search(q)
            if count == -1:
                # Rate-limited — wait and retry once
                logger.warning("Discogs rate-limited for %r, retrying after 3s", q)
                time.sleep(3)
                count = _search(q)
                if count == -1:
                    logger.warning("Discogs still rate-limited for %r, skipping", q)
                    return False
            logger.debug("Discogs search %r → %d results", q, count)
            if count > 0:
                return True
        except Exception:
            logger.exception("Discogs check failed for %r", q)

    return False
