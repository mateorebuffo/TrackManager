"""
Descubrir: los productores y sellos que más bajó el alumno, y productores y
sellos nuevos que suenan parecido.

Las recomendaciones salen de Cosine Club (similitud por audio, fuerte en
electrónica underground). Cosine no trae el sello: se lee de Discogs, cuyo
release viene en el `external_link` de cada track.
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.utils.text import split_artists

logger = logging.getLogger(__name__)

_COSINE_BULK = "https://cosine.club/api/v1/search/bulk"
_DISCOGS_RELEASE = "https://api.discogs.com/releases/{id}"
_UA = "TrackManager/1.0"
_RELEASE_RE = re.compile(r"discogs\.com/release/(\d+)")
_DISAMBIGUATION_RE = re.compile(r"\s*\(\d+\)$")  # Discogs: "Phonica (2)"

TOP = 10
PER_SEED = 2          # recomendaciones por tema bajado, para que no lo acapare uno solo
SEEDS = 50            # máximo de /search/bulk
SEEDS_PER_PRODUCER = 3  # 50 semillas = los ~17 productores que más bajó
DISCOGS_CALLS = 40    # por refresco: Discogs da 25-60 req/min
CACHE_SECONDS = 24 * 3600

# ponytail: caches en memoria, se pierden en cada deploy; pasar a tabla si molesta.
_releases: dict[str, dict | None] = {}
_recs: dict[int, tuple[float, dict]] = {}
_shown: dict[int, set[str]] = {}  # lo ya sugerido, para que "nuevas sugerencias" no repita


def set_label(st: SourceTrack, label: str | None) -> None:
    """Guarda el sello donde ya lo guarda la extensión de Traxsource."""
    label = (label or "").strip()[:255]
    if label:
        # Dict nuevo: SQLAlchemy no ve cambios dentro de un JSON mutado en el lugar.
        st.raw_metadata_json = {**(st.raw_metadata_json or {}), "label": label}


def _label_of(st: SourceTrack) -> str | None:
    return (st.raw_metadata_json or {}).get("label") or None


def _downloaded(db: Session, user_id: int) -> list[tuple[NormalizedTrack, SourceTrack]]:
    """Lo que bajó el alumno, lo más nuevo primero."""
    return (
        db.query(NormalizedTrack, SourceTrack)
        .join(NormalizedTrack.source_track)
        .join(NormalizedTrack.review_item)
        .filter(SourceTrack.user_id == user_id, ReviewItem.status == TrackStatus.downloaded)
        .order_by(ReviewItem.id.desc())
        .all()
    )


def _tally(names) -> list[tuple[str, int]]:
    """Cuenta sin distinguir mayúsculas; muestra la primera forma que aparece."""
    counts: dict[str, list] = {}
    for n in names:
        counts.setdefault(n.casefold(), [n, 0])[1] += 1
    return sorted(((n, c) for n, c in counts.values()), key=lambda x: -x[1])


def _seed_rows(rows: list[tuple[NormalizedTrack, SourceTrack]]) -> list[tuple[NormalizedTrack, SourceTrack]]:
    """
    Semillas para Cosine: temas de los productores que más bajó, en el orden
    del ranking, para que "porque bajaste…" salga de ahí. Atrás, el resto.
    """
    by_artist: dict[str, list] = defaultdict(list)
    for row in rows:
        for a in split_artists(row[0].normalized_artist):
            by_artist[a.casefold()].append(row)
    top = _tally(a for nt, _ in rows for a in split_artists(nt.normalized_artist))
    return [r for name, _ in top for r in by_artist[name.casefold()][:SEEDS_PER_PRODUCER]] + rows


def rankings(db: Session, user_id: int) -> dict:
    rows = _downloaded(db, user_id)
    producers = _tally(a for nt, _ in rows for a in split_artists(nt.normalized_artist))
    labels = _tally(lbl for _, st in rows if (lbl := _label_of(st)))
    return {
        "total": len(rows),
        "producers": producers[:TOP],
        "labels": labels[:TOP],
        "unlabeled": len(rows) - sum(c for _, c in labels),
    }


def tracks_of(db: Session, user_id: int, producer: str | None = None, label: str | None = None) -> list[dict]:
    """Lo que bajó de un productor o de un sello, lo más nuevo primero."""
    wanted = (producer or label or "").casefold()
    out = []
    for nt, st in _downloaded(db, user_id):
        if producer and wanted not in {a.casefold() for a in split_artists(nt.normalized_artist)}:
            continue
        if label and (_label_of(st) or "").casefold() != wanted:
            continue
        out.append({"artist": nt.normalized_artist, "title": nt.normalized_title,
                    "version": nt.version_info, "label": _label_of(st),
                    "source": st.source, "url": st.source_url})
    return out


def _discogs_release(link: str | None, budget: list[int]) -> dict | None:
    """{"label", "label_url"} del release de Discogs, o None."""
    m = _RELEASE_RE.search(link or "")
    if not m:
        return None
    rid = m.group(1)
    if rid in _releases:
        return _releases[rid]
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    headers = {"User-Agent": _UA}
    if settings.discogs_token:
        headers["Authorization"] = f"Discogs token={settings.discogs_token}"
    try:
        r = httpx.get(_DISCOGS_RELEASE.format(id=rid), headers=headers, timeout=10)
    except httpx.HTTPError as e:
        logger.warning("Discogs release %s: %s", rid, e)
        return None
    if r.status_code == 429:
        budget[0] = 0  # el resto, en el próximo refresco
        return None
    if r.status_code != 200:
        _releases[rid] = None
        return None
    labels = r.json().get("labels") or []
    found = None
    if labels and labels[0].get("name"):
        found = {
            "label": _DISAMBIGUATION_RE.sub("", labels[0]["name"]),
            "label_url": f"https://www.discogs.com/label/{labels[0]['id']}" if labels[0].get("id") else None,
        }
    _releases[rid] = found
    return found


def _spread(items: list[dict], n: int, seed_of) -> list[dict]:
    """
    Los n primeros, con como mucho PER_SEED por semilla. El score de Cosine no
    se compara bien entre semillas: un tema con vecinos muy parecidos (todos
    0.9+) se quedaba con toda la lista. Si no alcanza, se completa con el resto.
    """
    picked, rest, per = [], [], Counter()
    for it in items:
        seed = seed_of(it)
        (picked if per[seed] < PER_SEED else rest).append(it)
        per[seed] += 1
    return (picked + rest)[:n]


def _cosine(seeds: list[str]) -> list[dict]:
    r = httpx.post(
        _COSINE_BULK,
        json={"tracks": seeds, "similar_limit": 10},
        headers={"Authorization": f"Bearer {settings.cosine_api_key}", "User-Agent": _UA},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]["results"]


def recommend(db: Session, user_id: int, fresh: bool = False) -> dict:
    """fresh: saltear la caché y no repetir nada de lo ya sugerido."""
    hit = _recs.get(user_id)
    if not fresh and hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    shown = _shown.get(user_id, set()) if fresh else set()
    if not settings.cosine_api_key:
        return {"error": "Falta configurar COSINE_API_KEY."}

    rows = _downloaded(db, user_id)
    if not rows:
        return {"producers": [], "labels": []}
    seeds: dict[str, SourceTrack] = {}
    for nt, st in _seed_rows(rows):
        if nt.normalized_artist and nt.normalized_title:
            seeds.setdefault(f"{nt.normalized_artist} - {nt.normalized_title}", st)
        if len(seeds) == SEEDS:
            break
    try:
        results = _cosine(list(seeds))
    except Exception:
        logger.exception("Cosine Club falló para el usuario %s", user_id)
        return {"error": "Cosine Club no respondió. Probá de nuevo en un rato."}

    logger.info("Cosine: %d de %d temas encontrados (usuario %s)", len(results), len(seeds), user_id)
    budget = [DISCOGS_CALLS]
    # De paso, completar el sello de lo que ya bajó (tracks previos al agente con sello).
    for res in results:
        st = seeds.get(res.get("query"))
        if st and not _label_of(st):
            rel = _discogs_release((res.get("track") or {}).get("external_link"), budget)
            set_label(st, rel and rel["label"])
    db.commit()

    known_artists = {
        a.casefold()
        for (name,) in db.query(NormalizedTrack.normalized_artist)
        .join(NormalizedTrack.source_track).filter(SourceTrack.user_id == user_id)
        for a in split_artists(name)
    }
    known_labels = {
        lbl.casefold()
        for (meta,) in db.query(SourceTrack.raw_metadata_json).filter(SourceTrack.user_id == user_id)
        if (lbl := (meta or {}).get("label"))
    }

    similar: dict[str, dict] = {}  # por id de Cosine: el mismo track puede salir de varias semillas
    for res in results:
        for t in res.get("similar_tracks") or []:
            t = {**t, "because": res.get("query"), "score": t.get("score") or 0}
            prev = similar.get(t.get("id"))
            if not prev or t["score"] > prev["score"]:
                similar[t.get("id")] = t
    ranked = sorted(similar.values(), key=lambda t: -t["score"])

    producers: dict[str, dict] = {}
    for t in ranked:
        for name in split_artists(t.get("artist")):
            if name.casefold() in known_artists or f"p:{name.casefold()}" in shown:
                continue
            p = producers.setdefault(name.casefold(), {"name": name, "score": 0.0, "track": t})
            p["score"] += t["score"]

    labels: dict[str, dict] = {}
    for t in _spread(ranked, len(ranked), lambda t: t["because"]):
        if len(labels) == TOP:
            break
        rel = _discogs_release(t.get("external_link"), budget)
        if not rel or rel["label"].casefold() in known_labels or f"l:{rel['label'].casefold()}" in shown:
            continue
        lab = labels.setdefault(rel["label"].casefold(), {**rel, "score": 0.0, "track": t})
        lab["score"] += t["score"]

    out = {
        "producers": _spread(sorted(producers.values(), key=lambda p: -p["score"]), TOP,
                             lambda p: p["track"]["because"]),
        "labels": _spread(sorted(labels.values(), key=lambda p: -p["score"]), TOP,
                          lambda p: p["track"]["because"]),
    }
    if fresh and not (out["producers"] or out["labels"]):
        _shown.pop(user_id, None)  # ya se mostró todo: volver a empezar por lo mejor
        _recs.pop(user_id, None)
        return recommend(db, user_id)
    _shown[user_id] = shown | {f"p:{p['name'].casefold()}" for p in out["producers"]}         | {f"l:{l['label'].casefold()}" for l in out["labels"]}
    if budget[0] > 0:  # si Discogs cortó, el próximo refresco completa los sellos
        _recs[user_id] = (time.time(), out)
    return out
