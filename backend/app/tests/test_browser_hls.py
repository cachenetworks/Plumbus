from urllib.parse import parse_qs, urlparse

from app.api.playback import _delivery_for_target
from app.api.playback_web import (
    _build_browser_audio_url,
    _build_browser_copy_url,
    _build_browser_transcode_url,
    _rewrite_browser_playlist,
)
from app.models.models import MovieMedia
from app.services.playback.service import PlaybackService
from app.services.plex.audio_tracks import choose_direct_browser_audio_track
from app.services.plex.service import PlexService


def test_browser_transcode_forces_h264_aac_profile():
    plex = PlexService("http://plex:32400", "secret-token")
    url = _build_browser_transcode_url(
        plex,
        "12345",
        max_video_bitrate=12000,
        video_resolution="1920x1080",
    )
    query = parse_qs(urlparse(url).query)

    assert query["protocol"] == ["hls"]
    assert query["directPlay"] == ["0"]
    assert query["directStream"] == ["0"]
    assert query["directStreamAudio"] == ["0"]
    assert query["X-Plex-Client-Profile-Name"] == ["Chrome"]
    profile = query["X-Plex-Client-Profile-Extra"][0]
    assert "container=mpegts" in profile
    assert "videoCodec=h264" in profile
    assert "audioCodec=aac" in profile


def test_legacy_audio_fix_prefers_video_copy_and_aac_audio():
    plex = PlexService("http://plex:32400", "secret-token")
    media = MovieMedia(
        container="mkv",
        video_codec="h264",
        audio_codec="eac3",
        bitrate=18000,
    )
    url = _build_browser_audio_url(plex, "12345", media)
    query = parse_qs(urlparse(url).query)

    assert query["protocol"] == ["hls"]
    assert query["directPlay"] == ["0"]
    assert query["directStream"] == ["1"]
    assert query["directStreamAudio"] == ["0"]
    assert int(query["maxVideoBitrate"][0]) > media.bitrate


def test_alternate_audio_direct_stream_copies_both_tracks_and_exact_version():
    plex = PlexService("http://plex:32400", "secret-token")
    media = MovieMedia(
        container="mkv",
        video_codec="h264",
        audio_codec="dts",
        bitrate=18000,
    )
    track = {
        "id": "77",
        "codec": "aac",
        "media_index": 2,
        "part_index": 1,
    }
    url = _build_browser_copy_url(plex, "12345", media, track)
    query = parse_qs(urlparse(url).query)

    assert query["protocol"] == ["hls"]
    assert query["mediaIndex"] == ["2"]
    assert query["partIndex"] == ["1"]
    assert query["audioStreamID"] == ["77"]
    assert query["directPlay"] == ["0"]
    assert query["directStream"] == ["1"]
    assert query["directStreamAudio"] == ["1"]
    assert int(query["maxVideoBitrate"][0]) > media.bitrate
    profile = query["X-Plex-Client-Profile-Extra"][0]
    assert "videoCodec=h264" in profile
    assert "audioCodec=aac" in profile


def test_direct_audio_selection_preserves_primary_language_and_avoids_commentary():
    tracks = [
        {
            "id": "10",
            "codec": "dts",
            "language": "English",
            "language_code": "eng",
            "selected": True,
            "default": True,
            "channels": 6,
            "title": None,
        },
        {
            "id": "11",
            "codec": "aac",
            "language": "Japanese",
            "language_code": "jpn",
            "selected": False,
            "default": False,
            "channels": 2,
            "title": None,
        },
        {
            "id": "12",
            "codec": "aac",
            "language": "English",
            "language_code": "eng",
            "selected": False,
            "default": False,
            "channels": 2,
            "title": "Director Commentary",
        },
        {
            "id": "13",
            "codec": "aac",
            "language": "English",
            "language_code": "eng",
            "selected": False,
            "default": False,
            "channels": 2,
            "title": None,
        },
    ]

    selected = choose_direct_browser_audio_track(tracks, video_codec="h264")
    assert selected is not None
    assert selected["id"] == "13"
    assert choose_direct_browser_audio_track(tracks, video_codec="hevc") is None


def test_browser_hls_playlist_uses_same_origin_proxy_paths():
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"http://plex:32400/key\"\n"
        "segment-001.ts\n"
    )
    rewritten = _rewrite_browser_playlist(
        playlist,
        "http://plex:32400/video/:/transcode/session/master.m3u8",
        "temporary-token",
    )

    assert "http://plex:32400" not in rewritten
    assert "https://" not in rewritten
    assert rewritten.count("/stream/temporary-token/hls/") == 2


def test_browser_native_requires_browser_safe_audio():
    aac = MovieMedia(container="mp4", video_codec="h264", audio_codec="aac")
    ac3 = MovieMedia(container="mp4", video_codec="h264", audio_codec="ac3")
    eac3 = MovieMedia(container="mp4", video_codec="h264", audio_codec="eac3")
    hevc = MovieMedia(container="mp4", video_codec="hevc", audio_codec="aac")

    assert PlaybackService.browser_native_media(aac) is True
    assert PlaybackService.browser_native_media(ac3) is False
    assert PlaybackService.browser_native_media(eac3) is False
    assert PlaybackService.browser_native_media(hevc) is False


def test_browser_direct_mode_always_uses_original_progressive_stream():
    media_info = {
        "browser_native_candidate": False,
        "allow_plex_transcoding": True,
        "direct_play_candidate": False,
    }
    assert _delivery_for_target("browser", media_info, browser_mode="direct") == "progressive"


def test_browser_compatibility_mode_uses_transcoder_only_as_fallback():
    media_info = {
        "browser_native_candidate": False,
        "allow_plex_transcoding": True,
        "direct_play_candidate": False,
    }
    assert _delivery_for_target("browser", media_info, browser_mode="compatibility") == "hls"
