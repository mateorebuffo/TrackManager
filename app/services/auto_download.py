"""
Auto-download orchestrator.

Tries each source in order until the track is downloaded:
  1. Muzpa     (own library, MP3)
  2. Deezer    (via deemix, MP3 320)
  3. Bandcamp  (presence check only — marks as bandcamp_only if found)
  4. Discogs   (presence check only — marks as vinyl_only if found)

Returns one of:
  "downloaded"    — file saved at good quality (>= 300 kbps)
  "low_quality"   — file found but rejected for low bitrate
  "vinyl_only"    — found on Muzpa but marked as vinyl-only release
  "bandcamp_only" — not downloadable, but found on Bandcamp
  "not_found"     — no source had the track
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.services import muzpa, deezer_dl, bandcamp_check, discogs_check
from app.services.audio_verify import verify_mp3

logger = logging.getLogger(__name__)

_EP_RE = re.compile(r"\bep\d*\b|\balbum\b", re.IGNORECASE)


def _is_ep_query(query: str) -> bool:
    return bool(_EP_RE.search(query))


def try_download(search_query: str, dest: Path | None = None, user_settings=None) -> str:
    """
    Try to download a track from each configured source.
    user_settings: a UserSettings DB object (or None to fall back to global config).
    """
    # Resolve settings — prefer user_settings, fall back to global config
    if user_settings is None:
        from app.config import settings as global_settings
        muzpa_sess = global_settings.muzpa_sess
        deezer_arl = global_settings.deezer_arl
        download_full_eps = global_settings.download_full_eps
        if dest is None:
            dest = Path(global_settings.download_dir) if global_settings.download_dir else None
    else:
        muzpa_sess = user_settings.muzpa_sess or ""
        deezer_arl = user_settings.deezer_arl or ""
        download_full_eps = user_settings.download_full_eps or False
        if dest is None:
            dest = Path(user_settings.download_dir) if user_settings.download_dir else None

    if not dest:
        logger.error("download_dir not configured for user")
        return "not_found"

    found_low_quality = False

    # ── 1. Muzpa ────────────────────────────────────────────────────────────
    if muzpa_sess:
        try:
            track, muzpa_status = muzpa.search(search_query, muzpa_sess)
            if track and muzpa_status in ("found", "vinyl_only"):
                dest_file = muzpa.download(track["id"], track["filename"], dest, muzpa_sess)
                if verify_mp3(dest_file):
                    return "downloaded"
                else:
                    found_low_quality = True
                    logger.info("Muzpa: low quality for %r, trying next source", search_query)
        except Exception:
            logger.exception("Muzpa error for %r", search_query)
    else:
        logger.debug("muzpa_sess not configured, skipping")

    # ── 2. Deezer via deemix ────────────────────────────────────────────────
    if deezer_arl:
        try:
            if download_full_eps and _is_ep_query(search_query):
                result, _ = deezer_dl.download_album(search_query, dest, deezer_arl)
                if result == "downloaded":
                    return "downloaded"
            result = deezer_dl.download_track(search_query, dest, deezer_arl)
            if result == "downloaded":
                return "downloaded"
            elif result == "low_quality":
                found_low_quality = True
        except Exception:
            logger.exception("Deezer error for %r", search_query)
    else:
        logger.debug("deezer_arl not configured, skipping")

    if found_low_quality:
        return "low_quality"

    # ── 3. Bandcamp presence check ──────────────────────────────────────────
    try:
        if bandcamp_check.exists(search_query):
            logger.info("Bandcamp: found %r — marking as bandcamp_only", search_query)
            return "bandcamp_only"
    except Exception:
        logger.exception("Bandcamp check error for %r", search_query)

    # ── 4. Discogs presence check ───────────────────────────────────────────
    try:
        if discogs_check.exists(search_query):
            logger.info("Discogs: found %r — marking as vinyl_only (physical release)", search_query)
            return "vinyl_only"
    except Exception:
        logger.exception("Discogs check error for %r", search_query)

    logger.info("All sources exhausted for %r", search_query)
    return "not_found"
