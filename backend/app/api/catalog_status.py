from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import Movie, MovieMedia, PlexLibrary, Role, User
from app.security.security import get_current_user

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

VIDEO_READY = {"h264", "avc", "avc1"}
AUDIO_READY = {"aac", "aac-lc", "aac_lc", "mp4a"}


def _media_ready(media: MovieMedia) -> bool:
    return (
        str(media.video_codec or "").lower() in VIDEO_READY
        and str(media.audio_codec or "").lower() in AUDIO_READY
    )


def _quality_rank(value: str) -> tuple[int, str]:
    normalized = value.strip().lower()
    ranks = {
        "8k": 8000,
        "4320p": 8000,
        "4k": 4000,
        "2160p": 4000,
        "1440p": 1440,
        "1080p": 1080,
        "1080": 1080,
        "720p": 720,
        "720": 720,
        "576p": 576,
        "480p": 480,
    }
    return (ranks.get(normalized, 0), normalized)


def _ordered_qualities(values: set[str]) -> list[str]:
    return sorted((value for value in values if value), key=_quality_rank, reverse=True)


@router.get("/status")
def catalog_status(
    response: Response,
    ids: str = Query(min_length=1, max_length=2000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    parsed_ids: list[int] = []
    seen: set[int] = set()
    for raw in ids.split(","):
        try:
            media_id = int(raw.strip())
        except ValueError:
            continue
        if media_id <= 0 or media_id in seen:
            continue
        seen.add(media_id)
        parsed_ids.append(media_id)
        if len(parsed_ids) >= 120:
            break

    if not parsed_ids:
        return {"items": {}}

    stmt = (
        select(Movie)
        .join(PlexLibrary, Movie.library_id == PlexLibrary.id)
        .where(Movie.id.in_(parsed_ids), PlexLibrary.enabled.is_(True))
    )
    if user.role == Role.MEMBER:
        stmt = stmt.where(PlexLibrary.visible_to_members.is_(True))
    items = list(db.scalars(stmt).unique().all())

    direct_ids = [item.id for item in items if item.media_type in {"movie", "episode"}]
    direct_media = (
        list(db.scalars(select(MovieMedia).where(MovieMedia.movie_id.in_(direct_ids))).all())
        if direct_ids
        else []
    )
    media_by_id: dict[int, list[MovieMedia]] = defaultdict(list)
    for media in direct_media:
        media_by_id[media.movie_id].append(media)

    result: dict[str, dict] = {}
    for item in items:
        if item.media_type not in {"movie", "episode"}:
            continue
        versions = media_by_id.get(item.id, [])
        result[str(item.id)] = {
            "ready": any(_media_ready(version) for version in versions),
            "qualities": _ordered_qualities({str(version.resolution) for version in versions if version.resolution}),
        }

    shows = [item for item in items if item.media_type == "show"]
    if shows:
        show_keys = {item.rating_key for item in shows}
        library_ids = {item.library_id for item in shows}
        episodes = list(
            db.scalars(
                select(Movie).where(
                    Movie.media_type == "episode",
                    Movie.library_id.in_(library_ids),
                    Movie.grandparent_rating_key.in_(show_keys),
                )
            ).all()
        )
        episode_ids = [episode.id for episode in episodes]
        episode_media = (
            list(db.scalars(select(MovieMedia).where(MovieMedia.movie_id.in_(episode_ids))).all())
            if episode_ids
            else []
        )
        episode_versions: dict[int, list[MovieMedia]] = defaultdict(list)
        for media in episode_media:
            episode_versions[media.movie_id].append(media)

        episodes_by_show: dict[tuple[int, str], list[Movie]] = defaultdict(list)
        for episode in episodes:
            if episode.grandparent_rating_key:
                episodes_by_show[(episode.library_id, episode.grandparent_rating_key)].append(episode)

        for show in shows:
            show_episodes = episodes_by_show.get((show.library_id, show.rating_key), [])
            qualities: set[str] = set()
            ready_count = 0
            for episode in show_episodes:
                versions = episode_versions.get(episode.id, [])
                if any(_media_ready(version) for version in versions):
                    ready_count += 1
                qualities.update(str(version.resolution) for version in versions if version.resolution)
            result[str(show.id)] = {
                "ready": bool(show_episodes) and ready_count == len(show_episodes),
                "ready_count": ready_count,
                "episode_count": len(show_episodes),
                "qualities": _ordered_qualities(qualities),
            }

    response.headers["Cache-Control"] = "private, max-age=30, stale-while-revalidate=120"
    return {"items": result}
