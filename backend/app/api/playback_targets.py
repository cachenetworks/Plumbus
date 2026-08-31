from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Movie, MovieMedia, PlaybackHistory, User
from app.security.security import get_current_user
from app.services.configuration import IntegrationConfigurationService
from app.services.playback.service import PlaybackService
from app.services.plex.service import PlexService
from app.services.settings import ApplicationSettingsService

router = APIRouter(prefix="/api/playback/media", tags=["playback-targets"])


def _plex_for_media(db: Session, media_item: Movie) -> PlexService:
    plex = PlexService.for_movie(db, media_item)
    if not plex.base_url or not plex.token:
        raise HTTPException(503, "The Plex server for this media is not configured")
    connection = plex.connect()
    if not connection.connected:
        raise HTTPException(503, f"Plex server is unreachable: {connection.error or 'connection failed'}")
    return plex


def _browser_direct_safe(media: MovieMedia, max_bitrate: int) -> bool:
    container = (media.container or "").lower()
    video_codec = (media.video_codec or "").lower()
    audio_codec = (media.audio_codec or "").lower()
    bitrate_ok = media.bitrate is None or media.bitrate <= max_bitrate
    return (
        container in {"mp4", "m4v"}
        and video_codec in {"h264", "avc"}
        and audio_codec in {"", "aac", "mp3"}
        and bitrate_ok
    )


def _history_start(db: Session, user: User, media_item: Movie) -> int:
    history = db.scalar(
        select(PlaybackHistory).where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.movie_id == media_item.id,
        )
    )
    if history is None:
        history = PlaybackHistory(user_id=user.id, movie_id=media_item.id)
        db.add(history)
    history.playback_started_at = datetime.now(UTC)
    history.last_watched_at = datetime.now(UTC)
    history.completed = False
    return int(history.last_position_ms or 0)


def _create_target(media_id: int, target: str, request: Request, user: User, db: Session) -> dict:
    media_item = db.get(Movie, media_id)
    if not media_item:
        raise HTTPException(404, "Media not found")
    if media_item.media_type not in {"movie", "episode"}:
        raise HTTPException(409, "Only movies and episodes can be played directly")

    plex = _plex_for_media(db, media_item)
    playback = PlaybackService(db)
    token, raw = playback.create_token(
        user,
        media_item,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    selected_media = db.get(MovieMedia, token.movie_media_id)
    if not selected_media:
        playback.revoke(token)
        raise HTTPException(409, "No playable Plex media version is indexed")

    prefs = ApplicationSettingsService(db).playback()
    media_info = playback.get_media_info(selected_media)
    allow_transcoding = bool(prefs["allow_plex_transcoding"])

    if target == "browser":
        direct = _browser_direct_safe(selected_media, int(prefs["max_stream_bitrate_kbps"]))
        transcode = not direct
        if transcode and not allow_transcoding:
            playback.revoke(token)
            raise HTTPException(
                409,
                "This file is not browser-direct-play compatible. Enable Plex transcoding in Playback Settings to watch it in the web player.",
            )
    else:
        transcode = media_info["playback_mode"] == "Transcode Required"
        if transcode and not allow_transcoding:
            playback.revoke(token)
            raise HTTPException(
                409,
                "This file requires Plex transcoding before it can be exposed as a VRChat link, but transcoding is disabled.",
            )

    public_base_url = IntegrationConfigurationService(db).site().app_url.rstrip("/")
    playback_url = (
        f"{public_base_url}/stream/{raw}/master.m3u8"
        if transcode
        else f"{public_base_url}/stream/{raw}"
    )

    resume_position_ms = 0
    if target == "browser":
        resume_position_ms = _history_start(db, user, media_item)

    delivery = "hls" if transcode else "progressive"
    db.add(
        AuditLog(
            actor_user_id=user.id,
            event=f"playback.{target}_created",
            target_type="media",
            target_id=str(media_item.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={
                "delivery": delivery,
                "plex_server_id": plex.server_id,
                "media_type": media_item.media_type,
            },
        )
    )
    db.commit()

    response = {
        "media_id": media_item.id,
        "target": target,
        "playback_url": playback_url,
        "delivery": delivery,
        "expires_at": token.expires_at,
        "media": media_info,
        "plex_server_id": plex.server_id,
        "resume_position_ms": resume_position_ms,
    }
    if target == "vrchat":
        response["vrchat_url"] = playback_url
        response["compatibility"] = (
            "Direct/Range stream - preferred for AVPro"
            if not transcode
            else "HLS transcode - requires an AVPro/Udon player that accepts HLS URLs"
        )
    return response


@router.post("/{media_id}/browser")
def browser_playback(
    media_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _create_target(media_id, "browser", request, user, db)


@router.post("/{media_id}/vrchat")
def vrchat_playback(
    media_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _create_target(media_id, "vrchat", request, user, db)


@router.get("/{media_id}/navigation")
def episode_navigation(
    media_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    media_item = db.get(Movie, media_id)
    if not media_item:
        raise HTTPException(404, "Media not found")
    if media_item.media_type != "episode" or not media_item.grandparent_rating_key:
        return {"previous": None, "next": None, "series_title": media_item.grandparent_title}

    siblings = db.scalars(
        select(Movie)
        .where(
            Movie.library_id == media_item.library_id,
            Movie.media_type == "episode",
            Movie.grandparent_rating_key == media_item.grandparent_rating_key,
        )
        .order_by(
            Movie.season_number.asc().nullslast(),
            Movie.episode_number.asc().nullslast(),
            Movie.title.asc(),
        )
    ).all()
    ids = [row.id for row in siblings]
    try:
        index = ids.index(media_item.id)
    except ValueError:
        return {"previous": None, "next": None, "series_title": media_item.grandparent_title}

    def summary(row: Movie | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": row.id,
            "title": row.title,
            "season_number": row.season_number,
            "episode_number": row.episode_number,
        }

    previous = siblings[index - 1] if index > 0 else None
    next_item = siblings[index + 1] if index + 1 < len(siblings) else None
    return {
        "previous": summary(previous),
        "next": summary(next_item),
        "series_title": media_item.grandparent_title,
    }
