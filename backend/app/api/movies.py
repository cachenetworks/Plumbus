from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import Movie, MovieMedia, MovieTag, PlexLibrary, Role, User
from app.security.security import get_current_user
from app.services.plex.service import PlexService

router = APIRouter(prefix="/api/movies", tags=["movies"])


def _base_query(user: User):
    stmt = select(Movie).join(PlexLibrary, Movie.library_id == PlexLibrary.id).where(PlexLibrary.enabled.is_(True))
    if user.role == Role.MEMBER:
        stmt = stmt.where(PlexLibrary.visible_to_members.is_(True))
    return stmt


def _movie_dict(movie: Movie, db: Session, detail: bool = False) -> dict:
    media = db.scalars(select(MovieMedia).where(MovieMedia.movie_id == movie.id)).all()
    tags = db.scalars(select(MovieTag).where(MovieTag.movie_id == movie.id)).all()
    grouped: dict[str, list[str]] = {}
    for tag in tags:
        grouped.setdefault(tag.kind, []).append(tag.value)
    item = {
        "id": movie.id,
        "title": movie.local_overrides.get("title", movie.title),
        "year": movie.year,
        "poster_url": f"/media/poster/{movie.id}" if movie.poster_key else None,
        "backdrop_url": f"/media/backdrop/{movie.id}" if movie.art_key else None,
        "genres": grouped.get("genre", []),
        "collections": grouped.get("collection", []),
        "qualities": sorted({m.resolution for m in media if m.resolution}),
        "added_at": movie.added_at,
    }
    if detail:
        item.update(
            {
                "original_title": movie.original_title,
                "summary": movie.local_overrides.get("summary", movie.summary),
                "tagline": movie.tagline,
                "content_rating": movie.content_rating,
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
                    }
                    for m in media
                ],
            }
        )
    return item


@router.get("")
def browse(
    q: str | None = Query(default=None, max_length=160),
    genre: str | None = Query(default=None, max_length=80),
    year: int | None = Query(default=None, ge=1880, le=2200),
    resolution: str | None = Query(default=None, max_length=32),
    collection: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    stmt = _base_query(user)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Movie.title.ilike(like),
                exists().where(MovieTag.movie_id == Movie.id, MovieTag.value.ilike(like)),
            )
        )
    if year:
        stmt = stmt.where(Movie.year == year)
    if genre:
        stmt = stmt.where(exists().where(MovieTag.movie_id == Movie.id, MovieTag.kind == "genre", MovieTag.value == genre))
    if collection:
        stmt = stmt.where(exists().where(MovieTag.movie_id == Movie.id, MovieTag.kind == "collection", MovieTag.value == collection))
    if resolution:
        stmt = stmt.where(exists().where(MovieMedia.movie_id == Movie.id, MovieMedia.resolution == resolution))
    stmt = stmt.order_by(Movie.added_at.desc().nullslast(), Movie.title.asc()).offset(offset).limit(limit)
    movies = db.scalars(stmt).unique().all()
    return {"items": [_movie_dict(movie, db) for movie in movies], "offset": offset, "limit": limit}


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


@router.get("/search/suggest")
def search_suggest(
    q: str = Query(min_length=1, max_length=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    movies = db.scalars(_base_query(user).where(Movie.title.ilike(f"%{q}%")).order_by(Movie.title).limit(8)).all()
    return [{"id": m.id, "title": m.title, "year": m.year} for m in movies]


art_router = APIRouter(tags=["artwork"])


def _art(movie_id: int, attr: str, user: User, db: Session) -> Response:
    movie = db.scalar(_base_query(user).where(Movie.id == movie_id))
    if not movie:
        raise HTTPException(404, "Movie not found")
    plex_path = getattr(movie, attr)
    if not plex_path:
        raise HTTPException(404, "Artwork unavailable")
    upstream = PlexService().artwork_response(plex_path)
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
