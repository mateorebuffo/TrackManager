"""
Entry point for PyInstaller bundle.
Configures paths and starts the FastAPI server via uvicorn.
"""
import sys
import os
import multiprocessing

multiprocessing.freeze_support()

if getattr(sys, "frozen", False):
    # Running inside PyInstaller bundle
    # _MEIPASS has all bundled files — set it as cwd so relative paths work
    os.chdir(sys._MEIPASS)

    # Store database and config in user's AppData (survives app updates)
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    data_dir = os.path.join(appdata, "TrackManager")
    os.makedirs(data_dir, exist_ok=True)

    # Set DATABASE_URL only if not already set externally
    os.environ.setdefault(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(data_dir, 'trackmanager.db')}",
    )

    # Load .env from AppData if it exists (user config)
    env_file = os.path.join(data_dir, ".env")
    if os.path.exists(env_file):
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)

import uvicorn

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

uvicorn.run(
    "app.main:app",
    host="127.0.0.1",
    port=port,
    log_level="warning",
)
