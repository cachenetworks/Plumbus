from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Movie, MovieMedia, MovieTag, PlexLibrary, Role, User
from app.security.security import get_current_user, require_role
from app.services.playback.service import PlaybackService
from app.services.plex.service import PlexService

router = APIRouter(prefix="/api/movies", tags=["movies"])


class MetadataOverrideUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=10000)
    tagline: str | None = Field(default=None, max_length=1000)
    content_rating: str | None = Field(default=None, max_length=32)
    year: int | None = Field(default=None, ge=1880, le=2200)


def _base_query(user: User):
    stmt = select(Movie).join(PlexLibrary, Movie.library_id == PlexLibrary.id).where(PlexLibrary.enabled.is_(True))
    if user.role == Role.MEMBER:
        stmt = stmt.where(PlexLibrary.visible_to_members.is_(True))
    return stmt


def _effective(movie: Movie, key: str):
    value = movie.local_overrides.get(key) if movie.local_overrides else None
    return getattr(movie, key) if value is None else value


def _movie_dict(movie: Movie, db: Session, detail: bool = False) -> dict:
    media = db.scalars(select(MovieMedia).where(MovieMedia.movie_id == movie.id)).all()
    tags = db.scalars(select(MovieTag).where(MovieTag.movie_id == movie.id)).all()
    library = db.get(PlexLibrary, movie.library_id)
    grouped: dict[str, list[str]] = {}
    for tag in tags:
        grouped.setdefault(tag.kind, []).append(tag.value)
    item = {
        "id": movie.id,
        "title": _effective(movie, "title"),
        "year": _effective(movie, "year"),
        "content_rating": _effective(movie, "content_rating"),
        "poster_url": f"/media/poster/{movie.id}" if movie.poster_key else None,
        "backdrop_url": f"/media/backdrop/{movie.id}" if movie.art_key else None,
        "genres": grouped.get("genre", []),
        "collections": grouped.get("collection", []),
        "qualities": sorted({m.resolution for m in media if m.resolution}),
        "library": {"id": library.id, "title": library.title} if library else None,
        "added_at": movie.added_at,
        "updated_at": movie.plex_updated_at,
    }
    if detail:
        playback = PlaybackService(db)
        item.update(
            {
                "original_title": movie.original_title,
                "summary": _effective(movie, "summary"),
                "tagline": _effective(movie, "tagline"),
                "duration_ms": movie.duration_ms,
                "studio": movie.studio,
                "rating": movie.rating,
                "audience_rating": movie.audience_rating,
                "edition_title": movie.edition_title,
                "directors": grouped.get("director", []),
                "actors": grouped.get("actor", []),
                "writers": grouped.get("writer", []),
                "labels": grouped.get("label", []),
                "media": [
                    {
                        "id": m.id,
                        "container": m.container,
                        "video_codec": m.video_codec,
                        "audio_codec": m.audio_codec,
                        "width": m.width,
                        "height": m.height,
                        "resolution": m.resolution,
                        "bitrate": m.bitrate,
                        "hdr": m.hdr,
                        "audio_channels": m.audio_channels,
                        "file_size": m.file_size,
                        **playback.get_media_info(m),
                    }
                    for m in media
                ],
                "local_overrides": dict(movie.local_overrides or {}),
            }
        )
    return item


