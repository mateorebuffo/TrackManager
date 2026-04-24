from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Session
from typing import Generator
from app.config import settings


_url = settings.database_url_safe
_is_sqlite = _url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=not _is_sqlite)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Create all tables. Called on startup."""
    from app.models import source_track, normalized_track, review_item  # noqa: F401
    from app.models import app_event, track_history, user_report  # noqa: F401
    Base.metadata.create_all(bind=engine)
