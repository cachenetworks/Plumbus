from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.models import Movie, MovieMedia, MovieTag, PlexLibrary, PlexScanJob
from app.services.plex.service import PlexService
from app.services.plex.show_scan import iter_show_library


class PlexScanner:
    def __init__(self, db: Session, plex: PlexService | None = None):
        self.db = db
        self.plex = plex

    def scan_library(
        self,
        library: PlexLibrary,
        job: PlexScanJob,
        target_movie_id: int | None = None,
    ) -> PlexScanJob:
        job.status = "running"
        job.started_at = datetime.now(UTC)
        self.db.commit()

        target_movie = self.db.get(Movie, target_movie_id) if target_movie_id else None
        if target_movie_id and (not target_movie or target_movie.library_id != library.id):
            job.status = "failed"
            job.finished_at = datetime.now(UTC)
            job.last_error = "Target media is unavailable or belongs to a different library"
            self.db.commit()
            return job

        plex = self.plex or PlexService.for_library(self.db, library)
        connection = plex.connect()
        if not connection.connected:
            job.status = "failed"
            job.finished_at = datetime.now(UTC)
            job.last_error = connection.error or "Unable to reach the Plex server that owns this library"
            self.db.commit()
            return job

        seen_rating_keys: set[str] = set()
        try:
            rows = (
                iter_show_library(plex, library.plex_key)
                if library.library_type == "show" and not target_movie
                else plex.iter_items(library.plex_key, library.library_type)
            )
            for payload in rows:
                if target_movie and payload["rating_key"] != target_movie.rating_key:
                    continue

                seen_rating_keys.add(payload["rating_key"])
                media_item = self.db.scalar(
                    select(Movie).where(
                        Movie.library_id == library.id,
                        Movie.rating_key == payload["rating_key"],
                    )
                )
                created = media_item is None
                if media_item is None:
                    media_item = Movie(
                        library_id=library.id,
                        rating_key=payload["rating_key"],
                        title=payload["title"],
                        media_type=payload.get("media_type") or "movie",
                    )
                    self.db.add(media_item)
                    self.db.flush()

                for field in (
                    "media_type",
                    "parent_rating_key",
                    "grandparent_rating_key",
                    "parent_title",
                    "grandparent_title",
                    "season_number",
                    "episode_number",
                    "title",
                    "original_title",
                    "year",
                    "summary",
                    "tagline",
                    "content_rating",
                    "duration_ms",
                    "studio",
                    "rating",
                    "audience_rating",
                    "edition_title",
                    "poster_key",
                    "art_key",
                ):
                    if field in payload:
                        setattr(media_item, field, payload.get(field))
                media_item.added_at = payload.get("added_at")
                media_item.plex_updated_at = payload.get("updated_at")
                media_item.indexed_at = datetime.now(UTC)

                self.db.execute(delete(MovieMedia).where(MovieMedia.movie_id == media_item.id))
                for media in payload.get("media", []):
                    self.db.add(MovieMedia(movie_id=media_item.id, **media))

                self.db.execute(delete(MovieTag).where(MovieTag.movie_id == media_item.id))
                for kind, key in (
                    ("genre", "genres"),
                    ("director", "directors"),
                    ("actor", "actors"),
                    ("writer", "writers"),
                    ("collection", "collections"),
                    ("label", "labels"),
                ):
                    for value in payload.get(key, []) or []:
                        self.db.add(MovieTag(movie_id=media_item.id, kind=kind, value=value))

                job.items_scanned += 1
                if created:
                    job.items_added += 1
                else:
                    job.items_updated += 1

            if target_movie:
                if target_movie.rating_key not in seen_rating_keys:
                    self.db.delete(target_movie)
                    job.items_removed += 1
            else:
                existing = self.db.scalars(select(Movie).where(Movie.library_id == library.id)).all()
                for media_item in existing:
                    if media_item.rating_key not in seen_rating_keys:
                        self.db.delete(media_item)
                        job.items_removed += 1

            library.last_scan_at = datetime.now(UTC)
            job.status = "completed"
            job.finished_at = datetime.now(UTC)
            self.db.commit()
            return job
        except Exception as exc:
            self.db.rollback()
            failed_job = self.db.get(PlexScanJob, job.id)
            if failed_job:
                failed_job.status = "failed"
                failed_job.finished_at = datetime.now(UTC)
                failed_job.last_error = plex._safe_error(exc)[:4000]
                self.db.commit()
                return failed_job
            raise
