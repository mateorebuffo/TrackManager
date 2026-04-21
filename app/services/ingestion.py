"""
Ingestion service.

Orchestrates the full pipeline for a single sync run:
  collector → persist source_track → normalize → dedup → create review_item
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collectors.base import BaseCollector, RawTrack
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.services.deduplication import MatchStrength, check_duplicate
from app.services.normalization import NormalizationResult, normalize_track
from app.utils.text import build_fingerprint, clean_edges, extract_version, strip_noise

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    total_fetched: int = 0
    new_tracks: int = 0
    skipped_existing: int = 0
    strong_duplicates_flagged: int = 0
    weak_duplicates_flagged: int = 0
    errors: int = 0


def run_sync(collector: BaseCollector, db: Session) -> SyncResult:
    """
    Run a full sync for the given collector.
    Each track goes through: fetch → persist → normalize → dedup → review_item.
    """
    result = SyncResult()

    for raw in collector.fetch_liked_tracks():
        result.total_fetched += 1
        try:
            _process_track(raw, db, result)
        except Exception:
            logger.exception("Error processing track %s:%s", raw.source, raw.source_track_id)
            result.errors += 1
            db.rollback()

    logger.info(
        "Sync complete: fetched=%d new=%d skipped=%d strong_dups=%d weak_dups=%d errors=%d",
        result.total_fetched,
        result.new_tracks,
        result.skipped_existing,
        result.strong_duplicates_flagged,
        result.weak_duplicates_flagged,
        result.errors,
    )
    return result


def _process_track(raw: RawTrack, db: Session, result: SyncResult) -> None:
    # --- 1. Check if already ingested (idempotent) ---
    existing = (
        db.query(SourceTrack)
        .filter_by(source=raw.source, source_track_id=raw.source_track_id)
        .first()
    )
    if existing:
        logger.debug("Already ingested: %s:%s", raw.source, raw.source_track_id)
        result.skipped_existing += 1
        return

    # --- 2. Persist source track ---
    source_track = SourceTrack(
        source=raw.source,
        source_track_id=raw.source_track_id,
        source_url=raw.source_url,
        raw_title=raw.raw_title,
        raw_artist=raw.raw_artist,
        raw_metadata_json=raw.raw_metadata,
        duration_seconds=raw.duration_seconds,
        liked_at=raw.liked_at,
    )
    db.add(source_track)
    try:
        db.flush()  # get the PK without committing
    except IntegrityError:
        db.rollback()
        logger.debug("Race condition — already inserted: %s:%s", raw.source, raw.source_track_id)
        result.skipped_existing += 1
        return

    # --- 3. Normalize ---
    if raw.source == "spotify":
        # Spotify provides structured artist metadata — bypass the noisy title parser
        norm_result = _normalize_spotify(raw)
    else:
        # SoundCloud: publisher_artist from publisher_metadata is the most reliable
        # artist signal when the uploader is a label/promoter channel.
        # Use it only when it differs from the channel name (otherwise it's redundant).
        publisher_artist = (raw.raw_metadata or {}).get("publisher_artist")
        effective_artist = raw.raw_artist
        if publisher_artist and publisher_artist.strip().lower() != (raw.raw_artist or "").strip().lower():
            effective_artist = publisher_artist.strip()
        norm_result = normalize_track(raw.raw_title, effective_artist)

    # --- 4. Deduplication ---
    dup = check_duplicate(norm_result.fingerprint_text, db)

    # --- 5. Persist normalized track ---
    normalized = NormalizedTrack(
        source_track_id_fk=source_track.id,
        normalized_artist=norm_result.normalized_artist,
        normalized_title=norm_result.normalized_title,
        version_info=norm_result.version_info,
        search_query=norm_result.search_query,
        fingerprint_text=norm_result.fingerprint_text,
        confidence_score=norm_result.confidence_score,
    )
    db.add(normalized)
    db.flush()

    # --- 6. Create review item ---
    review_notes = None
    if dup.strength in (MatchStrength.STRONG, MatchStrength.WEAK):
        # Look up the ReviewItem of the matched NormalizedTrack so we can store
        # its review ID — used to build a comparison link in the UI.
        matched_review = (
            db.query(ReviewItem)
            .filter(ReviewItem.normalized_track_id_fk == dup.matched_id)
            .first()
        )
        if matched_review:
            review_notes = f"dup:{matched_review.id}"
        if dup.strength == MatchStrength.STRONG:
            result.strong_duplicates_flagged += 1
        else:
            result.weak_duplicates_flagged += 1

    is_set = (raw.duration_seconds or 0) > 2100  # > 35 minutes
    review = ReviewItem(
        normalized_track_id_fk=normalized.id,
        status=TrackStatus.set_mix if is_set else TrackStatus.pending,
        notes=review_notes,
    )
    db.add(review)
    db.commit()

    result.new_tracks += 1
    logger.debug("Ingested: %r -> %r", raw.raw_title, norm_result.search_query)


def _normalize_spotify(raw: RawTrack) -> NormalizationResult:
    """
    Normalize a Spotify track using its structured artist metadata.
    Bypasses split_artist_title — artist comes directly from raw_metadata["artists"].
    Only strips noise and extracts version from the title.
    """
    artists = (raw.raw_metadata or {}).get("artists") or []
    if artists:
        normalized_artist = ", ".join(a["name"] for a in artists if a.get("name"))
    else:
        normalized_artist = clean_edges(strip_noise(raw.raw_artist or ""))

    cleaned = strip_noise(raw.raw_title or "")
    cleaned, version = extract_version(cleaned)
    normalized_title = clean_edges(strip_noise(cleaned))

    if normalized_artist:
        search_query = f"{normalized_artist} {normalized_title}"
    else:
        search_query = normalized_title
    if version:
        search_query = f"{search_query} {version}"

    fingerprint = build_fingerprint(normalized_artist, normalized_title, version)
    confidence = 1.0 if normalized_artist and normalized_title else 0.7

    return NormalizationResult(
        normalized_artist=normalized_artist,
        normalized_title=normalized_title,
        version_info=version,
        search_query=search_query.strip(),
        fingerprint_text=fingerprint,
        confidence_score=confidence,
    )
