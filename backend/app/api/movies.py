from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Movie, MovieMedia, MovieTag, PlexLibrary, PlexServer, Role, User
from app.security.security import get_current_user, require_role
from app.services.playback.service import PlaybackService
from app.services.plex.service import PlexService

router = APIRouter(prefix="/api/movies", tags=["media"])


class MetadataOverrideUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=10000)
    tagline: str | None = Field(default=None, max_length=1000)
    content_rating: str | None = Field(default=None, max_length=32)
    year: int | None = Field(default=None, ge=1880, le=2200)


def _base_query(user: User, top_level_only: bool = True):
    stmt = select(Movie).join(PlexLibrary, Movie.library_id == PlexLibrary.id).where(PlexLibrary.enabled.is_(True))
    if user.role == Role.MEMBER:
        stmt = stmt.where(PlexLibrary.visible_to_members.is_(True))
    if top_level_only:
        stmt = stmt.where(Movie.media_type.in_(("movie", "show")))
    return stmt


def _effective(media_item: Movie, key: str):
    value = media_item.local_overrides.get(key) if media_item.local_overrides else None
    return getattr(media_item, key) if value is None else value


def _media_dict(media_item: Movie, db: Session, detail: bool = False) -> dict:
    media = db.scalars(select(MovieMedia).where(MovieMedia.movie_id == media_item.id)).all()
    tags = db.scalars(select(MovieTag).where(MovieTag.movie_id == media_item.id)).all()
    library = db.get(PlexLibrary, media_item.library_id)
    server = db.get(PlexServer, library.server_id) if library else None
    grouped: dict[str, list[str]] = {}
    for tag in tags:
        grouped.setdefault(tag.kind, []).append(tag.value)
    is_anime = bool(library and "anime" in library.title.lower()) or any(
        value.lower() == "anime" for value in grouped.get("genre", [])
    )
    item = {
        "id": media_item.id,
        "media_type": media_item.media_type,
        "title": _effective(media_item, "title"),
        "year": _effective(media_item, "year"),
        "content_rating": _effective(media_item, "content_rating"),
        "poster_url": f"/media/poster/{media_item.id}" if media_item.poster_key else None,
        "backdrop_url": f"/media/backdrop/{media_item.id}" if media_item.art_key else None,
        "genres": grouped.get("genre", []),
        "collections": grouped.get("collection", []),
        "qualities": sorted({m.resolution for m in media if m.resolution}),
        "playable": bool(media),
        "is_anime": is_anime,
        "season_number": media_item.season_number,
        "episode_number": media_item.episode_number,
        "parent_title": media_item.parent_title,
        "grandparent_title": media_item.grandparent_title,
        "library": {
            "id": library.id,
            "title": library.title,
            "type": library.library_type,
            "server_id": library.server_id,
            "server_name": server.server_name if server else None,
        } if library else None,
        "added_at": media_item.added_at,
        "updated_at": media_item.plex_updated_at,
    }
    if detail:
        playback = PlaybackService(db)
        item.update(
            {
                "original_title": media_item.original_title,
                "summary": _effective(media_item, "summary"),
                "tagline": _effective(media_item, "tagline"),
                "duration_ms": media_item.duration_ms,
                "studio": media_item.studio,
                "rating": media_item.rating,
                "audience_rating": media_item.audience_rating,
                "edition_title": media_item.edition_title,
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
                "local_overrides": dict(media_item.local_overrides or {}),
            }
        )
    return item


