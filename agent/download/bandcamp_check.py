"""
Bandcamp presence check via server-side Brave Search API proxy.

The agent sends the query to the server, which calls Brave Search using
a centrally-managed API key — no per-user configuration required.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://trackmanager.app"
_TIMEOUT = 15


def exists(query: str, token: str = "") -> bool:
    """Return True if the server finds a bandcamp.com result for `query`."""
    if not token:
        logger.info("Bandcamp check skipped: no token")
        return False
    try:
        resp = httpx.get(
            f"{API_URL}/api/check-bandcamp",
            params={"q": query},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.info("Bandcamp check HTTP %s for %r", resp.status_code, query)
            return False
        result = resp.json().get("found", False)
        logger.info("Bandcamp check %r → %s", query, result)
        return result
    except Exception:
        logger.exception("Bandcamp check failed for %r", query)
        return False
