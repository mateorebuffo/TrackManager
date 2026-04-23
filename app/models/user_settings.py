from sqlalchemy import Boolean, Column, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from app.db import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)

    soundcloud_oauth_token = Column(Text, nullable=True, default="")
    muzpa_sess = Column(Text, nullable=True, default="")
    deezer_arl = Column(Text, nullable=True, default="")
    download_dir = Column(Text, nullable=True, default="")
    download_full_eps = Column(Boolean, nullable=False, default=False)
    organize_by_like_date = Column(Boolean, nullable=False, default=False)  # legacy, kept for DB compat
    folder_organize_mode = Column(Text, nullable=False, default="none")  # none | like_date | import_date

    # OAuth token blobs stored as JSON strings
    spotify_token_json = Column(Text, nullable=True)
    youtube_token_json = Column(Text, nullable=True)

    # Selected playlists for sync
    spotify_playlist_id   = Column(Text, nullable=True)
    spotify_playlist_name = Column(Text, nullable=True)
    youtube_playlist_id   = Column(Text, nullable=True)
    youtube_playlist_name = Column(Text, nullable=True)

    user = relationship("User", back_populates="settings")

    def __repr__(self) -> str:
        return f"<UserSettings user_id={self.user_id}>"