def _hierarchy(media_item: Movie, db: Session) -> dict:
    if media_item.media_type == "show":
        seasons = db.scalars(
            select(Movie).where(
                Movie.library_id == media_item.library_id,
                Movie.media_type == "season",
                Movie.parent_rating_key == media_item.rating_key,
            ).order_by(Movie.season_number.asc().nullslast(), Movie.title.asc())
        ).all()
        episodes = db.scalars(
            select(Movie).where(
                Movie.library_id == media_item.library_id,
                Movie.media_type == "episode",
                Movie.grandparent_rating_key == media_item.rating_key,
            ).order_by(Movie.season_number.asc().nullslast(), Movie.episode_number.asc().nullslast(), Movie.title.asc())
        ).all()
        by_season: dict[str, list[Movie]] = {}
        for episode in episodes:
            by_season.setdefault(episode.parent_rating_key or "", []).append(episode)
        return {
            "season_count": len(seasons),
            "episode_count": len(episodes),
            "seasons": [
                {
                    **_media_dict(season, db, detail=True),
                    "episodes": [_media_dict(ep, db, detail=True) for ep in by_season.get(season.rating_key, [])],
                }
                for season in seasons
            ],
        }
    if media_item.media_type == "season":
        episodes = db.scalars(
            select(Movie).where(
                Movie.library_id == media_item.library_id,
                Movie.media_type == "episode",
                Movie.parent_rating_key == media_item.rating_key,
            ).order_by(Movie.episode_number.asc().nullslast(), Movie.title.asc())
        ).all()
        return {"episode_count": len(episodes), "episodes": [_media_dict(ep, db, detail=True) for ep in episodes]}
    return {}


@router.get("")
def browse(
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
            PlexLibrary.title.ilike(like),
            exists().where(MovieTag.movie_id == Movie.id, MovieTag.value.ilike(like)),
        ]
        if q.isdigit() and 1880 <= int(q) <= 2200:
            clauses.append(Movie.year == int(q))
        stmt = stmt.where(or_(*clauses))
    if year:
        stmt = stmt.where(Movie.year == year)
    if library_id:
        stmt = stmt.where(Movie.library_id == library_id)
    if media_type:
        stmt = stmt.where(Movie.media_type == media_type)
    if anime:
        stmt = stmt.where(
            or_(
                PlexLibrary.title.ilike("%anime%"),
                exists().where(MovieTag.movie_id == Movie.id, MovieTag.kind == "genre", MovieTag.value.ilike("anime")),
            )
        )
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

    items = db.scalars(stmt.offset(offset).limit(limit)).unique().all()
    return {"items": [_media_dict(item, db) for item in items], "offset": offset, "limit": limit}


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
    items = db.scalars(_base_query(user).where(condition).order_by(Movie.title).limit(8)).all()
    return [
        {"id": m.id, "media_type": m.media_type, "title": _effective(m, "title"), "year": _effective(m, "year")}
        for m in items
    ]


@router.get("/{media_id}")
def media_detail(
    media_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    media_item = db.scalar(_base_query(user, top_level_only=False).where(Movie.id == media_id))
    if not media_item:
        raise HTTPException(404, "Media item not found")
    result = _media_dict(media_item, db, detail=True)
    result.update(_hierarchy(media_item, db))
    return result


@router.patch("/{media_id}/metadata-override")
def update_metadata_override(
    media_id: int,
    payload: MetadataOverrideUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    media_item = db.get(Movie, media_id)
    if not media_item:
        raise HTTPException(404, "Media item not found")
    values = payload.model_dump(exclude_unset=True)
    overrides = dict(media_item.local_overrides or {})
    for key, value in values.items():
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    media_item.local_overrides = overrides
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="media.metadata_overridden",
            target_type=media_item.media_type,
            target_id=str(media_item.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={"fields": sorted(values.keys())},
        )
    )
    db.commit()
    result = _media_dict(media_item, db, detail=True)
    result.update(_hierarchy(media_item, db))
    return result


art_router = APIRouter(tags=["artwork"])


def _art(media_id: int, attr: str, user: User, db: Session) -> Response:
    media_item = db.scalar(_base_query(user, top_level_only=False).where(Movie.id == media_id))
    if not media_item:
        raise HTTPException(404, "Media item not found")
    plex_path = getattr(media_item, attr)
    if not plex_path:
        raise HTTPException(404, "Artwork unavailable")
    plex = PlexService.for_movie(db, media_item)
    if not plex.base_url or not plex.token:
        raise HTTPException(503, "Plex server for this media item is not configured")
    upstream = plex.artwork_response(plex_path)
    if upstream.status_code != 200:
        raise HTTPException(502, "Plex artwork unavailable")
    return Response(
        upstream.content,
        media_type=upstream.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "private, max-age=3600", "Vary": "Cookie"},
    )


@art_router.get("/media/poster/{media_id}")
def poster(media_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    return _art(media_id, "poster_key", user, db)


@art_router.get("/media/backdrop/{media_id}")
def backdrop(media_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    return _art(media_id, "art_key", user, db)