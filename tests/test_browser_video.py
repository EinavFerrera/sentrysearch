"""Tests for sentrysearch.browser_video."""

import os
import subprocess
from pathlib import Path

import pytest

from sentrysearch.browser_video import (
    _resolve_ffprobe,
    path_for_browser_playback,
    video_needs_h264_transcode_for_browser,
)


@pytest.fixture
def mpeg4_video(tmp_path, ffmpeg_exe):
    p = tmp_path / "legacy.mp4"
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=10:duration=1",
            "-c:v",
            "mpeg4",
            "-q:v",
            "5",
            str(p),
        ],
        capture_output=True,
        check=True,
    )
    return p


def test_mpeg4_needs_transcode(mpeg4_video):
    assert video_needs_h264_transcode_for_browser(mpeg4_video) is True


def test_h264_yuv420p_no_transcode(tiny_video):
    assert video_needs_h264_transcode_for_browser(Path(tiny_video)) is False


@pytest.fixture
def large_h264_video(tmp_path, ffmpeg_exe):
    """H.264 wider than preview cap (needs scaled preview, not source file)."""
    p = tmp_path / "wide.mp4"
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x800:rate=10:duration=0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(p),
        ],
        capture_output=True,
        check=True,
    )
    return p


def test_large_h264_gets_cached_preview(large_h264_video, monkeypatch, tmp_path):
    if not _resolve_ffprobe():
        pytest.skip("ffprobe required to detect video dimensions")
    cache = tmp_path / "cache"
    cache.mkdir()
    import sentrysearch.browser_video as bv

    monkeypatch.setattr(bv, "_transcode_cache_dir", lambda: cache)
    out = path_for_browser_playback(large_h264_video)
    assert out.resolve() != large_h264_video.resolve()
    assert video_needs_h264_transcode_for_browser(out) is False


def test_path_for_browser_playback_creates_h264(mpeg4_video, monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()

    import sentrysearch.browser_video as bv

    monkeypatch.setattr(bv, "_transcode_cache_dir", lambda: cache)

    out = path_for_browser_playback(mpeg4_video)
    assert out.resolve() != mpeg4_video.resolve()
    assert out.suffix == ".mp4"
    assert out.stat().st_size > 100
    assert video_needs_h264_transcode_for_browser(out) is False

    out2 = path_for_browser_playback(mpeg4_video)
    assert out2 == out