@router.get("")
def browse(
    q: str | None = Query(default=None, max_length=160),
    genre: str | None = Query(default=None, max_length=80),
    year: int | None = Query(default=None, ge=1880, le=2200),
    resolution: str | None = Query(default=None, max_length=32),
    library_id: int | None = Query(default=None, ge=1),
    collection: str | None = Query(default=None, max_length=160),
    content_rating: str | None = Query(default=None, max_length=32),
    sort: str = Query(default="recent", pattern="^(recent|updated|alphabetical)$"),
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = _base_query(user)
    if q:
        like = f"%{q}%"
        clauses = [
            Movie.title.ilike(like),
            Movie.original_title.ilike(like),
            exists().where(MovieTag.movie_id == Movie.id, MovieTag.value.ilike(like)),
        ]
        if q.isdigit() and 1880 <= int(q) <= 2200:
            clauses.append(Movie.year == int(q))
        stmt = stmt.where(or_(*clauses))
    if year:
        stmt = stmt.where(Movie.year == year)
    if library_id:
        stmt = stmt.where(Movie.library_id == library_id)
    if content_rating:
        stmt = stmt.where(Movie.content_rating == content_rating)
    if genre:
        stmt = stmt.where(exists().where(MovieTag.movie_id == Movie.id, MovieTag.kind == "genre", MovieTag.value == genre))
    if collection:
        stmt = stmt.where(exists().where(MovieTag.movie_id == Movie.id, MovieTag.kind == "collection", MovieTag.value == collection))
    if resolution:
        stmt = stmt.where(exists().where(MovieMedia.movie_id == Movie.id, MovieMedia.resolution == resolution))

    if sort == "alphabetical":
        stmt = stmt.order_by(Movie.title.asc(), Movie.year.desc().nullslast())
    elif sort == "updated":
        stmt = stmt.order_by(Movie.plex_updated_at.desc().nullslast(), Movie.title.asc())
    else:
        stmt = stmt.order_by(Movie.added_at.desc().nullslast(), Movie.title.asc())

    movies = db.scalars(stmt.offset(offset).limit(limit)).unique().all()
    return {"items": [_movie_dict(movie, db) for movie in movies], "offset": offset, "limit": limit}


@router.get("/search/suggest")
def search_suggest(
    q: str = Query(min_length=1, max_length=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    like = f"%{q}%"
    condition = or_(
        Movie.title.ilike(like),
        exists().where(MovieTag.movie_id == Movie.id, MovieTag.value.ilike(like)),
    )
    if q.isdigit() and 1880 <= int(q) <= 2200:
        condition = or_(condition, Movie.year == int(q))
    movies = db.scalars(_base_query(user).where(condition).order_by(Movie.title).limit(8)).all()
    return [{"id": m.id, "title": _effective(m, "title"), "year": _effective(m, "year")} for m in movies]


@router.get("/{movie_id}")
def movie_detail(
    movie_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    movie = db.scalar(_base_query(user).where(Movie.id == movie_id))
    if not movie:
        raise HTTPException(404, "Movie not found")
    return _movie_dict(movie, db, detail=True)


@router.patch("/{movie_id}/metadata-override")
def update_metadata_override(
    movie_id: int,
    payload: MetadataOverrideUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    values = payload.model_dump(exclude_unset=True)
    overrides = dict(movie.local_overrides or {})
    for key, value in values.items():
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    movie.local_overrides = overrides
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="movie.metadata_overridden",
            target_type="movie",
            target_id=str(movie.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={"fields": sorted(values.keys())},
        )
    )
    db.commit()
    return _movie_dict(movie, db, detail=True)


art_router = APIRouter(tags=["artwork"])


def _art(movie_id: int, attr: str, user: User, db: Session) -> Response:
    movie = db.scalar(_base_query(user).where(Movie.id == movie_id))
    if not movie:
        raise HTTPException(404, "Movie not found")
    plex_path = getattr(movie, attr)
    if not plex_path:
        raise HTTPException(404, "Artwork unavailable")
    upstream = PlexService.from_db(db).artwork_response(plex_path)
    if upstream.status_code != 200:
        raise HTTPException(502, "Plex artwork unavailable")
    return Response(
        upstream.content,
        media_type=upstream.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "private, max-age=3600", "Vary": "Cookie"},
    )


@art_router.get("/media/poster/{movie_id}")
def poster(movie_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    return _art(movie_id, "poster_key", user, db)


@art_router.get("/media/backdrop/{movie_id}")
def backdrop(movie_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    return _art(movie_id, "art_key", user, db)
