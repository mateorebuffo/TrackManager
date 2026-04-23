import enum
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
from app.db import Base


class ReportStatus(str, enum.Enum):
    open = "open"
    reviewed = "reviewed"
    resolved = "resolved"


REPORT_CATEGORIES = [
    ("metadata_incorrect",    "Metadata incorrecta"),
    ("wrong_file_downloaded", "Archivo incorrecto descargado"),
    ("wrong_folder",          "Carpeta incorrecta"),
    ("track_not_found",       "Track no encontrado"),
    ("duplicate_problem",     "Problema de duplicado"),
    ("other",                 "Otro"),
]


class UserReport(Base):
    __tablename__ = "user_reports"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    user_id = Column(Integer, nullable=False, index=True)
    track_id = Column(Integer, nullable=True)
    category = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(Enum(ReportStatus, native_enum=False), nullable=False, default=ReportStatus.open, index=True)
    resolution_notes = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<UserReport id={self.id} status={self.status} category={self.category!r}>"
