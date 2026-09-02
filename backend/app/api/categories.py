from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import Movie, MovieTag, PlexLibrary, Role, User
from app.security.security import get_current_user

router = APIRouter(prefix="/api/categories", tags=["media"])


@router.get("")
def categories(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
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
    items = [
        {"name": str(name).strip(), "count": int(count)}
        for name, count in rows
        if str(name).strip()
    ]
    return {"categories": items}
