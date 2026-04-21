"""
Deezer download service via the deemix Python library.

Install: pip install deemix
Requires a Deezer ARL (auth cookie from browser).
Returns 'downloaded', 'low_quality', or 'not_found'.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from app.services.audio_verify import verify_mp3

_MIN_SIMILARITY = 0.5  # minimum ratio to accept a Deezer result


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower())


def _similarity(query: str, track: dict) -> float:
    artist = track.get("artist", {}).get("name", "") if isinstance(track.get("artist"), dict) else ""
    title = track.get("title", "")
    candidate = _normalize(f"{artist} {title}")
    return SequenceMatcher(None, _normalize(query), candidate).ratio()

logger = logging.getLogger(__name__)

# deemix is not thread-safe — serialize all downloads through this lock
_DEEZER_LOCK = threading.Lock()


def download_track(query: str, dest_folder: Path, arl: str) -> str:
    """
    Search Deezer for query and download the best match as MP3 320.
    Returns 'downloaded', 'low_quality', or 'not_found'.
    """
    try:
        from deezer import Deezer
        from deemix import generateDownloadObject
        from deemix.downloader import Downloader
        from deemix.settings import DEFAULTS as DEFAULT_SETTINGS
    except ImportError:
        logger.warning("deemix not installed — skipping Deezer download")
        return "not_found"

    # Search via Deezer public API (no auth needed)
    try:
        resp = httpx.get(
            "https://api.deezer.com/search",
            params={"q": query, "limit": 5},
            timeout=15,
        )
        resp.raise_for_status()
        tracks = resp.json().get("data") or []
    except Exception as e:
        logger.warning("Deezer search failed: %s", e)
        return "not_found"

    if not tracks:
        logger.info("Deezer: no results for %r", query)
        return "not_found"

    # Pick the best-matching result above the similarity threshold
    best = max(tracks, key=lambda t: _similarity(query, t))
    score = _similarity(query, best)
    if score < _MIN_SIMILARITY:
        logger.info("Deezer: best match score %.2f too low for %r — skipping", score, query)
        return "not_found"

    track = best
    track_url = f"https://www.deezer.com/track/{track['id']}"
    logger.info("Deezer found: %r (id=%s, score=%.2f)", track.get("title"), track.get("id"), score)

    dz = Deezer()
    if not dz.login_via_arl(arl):
        logger.warning("Deezer ARL login failed")
        return "not_found"

    dest_folder.mkdir(parents=True, exist_ok=True)

    dl_settings = copy.deepcopy(DEFAULT_SETTINGS)
    dl_settings["downloadLocation"] = str(dest_folder)
    dl_settings["overwriteFile"] = "y"
    dl_settings["createPlaylistFolder"] = False
    dl_settings["createArtistFolder"] = False
    dl_settings["createAlbumFolder"] = False

    with _DEEZER_LOCK:
        files_before = set(dest_folder.glob("*.mp3"))
        try:
            download_obj = generateDownloadObject(dz, track_url, "3")
            downloader = Downloader(dz, download_obj, dl_settings)
            downloader.start()
        except Exception as e:
            logger.warning("Deezer download error for %r: %s", query, e)
            return "not_found"
        files_after = set(dest_folder.glob("*.mp3"))

    new_files = files_after - files_before
    if not new_files:
        logger.warning("Deezer: no new file created for %r", query)
        return "not_found"

    new_file = next(iter(new_files))
    if not verify_mp3(new_file):
        logger.warning("Deezer: quality check failed for %r", query)
        return "low_quality"

    logger.info("Deezer download complete: %s", new_file.name)
    return "downloaded"


def _album_similarity(query: str, album: dict) -> float:
    artist = album.get("artist", {}).get("name", "") if isinstance(album.get("artist"), dict) else ""
    title = album.get("title", "")
    candidate = _normalize(f"{artist} {title}")
    return SequenceMatcher(None, _normalize(query), candidate).ratio()


def download_album(query: str, base_dest: Path, arl: str) -> tuple[str, Path | None]:
    """
    Search Deezer for an album matching query and download all tracks into a subfolder.
    Returns ('downloaded', folder_path) or ('not_found', None).
    """
    try:
        from deezer import Deezer
        from deemix import generateDownloadObject
        from deemix.downloader import Downloader
        from deemix.settings import DEFAULTS as DEFAULT_SETTINGS
    except ImportError:
        logger.warning("deemix not installed — skipping Deezer album download")
        return "not_found", None

    try:
        resp = httpx.get(
            "https://api.deezer.com/search/album",
            params={"q": query, "limit": 5},
            timeout=15,
        )
        resp.raise_for_status()
        albums = resp.json().get("data") or []
    except Exception as e:
        logger.warning("Deezer album search failed: %s", e)
        return "not_found", None

    if not albums:
        logger.info("Deezer: no album results for %r", query)
        return "not_found", None

    best = max(albums, key=lambda a: _album_similarity(query, a))
    score = _album_similarity(query, best)
    if score < _MIN_SIMILARITY:
        logger.info("Deezer album: best match score %.2f too low for %r — skipping", score, query)
        return "not_found", None

    artist_name = best.get("artist", {}).get("name", "") if isinstance(best.get("artist"), dict) else ""
    album_title = best.get("title", "")
    folder_name = f"{artist_name} - {album_title}" if artist_name else album_title
    # Sanitize folder name for Windows
    for ch in r'\/:*?"<>|':
        folder_name = folder_name.replace(ch, "_")
    dest_folder = base_dest / folder_name
    dest_folder.mkdir(parents=True, exist_ok=True)

    album_url = f"https://www.deezer.com/album/{best['id']}"
    logger.info("Deezer album found: %r (id=%s, score=%.2f) → %s", album_title, best["id"], score, dest_folder)

    dz = Deezer()
    if not dz.login_via_arl(arl):
        logger.warning("Deezer ARL login failed")
        return "not_found", None

    dl_settings = copy.deepcopy(DEFAULT_SETTINGS)
    dl_settings["downloadLocation"] = str(dest_folder)
    dl_settings["overwriteFile"] = "y"
    dl_settings["createPlaylistFolder"] = False
    dl_settings["createArtistFolder"] = False
    dl_settings["createAlbumFolder"] = False

    with _DEEZER_LOCK:
        try:
            download_obj = generateDownloadObject(dz, album_url, "3")
            downloader = Downloader(dz, download_obj, dl_settings)
            downloader.start()
        except Exception as e:
            logger.warning("Deezer album download error for %r: %s", query, e)
            return "not_found", None

    mp3_files = list(dest_folder.glob("*.mp3"))
    if not mp3_files:
        logger.warning("Deezer album: no files created in %s", dest_folder)
        return "not_found", None

    logger.info("Deezer album download complete: %d tracks in %s", len(mp3_files), dest_folder)
    return "downloaded", dest_folder
