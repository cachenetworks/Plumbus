from collections.abc import Iterator
from datetime import UTC, datetime
import re
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import AuditLog, Movie, MovieMedia, PlaybackHistory, User, UserStatus
from app.security.secrets import decrypt_secret, encrypt_secret
from app.security.security import get_current_user
from app.services.playback.service import PlaybackService
from app.services.plex.service import PlexService
from app.services.settings import ApplicationSettingsService

router = APIRouter(tags=["playback"])
URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"')


def _active_playback(raw_token: str, db: Session):
    token = PlaybackService(db).validate_token(raw_token)
    user = db.get(User, token.user_id)
    if not user or user.status != UserStatus.ACTIVE:
        raise HTTPException(403, "Playback owner is not active")
    movie = db.get(Movie, token.movie_id)
    media = db.get(MovieMedia, token.movie_media_id) if token.movie_media_id else None
    if not movie or not media or not media.part_key:
        raise HTTPException(410, "Indexed media is no longer available")
    return token, user, movie, media


def _assert_plex_url(plex: PlexService, url: str) -> None:
    expected = urlparse(plex.base_url)
    candidate = urlparse(url)
    expected_port = expected.port or (443 if expected.scheme == "https" else 80)
    candidate_port = candidate.port or (443 if candidate.scheme == "https" else 80)
    if (
        candidate.scheme not in {"http", "https"}
        or candidate.hostname != expected.hostname
        or candidate_port != expected_port
    ):
        raise HTTPException(400, "Invalid Plex transcode resource")


def _hls_proxy_url(raw_token: str, upstream_url: str) -> str:
    opaque = encrypt_secret(upstream_url)
    return f"{settings.APP_URL}/stream/{raw_token}/hls/{opaque}"


def _rewrite_playlist(text: str, base_url: str, raw_token: str) -> str:
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            absolute = urljoin(base_url, stripped)
            output.append(_hls_proxy_url(raw_token, absolute))
            continue

        def replace(match: re.Match[str]) -> str:
            absolute = urljoin(base_url, match.group(1))
            return f'URI="{_hls_proxy_url(raw_token, absolute)}"'

        output.append(URI_ATTRIBUTE_RE.sub(replace, line))
    return "\n".join(output) + "\n"


