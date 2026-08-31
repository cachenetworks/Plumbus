from starlette.requests import Request

from app.api.playback_targets import _browser_direct_safe, _create_target
from app.models.models import Movie, MovieMedia, PlexLibrary, PlexServer, Role, User, UserStatus


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})


def _playable_movie(db, *, container="mp4", video_codec="h264", audio_codec="aac"):
    user = User(discord_id="target-user", username="target-user", role=Role.MEMBER, status=UserStatus.ACTIVE)
    db.add(user)
    server = PlexServer(base_url="mock", token_ciphertext="mock", enabled=True)
    db.add(server)
    db.flush()
    library = PlexLibrary(server_id=server.id, plex_key="1", title="Movies", library_type="movie", enabled=True)
    db.add(library)
    db.flush()
    movie = Movie(library_id=library.id, rating_key="target-movie", title="Target Movie", media_type="movie")
    db.add(movie)
    db.flush()
    media = MovieMedia(
        movie_id=movie.id,
        part_key="/library/parts/target/file.mp4",
        container=container,
        video_codec=video_codec,
        audio_codec=audio_codec,
        resolution="1080p",
        bitrate=8000,
        width=1920,
        height=1080,
    )
    db.add(media)
    db.commit()
    return user, movie, media


def test_browser_safe_media_detection(db):
    _user, _movie, media = _playable_movie(db)
    assert _browser_direct_safe(media, 20000) is True
    media.container = "mkv"
    assert _browser_direct_safe(media, 20000) is False


def test_browser_target_returns_same_origin_temporary_stream(db):
    user, movie, _media = _playable_movie(db)
    result = _create_target(movie.id, "browser", _request(), user, db)
    assert result["target"] == "browser"
    assert result["delivery"] == "progressive"
    assert result["playback_url"].startswith("http://localhost:8080/stream/")
    assert result["resume_position_ms"] == 0


def test_vrchat_target_returns_standalone_range_stream(db):
    user, movie, _media = _playable_movie(db)
    result = _create_target(movie.id, "vrchat", _request(), user, db)
    assert result["target"] == "vrchat"
    assert result["delivery"] == "progressive"
    assert result["vrchat_url"] == result["playback_url"]
    assert "AVPro" in result["compatibility"]
