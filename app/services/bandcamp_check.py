"""
Bandcamp presence check.

Searches Bandcamp for a track and returns True if at least one result is found.
Does not download — only used to classify tracks as bandcamp_only.
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 10


def exists(query: str) -> bool:
    """Return True if Bandcamp has at least one track result for `query`."""
    url = f"https://bandcamp.com/search?q={quote_plus(query)}&item_type=t"
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        # Bandcamp embeds result items in <li class="searchresult data-search">
        found = 'class="result-items"' in resp.text and 'searchresult' in resp.text
        logger.debug("Bandcamp search %r → %s", query, "found" if found else "not found")
        return found
    except Exception:
        logger.exception("Bandcamp check failed for %r", query)
        return False
