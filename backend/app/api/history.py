from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.movies import _serialize_many
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
    row = db.scalar(
        select(PlaybackHistory).where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.movie_id == movie_id,
        )
    )
    if row is None:
        row = PlaybackHistory(user_id=user.id, movie_id=movie_id)
        db.add(row)
        db.flush()
    return row


def _history_media_payload(rows: list[PlaybackHistory], db: Session) -> list[dict]:
    if not rows:
        return []
    movie_ids = [row.movie_id for row in rows]
    movies = db.scalars(select(Movie).where(Movie.id.in_(movie_ids))).all()
    movie_by_id = {movie.id: movie for movie in movies}
    ordered_movies = [movie_by_id[row.movie_id] for row in rows if row.movie_id in movie_by_id]
    media_by_id = {item["id"]: item for item in _serialize_many(ordered_movies, db)}
    result: list[dict] = []
    for row in rows:
        media = media_by_id.get(row.movie_id)
        movie = movie_by_id.get(row.movie_id)
        if not media or not movie:
            continue
        result.append(
            {
                **media,
                "duration_ms": movie.duration_ms,
                "position_ms": int(row.last_position_ms or 0),
                "completed": bool(row.completed),
                "last_watched_at": row.last_watched_at,
            }
        )
    return result


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
    return {"ok": True, "completed": True}


@router.post("/{movie_id}/watched")
def mark_watched(movie_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Media not found")
    row = _get_or_create(db, user, movie_id)
    row.last_position_ms = int(movie.duration_ms or row.last_position_ms or 0)
    row.last_watched_at = datetime.now(UTC)
    row.completed = True
    db.commit()
    return {"ok": True, "movie_id": movie_id, "completed": True}


@router.delete("/{movie_id}/watched")
def mark_unwatched(movie_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if not db.get(Movie, movie_id):
        raise HTTPException(404, "Media not found")
    row = db.scalar(
        select(PlaybackHistory).where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.movie_id == movie_id,
        )
    )
    if row:
        row.completed = False
        row.last_position_ms = 0
        row.last_watched_at = None
        db.commit()
    return {"ok": True, "movie_id": movie_id, "completed": False}


@router.get("/status")
def history_status(
    ids: str = Query(min_length=1, max_length=2500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    parsed: list[int] = []
    for value in ids.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            media_id = int(value)
        except ValueError as exc:
            raise HTTPException(400, "ids must be comma-separated integers") from exc
        if media_id not in parsed:
            parsed.append(media_id)
    if len(parsed) > 120:
        raise HTTPException(400, "Maximum 120 media ids")
    rows = db.scalars(
        select(PlaybackHistory).where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.movie_id.in_(parsed),
        )
    ).all()
    by_id = {row.movie_id: row for row in rows}
    return {
        "items": {
            str(media_id): {
                "position_ms": int(by_id[media_id].last_position_ms or 0) if media_id in by_id else 0,
                "completed": bool(by_id[media_id].completed) if media_id in by_id else False,
                "last_watched_at": by_id[media_id].last_watched_at if media_id in by_id else None,
            }
            for media_id in parsed
        }
    }


@router.get("/continue-watching")
def continue_watching(
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(PlaybackHistory)
        .where(
            PlaybackHistory.user_id == user.id,
            PlaybackHistory.completed.is_(False),
            PlaybackHistory.last_position_ms > 0,
        )
        .order_by(PlaybackHistory.last_watched_at.desc().nullslast(), PlaybackHistory.id.desc())
        .limit(limit)
    ).all()
    return _history_media_payload(list(rows), db)


@router.get("/watched")
def watched(
    limit: int = Query(default=120, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(PlaybackHistory)
        .where(PlaybackHistory.user_id == user.id, PlaybackHistory.completed.is_(True))
        .order_by(PlaybackHistory.last_watched_at.desc().nullslast(), PlaybackHistory.id.desc())
        .limit(limit)
    ).all()
    return _history_media_payload(list(rows), db)


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
