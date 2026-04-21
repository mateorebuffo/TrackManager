from sqlalchemy import Column, Integer, String, Float, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.db import Base


class NormalizedTrack(Base):
    __tablename__ = "normalized_tracks"

    id = Column(Integer, primary_key=True, index=True)
    source_track_id_fk = Column(
        Integer, ForeignKey("source_tracks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    normalized_artist = Column(String(255))
    normalized_title = Column(String(512))
    version_info = Column(String(255))      # e.g. "Extended Mix", "Radio Edit", "VIP"
    search_query = Column(String(512))      # ready-to-use search string
    fingerprint_text = Column(String(512))  # "artist|title|version" for dedup
    confidence_score = Column(Float, default=1.0)  # normalization confidence 0–1

    source_track = relationship("SourceTrack", back_populates="normalized_track")
    review_item = relationship(
        "ReviewItem", back_populates="normalized_track", uselist=False
    )

    __table_args__ = (
        Index("ix_normalized_tracks_fingerprint", "fingerprint_text"),
    )

    def __repr__(self) -> str:
        return (
            f"<NormalizedTrack id={self.id} "
            f"artist={self.normalized_artist!r} title={self.normalized_title!r}>"
        )
