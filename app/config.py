from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULT_KEY = "track-manager-secret-key-change-in-prod"


class Settings(BaseSettings):
    database_url: str = "postgresql://music_user:music_pass@localhost:5432/music_mvp"
    secret_key: str = _INSECURE_DEFAULT_KEY

    # Set DEBUG=true in your local .env to enable Swagger UI and local conveniences.
    debug: bool = False

    soundcloud_client_id: str = ""
    soundcloud_oauth_token: str = ""
    use_mock_collector: bool = True

    # Spotify OAuth — set SPOTIFY_REDIRECT_URI to your Railway URL in production.
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = ""

    # YouTube OAuth — set YOUTUBE_REDIRECT_URI to your Railway URL in production.
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = ""

    # Download credentials (stored per-user in UserSettings; these are fallback globals)
    muzpa_sess: str = ""
    deezer_arl: str = ""
    download_dir: str = ""
    download_full_eps: bool = False
    organize_by_like_date: bool = False

    # Agent distribution: set AGENT_DOWNLOAD_URL to an external URL (e.g. GitHub Release)
    # where users can download the TrackManagerAgent zip.  Leave empty if serving the exe
    # directly from app/static/agent/ (local / self-hosted only).
    agent_download_url: str = ""

    # Deduplication thresholds
    dedup_strong_match_score: float = 90.0
    dedup_weak_match_score: float = 75.0

    model_config = SettingsConfigDict(env_file=".env")

    @model_validator(mode="after")
    def _require_secret_key_in_production(self) -> "Settings":
        """Fail fast if the insecure default SECRET_KEY is used with a real database."""
        if self.secret_key == _INSECURE_DEFAULT_KEY and not self.database_url_safe.startswith("sqlite"):
            raise ValueError(
                "\n\n"
                "  FATAL: SECRET_KEY is set to the insecure default.\n"
                "  This must be changed before running in production.\n\n"
                "  Generate a key:  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
                "  Then set it as SECRET_KEY in your Railway environment variables.\n"
            )
        return self

    @property
    def database_url_safe(self) -> str:
        """Normalize postgres:// → postgresql:// for SQLAlchemy 2.x compatibility."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url


settings = Settings()
