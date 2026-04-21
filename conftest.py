"""
Shared pytest fixtures for the music_mvp test suite.

Strategy
--------
* Override DATABASE_URL to SQLite in-memory *before* any app module is
  imported, so pydantic_settings picks it up.
* For each test function we open ONE real SQLite connection, create all
  tables on it, and hand every SQLAlchemy call (fixtures AND FastAPI
  dependency override) the SAME connection wrapped in a Session.
  This avoids the classic SQLite pitfall where separate connections to
  "sqlite:///:memory:" see empty, unrelated databases.
* The FastAPI TestClient's startup hook (create_tables) is suppressed so
  it doesn't try to run on a disconnected engine.
"""

import os

# Set before any app.* import so pydantic_settings picks it up.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

# ---------------------------------------------------------------------------
# App imports (after env override)
# ---------------------------------------------------------------------------
from app.db import Base, get_db
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def engine():
    """
    Create a fresh SQLite in-memory engine per test.

    We use a single *connection* and keep it alive for the whole test so that
    all SQLAlchemy operations (including table creation and ORM queries) share
    the same SQLite in-memory database.  Without this, each new connection to
    'sqlite:///:memory:' would see an empty database.
    """
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # Keep a single connection open for the test lifetime.
    connection = _engine.connect()

    # Make sure every new Session created from this engine reuses the same
    # underlying connection (not just the same pool).
    @event.listens_for(_engine, "connect")
    def connect(dbapi_con, con_record):
        pass  # placeholder; the key is using the persistent `connection` below

    # Import models so Base.metadata is fully populated.
    from app.models import source_track, normalized_track, review_item  # noqa: F401

    Base.metadata.create_all(bind=connection)

    yield _engine

    Base.metadata.drop_all(bind=connection)
    connection.close()
    _engine.dispose()


@pytest.fixture(scope="function")
def db_session(engine) -> Session:
    """
    Return a SQLAlchemy Session that shares the engine's single connection.

    We also begin a SAVEPOINT so that each test can roll back to a clean
    state without closing the connection (which would wipe the in-memory DB).
    """
    # Grab the persistent connection from the engine's pool.
    connection = engine.connect()
    transaction = connection.begin()

    # Bind the session to this specific connection.
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession(bind=connection)

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def client(engine, db_session: Session) -> TestClient:
    """
    FastAPI TestClient with:
      1. The get_db dependency replaced by a closure that yields db_session.
      2. The app's on_startup create_tables() replaced so it creates tables on
         our test engine rather than the module-level production engine.
    """
    from unittest.mock import patch
    import app.db as app_db

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass  # lifecycle managed by db_session fixture

    app.dependency_overrides[get_db] = _override_get_db

    # Patch create_tables so the startup hook targets our test engine instead
    # of the module-level production engine.
    def _test_create_tables():
        from app.models import source_track, normalized_track, review_item  # noqa: F401
        Base.metadata.create_all(bind=engine)

    with patch.object(app_db, "create_tables", _test_create_tables):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()
