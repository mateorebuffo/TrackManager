"""
Muzpa search and download service.

Search endpoint: GET https://srv.muzpa.com/a/ms/media/search
Download endpoint: GET https://srv.muzpa.com/dwnld/track/{id}.mp3
Auth: session cookie SESS=...
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from app.services.audio_verify import verify_mp3


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower())


def _track_similarity(query: str, track: dict) -> float:
    """Compare query against the track filename (which Muzpa uses as display title)."""
    filename = track.get("filename") or track.get("title") or ""
    return SequenceMatcher(None, _normalize(query), _normalize(filename)).ratio()

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://srv.muzpa.com/a/ms/media/search"
_DOWNLOAD_URL = "https://srv.muzpa.com/dwnld/track/{id}.mp3?iframe"


def _is_vinyl_only(track: dict) -> bool:
    """Check if a Muzpa track is marked as vinyl-only."""
    text = " ".join([
        track.get("title") or "",
        track.get("filename") or "",
        track.get("fullnm_html") or "",
        track.get("subtitle") or "",
    ]).upper()
    return "VINYL ONLY" in text or "VINYL-ONLY" in text


def search(query: str, sess: str) -> tuple[dict | None, str]:
    """
    Search Muzpa for a track.

    Returns (track, status) where status is:
      "found"      — downloadable MP3 track found
      "vinyl_only" — track exists but is vinyl-only (no downloadable MP3)
      "not_found"  — no results match the query
    """
    try:
        resp = httpx.get(
            _SEARCH_URL,
            params={
                "mp3prefered": "true",
                "page": 0,
                "popular_order": "false",
                "text": query,
            },
            headers={"Cookie": f"SESS={sess}"},
            timeout=20,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning("Muzpa search HTTP error: %s", e)
        return None, "not_found"
    except httpx.RequestError as e:
        logger.warning("Muzpa search network error: %s", e)
        return None, "not_found"

    data = resp.json()

    satisfying: list[dict] = []
    all_tracks: list[dict] = []

    for album in data.get("albums") or []:
        for track in album.get("tracks") or []:
            # Include MP3 tracks AND non-MP3 tracks that have an MP3 version available
            if track.get("format") == "mp3" or track.get("mp3version"):
                all_tracks.append(track)
                if track.get("satisfies"):
                    satisfying.append(track)

    # Log all satisfying results for debugging
    if satisfying:
        for t in satisfying:
            vinyl = _is_vinyl_only(t)
            logger.info(
                "Muzpa satisfies=True: %r | vinyl_only=%s | id=%s",
                t.get("filename"), vinyl, t.get("id"),
            )
    else:
        logger.info(
            "Muzpa: no satisfies=True result for %r (%d total tracks returned)",
            query, len(all_tracks),
        )
        for t in all_tracks[:5]:  # log up to 5 results for reference
            logger.info("  Muzpa result: %r | satisfies=%s | vinyl=%s",
                        t.get("filename"), t.get("satisfies"), _is_vinyl_only(t))

    # Pick the satisfying non-vinyl track most similar to the query.
    # Muzpa often marks entire EPs as satisfying — without ranking we'd
    # grab whichever track happens to be first, not the one that was searched.
    non_vinyl = [t for t in satisfying if not _is_vinyl_only(t)]
    if non_vinyl:
        if len(non_vinyl) > 1:
            non_vinyl.sort(key=lambda t: _track_similarity(query, t), reverse=True)
            scores = [(t.get("filename"), round(_track_similarity(query, t), 2)) for t in non_vinyl]
            logger.info("Muzpa: %d satisfying tracks, ranked by similarity: %s", len(non_vinyl), scores)

        best = non_vinyl[0]
        logger.info("Muzpa found downloadable: %r (id=%s, score=%.2f)",
                    best.get("filename"), best.get("id"), _track_similarity(query, best))
        return best, "found"

    # All satisfying results are vinyl-only — still return the best-matching one
    if satisfying:
        satisfying.sort(key=lambda t: _track_similarity(query, t), reverse=True)
        logger.info("Muzpa: only vinyl-only results for %r — will attempt download anyway", query)
        return satisfying[0], "vinyl_only"

    # No satisfying results — check if any result at all is vinyl-only
    # (catches cases where satisfies=False but it's clearly the right track in vinyl)
    if all_tracks and all(_is_vinyl_only(t) for t in all_tracks):
        logger.info("Muzpa: all results are vinyl-only for %r", query)
        return None, "vinyl_only"

    return None, "not_found"


def download(track_id: int, filename: str, dest_folder: Path, sess: str) -> Path:
    """
    Download a track from Muzpa and save it to dest_folder.
    Returns the path to the saved file.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)
    url = _DOWNLOAD_URL.format(id=track_id)
    # URL always returns MP3 — save with .mp3 extension regardless of original format
    dest = dest_folder / (Path(filename).stem + ".mp3")

    logger.info("Muzpa downloading %r -> %s", filename, dest)
    with httpx.stream(
        "GET",
        url,
        headers={"Cookie": f"SESS={sess}"},
        timeout=120,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)

    logger.info("Muzpa download complete: %s (%.1f MB)", dest.name, dest.stat().st_size / 1_000_000)
    return dest
