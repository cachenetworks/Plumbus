from sqlalchemy import select

from app.models.models import Movie, MovieMedia, PlexLibrary, PlexScanJob, PlexServer
from app.security.secrets import encrypt_secret
from app.services.plex.scanner import _unique_tags
from app.workers.tasks import scan_library


def _server(db) -> PlexServer:
    server = PlexServer(
        base_url="mock",
        token_ciphertext=encrypt_secret("mock"),
        server_name="Mock Plex",
        server_identifier="mock-server",
        enabled=True,
    )
    db.add(server)
    db.flush()
    return server


def test_duplicate_plex_tags_are_sanitized_before_insert() -> None:
    payload = {
        "genres": ["Anime", "Anime", " anime ", ""],
        "actors": ["Mark Duncan", "Mark Duncan", " mark duncan ", None],
        "directors": [],
        "writers": [],
        "collections": [],
        "labels": [],
    }

    assert _unique_tags(payload) == [
        ("genre", "Anime"),
        ("actor", "Mark Duncan"),
    ]


def test_scan_task_indexes_movies(db) -> None:
    server = _server(db)
    library = PlexLibrary(
        server_id=server.id,
        plex_key="1",
        title="Movies",
        library_type="movie",
        enabled=True,
        visible_to_members=True,
    )
    db.add(library)
    db.flush()

    job = PlexScanJob(library_id=library.id, mode="single_library", status="queued")
    db.add(job)
    db.commit()

    result = scan_library.run(job.id)

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["items_scanned"] == 5
    assert result["items_added"] == 5

    db.expire_all()
    refreshed = db.get(PlexScanJob, job.id)
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.last_error is None
    assert refreshed.items_scanned == 5

    movies = db.scalars(select(Movie).where(Movie.library_id == library.id).order_by(Movie.rating_key)).all()
    assert {movie.media_type for movie in movies} == {"movie"}
    assert [movie.title for movie in movies] == [
        "Interstellar",
        "The Matrix",
        "Blade Runner 2049",
        "Dune",
        "The Dark Knight",
    ]


def test_scan_task_indexes_show_seasons_and_playable_episodes(db) -> None:
    server = _server(db)
    library = PlexLibrary(
        server_id=server.id,
        plex_key="2",
        title="Anime",
        library_type="show",
        enabled=True,
        visible_to_members=True,
    )
    db.add(library)
    db.flush()

    job = PlexScanJob(library_id=library.id, mode="single_library", status="queued")
    db.add(job)
    db.commit()

    result = scan_library.run(job.id)

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["items_scanned"] == 5

    rows = db.scalars(select(Movie).where(Movie.library_id == library.id).order_by(Movie.rating_key)).all()
    assert [row.media_type for row in rows] == ["show", "season", "episode", "episode", "episode"]

    show = next(row for row in rows if row.media_type == "show")
    season = next(row for row in rows if row.media_type == "season")
    episodes = [row for row in rows if row.media_type == "episode"]

    assert show.title == "Neon Ronin"
    assert season.parent_rating_key == show.rating_key
    assert season.season_number == 1
    assert [episode.episode_number for episode in episodes] == [1, 2, 3]
    assert all(episode.grandparent_rating_key == show.rating_key for episode in episodes)
    assert all(episode.parent_rating_key == season.rating_key for episode in episodes)
    assert all(
        db.scalar(select(MovieMedia).where(MovieMedia.movie_id == episode.id).limit(1)) is not None
        for episode in episodes
    )
