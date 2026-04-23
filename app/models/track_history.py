from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, JSON
from app.db import Base


class TrackHistory(Base):
    __tablename__ = "track_history"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    user_id = Column(Integer, nullable=True)
    track_id = Column(Integer, nullable=False, index=True)  # review_items.id
    action = Column(String(100), nullable=False)
    details_json = Column(JSON, nullable=True)

    def __repr__(self) -> str:
        return f"<TrackHistory id={self.id} track_id={self.track_id} action={self.action!r}>"
