"""
Re-normalize NormalizedTrack rows for Spotify and YouTube sources.

  Spotify: uses structured artist data from raw_metadata_json["artists"] directly,
           bypasses split_artist_title, only runs strip_noise + extract_version on title.
  YouTube: re-runs the full normalization pipeline (same as renormalize.py).

Run from the project root:
    python scripts/renormalize_sources.py [--source spotify|youtube|all] [--dry-run]
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.basicConfig(level=logging.WARNING)

from app.db import engine
from sqlalchemy.orm import sessionmaker, joinedload
from app.models.normalized_track import NormalizedTrack
from app.models.source_track import SourceTrack
from app.services.normalization import normalize_track
from app.utils.text import build_fingerprint, clean_edges, extract_version, strip_noise

Session = sessionmaker(bind=engine)


def normalize_spotify(st: SourceTrack):
    """
    For Spotify tracks, use structured artist metadata directly.
    Only runs noise stripping and version extraction on the title.
    Returns a dict of fields to update on NormalizedTrack.
    """
    metadata = st.raw_metadata_json or {}
    artists = metadata.get("artists") or []

    if artists:
        normalized_artist = ", ".join(a["name"] for a in artists if a.get("name"))
    else:
        # Fallback: use raw_artist
        normalized_artist = clean_edges(strip_noise(st.raw_artist or ""))

    # Clean title: strip noise, extract version, but do NOT split on " - "
    cleaned = strip_noise(st.raw_title or "")
    cleaned, version = extract_version(cleaned)
    normalized_title = clean_edges(stripped := strip_noise(cleaned))

    if normalized_artist:
        search_query = f"{normalized_artist} {normalized_title}"
    else:
        search_query = normalized_title
    if version:
        search_query = f"{search_query} {version}"

    fingerprint = build_fingerprint(normalized_artist, normalized_title, version)
    confidence = 1.0 if normalized_artist and normalized_title else 0.7

    return {
        "normalized_artist": normalized_artist,
        "normalized_title": normalized_title,
        "version_info": version,
        "search_query": search_query.strip(),
        "fingerprint_text": fingerprint,
        "confidence_score": confidence,
    }


def normalize_youtube(st: SourceTrack):
    """
    For YouTube tracks, run the standard normalization pipeline.
    """
    result = normalize_track(st.raw_title, st.raw_artist)
    return {
        "normalized_artist": result.normalized_artist,
        "normalized_title": result.normalized_title,
        "version_info": result.version_info,
        "search_query": result.search_query,
        "fingerprint_text": result.fingerprint_text,
        "confidence_score": result.confidence_score,
    }


def main():
    parser = argparse.ArgumentParser(description="Re-normalize Spotify/YouTube tracks")
    parser.add_argument("--source", choices=["spotify", "youtube", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without saving")
    args = parser.parse_args()

    db = Session()

    sources = ["spotify", "youtube"] if args.source == "all" else [args.source]

    query = (
        db.query(NormalizedTrack)
        .options(joinedload(NormalizedTrack.source_track))
        .join(NormalizedTrack.source_track)
        .filter(SourceTrack.source.in_(sources))
    )

    tracks = query.all()
    changed = 0
    unchanged = 0
    skipped = 0

    for nt in tracks:
        st: SourceTrack = nt.source_track
        if not st:
            skipped += 1
            continue

        if st.source == "spotify":
            new_fields = normalize_spotify(st)
        elif st.source == "youtube":
            new_fields = normalize_youtube(st)
        else:
            skipped += 1
            continue

        artist_changed = nt.normalized_artist != new_fields["normalized_artist"]
        title_changed  = nt.normalized_title  != new_fields["normalized_title"]
        version_changed = nt.version_info     != new_fields["version_info"]

        if artist_changed or title_changed or version_changed:
            print(
                f"  [{st.source:8s} #{st.id:4d}] {st.raw_title!r}\n"
                f"    artist:  {nt.normalized_artist!r:35s} -> {new_fields['normalized_artist']!r}\n"
                f"    title:   {nt.normalized_title!r:35s} -> {new_fields['normalized_title']!r}"
                + (f"\n    version: {nt.version_info!r} -> {new_fields['version_info']!r}" if version_changed else "")
            )
            if not args.dry_run:
                nt.normalized_artist  = new_fields["normalized_artist"]
                nt.normalized_title   = new_fields["normalized_title"]
                nt.version_info       = new_fields["version_info"]
                nt.search_query       = new_fields["search_query"]
                nt.fingerprint_text   = new_fields["fingerprint_text"]
                nt.confidence_score   = new_fields["confidence_score"]
            changed += 1
        else:
            unchanged += 1

    if not args.dry_run:
        db.commit()
    db.close()

    dry = " (dry run)" if args.dry_run else ""
    print(f"\nDone{dry}. Source(s): {sources}")
    print(f"  Changed: {changed} / Unchanged: {unchanged} / Skipped: {skipped} / Total: {changed + unchanged + skipped}")


if __name__ == "__main__":
    main()
