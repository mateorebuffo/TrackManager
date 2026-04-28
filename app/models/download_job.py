import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from app.db import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    not_found = "not_found"
    vinyl_only = "vinyl_only"
    bandcamp_only = "bandcamp_only"
    cancelled = "cancelled"


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id            = Column(Integer, primary_key=True, index=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    review_id     = Column(Integer, ForeignKey("review_items.id", ondelete="CASCADE"), nullable=False, index=True)
    status        = Column(Enum(JobStatus), default=JobStatus.pending, nullable=False, index=True)
    query         = Column(Text, nullable=False)
    attempt_count = Column(Integer, default=0, nullable=False)
    last_error    = Column(Text, nullable=True)
    downloaded_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<DownloadJob id={self.id} status={self.status} query={self.query!r}>"
