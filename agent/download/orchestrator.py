"""
Download orchestrator — same logic as the web app but runs locally.

Order:
  1. Muzpa     (MP3, high quality)
  2. Deezer    (MP3 320 via deemix, if installed and ARL configured)
  3. Bandcamp  (presence check → bandcamp_only)
  4. Discogs   (presence check → vinyl_only)

Returns job status: completed | vinyl_only | not_found
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from download import muzpa, discogs_check  # bandcamp_check disabled — re-enable when a reliable API is available
from download.audio_verify import verify_mp3

logger = logging.getLogger(__name__)

_EP_RE = re.compile(r"\bep\d*\b|\balbum\b", re.IGNORECASE)


def try_download(query: str, dest: Path, settings: dict) -> str:
    """
    settings: dict with muzpa_sess, deezer_arl, download_full_eps,
              folder_organize_mode (from /api/me/settings).

    Returns: completed | vinyl_only | not_found
    """
    muzpa_sess        = settings.get("muzpa_sess", "")
    deezer_arl        = settings.get("deezer_arl", "")
    download_full_eps = settings.get("download_full_eps", False)

    found_low_quality = False

    # ── 1. Muzpa ─────────────────────────────────────────────────────────────
    if muzpa_sess:
        try:
            track, status = muzpa.search(query, muzpa_sess)
            if track and status in ("found", "vinyl_only"):
                # Always attempt download even if flagged vinyl_only — flag is sometimes wrong
                path = muzpa.download(track["id"], track["filename"], dest, muzpa_sess)
                if verify_mp3(path):
                    return "completed"
                found_low_quality = True
                logger.info("Muzpa: low quality for %r, trying Deezer", query)
        except Exception:
            logger.exception("Muzpa error for %r", query)

    # ── 2. Deezer via deemix ─────────────────────────────────────────────────
    if deezer_arl:
        try:
            from download import deezer_dl
            if download_full_eps and bool(_EP_RE.search(query)):
                result, _ = deezer_dl.download_album(query, dest, deezer_arl)
                if result == "downloaded":
                    return "completed"
            result = deezer_dl.download_track(query, dest, deezer_arl)
            if result == "downloaded":
                return "completed"
            elif result == "low_quality":
                found_low_quality = True
        except Exception:
            logger.exception("Deezer error for %r", query)

    # ── 3. Bandcamp presence check (disabled — no reliable API found yet) ────────
    # Uncomment to re-enable (also restore bandcamp_check import above):
    # try:
    #     token = settings.get("_token", "")
    #     if bandcamp_check.exists(query, token):
    #         logger.info("Bandcamp: found %r — marking as bandcamp_only", query)
    #         return "bandcamp_only"
    # except Exception:
    #     logger.exception("Bandcamp check error for %r", query)

    # ── 4. Discogs presence check ─────────────────────────────────────────────
    try:
        if discogs_check.exists(query):
            logger.info("Discogs: found %r — marking as vinyl_only", query)
            return "vinyl_only"
    except Exception:
        logger.exception("Discogs check error for %r", query)

    if found_low_quality:
        return "not_found"

    return "not_found"
