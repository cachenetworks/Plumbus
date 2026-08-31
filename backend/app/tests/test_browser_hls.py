from urllib.parse import parse_qs, urlparse

from app.api.playback import _delivery_for_target
from app.api.playback_web import (
    _build_browser_audio_url,
    _build_browser_transcode_url,
    _needs_audio_compat,
    _rewrite_browser_playlist,
)
from app.models.models import MovieMedia
from app.services.playback.service import PlaybackService
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


def test_browser_audio_fix_prefers_video_copy_and_aac_audio():
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
    profile = query["X-Plex-Client-Profile-Extra"][0]
    assert "videoCodec=h264,hevc" in profile
    assert "audioCodec=aac" in profile


def test_silent_browser_audio_codecs_trigger_audio_fix():
    base = {
        "direct_play_candidate": True,
        "allow_plex_transcoding": True,
    }
    assert _needs_audio_compat({**base, "audio_codec": "ac3"}) is True
    assert _needs_audio_compat({**base, "audio_codec": "eac3"}) is True
    assert _needs_audio_compat({**base, "audio_codec": "dts"}) is True
    assert _needs_audio_compat({**base, "audio_codec": "truehd"}) is True
    assert _needs_audio_compat({**base, "audio_codec": "aac"}) is False
    assert _needs_audio_compat({**base, "audio_codec": "mp3"}) is False
    assert _needs_audio_compat({**base, "audio_codec": ""}) is False
    assert _needs_audio_compat({**base, "audio_codec": "ac3", "allow_plex_transcoding": False}) is False


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
