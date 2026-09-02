from fastapi import APIRouter, Depends
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.api.catalog_status import AUDIO_READY, VIDEO_READY
from app.db.database import get_db
from app.models.models import Movie, MovieMedia, MovieTag, PlexLibrary, Role, User
from app.security.security import get_current_user

router = APIRouter(prefix="/api/categories", tags=["media"])


@router.get("")
def categories(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    ready_stmt = (
        select(func.count(func.distinct(Movie.id)))
        .join(PlexLibrary, PlexLibrary.id == Movie.library_id)
        .where(
            PlexLibrary.enabled.is_(True),
            Movie.media_type == "movie",
            exists().where(
                MovieMedia.movie_id == Movie.id,
                func.lower(MovieMedia.video_codec).in_(VIDEO_READY),
                func.lower(MovieMedia.audio_codec).in_(AUDIO_READY),
            ),
        )
    )
    if user.role == Role.MEMBER:
        ready_stmt = ready_stmt.where(PlexLibrary.visible_to_members.is_(True))
    ready_count = int(db.scalar(ready_stmt) or 0)

    stmt = (
        select(MovieTag.value, func.count(func.distinct(Movie.id)))
        .join(Movie, Movie.id == MovieTag.movie_id)
        .join(PlexLibrary, PlexLibrary.id == Movie.library_id)
        .where(
            MovieTag.kind == "genre",
            PlexLibrary.enabled.is_(True),
            Movie.media_type.in_(("movie", "show")),
        )
        .group_by(MovieTag.value)
        .order_by(func.count(func.distinct(Movie.id)).desc(), MovieTag.value.asc())
    )
    if user.role == Role.MEMBER:
        stmt = stmt.where(PlexLibrary.visible_to_members.is_(True))

    rows = db.execute(stmt).all()
    items = [{"name": "Ready Movies", "count": ready_count}]
    items.extend(
        {"name": str(name).strip(), "count": int(count)}
        for name, count in rows
        if str(name).strip() and str(name).strip().casefold() != "ready movies"
    )
    return {"categories": items}
