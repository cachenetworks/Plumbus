import re
from collections.abc import Iterator
from urllib.parse import urlencode, urljoin, urlparse
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
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


def _browser_proxy_path(raw_token: str, upstream_url: str) -> str:
    """Return a same-origin HLS resource path.

    Relative paths intentionally avoid APP_URL/CORS/mixed-content failures in the
    browser. They also remain valid inside an absolute VRChat HLS master URL,
    because AVPro resolves relative playlist resources against the master URL.
    """
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


def _build_browser_transcode_url(
    plex: PlexService,
    rating_key: str,
    max_video_bitrate: int,
    video_resolution: str,
) -> str:
    """Build a Plex HLS session that is deliberately browser-safe.

    Do not let PMS direct-stream the original HEVC/DTS/EAC3/etc. tracks into an
    HLS container. The web player asks Plex for H.264 video + AAC audio instead.
    """
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
        "videoQuality": 100,
        "videoResolution": video_resolution,
        "maxVideoBitrate": max_video_bitrate,
        "audioBoost": 100,
        "subtitles": "none",
        "mediaBufferSize": 102400,
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    result = _build_playback(media_id, "browser", request, user, db)

    # Browser playback must always use the current page origin. APP_URL is still
    # used for externally shareable VRChat links, but it should never be able to
    # break HLS inside the logged-in website.
    parsed = urlparse(result["playback_url"])
    relative = parsed.path
    if parsed.query:
        relative += f"?{parsed.query}"
    result["playback_url"] = relative
    result["browser_codec_profile"] = "H.264/AAC" if result["delivery"] == "hls" else "native"
    return result


@router.get("/stream/{raw_token}/master.m3u8")
def browser_transcode_master(raw_token: str, db: Session = Depends(get_db)) -> Response:
    _token, _user, movie, media = _active_playback(raw_token, db)
    media_info = ApplicationSettingsService(db).playback()
    if not bool(media_info["allow_plex_transcoding"]):
        raise HTTPException(409, "Plex transcoding is disabled")

    plex = _movie_plex(db, movie)
    upstream_url = _build_browser_transcode_url(
        plex,
        movie.rating_key,
        max_video_bitrate=int(media_info["max_stream_bitrate_kbps"]),
        video_resolution=_resolution(media_info),
    )
    _assert_plex_url(plex, upstream_url)

    try:
        response = httpx.get(
            upstream_url,
            headers=_plex_media_headers(plex),
            timeout=60,
            follow_redirects=True,
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

    client = httpx.Client(timeout=None, follow_redirects=True)
    upstream = client.send(client.build_request("GET", upstream_url, headers=headers), stream=True)
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
        rewritten = _rewrite_browser_playlist(data, str(upstream.url), raw_token)
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
