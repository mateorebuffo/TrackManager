# Music Collector

A self-hosted web app for DJs to collect, organize, and automatically download liked tracks from SoundCloud, Spotify, and YouTube.

## Features

- **Multi-source sync** — SoundCloud, Spotify, YouTube
- **Metadata normalization** — cleans titles, splits artist/title, strips promo noise, detects remix versions
- **Deduplication** — fuzzy fingerprint matching flags duplicates before they enter the library
- **Review workflow** — pending → queue → downloaded, with manual metadata editing
- **Automated download pipeline** — Muzpa → Deezer (via deemix) → Bandcamp check → Discogs check
- **Download organization** — optionally organizes files into `YYYY/YYYY-MM/` folders by like date
- **EP/Album download** — when enabled, downloads complete EPs into a named subfolder
- **Multi-user auth** — username + password login, admin user management
- **Settings UI** — configure all credentials and options from the browser

---

## Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI + SQLAlchemy 2 |
| Database | SQLite (default) or PostgreSQL |
| UI | Jinja2 + Bootstrap 5 |
| Dedup | rapidfuzz |
| Downloads | httpx, mutagen, deemix (optional) |
| Auth | itsdangerous + PBKDF2-SHA256 |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+

### 2. Install

```bash
git clone https://github.com/your-username/music-collector.git
cd music-collector
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 4. Run

```bash
uvicorn app.main:app --reload
```

Open http://localhost:8000 — on first run you'll be prompted to create an admin account.

---

## Configuration

All settings can be edited at `/settings` in the UI or directly in `.env`.

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | SQLite (default) or PostgreSQL connection string |
| `SOUNDCLOUD_CLIENT_ID` | SoundCloud API client ID |
| `SOUNDCLOUD_OAUTH_TOKEN` | SoundCloud OAuth token |
| `SPOTIFY_CLIENT_ID` | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret |
| `YOUTUBE_CLIENT_ID` | Google OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | Google OAuth client secret |
| `MUZPA_SESS` | Muzpa session cookie |
| `DEEZER_ARL` | Deezer ARL cookie (for deemix) |
| `DOWNLOAD_DIR` | Base folder for downloaded tracks |
| `DOWNLOAD_FULL_EPS` | `true` — download entire EP/album when detected |
| `ORGANIZE_BY_LIKE_DATE` | `true` — organize downloads into `YYYY/YYYY-MM/` subfolders |
| `DEDUP_STRONG_MATCH_SCORE` | Similarity threshold for definite duplicates (default `90.0`) |
| `DEDUP_WEAK_MATCH_SCORE` | Similarity threshold for possible duplicates (default `75.0`) |

> **Note:** changes to `.env` require a server restart.

---

## Download Pipeline

For each queued track the app tries sources in order:

1. **Muzpa** — searches the promo pool; downloads MP3 if available
2. **Deezer** (via deemix) — searches and downloads MP3 320 kbps
3. **Bandcamp** — presence check only → marks as `bandcamp_only`
4. **Discogs** — presence check only → marks as `vinyl_only`
5. → `not_found` if all sources fail

Quality gate: files under 300 kbps are rejected and marked `low_quality`.

### Optional: Deezer via deemix

```bash
pip install deemix deezer-py
```

Requires a valid Deezer ARL (auth cookie from your browser). Set `DEEZER_ARL` in `.env`.

---

## Project Structure

```
app/
├── main.py                  # App entry point, middleware, router registration
├── config.py                # Settings (pydantic-settings)
├── db.py                    # SQLAlchemy engine + session
├── auth_middleware.py        # Session-cookie auth middleware
├── models/
│   ├── source_track.py      # Raw imported track
│   ├── normalized_track.py  # Parsed + cleaned track
│   ├── review_item.py       # Workflow state + TrackStatus enum
│   └── user.py              # User accounts
├── collectors/              # Source-specific importers (SoundCloud, Spotify, YouTube)
├── services/
│   ├── ingestion.py         # Full sync pipeline orchestrator
│   ├── normalization.py     # Metadata parsing pipeline
│   ├── deduplication.py     # Fingerprint-based dedup
│   ├── auto_download.py     # Download orchestrator
│   ├── muzpa.py             # Muzpa search + download
│   ├── deezer_dl.py         # Deezer/deemix download
│   ├── bandcamp_check.py    # Bandcamp presence check
│   ├── discogs_check.py     # Discogs presence check
│   ├── audio_verify.py      # MP3 quality gate (mutagen)
│   └── auth.py              # Password hashing + session tokens
├── api/
│   ├── sync.py              # Sync endpoints
│   ├── tracks.py            # Pending tracks UI + API
│   ├── review.py            # Review actions
│   ├── auto_download.py     # Bulk download + SSE progress stream
│   ├── auth.py              # Login / logout / user management
│   └── settings_page.py     # Settings UI
├── templates/               # Jinja2 + Bootstrap 5 HTML
└── utils/
    ├── text.py              # Title cleaning, fingerprinting
    └── fs.py                # Cross-platform path helpers
alembic/                     # DB migrations
requirements.txt
.env.example
```

---

## User Management

The first time you start the app with an empty database you'll be redirected to `/setup` to create an admin account.

Admins can manage users at `/admin/users`:
- Create new users (DJ or admin role)
- Reset passwords
- Delete accounts

---

## Database Migrations

Tables are auto-created on startup. For schema changes:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

---

## Track Statuses

| Status | Meaning |
|--------|---------|
| `pending` | Imported, waiting for review |
| `queued` | Added to download queue |
| `downloaded` | Successfully downloaded |
| `not_found` | Not found on any source |
| `low_quality` | Found but rejected (< 300 kbps) |
| `vinyl_only` | Only available on vinyl / Discogs |
| `bandcamp_only` | Found on Bandcamp, not auto-downloadable |
| `set_mix` | Duration > 35 min — treated as a DJ set |
| `discarded` | Manually discarded |

---

## License

MIT
