import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from app.db import Base


class TrackStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    downloaded = "downloaded"
    not_found = "not_found"
    vinyl_only = "vinyl_only"
    bandcamp_only = "bandcamp_only"
    set_mix = "set_mix"
    discarded = "discarded"


class ReviewItem(Base):
    __tablename__ = "review_items"

    id = Column(Integer, primary_key=True, index=True)
    normalized_track_id_fk = Column(
        Integer,
        ForeignKey("normalized_tracks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status = Column(Enum(TrackStatus), default=TrackStatus.pending, nullable=False, index=True)
    notes = Column(Text)
    reviewed_at = Column(DateTime)

    normalized_track = relationship("NormalizedTrack", back_populates="review_item")

    def mark_reviewed(self, status: TrackStatus, notes: str | None = None) -> None:
        self.status = status
        self.reviewed_at = datetime.now(timezone.utc)
        if notes is not None:
            self.notes = notes

    def __repr__(self) -> str:
        return f"<ReviewItem id={self.id} status={self.status}>"
