"""
Audio file quality verification using mutagen.
Reads only the file header — no performance impact.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MIN_BITRATE_KBPS = 300  # accept 320 kbps; reject 128/192/256


def verify_mp3(path: Path) -> bool:
    """
    Returns True if the file is a valid MP3 with bitrate >= 300 kbps.
    Deletes the file and returns False if it fails verification.
    """
    def _try_delete(p: Path) -> None:
        try:
            p.unlink(missing_ok=True)
        except OSError as ex:
            logger.warning("Could not delete %s: %s (file may still be locked)", p.name, ex)

    try:
        from mutagen.mp3 import MP3
        audio = MP3(path)
        bitrate = audio.info.bitrate // 1000
        if bitrate < _MIN_BITRATE_KBPS:
            logger.warning("Quality too low: %s (%d kbps) — deleting", path.name, bitrate)
            _try_delete(path)
            return False
        logger.info("Quality OK: %s (%d kbps)", path.name, bitrate)
        return True
    except Exception as e:
        logger.warning("Could not verify %s: %s — deleting", path.name, e)
        _try_delete(path)
        return False
