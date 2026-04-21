import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api import auth, auto_download, review, settings_page, sync, tracks
from app.auth_middleware import AuthMiddleware
from app.db import create_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    create_tables()
    from app.models import user  # noqa: F401 — ensure User table is created
    from app.db import Base, engine
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Music Track Collector",
    description="Collect, normalize, deduplicate, and review liked tracks.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(AuthMiddleware)

# Routers
app.include_router(auth.router)
app.include_router(sync.router)
app.include_router(tracks.router)
app.include_router(review.router)
app.include_router(auto_download.router)
app.include_router(settings_page.router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/tracks/pending")
