import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, auto_download, debug, download_jobs, review, settings_page, sync, tracks
from app.auth_middleware import AuthMiddleware
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Keep model imports here so SQLAlchemy/Alembic can discover all tables.
    from app.models import user, user_settings, source_track, normalized_track, review_item  # noqa: F401
    from app.models import app_event, track_history, user_report, download_job  # noqa: F401

    # Local dev convenience: auto-create tables when using SQLite.
    # In production, Alembic (Procfile: alembic upgrade head) is the sole schema authority.
    if settings.database_url_safe.startswith("sqlite"):
        from app.db import Base, engine
        Base.metadata.create_all(bind=engine)

    # Run log retention cleanup on every startup — fast when nothing is expired.
    from app.db import SessionLocal
    from app.services.log_cleanup import run_cleanup
    db = SessionLocal()
    try:
        run_cleanup(db)
    except Exception:
        logger.warning("Startup log cleanup failed (non-fatal)", exc_info=True)
    finally:
        db.close()

    yield


app = FastAPI(
    title="Track Manager",
    description="Collect, normalize, deduplicate, and review liked tracks.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)

app.add_middleware(AuthMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(tracks.router)
app.include_router(review.router)
app.include_router(auto_download.router)
app.include_router(download_jobs.router)
app.include_router(settings_page.router)
app.include_router(debug.router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/tracks/pending")


@app.get("/health", include_in_schema=False)
def health() -> JSONResponse:
    return JSONResponse({"ok": True})
