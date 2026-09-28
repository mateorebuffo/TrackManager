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


class _SessionNoClose:
    """
    Wrapper que ignora close().

    AuthMiddleware abre su propia sesión con SessionLocal() y la cierra en un
    finally, salteándose dependency_overrides. Para que vea la base de prueba hay
    que darle la sesión del test, pero sin dejar que la cierre.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


@pytest.fixture(scope="function")
def client(engine, db_session: Session, monkeypatch) -> TestClient:
    """
    FastAPI TestClient with:
      1. The get_db dependency replaced by a closure that yields db_session.
      2. The app's on_startup create_tables() replaced so it creates tables on
         our test engine rather than the module-level production engine.
      3. AuthMiddleware pointed at the test session, and an authenticated user.
    """
    from unittest.mock import patch
    import app.db as app_db
    import app.auth_middleware as auth_middleware
    from app.models.user import User
    from app.services.auth import make_session_token

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass  # lifecycle managed by db_session fixture

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(auth_middleware, "SessionLocal",
                        lambda: _SessionNoClose(db_session))

    # Sin rate limit en los tests. El estado vive a nivel módulo y el user_id es 1
    # en todos, así que se arrastra entre tests; y hay tests que llaman dos veces
    # al mismo endpoint a propósito (idempotencia), que con 1 llamada por 60s es
    # imposible. Ninguna prueba cubre el 429 hoy.
    from app.utils.rate_limit import UserRateLimiter
    monkeypatch.setattr(UserRateLimiter, "acquire", lambda self, user_id: True)
    monkeypatch.setattr(UserRateLimiter, "is_limited", lambda self, user_id: False)

    # Estos tests son anteriores al login. Sin un usuario, AuthMiddleware ve la
    # base vacía y manda todo a /setup con un 302 — que es por qué fallaban 43.
    user = User(username="tester", hashed_password="x", is_admin=True, api_token="tok-test")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # /sync/soundcloud corta con "OAuth token no configurado" si no hay fila de
    # settings. El valor da igual: con USE_MOCK_COLLECTOR el collector no lo usa.
    from app.models.user_settings import UserSettings
    db_session.add(UserSettings(
        user_id=user.id,
        soundcloud_oauth_token="token-de-prueba",
        onboarding_done=True,   # que "/" no redirija al asistente
    ))
    db_session.commit()

    # Patch create_tables so the startup hook targets our test engine instead
    # of the module-level production engine.
    def _test_create_tables():
        from app.models import source_track, normalized_track, review_item  # noqa: F401
        Base.metadata.create_all(bind=engine)

    # base_url con un host permitido: TrustedHostMiddleware (app/main.py) sólo
    # acepta trackmanager.app, localhost y 127.0.0.1, y el default del TestClient
    # es "testserver" — con eso toda request web moría en "Invalid host header".
    with patch.object(app_db, "create_tables", _test_create_tables):
        with TestClient(app, base_url="http://localhost", raise_server_exceptions=True) as c:
            c.cookies.set("mc_session", make_session_token(user.id))
            yield c

    app.dependency_overrides.clear()
