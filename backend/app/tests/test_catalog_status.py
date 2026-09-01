from datetime import UTC, datetime, timedelta

from app.models.models import Movie, MovieMedia, PlexLibrary, PlexServer, Role, Session as UserSession, User, UserStatus
from app.security.security import random_token, token_hash


def _login(db, client):
    user = User(discord_id="catalog-status-user", username="catalog-user", role=Role.MEMBER, status=UserStatus.ACTIVE)
    db.add(user)
    db.flush()
    raw = random_token(32)
    db.add(UserSession(session_hash=token_hash(raw), user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=1)))
    db.commit()
    client.cookies.set("plumbus_session", raw)
    return user


def test_catalog_status_marks_movies_and_only_fully_ready_series(client, db):
    _login(db, client)
    server = PlexServer(id=1, base_url="mock", token_ciphertext="mock")
    db.add(server)
    db.flush()
    library = PlexLibrary(
        server_id=server.id,
        plex_key="1",
        title="Mixed Library",
        library_type="show",
        enabled=True,
        visible_to_members=True,
    )
    db.add(library)
    db.flush()

    movie = Movie(library_id=library.id, rating_key="movie-1", title="Ready Movie", media_type="movie")
    show = Movie(library_id=library.id, rating_key="show-1", title="Mixed Show", media_type="show")
    db.add_all([movie, show])
    db.flush()
    db.add(MovieMedia(movie_id=movie.id, part_key="/movie.mp4", container="mp4", video_codec="h264", audio_codec="aac", resolution="1080p"))

    episode_one = Movie(
        library_id=library.id,
        rating_key="ep-1",
        title="Episode 1",
        media_type="episode",
        grandparent_rating_key=show.rating_key,
        season_number=1,
        episode_number=1,
    )
    episode_two = Movie(
        library_id=library.id,
        rating_key="ep-2",
        title="Episode 2",
        media_type="episode",
        grandparent_rating_key=show.rating_key,
        season_number=1,
        episode_number=2,
    )
    db.add_all([episode_one, episode_two])
    db.flush()
    db.add_all(
        [
            MovieMedia(movie_id=episode_one.id, part_key="/ep1.mp4", container="mp4", video_codec="avc", audio_codec="aac", resolution="1080p"),
            MovieMedia(movie_id=episode_two.id, part_key="/ep2.mkv", container="mkv", video_codec="hevc", audio_codec="aac", resolution="4K"),
        ]
    )
    db.commit()

    response = client.get(f"/api/catalog/status?ids={movie.id},{show.id}")
    assert response.status_code == 200
    statuses = response.json()["items"]
    assert statuses[str(movie.id)]["ready"] is True
    assert statuses[str(movie.id)]["qualities"] == ["1080p"]
    assert statuses[str(show.id)]["ready"] is False
    assert statuses[str(show.id)]["ready_count"] == 1
    assert statuses[str(show.id)]["episode_count"] == 2
    assert statuses[str(show.id)]["qualities"] == ["4K", "1080p"]
