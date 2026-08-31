import re
from collections.abc import Iterator
from typing import Literal
from urllib.parse import urlencode, urljoin, urlparse
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.playback import (
    _active_playback,
    _assert_plex_url,
    _build_playback,
    _movie_plex,
    _plex_media_headers,
)
from app.db.database import get_db
from app.models.models import User
from app.security.secrets import decrypt_secret, encrypt_secret
from app.security.security import get_current_user
from app.services.plex.service import PlexService
from app.services.settings import ApplicationSettingsService

router = APIRouter(tags=["browser-playback"])
URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"')
BrowserMode = Literal["direct", "compatibility"]

# HLS is only used as a compatibility fallback now. Keep the pool warm when it
# is needed so fallback playback does not reconnect for every segment.
PLEX_STREAM_CLIENT = httpx.Client(
    timeout=None,
    follow_redirects=True,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=32, keepalive_expiry=90),
)


def _browser_proxy_path(raw_token: str, upstream_url: str) -> str:
    return f"/stream/{raw_token}/hls/{encrypt_secret(upstream_url)}"


def _rewrite_browser_playlist(text: str, base_url: str, raw_token: str) -> str:
    output: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            output.append(_browser_proxy_path(raw_token, urljoin(base_url, stripped)))
            continue

        def replace(match: re.Match[str]) -> str:
            return f'URI="{_browser_proxy_path(raw_token, urljoin(base_url, match.group(1)))}"'

        output.append(URI_ATTRIBUTE_RE.sub(replace, line))
    return "\n".join(output) + "\n"


def _browser_bitrate(prefs: dict) -> int:
    configured = int(prefs["max_stream_bitrate_kbps"])
    preferred = str(prefs["preferred_resolution"]).lower()
    ceiling = {
        "720p": 4500,
        "1080p": 8000,
        "1440p": 10000,
        "4k": 12000,
        "2160p": 12000,
    }.get(preferred, 8000)
    return min(configured, ceiling)


def _build_browser_transcode_url(
    plex: PlexService,
    rating_key: str,
    max_video_bitrate: int,
    video_resolution: str,
) -> str:
    session = uuid4().hex
    profile_extra = (
        "add-transcode-target(type=videoProfile&context=streaming&protocol=hls"
        "&container=mpegts&videoCodec=h264&audioCodec=aac&replace=true)"
        "+add-limitation(scope=videoAudioCodec&scopeName=aac&type=upperBound"
        "&name=audio.channels&value=2)"
    )
    params = {
        "path": f"/library/metadata/{rating_key}",
        "mediaIndex": 0,
        "partIndex": 0,
        "protocol": "hls",
        "hasMDE": 1,
        "offset": 0,
        "fastSeek": 1,
        "directPlay": 0,
        "directStream": 0,
        "directStreamAudio": 0,
        "copyts": 1,
        "videoQuality": 80,
        "videoResolution": video_resolution,
        "maxVideoBitrate": max_video_bitrate,
        "audioBoost": 100,
        "subtitles": "none",
        "mediaBufferSize": 204800,
        "session": session,
        "X-Plex-Session-Identifier": session,
        "X-Plex-Client-Identifier": "plumbus-web",
        "X-Plex-Product": "Plumbus",
        "X-Plex-Version": "1.0.0",
        "X-Plex-Platform": "Chrome",
        "X-Plex-Client-Platform": "Chrome",
        "X-Plex-Client-Profile-Name": "Chrome",
        "X-Plex-Client-Profile-Extra": profile_extra,
        "X-Plex-Token": plex.token,
    }
    return f"{plex.base_url}/video/:/transcode/universal/start.m3u8?{urlencode(params)}"


def _resolution(prefs: dict) -> str:
    return {
        "720p": "1280x720",
        "1080p": "1920x1080",
        "1440p": "2560x1440",
        "4k": "3840x2160",
        "2160p": "3840x2160",
    }.get(str(prefs["preferred_resolution"]).lower(), "1920x1080")


@router.post("/api/playback/media/{media_id}/browser")
def browser_playback(
    media_id: int,
    request: Request,
    mode: BrowserMode = Query(default="direct"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    result = _build_playback(
        media_id,
        "browser",
        request,
        user,
        db,
        browser_mode=mode,
    )
    parsed = urlparse(result["playback_url"])
    relative = parsed.path
    if parsed.query:
        relative += f"?{parsed.query}"
    result["playback_url"] = relative
    result["browser_codec_profile"] = (
        "ORIGINAL_PLEX_SOURCE" if mode == "direct" else "H.264/AAC"
    )
    result["stream_mode"] = mode
    return result


@router.get("/stream/{raw_token}/master.m3u8")
def browser_transcode_master(raw_token: str, db: Session = Depends(get_db)) -> Response:
    _token, _user, movie, _media = _active_playback(raw_token, db)
    prefs = ApplicationSettingsService(db).playback()
    if not bool(prefs["allow_plex_transcoding"]):
        raise HTTPException(409, "Plex transcoding is disabled")

    plex = _movie_plex(db, movie)
    upstream_url = _build_browser_transcode_url(
        plex,
        movie.rating_key,
        max_video_bitrate=_browser_bitrate(prefs),
        video_resolution=_resolution(prefs),
    )
    _assert_plex_url(plex, upstream_url)

    try:
        response = PLEX_STREAM_CLIENT.get(
            upstream_url,
            headers=_plex_media_headers(plex),
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Unable to start Plex web transcode: {type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise HTTPException(502, f"Plex web transcoder returned HTTP {response.status_code}")
    _assert_plex_url(plex, str(response.url))
    if "#EXTM3U" not in response.text:
        raise HTTPException(502, "Plex did not return a valid HLS manifest")

    rewritten = _rewrite_browser_playlist(response.text, str(response.url), raw_token)
    return Response(
        rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "private, no-store",
            "X-Plumbus-Video-Codec": "h264",
            "X-Plumbus-Audio-Codec": "aac",
            "X-Plumbus-Max-Bitrate-Kbps": str(_browser_bitrate(prefs)),
        },
    )


@router.get("/stream/{raw_token}/hls/{opaque}")
def browser_transcode_resource(
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

    upstream = PLEX_STREAM_CLIENT.send(
        PLEX_STREAM_CLIENT.build_request("GET", upstream_url, headers=headers),
        stream=True,
    )
    if upstream.status_code >= 400:
        status = upstream.status_code
        upstream.close()
        raise HTTPException(502, f"Plex transcode resource returned HTTP {status}")
    _assert_plex_url(plex, str(upstream.url))

    content_type = upstream.headers.get("content-type", "application/octet-stream")
    is_playlist = (
        "mpegurl" in content_type.lower()
        or str(upstream.url).lower().split("?", 1)[0].endswith(".m3u8")
    )
    if is_playlist:
        try:
            data = b"".join(upstream.iter_bytes()).decode("utf-8")
        finally:
            upstream.close()
        rewritten = _rewrite_browser_playlist(data, str(upstream.url), raw_token)
        return Response(
            rewritten,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "private, max-age=1"},
        )

    response_headers = {"Cache-Control": "private, max-age=3600"}
    for source, target in (
        ("content-length", "Content-Length"),
        ("content-range", "Content-Range"),
        ("accept-ranges", "Accept-Ranges"),
        ("etag", "ETag"),
        ("last-modified", "Last-Modified"),
    ):
        if source in upstream.headers:
            response_headers[target] = upstream.headers[source]

    def body() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_bytes(chunk_size=2 * 1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=content_type,
    )
