from __future__ import annotations

from typing import Any

from plexapi.server import PlexServer as PlexApiServer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Movie, MovieMedia
from app.services.plex.service import PlexService

BROWSER_COPY_AUDIO_CODECS = {"aac", "mp3"}
BROWSER_COPY_VIDEO_CODECS = {"h264", "avc"}


def _track_row(stream: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(stream, "id", "") or ""),
        "codec": str(getattr(stream, "codec", "") or "").lower(),
        "language": getattr(stream, "language", None),
        "language_code": getattr(stream, "languageCode", None),
        "language_tag": getattr(stream, "languageTag", None),
        "channels": getattr(stream, "channels", None),
        "selected": bool(getattr(stream, "selected", False)),
        "default": bool(getattr(stream, "default", False)),
        "title": getattr(stream, "title", None),
        "display_title": getattr(stream, "displayTitle", None),
        "extended_display_title": getattr(stream, "extendedDisplayTitle", None),
    }


def fetch_audio_tracks(
    plex: PlexService,
    rating_key: str,
    *,
    part_key: str | None = None,
    plex_media_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch every audio stream for the exact indexed Plex media part.

    This is deliberately read-only. We do not call Plex's setSelectedAudioStream,
    because that mutates server-side selection state and can affect other clients.
    """
    if settings.MOCK_PLEX:
        return [
            {
                "id": "1",
                "codec": "aac",
                "language": "English",
                "language_code": "eng",
                "language_tag": "en",
                "channels": 2,
                "selected": True,
                "default": True,
                "title": None,
                "display_title": "English (AAC Stereo)",
                "extended_display_title": "English (AAC Stereo)",
            }
        ]

    server = PlexApiServer(plex.base_url, plex.token, timeout=20)
    key: int | str = int(rating_key) if str(rating_key).isdigit() else f"/library/metadata/{rating_key}"
    item = server.fetchItem(key)

    fallback: list[dict[str, Any]] = []
    for media in getattr(item, "media", []) or []:
        media_id = str(getattr(media, "id", "") or "")
        if plex_media_id and media_id and media_id != str(plex_media_id):
            continue
        for part in getattr(media, "parts", []) or []:
            current_part_key = str(getattr(part, "key", "") or "")
            try:
                streams = list(part.audioStreams())
            except Exception:
                streams = [
                    stream
                    for stream in (getattr(part, "streams", []) or [])
                    if int(getattr(stream, "streamType", 0) or 0) == 2
                ]
            rows = [_track_row(stream) for stream in streams]
            rows = [row for row in rows if row["id"] or row["codec"]]
            if rows and not fallback:
                fallback = rows
            if part_key and current_part_key == str(part_key):
                return rows
            if not part_key:
                return rows
    return fallback


def _track_language(track: dict[str, Any]) -> str:
    return str(
        track.get("language_code")
        or track.get("language_tag")
        or track.get("language")
        or ""
    ).strip().casefold()


def _looks_like_commentary(track: dict[str, Any]) -> bool:
    text = " ".join(
        str(track.get(key) or "")
        for key in ("title", "display_title", "extended_display_title")
    ).casefold()
    return any(word in text for word in ("commentary", "description", "descriptive", "audio description"))


def primary_audio_track(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tracks:
        return None
    return next((track for track in tracks if track.get("selected")), None) or next(
        (track for track in tracks if track.get("default")),
        None,
    ) or tracks[0]


def choose_direct_browser_audio_track(
    tracks: list[dict[str, Any]],
    *,
    video_codec: str | None,
) -> dict[str, Any] | None:
    """Pick an existing browser-safe audio track without encoding it.

    We only auto-remux when the video itself is H.264/AVC. The selected/default
    track's language is preserved whenever possible so an English DTS primary
    track does not silently switch the user to a Japanese AAC dub, for example.
    """
    if str(video_codec or "").lower() not in BROWSER_COPY_VIDEO_CODECS:
        return None

    primary = primary_audio_track(tracks)
    primary_language = _track_language(primary or {})
    primary_commentary = _looks_like_commentary(primary or {})
    candidates = [
        track
        for track in tracks
        if str(track.get("codec") or "").lower() in BROWSER_COPY_AUDIO_CODECS
        and str(track.get("id") or "")
    ]
    if not candidates:
        return None

    def score(track: dict[str, Any]) -> tuple[int, int, int, int, int]:
        language = _track_language(track)
        same_language = int(bool(primary_language) and language == primary_language)
        commentary_match = int(_looks_like_commentary(track) == primary_commentary)
        selected = int(bool(track.get("selected")))
        default = int(bool(track.get("default")))
        stereo = int((track.get("channels") or 99) <= 2)
        return (same_language, commentary_match, selected, default, stereo)

    return max(candidates, key=score)


def cached_audio_tracks(movie: Movie, media: MovieMedia) -> list[dict[str, Any]]:
    metadata = movie.plex_metadata or {}
    mapping = metadata.get("audio_tracks") if isinstance(metadata, dict) else None
    if not isinstance(mapping, dict) or not media.part_key:
        return []
    tracks = mapping.get(media.part_key)
    return tracks if isinstance(tracks, list) else []


def ensure_audio_tracks(
    db: Session,
    movie: Movie,
    media: MovieMedia,
    plex: PlexService,
) -> list[dict[str, Any]]:
    """Return cached tracks, lazily filling old scan rows from Plex when needed."""
    tracks = cached_audio_tracks(movie, media)
    if tracks:
        return tracks

    try:
        tracks = fetch_audio_tracks(
            plex,
            movie.rating_key,
            part_key=media.part_key,
            plex_media_id=media.plex_media_id,
        )
    except Exception:
        return []
    if not tracks or not media.part_key:
        return tracks

    metadata = dict(movie.plex_metadata or {})
    mapping = dict(metadata.get("audio_tracks") or {})
    mapping[media.part_key] = tracks
    metadata["audio_tracks"] = mapping
    movie.plex_metadata = metadata
    db.add(movie)
    db.commit()
    return tracks
