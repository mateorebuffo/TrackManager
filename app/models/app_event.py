import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, JSON
from app.db import Base


class EventLevel(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"


class AppEvent(Base):
    __tablename__ = "app_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    track_id = Column(Integer, nullable=True, index=True)
    level = Column(Enum(EventLevel, native_enum=False), nullable=False, default=EventLevel.info, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    message = Column(Text, nullable=False)
    context_json = Column(JSON, nullable=True)
    operation_id = Column(String(36), nullable=True, index=True)
    source = Column(String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<AppEvent id={self.id} level={self.level} type={self.event_type!r}>"
