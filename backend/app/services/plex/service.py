from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from plexapi.server import PlexServer as PlexApiServer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import ApplicationSetting, Movie, PlexLibrary, PlexServer
from app.security.secrets import decrypt_secret


@dataclass(slots=True)
class PlexConnectionInfo:
    connected: bool
    name: str | None = None
    version: str | None = None
    machine_identifier: str | None = None
    libraries: list[dict[str, Any]] | None = None
    error: str | None = None


class PlexService:
    def __init__(self, base_url: str | None = None, token: str | None = None, server_id: int | None = None):
        self.base_url = (base_url if base_url is not None else settings.PLEX_URL).rstrip("/")
        self.token = token if token is not None else settings.PLEX_TOKEN
        self.server_id = server_id

    @staticmethod
    def active_server_id(db: Session) -> int | None:
        row = db.get(ApplicationSetting, "plex_active_server")
        if not row or not isinstance(row.value, dict):
            return None
        try:
            value = int(row.value.get("server_id"))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @classmethod
    def from_db(cls, db: Session, server_id: int | None = None) -> "PlexService":
        if settings.MOCK_PLEX:
            return cls("mock", "mock", server_id=server_id)

        resolved_id = server_id or cls.active_server_id(db)
        row: PlexServer | None = None
        if resolved_id:
            row = db.get(PlexServer, resolved_id)
            if row and not row.enabled:
                row = None

        if row is None:
            row = db.scalar(
                select(PlexServer)
                .where(
                    PlexServer.enabled.is_(True),
                    PlexServer.base_url != "environment",
                    PlexServer.token_ciphertext != "environment",
                )
                .order_by(PlexServer.id)
                .limit(1)
            )

        if not row or row.base_url == "environment" or row.token_ciphertext == "environment":
            return cls()
        return cls(row.base_url, decrypt_secret(row.token_ciphertext), server_id=row.id)

    @classmethod
    def for_library(cls, db: Session, library: PlexLibrary | int) -> "PlexService":
        row = db.get(PlexLibrary, library) if isinstance(library, int) else library
        if not row:
            return cls()
        return cls.from_db(db, server_id=row.server_id)

    @classmethod
    def for_movie(cls, db: Session, movie: Movie | int) -> "PlexService":
        row = db.get(Movie, movie) if isinstance(movie, int) else movie
        if not row:
            return cls()
        return cls.for_library(db, row.library_id)

    def _headers(self) -> dict[str, str]:
        return {
            "X-Plex-Token": self.token,
            "X-Plex-Client-Identifier": "plumbus-server",
            "X-Plex-Product": "Plumbus",
            "X-Plex-Version": "1.0.0",
            "Accept": "application/json",
        }

    def _safe_error(self, exc: Exception) -> str:
        message = f"{type(exc).__name__}: {exc}"
        if self.token:
            message = message.replace(self.token, "[redacted]")
        lower = message.lower()
        html_index = lower.find("<html")
        if html_index >= 0:
            message = message[:html_index]
        message = " ".join(message.split()).rstrip(" :-")
        if "401" in message and "unauthorized" in message.lower():
            return f"Plex rejected the server token with HTTP 401 Unauthorized ({self.base_url})"
        return message[:500]

    def connect(self) -> PlexConnectionInfo:
        if settings.MOCK_PLEX:
            return PlexConnectionInfo(
                connected=True,
                name="Plumbus Mock Plex",
                version="mock",
                machine_identifier="mock-server",
                libraries=self.get_libraries(),
            )
        if not self.base_url or not self.token:
            return PlexConnectionInfo(connected=False, libraries=[], error="Plex server URL or access token is missing")
        try:
            server = PlexApiServer(self.base_url, self.token, timeout=10)
            libraries = [
                {"key": str(section.key), "title": section.title, "type": section.type}
                for section in server.library.sections()
                if section.type in {"movie", "show"}
            ]
            return PlexConnectionInfo(
                connected=True,
                name=server.friendlyName,
                version=server.version,
                machine_identifier=server.machineIdentifier,
                libraries=libraries,
            )
        except Exception as exc:
            return PlexConnectionInfo(connected=False, libraries=[], error=self._safe_error(exc))

    def get_libraries(self) -> list[dict[str, Any]]:
        if settings.MOCK_PLEX:
            return [
                {"key": "1", "title": "Movies", "type": "movie"},
                {"key": "2", "title": "Anime", "type": "show"},
            ]
        return self.connect().libraries or []

    @staticmethod
    def _media_rows(item: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for media in getattr(item, "media", []) or []:
            for part in getattr(media, "parts", []) or []:
                dynamic_range = getattr(media, "videoDynamicRange", None)
                rows.append(
                    {
                        "plex_media_id": str(getattr(media, "id", "")) or None,
                        "part_key": getattr(part, "key", None),
                        "file_path": getattr(part, "file", None),
                        "container": getattr(media, "container", None),
                        "video_codec": getattr(media, "videoCodec", None),
                        "audio_codec": getattr(media, "audioCodec", None),
                        "width": getattr(media, "width", None),
                        "height": getattr(media, "height", None),
                        "resolution": getattr(media, "videoResolution", None),
                        "bitrate": getattr(media, "bitrate", None),
                        "hdr": str(dynamic_range) if dynamic_range else None,
                        "audio_channels": getattr(media, "audioChannels", None),
                        "file_size": getattr(part, "size", None),
                    }
                )
        return rows

    @classmethod
    def _payload(
        cls,
        item: Any,
        media_type: str,
        *,
        parent_rating_key: str | None = None,
        grandparent_rating_key: str | None = None,
        parent_title: str | None = None,
        grandparent_title: str | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> dict[str, Any]:
        return {
            "rating_key": str(item.ratingKey),
            "media_type": media_type,
            "parent_rating_key": parent_rating_key,
            "grandparent_rating_key": grandparent_rating_key,
            "parent_title": parent_title,
            "grandparent_title": grandparent_title,
            "season_number": season_number,
            "episode_number": episode_number,
            "title": item.title,
            "original_title": getattr(item, "originalTitle", None),
            "year": getattr(item, "year", None),
            "summary": getattr(item, "summary", None),
            "tagline": getattr(item, "tagline", None),
            "content_rating": getattr(item, "contentRating", None),
            "duration_ms": getattr(item, "duration", None),
            "studio": getattr(item, "studio", None),
            "rating": str(getattr(item, "rating", "") or "") or None,
            "audience_rating": str(getattr(item, "audienceRating", "") or "") or None,
            "edition_title": getattr(item, "editionTitle", None),
            "genres": [x.tag for x in getattr(item, "genres", []) or []],
            "directors": [x.tag for x in getattr(item, "directors", []) or []],
            "actors": [x.tag for x in getattr(item, "roles", []) or []],
            "writers": [x.tag for x in getattr(item, "writers", []) or []],
            "collections": [x.tag for x in getattr(item, "collections", []) or []],
            "labels": [x.tag for x in getattr(item, "labels", []) or []],
            "poster_key": getattr(item, "thumb", None),
            "art_key": getattr(item, "art", None),
            "added_at": getattr(item, "addedAt", None),
            "updated_at": getattr(item, "updatedAt", None),
            "media": cls._media_rows(item),
        }

    @staticmethod
    def _mock_movie_rows() -> list[dict[str, Any]]:
        samples = [
            ("1001", "Interstellar", 2014, "1080", "h264"),
            ("1002", "The Matrix", 1999, "1080", "h264"),
            ("1003", "Blade Runner 2049", 2017, "2160", "hevc"),
            ("1004", "Dune", 2021, "2160", "hevc"),
            ("1005", "The Dark Knight", 2008, "1080", "h264"),
        ]
        return [
            {
                "rating_key": key,
                "media_type": "movie",
                "parent_rating_key": None,
                "grandparent_rating_key": None,
                "parent_title": None,
                "grandparent_title": None,
                "season_number": None,
                "episode_number": None,
                "title": title,
                "year": year,
                "summary": f"Development fixture for {title}.",
                "genres": ["Drama", "Science Fiction"],
                "directors": [],
                "actors": [],
                "writers": [],
                "collections": [],
                "labels": [],
                "poster_key": None,
                "art_key": None,
                "added_at": None,
                "updated_at": None,
                "media": [
                    {
                        "plex_media_id": f"m-{key}",
                        "part_key": f"/library/parts/{key}/file.mp4",
                        "file_path": None,
                        "container": "mp4",
                        "video_codec": codec,
                        "audio_codec": "aac",
                        "width": 3840 if resolution == "2160" else 1920,
                        "height": 2160 if resolution == "2160" else 1080,
                        "resolution": f"{resolution}p" if resolution != "2160" else "4K",
                        "bitrate": 12000,
                        "hdr": "HDR10" if resolution == "2160" else None,
                        "audio_channels": 6,
                        "file_size": None,
                    }
                ],
            }
            for key, title, year, resolution, codec in samples
        ]

    @staticmethod
    def _mock_show_rows() -> list[dict[str, Any]]:
        show_key = "2000"
        season_key = "2001"
        rows: list[dict[str, Any]] = [
            {
                "rating_key": show_key,
                "media_type": "show",
                "parent_rating_key": None,
                "grandparent_rating_key": None,
                "parent_title": None,
                "grandparent_title": None,
                "season_number": None,
                "episode_number": None,
                "title": "Neon Ronin",
                "year": 2026,
                "summary": "Mock anime series.",
                "genres": ["Anime", "Action"],
                "directors": [],
                "actors": [],
                "writers": [],
                "collections": [],
                "labels": [],
                "poster_key": None,
                "art_key": None,
                "added_at": None,
                "updated_at": None,
                "media": [],
            },
            {
                "rating_key": season_key,
                "media_type": "season",
                "parent_rating_key": show_key,
                "grandparent_rating_key": None,
                "parent_title": "Neon Ronin",
                "grandparent_title": None,
                "season_number": 1,
                "episode_number": None,
                "title": "Season 1",
                "year": 2026,
                "summary": None,
                "genres": [],
                "directors": [],
                "actors": [],
                "writers": [],
                "collections": [],
                "labels": [],
                "poster_key": None,
                "art_key": None,
                "added_at": None,
                "updated_at": None,
                "media": [],
            },
        ]
        for episode in range(1, 4):
            key = f"20{episode + 1:02d}"
            rows.append(
                {
                    "rating_key": key,
                    "media_type": "episode",
                    "parent_rating_key": season_key,
                    "grandparent_rating_key": show_key,
                    "parent_title": "Season 1",
                    "grandparent_title": "Neon Ronin",
                    "season_number": 1,
                    "episode_number": episode,
                    "title": f"Episode {episode}",
                    "year": 2026,
                    "summary": f"Mock anime episode {episode}.",
                    "genres": ["Anime"],
                    "directors": [],
                    "actors": [],
                    "writers": [],
                    "collections": [],
                    "labels": [],
                    "poster_key": None,
                    "art_key": None,
                    "added_at": None,
                    "updated_at": None,
                    "media": [
                        {
                            "plex_media_id": f"m-{key}",
                            "part_key": f"/library/parts/{key}/episode.mp4",
                            "file_path": None,
                            "container": "mp4",
                            "video_codec": "h264",
                            "audio_codec": "aac",
                            "width": 1920,
                            "height": 1080,
                            "resolution": "1080p",
                            "bitrate": 8000,
                            "hdr": None,
                            "audio_channels": 2,
                            "file_size": None,
                        }
                    ],
                }
            )
        return rows

    def iter_items(self, library_key: str, library_type: str | None = None) -> list[dict[str, Any]]:
        if settings.MOCK_PLEX:
            return self._mock_show_rows() if library_type == "show" else self._mock_movie_rows()

        server = PlexApiServer(self.base_url, self.token, timeout=30)
        section = server.library.sectionByID(int(library_key))
        effective_type = library_type or getattr(section, "type", None)
        rows: list[dict[str, Any]] = []

        for item in section.all():
            item_type = str(getattr(item, "TYPE", None) or getattr(item, "type", None) or effective_type or "")
            if item_type == "movie":
                rows.append(self._payload(item, "movie"))
                continue
            if item_type != "show":
                continue

            show_key = str(item.ratingKey)
            show_title = item.title
            rows.append(self._payload(item, "show"))
            for season in item.seasons():
                season_key = str(season.ratingKey)
                season_number = getattr(season, "index", None)
                rows.append(
                    self._payload(
                        season,
                        "season",
                        parent_rating_key=show_key,
                        parent_title=show_title,
                        season_number=season_number,
                    )
                )
                for episode in season.episodes():
                    rows.append(
                        self._payload(
                            episode,
                            "episode",
                            parent_rating_key=season_key,
                            grandparent_rating_key=show_key,
                            parent_title=season.title,
                            grandparent_title=show_title,
                            season_number=getattr(episode, "parentIndex", None) or season_number,
                            episode_number=getattr(episode, "index", None),
                        )
                    )
        return rows

    def iter_movies(self, library_key: str) -> list[dict[str, Any]]:
        """Backward-compatible alias for existing integrations/tests."""
        return self.iter_items(library_key, "movie")

    def artwork_response(self, plex_path: str) -> httpx.Response:
        return httpx.get(
            urljoin(f"{self.base_url}/", plex_path.lstrip("/")),
            headers=self._headers(),
            timeout=30,
            follow_redirects=True,
        )

    def get_direct_play_url(self, part_key: str) -> str:
        return urljoin(f"{self.base_url}/", part_key.lstrip("/"))

    def get_transcode_url(
        self,
        rating_key: str,
        max_video_bitrate: int = 12000,
        video_resolution: str = "1920x1080",
    ) -> str:
        params = {
            "path": f"/library/metadata/{rating_key}",
            "mediaIndex": 0,
            "partIndex": 0,
            "protocol": "hls",
            "offset": 0,
            "fastSeek": 1,
            "directPlay": 0,
            "directStream": 1,
            "videoQuality": 100,
            "videoResolution": video_resolution,
            "maxVideoBitrate": max_video_bitrate,
            "audioBoost": 100,
            "X-Plex-Client-Identifier": "plumbus-server",
            "X-Plex-Product": "Plumbus",
            "X-Plex-Version": "1.0.0",
            "X-Plex-Token": self.token,
        }
        return f"{self.base_url}/video/:/transcode/universal/start.m3u8?{urlencode(params)}"

    def stream_url(self, part_key: str) -> str:
        return self.get_direct_play_url(part_key)