@router.post("/api/playback/movies/{movie_id}")
def create_playback(
    movie_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")

    playback_service = PlaybackService(db)
    token, raw = playback_service.create_token(
        user,
        movie,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    media = db.get(MovieMedia, token.movie_media_id)
    media_info = playback_service.get_media_info(media)
    if media_info["playback_mode"] == "Transcode Required" and not media_info["allow_plex_transcoding"]:
        playback_service.revoke(token)
        raise HTTPException(409, "This media requires transcoding, but Plex transcoding is disabled")

    history = db.scalar(
        select(PlaybackHistory).where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.movie_id == movie.id,
        )
    )
    if history is None:
        history = PlaybackHistory(user_id=user.id, movie_id=movie.id)
        db.add(history)
    history.playback_started_at = datetime.now(UTC)
    history.last_watched_at = datetime.now(UTC)
    history.completed = False

    transcode = media_info["playback_mode"] == "Transcode Required"
    playback_url = (
        f"{settings.APP_URL}/stream/{raw}/master.m3u8"
        if transcode
        else f"{settings.APP_URL}/stream/{raw}"
    )
    db.add(
        AuditLog(
            actor_user_id=user.id,
            event="playback.created",
            target_type="movie",
            target_id=str(movie.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={"playback_mode": media_info["playback_mode"]},
        )
    )
    db.commit()
    return {
        "playback_url": playback_url,
        "expires_at": token.expires_at,
        "media": media_info,
    }


@router.get("/stream/{raw_token}")
def stream(raw_token: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    _token, _user, _movie, media = _active_playback(raw_token, db)
    plex = PlexService.from_db(db)
    if not plex.base_url or not plex.token:
        raise HTTPException(503, "Plex is not configured")
    upstream_url = plex.stream_url(media.part_key)
    headers = plex._headers()
    headers.pop("Accept", None)
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.Client(timeout=None, follow_redirects=True)
    upstream_request = client.build_request("GET", upstream_url, headers=headers)
    upstream = client.send(upstream_request, stream=True)
    if upstream.status_code >= 400:
        upstream.close()
        client.close()
        raise HTTPException(502, f"Plex stream returned HTTP {upstream.status_code}")

    allowed_headers = {
        "content-type": "Content-Type",
        "content-length": "Content-Length",
        "content-range": "Content-Range",
        "accept-ranges": "Accept-Ranges",
        "etag": "ETag",
        "last-modified": "Last-Modified",
    }
    response_headers = {
        out_name: upstream.headers[in_name]
        for in_name, out_name in allowed_headers.items()
        if in_name in upstream.headers
    }
    response_headers.setdefault("Accept-Ranges", "bytes")
    response_headers["Cache-Control"] = "private, no-store"

    def body() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_bytes(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            client.close()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )


@router.get("/stream/{raw_token}/master.m3u8")
def transcode_master(raw_token: str, db: Session = Depends(get_db)) -> Response:
    token, _user, movie, media = _active_playback(raw_token, db)
    media_info = PlaybackService(db).get_media_info(media)
    if not media_info["allow_plex_transcoding"]:
        raise HTTPException(409, "Plex transcoding is disabled")

    prefs = ApplicationSettingsService(db).playback()
    resolution_map = {
        "720p": "1280x720",
        "1080p": "1920x1080",
        "1440p": "2560x1440",
        "4k": "3840x2160",
        "2160p": "3840x2160",
    }
    resolution = resolution_map.get(str(prefs["preferred_resolution"]).lower(), "1920x1080")
    plex = PlexService.from_db(db)
    upstream_url = plex.get_transcode_url(
        movie.rating_key,
        max_video_bitrate=int(prefs["max_stream_bitrate_kbps"]),
        video_resolution=resolution,
    )
    _assert_plex_url(plex, upstream_url)
    response = httpx.get(upstream_url, headers=plex._headers(), timeout=30, follow_redirects=True)
    if response.status_code >= 400:
        raise HTTPException(502, f"Plex transcoder returned HTTP {response.status_code}")
    _assert_plex_url(plex, str(response.url))
    rewritten = _rewrite_playlist(response.text, str(response.url), raw_token)
    return Response(
        rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/stream/{raw_token}/hls/{opaque}")
def transcode_resource(
    raw_token: str,
    opaque: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _active_playback(raw_token, db)
    plex = PlexService.from_db(db)
    try:
        upstream_url = decrypt_secret(opaque)
    except RuntimeError as exc:
        raise HTTPException(400, "Invalid transcode resource") from exc
    _assert_plex_url(plex, upstream_url)

    headers = plex._headers()
    headers.pop("Accept", None)
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]

    client = httpx.Client(timeout=None, follow_redirects=True)
    upstream_request = client.build_request("GET", upstream_url, headers=headers)
    upstream = client.send(upstream_request, stream=True)
    if upstream.status_code >= 400:
        upstream.close()
        client.close()
        raise HTTPException(502, f"Plex transcode resource returned HTTP {upstream.status_code}")
    _assert_plex_url(plex, str(upstream.url))

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    if "mpegurl" in content_type.lower() or str(upstream.url).lower().split("?", 1)[0].endswith(".m3u8"):
        try:
            data = b"".join(upstream.iter_bytes()).decode("utf-8")
        finally:
            upstream.close()
            client.close()
        rewritten = _rewrite_playlist(data, str(upstream.url), raw_token)
        return Response(
            rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "private, no-store"},
        )

    response_headers = {"Cache-Control": "private, no-store"}
    for source, target in (
        ("content-length", "Content-Length"),
        ("content-range", "Content-Range"),
        ("accept-ranges", "Accept-Ranges"),
    ):
        if source in upstream.headers:
            response_headers[target] = upstream.headers[source]

    def body() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_bytes(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            client.close()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=content_type,
    )
