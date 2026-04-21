"""
Re-normalize all existing NormalizedTrack rows using the current normalization logic.

Run from the project root:
    python scripts/renormalize.py

Prints a summary of how many tracks changed artist/title.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.basicConfig(level=logging.WARNING)

from app.db import engine
from sqlalchemy.orm import sessionmaker, joinedload
from app.models.normalized_track import NormalizedTrack
from app.models.source_track import SourceTrack
from app.services.normalization import normalize_track
from app.utils.text import build_fingerprint

Session = sessionmaker(bind=engine)
db = Session()

tracks = (
    db.query(NormalizedTrack)
    .options(joinedload(NormalizedTrack.source_track))
    .all()
)

changed = 0
unchanged = 0

for nt in tracks:
    st: SourceTrack = nt.source_track
    if not st:
        continue

    result = normalize_track(st.raw_title, st.raw_artist)

    artist_changed = nt.normalized_artist != result.normalized_artist
    title_changed  = nt.normalized_title  != result.normalized_title

    if artist_changed or title_changed:
        if changed < 20:  # print first 20 for review
            print(
                f"  [{st.id:4d}] artist: {nt.normalized_artist!r:25s} ->{result.normalized_artist!r}\n"
                f"         title:  {nt.normalized_title!r:25s} ->{result.normalized_title!r}"
            )
        nt.normalized_artist  = result.normalized_artist
        nt.normalized_title   = result.normalized_title
        nt.version_info       = result.version_info
        nt.search_query       = result.search_query
        nt.fingerprint_text   = result.fingerprint_text
        nt.confidence_score   = result.confidence_score
        changed += 1
    else:
        unchanged += 1

db.commit()
db.close()

print(f"\nDone. Changed: {changed} / Unchanged: {unchanged} / Total: {changed + unchanged}")
