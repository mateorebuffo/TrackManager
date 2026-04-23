"""
Filesystem utilities — cross-platform (Windows + macOS).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def resolve_download_folder(
    base: Path,
    liked_at: datetime | None,
    collected_at: datetime | None = None,
    mode: str = "none",
    # legacy param kept for callers not yet updated
    organize_by_date: bool = False,
) -> Path:
    """
    Return the target folder for a downloaded track.

    mode="none"        → <base>/
    mode="like_date"   → <base>/<YYYY>/<YYYY-MM>/  based on liked_at
    mode="import_date" → <base>/<YYYY>/<YYYY-MM>/  based on collected_at
    """
    # backwards compat: if old bool param used, translate it
    if mode == "none" and organize_by_date:
        mode = "like_date"

    if mode == "like_date":
        dt = liked_at
    elif mode == "import_date":
        dt = collected_at
    else:
        dt = None

    if dt is not None:
        if mode == "import_date":
            folder = base / dt.strftime("%Y") / dt.strftime("%Y-%m-%d")
        else:
            folder = base / dt.strftime("%Y") / dt.strftime("%Y-%m")
    elif mode != "none":
        folder = base / "unknown"
    else:
        folder = base

    folder.mkdir(parents=True, exist_ok=True)
    return folder
