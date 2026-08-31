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
from app.models.models import Movie, MovieMedia, User
from app.security.secrets import decrypt_secret, encrypt_secret
from app.security.security import get_current_user
from app.services.playback.service import PlaybackService
from app.services.plex.audio_tracks import (
    BROWSER_COPY_AUDIO_CODECS,
    BROWSER_COPY_VIDEO_CODECS,
    choose_direct_browser_audio_track,
    ensure_audio_tracks,
    primary_audio_track,
)
from app.services.plex.service import PlexService
from app.services.settings import ApplicationSettingsService

router = APIRouter(tags=["browser-playback"])
URI_ATTRIBUTE_RE = re.compile(r'URI="([^"]+)"')
BrowserMode = Literal["direct", "compatibility"]
BROWSER_AUDIO_CODECS = {"aac", "mp3"}
BROWSER_DIRECT_CONTAINERS = {"mp4", "m4v"}

# HLS is only used for a no-encode Direct Stream/remux or as a final full
# compatibility fallback. Keep the Plex connection pool warm for both cases.
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
    """Final browser compatibility path: encode video to H.264 and audio to AAC."""
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


def _build_browser_audio_url(
    plex: PlexService,
    rating_key: str,
    media: MovieMedia,
) -> str:
    """Last-resort path: copy video while converting unsupported audio."""
    session = uuid4().hex
    source_bitrate = int(media.bitrate or 0)
    profile_extra = (
        "add-transcode-target(type=videoProfile&context=streaming&protocol=hls"
        "&container=mpegts&videoCodec=h264,hevc&audioCodec=aac&replace=true)"
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
        "directStream": 1,
        "directStreamAudio": 0,
        "copyts": 1,
        "videoQuality": 100,
        "maxVideoBitrate": max(200000, source_bitrate + 1000),
        "audioBoost": 100,
        "subtitles": "none",
        "mediaBufferSize": 204800,
        "session": session,
        "X-Plex-Session-Identifier": session,
        "X-Plex-Client-Identifier": "plumbus-web-audio",
        "X-Plex-Product": "Plumbus",
        "X-Plex-Version": "1.0.0",
        "X-Plex-Platform": "Chrome",
        "X-Plex-Client-Platform": "Chrome",
        "X-Plex-Client-Profile-Name": "Chrome",
        "X-Plex-Client-Profile-Extra": profile_extra,
        "X-Plex-Token": plex.token,
    }
    return f"{plex.base_url}/video/:/transcode/universal/start.m3u8?{urlencode(params)}"


