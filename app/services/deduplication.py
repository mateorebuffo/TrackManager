"""
Deduplication service.

Compares a candidate NormalizedTrack against existing ones using
fuzzy string matching on the fingerprint components.

Match levels:
  STRONG  (≥ strong_threshold) → definite duplicate, skip
  WEAK    (≥ weak_threshold)   → possible duplicate, flag but still create
  NONE    (< weak_threshold)   → new track, proceed normally
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.config import settings
from app.models.normalized_track import NormalizedTrack

logger = logging.getLogger(__name__)


class MatchStrength(str, Enum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"


@dataclass
class DuplicateMatch:
    strength: MatchStrength
    score: float
    matched_id: int | None = None
    matched_fingerprint: str | None = None


def check_duplicate(
    fingerprint: str,
    db: Session,
    exclude_source_track_id: int | None = None,
    user_id: int | None = None,
) -> DuplicateMatch:
    """
    Check if `fingerprint` already exists in normalized_tracks.
    When user_id is provided, only checks within that user's tracks.
    """
    strong_threshold = settings.dedup_strong_match_score
    weak_threshold = settings.dedup_weak_match_score

    from app.models.source_track import SourceTrack

    query = (
        db.query(NormalizedTrack)
        .join(NormalizedTrack.source_track)
        .filter(NormalizedTrack.fingerprint_text.isnot(None))
    )

    if user_id is not None:
        query = query.filter(SourceTrack.user_id == user_id)

    if exclude_source_track_id is not None:
        query = query.filter(
            NormalizedTrack.source_track_id_fk != exclude_source_track_id
        )

    candidates = query.all()

    best_score = 0.0
    best_match: NormalizedTrack | None = None

    for candidate in candidates:
        score = _compare_fingerprints(fingerprint, candidate.fingerprint_text or "")
        if score > best_score:
            best_score = score
            best_match = candidate

    if best_score >= strong_threshold:
        logger.info(
            "Strong duplicate: %r ≈ %r (score=%.1f)",
            fingerprint,
            best_match.fingerprint_text if best_match else "",
            best_score,
        )
        return DuplicateMatch(
            strength=MatchStrength.STRONG,
            score=best_score,
            matched_id=best_match.id if best_match else None,
            matched_fingerprint=best_match.fingerprint_text if best_match else None,
        )

    if best_score >= weak_threshold:
        logger.info(
            "Weak duplicate: %r ≈ %r (score=%.1f)",
            fingerprint,
            best_match.fingerprint_text if best_match else "",
            best_score,
        )
        return DuplicateMatch(
            strength=MatchStrength.WEAK,
            score=best_score,
            matched_id=best_match.id if best_match else None,
            matched_fingerprint=best_match.fingerprint_text if best_match else None,
        )

    return DuplicateMatch(strength=MatchStrength.NONE, score=best_score)


def _compare_fingerprints(a: str, b: str) -> float:
    if not a or not b:
        return 0.0

    score = _score_pair(a, b)

    a_base = _base_fingerprint(a)
    b_base = _base_fingerprint(b)
    if a_base != a or b_base != b:
        base_score = _score_pair(a_base, b_base)
        score = max(score, base_score * 0.95)

    return score


def _score_pair(a: str, b: str) -> float:
    token_sort = fuzz.token_sort_ratio(a, b)
    ratio = fuzz.ratio(a, b)
    partial = fuzz.partial_ratio(a, b)
    return token_sort * 0.6 + ratio * 0.25 + partial * 0.15


def _base_fingerprint(fp: str) -> str:
    parts = fp.split("|")
    return "|".join(parts[:2]) if len(parts) > 2 else fp
