from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from plexapi.server import PlexServer as PlexApiServer

from app.core.config import settings


@dataclass(slots=True)
class PlexConnectionInfo:
    connected: bool
    name: str | None = None
    version: str | None = None
    machine_identifier: str | None = None
    libraries: list[dict[str, Any]] | None = None


class PlexService:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or settings.PLEX_URL).rstrip("/")
        self.token = token or settings.PLEX_TOKEN

    def _headers(self) -> dict[str, str]:
        return {
            "X-Plex-Token": self.token,
            "X-Plex-Client-Identifier": "plumbus-server",
            "X-Plex-Product": "Plumbus",
            "X-Plex-Version": "0.1.0",
            "Accept": "application/json",
        }

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
            return PlexConnectionInfo(connected=False, libraries=[])
        try:
            server = PlexApiServer(self.base_url, self.token, timeout=10)
            libraries = [
                {"key": str(section.key), "title": section.title, "type": section.type}
                for section in server.library.sections()
            ]
            return PlexConnectionInfo(
                connected=True,
                name=server.friendlyName,
                version=server.version,
                machine_identifier=server.machineIdentifier,
                libraries=libraries,
            )
        except Exception:
            return PlexConnectionInfo(connected=False, libraries=[])

    def get_libraries(self) -> list[dict[str, Any]]:
        if settings.MOCK_PLEX:
            return [
                {"key": "1", "title": "Movies", "type": "movie"},
                {"key": "2", "title": "4K Movies", "type": "movie"},
            ]
        return self.connect().libraries or []

    def iter_movies(self, library_key: str) -> list[dict[str, Any]]:
        if settings.MOCK_PLEX:
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

        server = PlexApiServer(self.base_url, self.token, timeout=30)
        section = server.library.sectionByID(int(library_key))
        movies: list[dict[str, Any]] = []
        for item in section.all():
            if getattr(item, "TYPE", None) != "movie" and getattr(item, "type", None) != "movie":
                continue
            media_rows: list[dict[str, Any]] = []
            for media in getattr(item, "media", []) or []:
                for part in getattr(media, "parts", []) or []:
                    hdr = None
                    if getattr(media, "videoDynamicRange", None):
                        hdr = str(media.videoDynamicRange)
                    media_rows.append(
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
                            "hdr": hdr,
                            "audio_channels": getattr(media, "audioChannels", None),
                            "file_size": getattr(part, "size", None),
                        }
                    )
            movies.append(
                {
                    "rating_key": str(item.ratingKey),
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
                    "media": media_rows,
                }
            )
        return movies

    def artwork_response(self, plex_path: str) -> httpx.Response:
        return httpx.get(
            urljoin(f"{self.base_url}/", plex_path.lstrip("/")),
            headers=self._headers(),
            timeout=30,
        )

    def stream_url(self, part_key: str) -> str:
        return urljoin(f"{self.base_url}/", part_key.lstrip("/"))

    def request_stream(self, url: str, range_header: str | None = None) -> httpx.Response:
        headers = self._headers()
        headers.pop("Accept", None)
        if range_header:
            headers["Range"] = range_header
        client = httpx.Client(timeout=None, follow_redirects=True)
        request = client.build_request("GET", url, headers=headers)
        return client.send(request, stream=True)
