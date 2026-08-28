from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Role, User
from app.security.security import require_role
from app.services.settings import ApplicationSettingsService

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PlaybackSettingsUpdate(BaseModel):
    preferred_video_codec: str = Field(default="h264", max_length=32)
    preferred_resolution: str = Field(default="1080p", max_length=32)
    max_stream_bitrate_kbps: int = Field(default=20000, ge=500, le=200000)
    allow_plex_transcoding: bool = False


@router.get("/playback")
def read_playback_settings(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    return ApplicationSettingsService(db).playback()


@router.put("/playback")
def update_playback_settings(
    payload: PlaybackSettingsUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    service = ApplicationSettingsService(db)
    values = service.set_playback(payload.model_dump(), actor.id)
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
    return values
