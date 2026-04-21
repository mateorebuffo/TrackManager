"""
Filesystem utilities — cross-platform (Windows + macOS).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def resolve_download_folder(
    base: Path,
    liked_at: datetime | None,
    organize_by_date: bool,
) -> Path:
    """
    Return the target folder for a downloaded track.

    When organize_by_date is True:
        <base>/<YYYY>/<YYYY-MM>/     ← liked_at date
        <base>/unknown/              ← no date available

    When organize_by_date is False:
        <base>/                      ← flat, current behavior
    """
    if not organize_by_date:
        folder = base
    elif liked_at is not None:
        year   = liked_at.strftime("%Y")
        month  = liked_at.strftime("%Y-%m")
        folder = base / year / month
    else:
        folder = base / "unknown"

    folder.mkdir(parents=True, exist_ok=True)
    return folder
