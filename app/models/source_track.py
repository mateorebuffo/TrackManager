from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db import Base


class SourceTrack(Base):
    __tablename__ = "source_tracks"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=False)           # soundcloud | spotify | youtube
    source_track_id = Column(String(255), nullable=False)
    source_url = Column(String(2048))
    raw_title = Column(String(512), nullable=False)
    raw_artist = Column(String(255))
    raw_metadata_json = Column(JSON, default=dict)
    duration_seconds = Column(Float)
    liked_at = Column(DateTime)
    collected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    normalized_track = relationship(
        "NormalizedTrack", back_populates="source_track", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("source", "source_track_id", name="uq_source_source_track_id"),
    )

    def __repr__(self) -> str:
        return f"<SourceTrack id={self.id} source={self.source} title={self.raw_title!r}>"
