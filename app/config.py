from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://music_user:music_pass@localhost:5432/music_mvp"
    soundcloud_client_id: str = ""
    soundcloud_oauth_token: str = ""
    use_mock_collector: bool = True

    # Spotify OAuth
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8000/sync/spotify/callback"

    # YouTube OAuth
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://127.0.0.1:8000/sync/youtube/callback"

    # Auto-download
    muzpa_sess: str = ""
    deezer_arl: str = ""
    download_dir: str = ""
    download_full_eps: bool = False  # download entire EP/album when query contains EP keyword
    organize_by_like_date: bool = False  # organize downloads into <base>/<YYYY>/<YYYY-MM>/

    # Deduplication thresholds
    dedup_strong_match_score: float = 90.0
    dedup_weak_match_score: float = 75.0

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
