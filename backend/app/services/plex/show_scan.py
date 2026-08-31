from __future__ import annotations

from typing import Any

from plexapi.server import PlexServer as PlexApiServer

from app.services.plex.service import PlexService


def _item_type(item: Any) -> str:
    return str(getattr(item, "TYPE", None) or getattr(item, "type", None) or "").lower()


def _season_payload_from_episode(episode: Any, show_key: str, show_title: str) -> dict[str, Any]:
    season_number = getattr(episode, "parentIndex", None)
    raw_season_key = getattr(episode, "parentRatingKey", None)
    season_key = str(raw_season_key) if raw_season_key is not None else f"{show_key}:season:{season_number or 0}"
    season_title = getattr(episode, "parentTitle", None) or (
        "Specials" if season_number == 0 else f"Season {season_number}" if season_number is not None else "Season"
    )
    return {
        "rating_key": season_key,
        "media_type": "season",
        "parent_rating_key": show_key,
        "grandparent_rating_key": None,
        "parent_title": show_title,
        "grandparent_title": None,
        "season_number": season_number,
        "episode_number": None,
        "title": season_title,
        "original_title": None,
        "year": getattr(episode, "parentYear", None),
        "summary": None,
        "tagline": None,
        "content_rating": None,
        "duration_ms": None,
        "studio": None,
        "rating": None,
        "audience_rating": None,
        "edition_title": None,
        "genres": [],
        "directors": [],
        "actors": [],
        "writers": [],
        "collections": [],
        "labels": [],
        "poster_key": getattr(episode, "parentThumb", None),
        "art_key": getattr(episode, "grandparentArt", None),
        "added_at": None,
        "updated_at": None,
        "media": [],
    }


def iter_show_library(plex: PlexService, library_key: str) -> list[dict[str, Any]]:
    """Return shows, synthesized seasons, and playable episodes for a Plex show library.

    PlexAPI's Show.episodes() uses /library/metadata/<show>/allLeaves, which is
    considerably more reliable than recursively asking every Season object for
    its children. Episode parent metadata is sufficient to reconstruct seasons,
    including Specials (season 0) and anime libraries with unusual layouts.
    """
    server = PlexApiServer(plex.base_url, plex.token, timeout=30)
    section = server.library.sectionByID(int(library_key))
    rows: list[dict[str, Any]] = []

    for show in section.all():
        if _item_type(show) != "show":
            continue

        show_key = str(show.ratingKey)
        show_title = show.title
        rows.append(plex._payload(show, "show"))

        seasons: dict[str, dict[str, Any]] = {}
        for episode in show.episodes():
            season_number = getattr(episode, "parentIndex", None)
            raw_season_key = getattr(episode, "parentRatingKey", None)
            season_key = str(raw_season_key) if raw_season_key is not None else f"{show_key}:season:{season_number or 0}"
            seasons.setdefault(season_key, _season_payload_from_episode(episode, show_key, show_title))

            rows.append(
                plex._payload(
                    episode,
                    "episode",
                    parent_rating_key=season_key,
                    grandparent_rating_key=str(getattr(episode, "grandparentRatingKey", None) or show_key),
                    parent_title=getattr(episode, "parentTitle", None) or seasons[season_key]["title"],
                    grandparent_title=getattr(episode, "grandparentTitle", None) or show_title,
                    season_number=season_number,
                    episode_number=getattr(episode, "index", None),
                )
            )

        rows.extend(
            sorted(
                seasons.values(),
                key=lambda season: (
                    season.get("season_number") is None,
                    season.get("season_number") if season.get("season_number") is not None else 999999,
                    season.get("title") or "",
                ),
            )
        )

    return rows
