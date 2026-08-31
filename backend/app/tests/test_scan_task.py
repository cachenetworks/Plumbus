from sqlalchemy import select

from app.models.models import Movie, PlexLibrary, PlexScanJob, PlexServer
from app.security.secrets import encrypt_secret
from app.workers.tasks import scan_library


def test_scan_task_indexes_movies(db) -> None:
    server = PlexServer(
        base_url="mock",
        token_ciphertext=encrypt_secret("mock"),
        server_name="Mock Plex",
        server_identifier="mock-server",
        enabled=True,
    )
    db.add(server)
    db.flush()

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

    job = PlexScanJob(
        library_id=library.id,
        mode="single_library",
        status="queued",
    )
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
    assert [movie.title for movie in movies] == [
        "Interstellar",
        "The Matrix",
        "Blade Runner 2049",
        "Dune",
        "The Dark Knight",
    ]
