"""
Backfill duration_seconds for existing YouTube source_tracks.

Fetches video durations from the YouTube Data API in batches of 50
and updates any SourceTrack rows where duration_seconds is NULL.

Run from the project root:
    python scripts/backfill_youtube_durations.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.basicConfig(level=logging.WARNING)

import httpx
from app.db import engine
from sqlalchemy.orm import sessionmaker
from app.models.source_track import SourceTrack
from app.services import youtube_auth
from app.collectors.youtube import _parse_duration

_BASE = "https://www.googleapis.com/youtube/v3"
Session = sessionmaker(bind=engine)


def fetch_durations(video_ids: list[str], token: str) -> dict[str, float]:
    resp = httpx.get(
        f"{_BASE}/videos",
        headers={"Authorization": f"Bearer {token}"},
        params={"part": "contentDetails", "id": ",".join(video_ids), "maxResults": 50},
        timeout=15,
    )
    resp.raise_for_status()
    result = {}
    for v in resp.json().get("items", []):
        vid = v.get("id")
        iso = v.get("contentDetails", {}).get("duration", "")
        parsed = _parse_duration(iso)
        if vid and parsed is not None:
            result[vid] = parsed
    return result


def main():
    db = Session()

    tracks = (
        db.query(SourceTrack)
        .filter(SourceTrack.source == "youtube", SourceTrack.duration_seconds.is_(None))
        .all()
    )

    if not tracks:
        print("No YouTube tracks with missing duration.")
        db.close()
        return

    print(f"Found {len(tracks)} YouTube tracks without duration.")

    token = youtube_auth.get_valid_access_token()
    updated = 0

    # Process in batches of 50
    for i in range(0, len(tracks), 50):
        batch = tracks[i:i + 50]
        ids = [st.source_track_id for st in batch]
        durations = fetch_durations(ids, token)

        for st in batch:
            d = durations.get(st.source_track_id)
            if d is not None:
                st.duration_seconds = d
                updated += 1
                print(f"  [{st.id}] {st.raw_title!r}: {d:.0f}s")
            else:
                print(f"  [{st.id}] {st.raw_title!r}: no duration returned (video may be unavailable)")

    db.commit()
    db.close()
    print(f"\nDone. Updated {updated} / {len(tracks)} tracks.")


if __name__ == "__main__":
    main()
