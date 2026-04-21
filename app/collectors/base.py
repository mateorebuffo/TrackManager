"""
Abstract base for all platform collectors.
Each collector yields RawTrack objects — platform-agnostic data containers.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator


@dataclass
class RawTrack:
    """Normalized raw data coming out of any collector."""
    source: str                          # e.g. "soundcloud"
    source_track_id: str                 # platform-native ID (string)
    source_url: str
    raw_title: str
    raw_artist: str | None
    duration_seconds: float | None
    liked_at: datetime | None
    raw_metadata: dict = field(default_factory=dict)


class BaseCollector(ABC):
    """
    Contract that every collector must implement.
    Collectors are stateless — they fetch and yield; persistence is handled upstream.
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Short lowercase identifier, e.g. 'soundcloud'."""
        ...

    @abstractmethod
    def fetch_liked_tracks(self) -> Iterator[RawTrack]:
        """
        Yield RawTrack objects for every liked track on the platform.
        Implementors should handle pagination internally.
        """
        ...
