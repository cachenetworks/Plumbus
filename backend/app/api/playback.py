import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Movie, MovieMedia, PlaybackHistory, User, UserStatus
from app.security.secrets import decrypt_secret, encrypt_secret
from app.security.security import get_current_user
from app.services.configuration import IntegrationConfigurationService
from app.services.playback.service import PlaybackService
from app.services.plex.service import PlexService
from app.services.settings import ApplicationSettingsService

router = APIRouter(tags=["playback"])
URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"')
PlaybackTarget = Literal["browser", "vrchat"]
BrowserMode = Literal["direct", "compatibility"]

# Direct media can issue many Range requests while seeking/buffering. Keep the
# Plex connection pool warm instead of creating a fresh TCP/TLS client for each
# request. The original media bytes are never transcoded on this path.
PLEX_DIRECT_CLIENT = httpx.Client(
    timeout=None,
    follow_redirects=True,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=32, keepalive_expiry=120),
)


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
    if candidate.scheme not in {"http", "https"} or candidate.hostname != expected.hostname or candidate_port != expected_port:
        raise HTTPException(400, "Invalid Plex transcode resource")


def _plex_media_headers(plex: PlexService) -> dict[str, str]:
    headers = plex._headers()
    headers.pop("Accept", None)
    return headers


def _hls_proxy_url(public_base_url: str, raw_token: str, upstream_url: str) -> str:
    return f"{public_base_url}/stream/{raw_token}/hls/{encrypt_secret(upstream_url)}"


def _rewrite_playlist(text: str, base_url: str, raw_token: str, public_base_url: str) -> str:
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            output.append(_hls_proxy_url(public_base_url, raw_token, urljoin(base_url, stripped)))
            continue

        def replace(match: re.Match[str]) -> str:
            return f'URI="{_hls_proxy_url(public_base_url, raw_token, urljoin(base_url, match.group(1)))}"'

        output.append(URI_ATTRIBUTE_RE.sub(replace, line))
    return "\n".join(output) + "\n"


def _movie_plex(db: Session, movie: Movie) -> PlexService:
    plex = PlexService.for_movie(db, movie)
    if not plex.base_url or not plex.token:
        raise HTTPException(503, "The Plex server for this media item is not configured")
    return plex


def _delivery_for_target(
    target: PlaybackTarget,
    media_info: dict,
    browser_mode: BrowserMode = "direct",
) -> str:
    if target == "browser":
        # Browser playback now prefers the original Plex media file. This avoids
        # PMS transcoder startup, HLS segment generation and encode bottlenecks.
        # Compatibility mode is only requested after the browser proves it cannot
        # decode the original source.
        if browser_mode == "direct":
            return "progressive"
        if media_info["allow_plex_transcoding"]:
            return "hls"
        raise HTTPException(
            409,
            "The browser cannot decode the direct Plex source and compatibility transcoding is disabled.",
        )
    if media_info["direct_play_candidate"]:
        return "progressive"
    if media_info["allow_plex_transcoding"]:
        return "hls"
    raise HTTPException(409, "This file needs Plex transcoding for VRChat, but Plex transcoding is disabled.")


