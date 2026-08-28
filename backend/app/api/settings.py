from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import ApplicationSetting, AuditLog, Role, User
from app.security.security import require_role

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PlaybackSettingsUpdate(BaseModel):
    preferred_video_codec: str = Field(default="h264", max_length=32)
    preferred_resolution: str = Field(default="1080p", max_length=32)
    max_stream_bitrate_kbps: int = Field(default=20000, ge=500, le=200000)
    allow_plex_transcoding: bool = False


def _defaults() -> dict:
    return {
        "preferred_video_codec": settings.PREFERRED_VIDEO_CODEC,
        "preferred_resolution": settings.PREFERRED_RESOLUTION,
        "max_stream_bitrate_kbps": settings.MAX_STREAM_BITRATE_KBPS,
        "allow_plex_transcoding": settings.ALLOW_PLEX_TRANSCODING,
    }


def get_playback_settings(db: Session) -> dict:
    row = db.get(ApplicationSetting, "playback")
    values = _defaults()
    if row and isinstance(row.value, dict):
        values.update({k: v for k, v in row.value.items() if k in values})
    return values


@router.get("/playback")
def read_playback_settings(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    return get_playback_settings(db)


@router.put("/playback")
def update_playback_settings(
    payload: PlaybackSettingsUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ApplicationSetting, "playback")
    if row is None:
        row = ApplicationSetting(key="playback", value={}, updated_by_id=actor.id)
        db.add(row)
    row.value = payload.model_dump()
    row.updated_by_id = actor.id
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="playback.settings_changed",
            target_type="settings",
            target_id="playback",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={"keys": sorted(payload.model_dump().keys())},
        )
    )
    db.commit()
    return get_playback_settings(db)
