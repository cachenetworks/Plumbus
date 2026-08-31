from types import SimpleNamespace

from app.services.plex.service import PlexService
from app.services.plex import show_scan


def _base(**kwargs):
    defaults = {
        "originalTitle": None,
        "year": 2026,
        "summary": None,
        "tagline": None,
        "contentRating": None,
        "duration": None,
        "studio": None,
        "rating": None,
        "audienceRating": None,
        "editionTitle": None,
        "genres": [],
        "directors": [],
        "roles": [],
        "writers": [],
        "collections": [],
        "labels": [],
        "thumb": None,
        "art": None,
        "addedAt": None,
        "updatedAt": None,
        "media": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_show_scan_uses_all_leaves_episode_parent_metadata(monkeypatch) -> None:
    part = SimpleNamespace(key="/library/parts/9001/file.mkv", file="/media/Anime/Show/S01E01.mkv", size=1234)
    media = SimpleNamespace(
        id=7001,
        parts=[part],
        videoDynamicRange=None,
        container="mkv",
        videoCodec="h264",
        audioCodec="aac",
        width=1920,
        height=1080,
        videoResolution="1080",
        bitrate=8000,
        audioChannels=2,
    )
    episode = _base(
        TYPE="episode",
        type="episode",
        ratingKey=9001,
        title="The Signal",
        duration=1440000,
        parentIndex=1,
        index=1,
        parentRatingKey=8001,
        grandparentRatingKey=8000,
        parentTitle="Season 1",
        grandparentTitle="Neon Ronin",
        parentYear=2026,
        parentThumb="/library/metadata/8001/thumb/1",
        grandparentArt="/library/metadata/8000/art/1",
        media=[media],
    )

    class FakeShow:
        TYPE = "show"
        type = "show"
        ratingKey = 8000
        title = "Neon Ronin"
        originalTitle = None
        year = 2026
        summary = "Anime fixture"
        tagline = None
        contentRating = None
        duration = None
        studio = None
        rating = None
        audienceRating = None
        editionTitle = None
        genres = [SimpleNamespace(tag="Anime")]
        directors = []
        roles = []
        writers = []
        collections = []
        labels = []
        thumb = "/library/metadata/8000/thumb/1"
        art = "/library/metadata/8000/art/1"
        addedAt = None
        updatedAt = None
        media = []

        def episodes(self):
            return [episode]

    class FakeSection:
        def all(self):
            return [FakeShow()]

    class FakeLibrary:
        def sectionByID(self, library_id):
            assert library_id == 2
            return FakeSection()

    class FakeServer:
        def __init__(self, base_url, token, timeout):
            assert base_url == "http://plex.local:32400"
            assert token == "server-token"
            assert timeout == 30
            self.library = FakeLibrary()

    monkeypatch.setattr(show_scan.settings, "MOCK_PLEX", False)
    monkeypatch.setattr(show_scan, "PlexApiServer", FakeServer)

    rows = show_scan.iter_show_library(PlexService("http://plex.local:32400", "server-token"), "2")
    by_type = {row["media_type"]: row for row in rows}

    assert set(by_type) == {"show", "season", "episode"}
    assert by_type["show"]["title"] == "Neon Ronin"
    assert by_type["season"]["rating_key"] == "8001"
    assert by_type["season"]["season_number"] == 1
    assert by_type["episode"]["grandparent_rating_key"] == "8000"
    assert by_type["episode"]["parent_rating_key"] == "8001"
    assert by_type["episode"]["episode_number"] == 1
    assert by_type["episode"]["media"][0]["part_key"] == "/library/parts/9001/file.mkv"
