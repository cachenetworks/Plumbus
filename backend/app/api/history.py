from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import Movie, PlaybackHistory, User
from app.security.security import get_current_user

router = APIRouter(prefix="/api/history", tags=["history"])


class HistoryPayload(BaseModel):
    movie_id: int
    position_ms: int = Field(default=0, ge=0)


def _get_or_create(db: Session, user: User, movie_id: int) -> PlaybackHistory:
    if not db.get(Movie, movie_id):
        raise HTTPException(404, "Media not found")
    row = db.scalar(select(PlaybackHistory).where(PlaybackHistory.user_id == user.id, PlaybackHistory.movie_id == movie_id))
    if row is None:
        row = PlaybackHistory(user_id=user.id, movie_id=movie_id)
        db.add(row)
        db.flush()
    return row


@router.post("/start")
def start(payload: HistoryPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = _get_or_create(db, user, payload.movie_id)
    row.playback_started_at = datetime.now(UTC)
    row.last_position_ms = payload.position_ms
    row.completed = False
    db.commit()
    return {"ok": True}


@router.post("/progress")
def progress(payload: HistoryPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = _get_or_create(db, user, payload.movie_id)
    row.last_position_ms = payload.position_ms
    row.last_watched_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


@router.post("/complete")
def complete(payload: HistoryPayload, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = _get_or_create(db, user, payload.movie_id)
    row.last_position_ms = payload.position_ms
    row.last_watched_at = datetime.now(UTC)
    row.completed = True
    db.commit()
    return {"ok": True}


@router.get("/continue-watching")
def continue_watching(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(PlaybackHistory)
        .where(PlaybackHistory.user_id == user.id, PlaybackHistory.completed.is_(False), PlaybackHistory.last_position_ms > 0)
        .order_by(PlaybackHistory.last_watched_at.desc())
        .limit(30)
    ).all()
    return [{"movie_id": row.movie_id, "position_ms": row.last_position_ms, "last_watched_at": row.last_watched_at} for row in rows]


@router.get("/{movie_id}")
def history_for_media(
    movie_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not db.get(Movie, movie_id):
        raise HTTPException(404, "Media not found")
    row = db.scalar(
        select(PlaybackHistory).where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.movie_id == movie_id,
        )
    )
    if row is None:
        return {"movie_id": movie_id, "position_ms": 0, "completed": False, "last_watched_at": None}
    return {
        "movie_id": movie_id,
        "position_ms": int(row.last_position_ms or 0),
        "completed": bool(row.completed),
        "last_watched_at": row.last_watched_at,
    }
