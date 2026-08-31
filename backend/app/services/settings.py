from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import ApplicationSetting


class ApplicationSettingsService:
    def __init__(self, db: Session):
        self.db = db

    def playback(self) -> dict:
        values = {
            "preferred_video_codec": settings.PREFERRED_VIDEO_CODEC,
            "preferred_resolution": settings.PREFERRED_RESOLUTION,
            "max_stream_bitrate_kbps": settings.MAX_STREAM_BITRATE_KBPS,
            "allow_plex_transcoding": settings.ALLOW_PLEX_TRANSCODING,
        }
        row = self.db.get(ApplicationSetting, "playback")
        if row and isinstance(row.value, dict):
            values.update({key: value for key, value in row.value.items() if key in values})
        return values

    def set_playback(self, values: dict, updated_by_id: int | None = None) -> dict:
        row = self.db.get(ApplicationSetting, "playback")
        if row is None:
            row = ApplicationSetting(key="playback", value={}, updated_by_id=updated_by_id)
            self.db.add(row)
        row.value = dict(values)
        row.updated_by_id = updated_by_id
        self.db.flush()
        return self.playback()
