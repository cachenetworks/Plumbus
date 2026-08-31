from app.api.playback import _rewrite_playlist
from app.services.plex.service import PlexService


def test_plex_transcode_uses_hls_and_is_server_side():
    plex = PlexService("http://plex:32400", "super-secret-plex-token")
    url = plex.get_transcode_url("123", max_video_bitrate=8000, video_resolution="1920x1080")
    assert "start.m3u8" in url
    assert "protocol=hls" in url
    assert "maxVideoBitrate=8000" in url


def test_rewritten_hls_playlist_does_not_expose_plex_token():
    raw_token = "temporary-playback-token"
    plex_token = "super-secret-plex-token"
    public_url = "https://cinema.example.test"
    upstream = (
        "#EXTM3U\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"http://plex:32400/key?X-Plex-Token="
        + plex_token
        + "\"\n"
        "http://plex:32400/video/segment.ts?X-Plex-Token="
        + plex_token
        + "\n"
    )
    rewritten = _rewrite_playlist(
        upstream,
        "http://plex:32400/master.m3u8",
        raw_token,
        public_url,
    )
    assert plex_token not in rewritten
    assert "X-Plex-Token" not in rewritten
    assert f"{public_url}/stream/{raw_token}/hls/" in rewritten