def _build_playback(
    movie_id: int,
    target: PlaybackTarget,
    request: Request,
    user: User,
    db: Session,
    browser_mode: BrowserMode = "direct",
) -> dict:
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Media item not found")
    if movie.media_type not in {"movie", "episode"}:
        raise HTTPException(409, "Only movies and episodes can be played")

    plex = _movie_plex(db, movie)
    connection = plex.connect()
    if not connection.connected:
        raise HTTPException(503, f"Plex server is unreachable: {connection.error or 'connection failed'}")

    playback_service = PlaybackService(db)
    token, raw = playback_service.create_token(
        user,
        movie,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        target=target,
    )
    media = db.get(MovieMedia, token.movie_media_id)
    if media is None:
        playback_service.revoke(token)
        raise HTTPException(410, "Indexed media is no longer available")

    media_info = playback_service.get_media_info(media)
    try:
        delivery = _delivery_for_target(target, media_info, browser_mode=browser_mode)
    except HTTPException:
        playback_service.revoke(token)
        raise

    history = db.scalar(
        select(PlaybackHistory).where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.movie_id == movie.id,
        )
    )
    resume_position_ms = int(history.last_position_ms or 0) if history and not history.completed else 0
    if history is None:
        history = PlaybackHistory(user_id=user.id, movie_id=movie.id)
        db.add(history)
    history.playback_started_at = datetime.now(UTC)
    history.last_watched_at = datetime.now(UTC)
    history.completed = False

    public_base_url = IntegrationConfigurationService(db).site().app_url.rstrip("/")
    playback_url = (
        f"{public_base_url}/stream/{raw}/master.m3u8"
        if delivery == "hls"
        else f"{public_base_url}/stream/{raw}"
    )
    event_metadata = {
        "target": target,
        "delivery": delivery,
        "playback_mode": media_info["playback_mode"],
        "plex_server_id": plex.server_id,
    }
    if target == "browser":
        event_metadata["browser_mode"] = browser_mode
    db.add(
        AuditLog(
            actor_user_id=user.id,
            event=f"playback.{target}.created",
            target_type=movie.media_type,
            target_id=str(movie.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata=event_metadata,
        )
    )
    db.commit()
    return {
        "media_id": movie.id,
        "target": target,
        "delivery": delivery,
        "playback_url": playback_url,
        "expires_at": token.expires_at,
        "resume_position_ms": resume_position_ms,
        "media": media_info,
        "plex_server_id": plex.server_id,
        "browser_mode": browser_mode if target == "browser" else None,
    }


@router.post("/api/playback/movies/{movie_id}")
def create_playback(
    movie_id: int,
    request: Request,
    target: PlaybackTarget = Query(default="browser"),
    browser_mode: BrowserMode = Query(default="direct"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _build_playback(movie_id, target, request, user, db, browser_mode=browser_mode)


@router.post("/api/playback/media/{media_id}/browser")
def browser_playback(
    media_id: int,
    request: Request,
    mode: BrowserMode = Query(default="direct"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _build_playback(media_id, "browser", request, user, db, browser_mode=mode)


@router.post("/api/playback/media/{media_id}/vrchat")
def vrchat_playback(
    media_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    result = _build_playback(media_id, "vrchat", request, user, db)
    result["vrchat_url"] = result["playback_url"]
    result["compatibility"] = (
        "Direct progressive route with HTTP Range support. Paste this URL into an AVPro/Udon video player and allow untrusted URLs in VRChat."
        if result["delivery"] == "progressive"
        else "HLS route generated through Plex transcoding. Paste this URL into an AVPro/Udon video player and allow untrusted URLs in VRChat."
    )
    return result


@router.get("/api/playback/media/{media_id}/navigation")
def episode_navigation(
    media_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    current = db.get(Movie, media_id)
    if not current:
        raise HTTPException(404, "Media item not found")
    if current.media_type != "episode" or not current.grandparent_rating_key:
        return {"previous": None, "next": None, "series_title": current.grandparent_title}
    episodes = db.scalars(
        select(Movie).where(
            Movie.library_id == current.library_id,
            Movie.media_type == "episode",
            Movie.grandparent_rating_key == current.grandparent_rating_key,
        )
    ).all()
    episodes = sorted(
        episodes,
        key=lambda item: (
            item.season_number if item.season_number is not None else 999999,
            item.episode_number if item.episode_number is not None else 999999,
            item.id,
        ),
    )
    index = next((i for i, item in enumerate(episodes) if item.id == current.id), -1)

    def summary(item: Movie | None):
        return (
            None
            if item is None
            else {
                "id": item.id,
                "title": item.title,
                "season_number": item.season_number,
                "episode_number": item.episode_number,
            }
        )

    previous = episodes[index - 1] if index > 0 else None
    next_item = episodes[index + 1] if index >= 0 and index + 1 < len(episodes) else None
    return {
        "previous": summary(previous),
        "next": summary(next_item),
        "series_title": current.grandparent_title,
    }


@router.get("/stream/{raw_token}")
def stream(raw_token: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    _token, _user, movie, media = _active_playback(raw_token, db)
    plex = _movie_plex(db, movie)
    headers = _plex_media_headers(plex)
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]

    upstream = PLEX_DIRECT_CLIENT.send(
        PLEX_DIRECT_CLIENT.build_request("GET", plex.stream_url(media.part_key), headers=headers),
        stream=True,
    )
    if upstream.status_code >= 400:
        status = upstream.status_code
        upstream.close()
        raise HTTPException(502, f"Plex stream returned HTTP {status}")

    allowed = {
        "content-type": "Content-Type",
        "content-length": "Content-Length",
        "content-range": "Content-Range",
        "accept-ranges": "Accept-Ranges",
        "etag": "ETag",
        "last-modified": "Last-Modified",
    }
    response_headers = {
        out: upstream.headers[src]
        for src, out in allowed.items()
        if src in upstream.headers
    }
    response_headers.setdefault("Accept-Ranges", "bytes")
    response_headers["Cache-Control"] = "private, no-store, no-transform"
    response_headers["X-Accel-Buffering"] = "no"

    def body() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_bytes(chunk_size=4 * 1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )


@router.get("/stream/{raw_token}/master.m3u8")
def transcode_master(raw_token: str, db: Session = Depends(get_db)) -> Response:
    _token, _user, movie, media = _active_playback(raw_token, db)
    media_info = PlaybackService(db).get_media_info(media)
    if not media_info["allow_plex_transcoding"]:
        raise HTTPException(409, "Plex transcoding is disabled")
    prefs = ApplicationSettingsService(db).playback()
    resolution = {
        "720p": "1280x720",
        "1080p": "1920x1080",
        "1440p": "2560x1440",
        "4k": "3840x2160",
        "2160p": "3840x2160",
    }.get(str(prefs["preferred_resolution"]).lower(), "1920x1080")
    plex = _movie_plex(db, movie)
    upstream_url = plex.get_transcode_url(
        movie.rating_key,
        max_video_bitrate=int(prefs["max_stream_bitrate_kbps"]),
        video_resolution=resolution,
    )
    _assert_plex_url(plex, upstream_url)
    response = httpx.get(
        upstream_url,
        headers=_plex_media_headers(plex),
        timeout=30,
        follow_redirects=True,
    )
    if response.status_code >= 400:
        raise HTTPException(502, f"Plex transcoder returned HTTP {response.status_code}")
    _assert_plex_url(plex, str(response.url))
    rewritten = _rewrite_playlist(
        response.text,
        str(response.url),
        raw_token,
        IntegrationConfigurationService(db).site().app_url,
    )
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
    _token, _user, movie, _media = _active_playback(raw_token, db)
    plex = _movie_plex(db, movie)
    try:
        upstream_url = decrypt_secret(opaque)
    except RuntimeError as exc:
        raise HTTPException(400, "Invalid transcode resource") from exc
    _assert_plex_url(plex, upstream_url)
    headers = _plex_media_headers(plex)
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    client = httpx.Client(timeout=None, follow_redirects=True)
    upstream = client.send(
        client.build_request("GET", upstream_url, headers=headers),
        stream=True,
    )
    if upstream.status_code >= 400:
        status = upstream.status_code
        upstream.close()
        client.close()
        raise HTTPException(502, f"Plex transcode resource returned HTTP {status}")
    _assert_plex_url(plex, str(upstream.url))
    content_type = upstream.headers.get("content-type", "application/octet-stream")
    if "mpegurl" in content_type.lower() or str(upstream.url).lower().split("?", 1)[0].endswith(".m3u8"):
        try:
            data = b"".join(upstream.iter_bytes()).decode("utf-8")
        finally:
            upstream.close()
            client.close()
        rewritten = _rewrite_playlist(
            data,
            str(upstream.url),
            raw_token,
            IntegrationConfigurationService(db).site().app_url,
        )
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
