from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import movies
from app.db.database import get_db
from app.models.models import User
from app.security.security import get_current_user

router = APIRouter(prefix="/api/movies", tags=["media"])


@router.get("")
def browse_all_titles(
    q: str | None = Query(default=None, max_length=160),
    genre: str | None = Query(default=None, max_length=80),
    year: int | None = Query(default=None, ge=1880, le=2200),
    resolution: str | None = Query(default=None, max_length=32),
    library_id: int | None = Query(default=None, ge=1),
    collection: str | None = Query(default=None, max_length=160),
    content_rating: str | None = Query(default=None, max_length=32),
    media_type: str | None = Query(default=None, pattern="^(movie|show)$"),
    anime: bool = Query(default=False),
    sort: str = Query(default="recent", pattern="^(recent|updated|alphabetical)$"),
    limit: int = Query(default=36, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Compatibility browse route for the current catalog UI.

    The React catalog currently asks for ``limit=36`` and has no pagination or
    load-more path. That meant the UI silently presented the first response as
    the complete library. Until the client pagination pass lands, expand only
    that legacy first-page request so the catalog actually contains the full
    indexed title set instead of stopping after one page.

    Explicit callers using a different limit/offset keep their requested
    pagination semantics.
    """
    effective_limit = 5000 if limit == 36 and offset == 0 else limit
    response = movies.browse(
        q=q,
        genre=genre,
        year=year,
        resolution=resolution,
        library_id=library_id,
        collection=collection,
        content_rating=content_rating,
        media_type=media_type,
        anime=anime,
        sort=sort,
        limit=effective_limit,
        offset=offset,
        user=user,
        db=db,
    )
    response["requested_limit"] = limit
    response["expanded_legacy_page"] = effective_limit != limit
    return response
