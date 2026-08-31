from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Movie, MovieMedia, PlaybackToken, User
from app.security.security import random_token, token_hash
from app.services.settings import ApplicationSettingsService

PlaybackTarget = Literal["browser", "vrchat"]


class PlaybackService:
    def __init__(self, db: Session):
        self.db = db

    def select_best_media(self, movie: Movie, target: PlaybackTarget = "browser") -> MovieMedia:
        prefs = ApplicationSettingsService(self.db).playback()
        preferred_codec = str(prefs["preferred_video_codec"]).lower()
        preferred_resolution = str(prefs["preferred_resolution"]).lower()
        max_bitrate = int(prefs["max_stream_bitrate_kbps"])

        candidates = self.db.scalars(
            select(MovieMedia)
            .where(MovieMedia.movie_id == movie.id, MovieMedia.part_key.is_not(None))
            .order_by(
                case((MovieMedia.video_codec.ilike(preferred_codec), 0), else_=1),
                case((MovieMedia.resolution.ilike(preferred_resolution), 0), else_=1),
                case((MovieMedia.bitrate <= max_bitrate, 0), else_=1),
                MovieMedia.height.desc().nullslast(),
                MovieMedia.bitrate.desc().nullslast(),
            )
        ).all()
        if not candidates:
            raise HTTPException(409, "No playable Plex media part is indexed for this media item")

        if target == "browser":
            browser_native = [
                media
                for media in candidates
                if (media.container or "").lower() in {"mp4", "m4v"}
                and (media.video_codec or "").lower() in {"h264", "avc"}
                and (media.audio_codec or "").lower() in {"aac", "mp3", ""}
                and (media.bitrate is None or media.bitrate <= max_bitrate)
            ]
            if browser_native:
                return browser_native[0]

        return candidates[0]

    def get_media_info(self, media: MovieMedia, prefs: dict | None = None) -> dict:
        prefs = prefs or ApplicationSettingsService(self.db).playback()
        codec = (media.video_codec or "").lower()
        container = (media.container or "").lower()
        audio_codec = (media.audio_codec or "").lower()
        direct_play = codec in {"h264", "avc", "hevc", "h265"} and container in {"mp4", "m4v", "mkv"}
        browser_native = (
            codec in {"h264", "avc"}
            and container in {"mp4", "m4v"}
            and audio_codec in {"aac", "mp3", ""}
        )
        bitrate_ok = media.bitrate is None or media.bitrate <= int(prefs["max_stream_bitrate_kbps"])
        preferred_codec = codec in {
            str(prefs["preferred_video_codec"]).lower(),
            "avc" if str(prefs["preferred_video_codec"]).lower() == "h264" else "",
        }
        if direct_play and bitrate_ok and preferred_codec:
            playback_mode = "Direct Play"
        elif direct_play and bitrate_ok:
            playback_mode = "Direct Stream"
        else:
            playback_mode = "Transcode Required"
        return {
            "container": media.container,
            "video_codec": media.video_codec,
            "audio_codec": media.audio_codec,
            "resolution": media.resolution,
            "width": media.width,
            "height": media.height,
            "bitrate": media.bitrate,
            "hdr": media.hdr,
            "playback_mode": playback_mode,
            "direct_play_candidate": direct_play,
            "browser_native_candidate": browser_native and bitrate_ok,
            "allow_plex_transcoding": bool(prefs["allow_plex_transcoding"]),
        }

    def create_token(
        self,
        user: User,
        movie: Movie,
        ip: str | None = None,
        user_agent: str | None = None,
        target: PlaybackTarget = "browser",
    ) -> tuple[PlaybackToken, str]:
        media = self.select_best_media(movie, target=target)
        raw = random_token(32)
        token = PlaybackToken(
            token_hash=token_hash(raw),
            user_id=user.id,
            movie_id=movie.id,
            movie_media_id=media.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.PLAYBACK_TOKEN_LIFETIME_MINUTES),
            ip=ip,
            user_agent=user_agent,
        )
        self.db.add(token)
        self.db.commit()
        self.db.refresh(token)
        return token, raw

    def validate_token(self, raw: str) -> PlaybackToken:
        token = self.db.scalar(
            select(PlaybackToken).where(
                PlaybackToken.token_hash == token_hash(raw),
                PlaybackToken.revoked_at.is_(None),
                PlaybackToken.expires_at > datetime.now(UTC),
            )
        )
        if not token:
            raise HTTPException(404, "Playback token is invalid or expired")
        return token

    def revoke(self, token: PlaybackToken) -> None:
        token.revoked_at = datetime.now(UTC)
        self.db.commit()
