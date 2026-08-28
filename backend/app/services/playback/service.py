from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Movie, MovieMedia, PlaybackToken, User
from app.security.security import random_token, token_hash


class PlaybackService:
    def __init__(self, db: Session):
        self.db = db

    def select_best_media(self, movie: Movie) -> MovieMedia:
        media = self.db.scalars(
            select(MovieMedia)
            .where(MovieMedia.movie_id == movie.id)
            .order_by(MovieMedia.height.desc().nullslast(), MovieMedia.bitrate.desc().nullslast())
        ).first()
        if not media or not media.part_key:
            raise HTTPException(409, "No playable Plex media part is indexed for this movie")
        return media

    def get_media_info(self, media: MovieMedia) -> dict:
        codec = (media.video_codec or "").lower()
        direct_play = codec in {"h264", "avc", "hevc", "h265"} and (media.container or "").lower() in {"mp4", "mkv"}
        return {
            "container": media.container,
            "video_codec": media.video_codec,
            "audio_codec": media.audio_codec,
            "resolution": media.resolution,
            "width": media.width,
            "height": media.height,
            "bitrate": media.bitrate,
            "hdr": media.hdr,
            "direct_play_candidate": direct_play,
        }

    def create_token(
        self,
        user: User,
        movie: Movie,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[PlaybackToken, str]:
        media = self.select_best_media(movie)
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
