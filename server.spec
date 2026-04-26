# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the FastAPI server bundle.
Run: pyinstaller server.spec
Output: dist/server/ (directory bundle)
"""

block_cipher = None

datas = [
    ("app/templates",  "app/templates"),
    ("app/static",     "app/static"),
    ("alembic",        "alembic"),
    ("alembic.ini",    "."),
]

hiddenimports = [
    # App modules
    "app.main",
    "app.db",
    "app.config",
    "app.auth_middleware",
    "app.models.user",
    "app.models.user_settings",
    "app.models.source_track",
    "app.models.normalized_track",
    "app.models.review_item",
    "app.models.app_event",
    "app.models.track_history",
    "app.models.user_report",
    "app.api.auth",
    "app.api.auto_download",
    "app.api.debug",
    "app.api.review",
    "app.api.settings_page",
    "app.api.sync",
    "app.api.tracks",
    "app.collectors.soundcloud",
    "app.collectors.spotify",
    "app.collectors.youtube",
    "app.services.auto_download",
    "app.services.auth",
    "app.services.bandcamp_check",
    "app.services.deezer_dl",
    "app.services.discogs_check",
    "app.services.audio_verify",
    "app.services.ingestion",
    "app.services.log_cleanup",
    "app.services.log_service",
    "app.services.muzpa",
    "app.services.spotify_auth",
    "app.services.youtube_auth",
    "app.utils.fs",
    # Uvicorn internals
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # SQLAlchemy
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.postgresql",
    # Pydantic / passlib
    "pydantic_settings",
    "passlib.handlers.pbkdf2",
    # Multiprocessing
    "multiprocessing",
    "multiprocessing.pool",
]

a = Analysis(
    ["server_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zlib_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No black console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="server",
)
