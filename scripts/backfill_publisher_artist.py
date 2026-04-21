"""
Backfill publisher_metadata.artist for existing SoundCloud tracks.

For each track that still has no normalized_artist (or has one that came from
the channel name rather than the real artist), this script:
  1. Fetches the track from the SoundCloud API
  2. Extracts publisher_metadata.artist
  3. Updates raw_metadata_json with the publisher_artist field
  4. Re-normalizes the track using the enriched artist

Run from project root:
    python scripts/backfill_publisher_artist.py

Rate-limited to ~2 req/s to stay within SoundCloud API limits.
"""
import sys, os, time, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(level=logging.WARNING)

import httpx
from sqlalchemy.orm import sessionmaker, joinedload

from app.config import settings
from app.db import engine
from app.models.normalized_track import NormalizedTrack
from app.models.source_track import SourceTrack
from app.services.normalization import normalize_track
from app.utils.text import is_label_channel

Session = sessionmaker(bind=engine)
db = Session()

# Only process tracks where normalized_artist is empty or matches the channel
# (meaning we never had a real artist)
candidates = (
    db.query(NormalizedTrack)
    .options(joinedload(NormalizedTrack.source_track))
    .join(NormalizedTrack.source_track)
    .filter(SourceTrack.source == "soundcloud")
    .all()
)

# Filter to tracks where publisher_artist is not yet stored
to_process = [
    nt for nt in candidates
    if nt.source_track
    and (nt.source_track.raw_metadata_json or {}).get("publisher_artist") is None
]

print(f"Tracks to backfill: {len(to_process)}")

headers = {"Authorization": f"OAuth {settings.soundcloud_oauth_token}"}
base_params = {"client_id": settings.soundcloud_client_id}

updated = 0
skipped = 0
errors = 0

with httpx.Client(timeout=15) as client:
    for i, nt in enumerate(to_process):
        st: SourceTrack = nt.source_track
        try:
            url = f"https://api-v2.soundcloud.com/tracks/{st.source_track_id}"
            resp = client.get(url, headers=headers, params=base_params)
            resp.raise_for_status()
            data = resp.json()

            pm = data.get("publisher_metadata") or {}
            publisher_artist = pm.get("artist") or None

            # Update raw_metadata_json
            meta = dict(st.raw_metadata_json or {})
            meta["publisher_artist"] = publisher_artist
            st.raw_metadata_json = meta

            # Determine effective artist (same logic as ingestion.py)
            effective_artist = st.raw_artist
            if publisher_artist and publisher_artist.strip().lower() != (st.raw_artist or "").strip().lower():
                effective_artist = publisher_artist.strip()

            # Re-normalize
            result = normalize_track(st.raw_title, effective_artist)

            artist_changed = nt.normalized_artist != result.normalized_artist
            if artist_changed or nt.normalized_title != result.normalized_title:
                old_artist = nt.normalized_artist
                nt.normalized_artist  = result.normalized_artist
                nt.normalized_title   = result.normalized_title
                nt.version_info       = result.version_info
                nt.search_query       = result.search_query
                nt.fingerprint_text   = result.fingerprint_text
                nt.confidence_score   = result.confidence_score
                if updated < 30:
                    pa_str = repr(publisher_artist)
                    print(f"  [{st.id:4d}] {old_artist!r:20s} -> {result.normalized_artist!r}  (pm={pa_str})")
                updated += 1
            else:
                skipped += 1

            db.commit()

            # ~2 req/s
            time.sleep(0.5)

        except Exception as e:
            errors += 1
            db.rollback()
            if errors <= 5:
                print(f"  [{st.id}] ERROR: {e}")

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(to_process)}  updated={updated} skipped={skipped} errors={errors}")

db.close()
print(f"\nDone. Updated: {updated} / Skipped: {skipped} / Errors: {errors}")
