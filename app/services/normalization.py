"""
Normalization service.

Takes a raw SourceTrack and produces a NormalizedTrack with cleaned
artist/title, version info, a search query, and a deduplication fingerprint.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.utils.text import (
    build_fingerprint,
    clean_edges,
    extract_version,
    split_artist_title,
    strip_label_parens,
    strip_noise,
)

logger = logging.getLogger(__name__)


@dataclass
class NormalizationResult:
    normalized_artist: str
    normalized_title: str
    version_info: str | None
    search_query: str
    fingerprint_text: str
    confidence_score: float


def normalize_track(raw_title: str, raw_artist: str | None) -> NormalizationResult:
    """
    Main entry point.  Accepts the raw strings from a SourceTrack and returns
    a fully populated NormalizationResult.

    Pipeline:
      1. Strip noise from the raw title
      2. Extract version label (e.g. 'Extended Mix')
      3. Split into artist + title (use raw_artist when reliable)
      4. Build fingerprint and search query
      5. Estimate confidence
    """
    # Step 1 — strip promotional / meta noise
    cleaned = strip_noise(raw_title)

    # Step 2 — extract version from parentheses/brackets
    cleaned, version = extract_version(cleaned)

    # Step 3 — split artist / title
    # Try separator in title first; fall back to raw_artist if it looks like a real artist.
    artist, title = split_artist_title(cleaned, raw_artist=raw_artist)

    # Step 4 — further clean both fields
    artist = clean_edges(strip_noise(artist))
    title = clean_edges(strip_noise(strip_label_parens(title)))

    # Step 5 — search query: "Artist Title" or just title if artist unknown
    if artist:
        search_query = f"{artist} {title}"
    else:
        search_query = title

    if version:
        search_query = f"{search_query} {version}"

    fingerprint = build_fingerprint(artist, title, version)

    confidence = _estimate_confidence(artist, title, raw_artist)

    logger.debug(
        "Normalized %r -> artist=%r title=%r version=%r (confidence=%.2f)",
        raw_title,
        artist,
        title,
        version,
        confidence,
    )

    return NormalizationResult(
        normalized_artist=artist,
        normalized_title=title,
        version_info=version,
        search_query=search_query.strip(),
        fingerprint_text=fingerprint,
        confidence_score=confidence,
    )


def _estimate_confidence(
    artist: str, title: str, raw_artist: str | None
) -> float:
    """
    Heuristic confidence score (0.0 – 1.0).
    Drops when key fields are missing or suspiciously short.
    """
    score = 1.0

    if not artist:
        score -= 0.3
    elif len(artist) < 2:
        score -= 0.1

    if not title:
        score -= 0.4
    elif len(title) < 3:
        score -= 0.2

    # If we had to guess the artist from a dash-split rather than using raw_artist
    if not raw_artist and artist:
        score -= 0.1

    return max(0.0, min(1.0, score))
