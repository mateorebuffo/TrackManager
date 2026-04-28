"""
Entry point for PyInstaller bundle.
Configures paths and starts the FastAPI server via uvicorn.
"""
import sys
import os
import multiprocessing

multiprocessing.freeze_support()

def _write_log(msg):
    try:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        log_path = os.path.join(appdata, "TrackManager", "startup.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            import datetime
            f.write(f"[{datetime.datetime.now()}] {msg}\n")
    except Exception:
        pass

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

if getattr(sys, "frozen", False):
    # Running inside PyInstaller bundle
    # _MEIPASS has all bundled files — set it as cwd so relative paths work
    os.chdir(sys._MEIPASS)

    # Store database and config in user's AppData (survives app updates)
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    data_dir = os.path.join(appdata, "TrackManager")
    os.makedirs(data_dir, exist_ok=True)

    os.environ.setdefault(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(data_dir, 'trackmanager.db')}",
    )
    os.environ.setdefault(
        "SPOTIFY_REDIRECT_URI",
        f"http://127.0.0.1:{port}/sync/spotify/callback",
    )
    os.environ.setdefault(
        "YOUTUBE_REDIRECT_URI",
        f"http://127.0.0.1:{port}/sync/youtube/callback",
    )

    from dotenv import load_dotenv
    # 1. Load bundled production credentials (lowest priority)
    bundled_creds = os.path.join(sys._MEIPASS, "credentials.env")
    if os.path.exists(bundled_creds):
        load_dotenv(bundled_creds, override=False)
    # 2. Load user's AppData .env (can override bundled credentials)
    user_env = os.path.join(data_dir, ".env")
    if os.path.exists(user_env):
        load_dotenv(user_env, override=True)

_write_log("importing uvicorn...")
try:
    import uvicorn
    _write_log("uvicorn imported OK")
except Exception as e:
    _write_log(f"uvicorn import FAILED: {e}")
    raise
_write_log(f"starting uvicorn on port {port}")

try:
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=None,
    )
except Exception as e:
    _write_log(f"uvicorn.run FAILED: {e}")
