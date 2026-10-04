"""
Importación desde la extensión de Chrome (extension/ en la raíz del repo).

POST /api/tracks/import — agrega a Pendiente el tema que suena en Traxsource.

Se autentica con el token del agente: es lo que ya valida AuthMiddleware en /api/*.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.download_jobs import agent_auth
from app.collectors.base import RawTrack
from app.db import get_db
from app.models.source_track import SourceTrack
from app.models.user import User
from app.services.ingestion import import_one

router = APIRouter(tags=["extension"])

_SOURCE = "traxsource"


class ImportedTrack(BaseModel):
    # Los máximos son los de las columnas de SourceTrack: la entrada viene de afuera.
    source_track_id: str = Field(min_length=1, max_length=255)
    url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    artists: list[str] = Field(min_length=1, max_length=20)
    duration_seconds: float | None = Field(default=None, ge=0, le=86400)
    label: str | None = Field(default=None, max_length=255)
    genre: str | None = Field(default=None, max_length=255)
    release_date: str | None = Field(default=None, max_length=32)


@router.post("/api/tracks/import")
def import_track(
    payload: ImportedTrack,
    user: User = Depends(agent_auth),
    db: Session = Depends(get_db),
) -> dict:
    artist = ", ".join(a.strip() for a in payload.artists if a.strip())[:255]
    raw = RawTrack(
        source=_SOURCE,
        source_track_id=payload.source_track_id,
        source_url=payload.url,
        raw_title=payload.title.strip(),
        raw_artist=artist or None,
        duration_seconds=payload.duration_seconds,
        liked_at=datetime.now(timezone.utc),
        raw_metadata={"label": payload.label, "genre": payload.genre,
                      "release_date": payload.release_date},
    )
    result = import_one(raw, db, user.id)

    if result.skipped_existing:
        status = "exists"
    elif result.strong_duplicates_flagged or result.weak_duplicates_flagged:
        status = "duplicate"
    else:
        status = "added"

    st = (db.query(SourceTrack)
          .filter_by(source=_SOURCE, source_track_id=payload.source_track_id, user_id=user.id)
          .first())
    nt = st.normalized_track if st else None
    review = nt.review_item if nt else None
    return {
        "status": status,
        "track": f"{nt.normalized_artist} - {nt.normalized_title}" if nt else payload.title,
        "review_id": review.id if review else None,
    }