def _build_browser_copy_url(
    plex: PlexService,
    rating_key: str,
    media: MovieMedia,
    track: dict,
) -> str:
    """Remux a compatible existing audio track without encoding video or audio."""
    audio_codec = str(track.get("codec") or "").lower()
    video_codec = str(media.video_codec or "").lower()
    if audio_codec not in BROWSER_COPY_AUDIO_CODECS:
        raise HTTPException(409, "Selected audio track cannot be copied to the browser")
    if video_codec not in BROWSER_COPY_VIDEO_CODECS:
        raise HTTPException(409, "Selected video codec cannot be browser-remuxed without encoding")

    session = uuid4().hex
    source_bitrate = int(media.bitrate or 0)
    normalized_video = "h264" if video_codec == "avc" else video_codec
    profile_extra = (
        "add-transcode-target(type=videoProfile&context=streaming&protocol=hls"
        f"&container=mpegts&videoCodec={normalized_video}&audioCodec={audio_codec}&replace=true)"
    )
    params = {
        "path": f"/library/metadata/{rating_key}",
        "mediaIndex": int(track.get("media_index") or 0),
        "partIndex": int(track.get("part_index") or 0),
        "audioStreamID": str(track.get("id") or ""),
        "protocol": "hls",
        "hasMDE": 1,
        "offset": 0,
        "fastSeek": 1,
        "directPlay": 0,
        "directStream": 1,
        "directStreamAudio": 1,
        "copyts": 1,
        "videoQuality": 100,
        "maxVideoBitrate": max(200000, source_bitrate + 1000),
        "audioBoost": 100,
        "subtitles": "none",
        "subtitleStreamID": 0,
        "mediaBufferSize": 204800,
        "session": session,
        "X-Plex-Session-Identifier": session,
        "X-Plex-Client-Identifier": "plumbus-web-copy",
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


def _public_audio_track(track: dict) -> dict:
    return {
        key: track.get(key)
        for key in (
            "id",
            "codec",
            "language",
            "language_code",
            "language_tag",
            "channels",
            "selected",
            "default",
            "title",
            "display_title",
            "extended_display_title",
        )
    }


def _audio_tracks_for_media(db: Session, movie: Movie, media: MovieMedia) -> list[dict]:
    return ensure_audio_tracks(db, movie, media, _movie_plex(db, movie))


@router.get("/api/playback/media/{media_id}/audio-tracks")
def browser_audio_tracks(
    media_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    movie = db.get(Movie, media_id)
    if not movie:
        raise HTTPException(404, "Media item not found")
    if movie.media_type not in {"movie", "episode"}:
        raise HTTPException(409, "Only movies and episodes have audio tracks")
    media = PlaybackService(db).select_best_media(movie, target="browser")
    tracks = _audio_tracks_for_media(db, movie, media)
    primary = primary_audio_track(tracks)
    return {
        "audio_tracks": [_public_audio_track(track) for track in tracks],
        "selected_audio_stream_id": str(primary.get("id") or "") if primary else None,
        "can_direct_switch": str(media.video_codec or "").lower() in BROWSER_COPY_VIDEO_CODECS,
    }


@router.post("/api/playback/media/{media_id}/browser")
def browser_playback(
    media_id: int,
    request: Request,
    mode: BrowserMode = Query(default="direct"),
    audio_stream_id: str | None = Query(default=None),
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

    raw_token = parsed.path.removeprefix("/stream/").split("/", 1)[0]
    _token, _playback_user, movie, media = _active_playback(raw_token, db)
    plex = _movie_plex(db, movie)
    source_audio_codec = str(result["media"].get("audio_codec") or "").lower()
    source_container = str(result["media"].get("container") or "").lower()
    source_video_codec = str(result["media"].get("video_codec") or "").lower()
    needs_container_remux = (
        source_video_codec in BROWSER_COPY_VIDEO_CODECS
        and source_container not in BROWSER_DIRECT_CONTAINERS
    )

    # Native MP4/M4V + AAC/MP3 Direct Play starts without another Plex metadata
    # request. Track discovery is lazy unless the source needs audio/container
    # help or a user explicitly asks to switch tracks.
    tracks: list[dict] = []
    if audio_stream_id or (
        mode == "direct"
        and (source_audio_codec not in BROWSER_AUDIO_CODECS or needs_container_remux)
    ):
        tracks = ensure_audio_tracks(db, movie, media, plex)
        result["audio_tracks"] = [_public_audio_track(track) for track in tracks]
        primary = primary_audio_track(tracks)
        if primary:
            result["selected_audio_stream_id"] = str(primary.get("id") or "") or None

    if mode == "direct":
        chosen: dict | None = None
        if audio_stream_id:
            chosen = next(
                (track for track in tracks if str(track.get("id") or "") == audio_stream_id),
                None,
            )
            if not chosen:
                raise HTTPException(404, "Requested Plex audio track was not found")
            if str(chosen.get("codec") or "").lower() not in BROWSER_COPY_AUDIO_CODECS:
                raise HTTPException(409, "Requested audio track is not browser-copy compatible")
            if str(media.video_codec or "").lower() not in BROWSER_COPY_VIDEO_CODECS:
                raise HTTPException(409, "This video codec cannot switch audio tracks without encoding video")
        elif source_audio_codec not in BROWSER_AUDIO_CODECS or needs_container_remux:
            chosen = choose_direct_browser_audio_track(
                tracks,
                video_codec=media.video_codec,
            )

        if chosen:
            result["delivery"] = "hls"
            result["playback_url"] = (
                f"{relative}/master.m3u8?copy_audio_stream_id={chosen['id']}"
            )
            result["browser_codec_profile"] = (
                f"VIDEO_COPY/{str(chosen.get('codec') or '').upper()}_COPY"
            )
            result["stream_mode"] = "direct_stream"
            result["selected_audio_stream_id"] = str(chosen.get("id") or "")
            result["audio_strategy"] = "copy_existing_track"
            return result

        result["playback_url"] = relative
        result["browser_codec_profile"] = "ORIGINAL_PLEX_SOURCE"
        result["stream_mode"] = "direct"
        result["audio_strategy"] = "original_file"
        if source_audio_codec not in BROWSER_AUDIO_CODECS:
            result["audio_warning"] = (
                "The original file uses an audio codec this browser may not decode, and Plex does not expose a compatible AAC/MP3 track that can be copied directly."
            )
        elif needs_container_remux:
            result["audio_warning"] = (
                "This H.264 source uses a browser-unfriendly container and Plex did not expose a copy-compatible audio track for a no-encode remux."
            )
        return result

    result["playback_url"] = relative
    result["browser_codec_profile"] = "H.264/AAC"
    result["stream_mode"] = "compatibility"
    result["audio_strategy"] = "compatibility_encode"
    return result


@router.get("/stream/{raw_token}/master.m3u8")
def browser_transcode_master(
    raw_token: str,
    audio_only: bool = Query(default=False),
    copy_audio_stream_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    _token, _user, movie, media = _active_playback(raw_token, db)
    prefs = ApplicationSettingsService(db).playback()
    plex = _movie_plex(db, movie)

    stream_mode = "compatibility"
    selected_track: dict | None = None
    if copy_audio_stream_id:
        tracks = ensure_audio_tracks(db, movie, media, plex)
        selected_track = next(
            (
                track
                for track in tracks
                if str(track.get("id") or "") == copy_audio_stream_id
            ),
            None,
        )
        if not selected_track:
            raise HTTPException(404, "Requested Plex audio track was not found")
        upstream_url = _build_browser_copy_url(
            plex,
            movie.rating_key,
            media,
            selected_track,
        )
        stream_mode = "direct-stream-copy"
    elif audio_only:
        if not bool(prefs["allow_plex_transcoding"]):
            raise HTTPException(409, "Plex audio transcoding is disabled")
        upstream_url = _build_browser_audio_url(plex, movie.rating_key, media)
        stream_mode = "audio-only-encode"
    else:
        if not bool(prefs["allow_plex_transcoding"]):
            raise HTTPException(409, "Plex transcoding is disabled")
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
        raise HTTPException(502, f"Unable to start Plex web stream: {type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise HTTPException(502, f"Plex web stream returned HTTP {response.status_code}")
    _assert_plex_url(plex, str(response.url))
    if "#EXTM3U" not in response.text:
        raise HTTPException(502, "Plex did not return a valid HLS manifest")

    rewritten = _rewrite_browser_playlist(response.text, str(response.url), raw_token)
    video_header = "copy" if stream_mode != "compatibility" else "h264"
    audio_header = (
        str(selected_track.get("codec") or "copy")
        if selected_track
        else "aac"
    )
    return Response(
        rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "private, no-store",
            "X-Plumbus-Video-Codec": video_header,
            "X-Plumbus-Audio-Codec": audio_header,
            "X-Plumbus-Stream-Mode": stream_mode,
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
        raise HTTPException(502, f"Plex stream resource returned HTTP {status}")
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
