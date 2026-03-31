"""Browser-friendly MP4 paths for HTML5 <video> playback.

Chrome, Firefox, and Safari expect H.264 (AVC) with 8-bit 4:2:0 in typical MP4
files. Legacy MPEG-4 Part 2 (``mp4v`` / ``mpeg4``), 10-bit H.264, etc. will
stream over HTTP but the element stays black.

We also cap resolution for in-app playback: a cached **preview** MP4 (H.264,
faststart) is built when the source needs a codec fix or is larger than the
preview bounds, so the Library / chat player loads faster.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from .chunker import _get_ffmpeg_executable
from .store import get_data_root

logger = logging.getLogger(__name__)

# Max dimensions for web preview (smaller = faster transcode + download).
_PREVIEW_MAX_W = 1280
_PREVIEW_MAX_H = 720
# Bump when transcode settings change so stale cache files are not reused.
_TRANSCODE_CACHE_TAG = "v4-preview720"
_PREVIEW_CRF = "28"


def _transcode_cache_dir() -> Path:
    root = get_data_root()
    d = (
        (root / "transcode_cache")
        if root
        else Path.home() / ".sentrysearch" / "transcode_cache"
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_ffprobe() -> str | None:
    p = shutil.which("ffprobe")
    if p:
        return p
    ffmpeg = Path(_get_ffmpeg_executable())
    for name in ("ffprobe", "ffprobe.exe"):
        cand = ffmpeg.parent / name
        if cand.is_file():
            return str(cand)
    return None


def _ffprobe_streams(path: Path, ffprobe: str) -> list[dict]:
    r = subprocess.run(
        [
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout).get("streams") or []
    except json.JSONDecodeError:
        return []


def _primary_video_dimensions(path: Path) -> tuple[int, int] | None:
    ffprobe = _resolve_ffprobe()
    if not ffprobe:
        return None
    streams = _ffprobe_streams(path, ffprobe)
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        return None
    try:
        w = int(v["width"])
        h = int(v["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return w, h


def _exceeds_preview_resolution(path: Path) -> bool:
    dim = _primary_video_dimensions(path)
    if dim is None:
        return False
    w, h = dim
    return w > _PREVIEW_MAX_W or h > _PREVIEW_MAX_H


def _needs_preview_mp4(path: Path) -> bool:
    """True if we should serve a derived low-res H.264 file for web playback."""
    return video_needs_h264_transcode_for_browser(path) or _exceeds_preview_resolution(
        path
    )


def _video_from_ffmpeg_stderr(path: Path) -> tuple[str | None, str | None]:
    """Parse ``Video:`` line from ``ffmpeg -i`` when ffprobe is unavailable."""
    ffmpeg_exe = _get_ffmpeg_executable()
    r = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    for line in r.stderr.splitlines():
        if "Video:" not in line or "Stream #" not in line:
            continue
        m = re.search(r"Video:\s*(\S+).*?,\s*(yuv[a-z0-9]+)", line)
        if m:
            return m.group(1), m.group(2)
        m2 = re.search(r"Video:\s*(\S+)", line)
        if m2:
            return m2.group(1), None
    return None, None


def _has_audio_stream_ffmpeg(path: Path) -> bool:
    ffmpeg_exe = _get_ffmpeg_executable()
    r = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return bool(re.search(r"Stream #\d+:\d+[^:]*:\s*Audio:", r.stderr))


def video_needs_h264_transcode_for_browser(path: Path) -> bool:
    """True if the first video stream is not plain 8-bit H.264."""
    path = path.resolve()
    if not path.is_file():
        return False

    ffprobe = _resolve_ffprobe()
    if ffprobe:
        streams = _ffprobe_streams(path, ffprobe)
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        if v is not None:
            if v.get("codec_name") != "h264":
                return True
            pf = (v.get("pix_fmt") or "").lower()
            return pf not in ("yuv420p", "yuvj420p")

    codec, pix = _video_from_ffmpeg_stderr(path)
    if codec is None:
        return False
    if codec != "h264":
        return True
    if pix is None:
        return False
    return pix.lower() not in ("yuv420p", "yuvj420p")


def path_for_browser_playback(source: Path) -> Path:
    """Return *source* or a cached low-res H.264 + yuv420p + faststart MP4 copy."""
    source = source.resolve()
    if not source.is_file():
        logger.warning("browser_video: source missing: %s", source)
        return source
    if not _needs_preview_mp4(source):
        logger.info(
            "browser_video: serving original (codec/size ok for preview): %s",
            source,
        )
        return source

    st = source.stat()
    key = hashlib.sha256(
        f"{_TRANSCODE_CACHE_TAG}:{source}:{st.st_mtime_ns}:{st.st_size}".encode()
    ).hexdigest()
    cache_dir = _transcode_cache_dir()
    out = cache_dir / f"{key}.mp4"
    if out.is_file() and out.stat().st_size > 1024:
        logger.info(
            "browser_video: preview cache hit bytes=%s path=%s (source=%s)",
            out.stat().st_size,
            out,
            source,
        )
        return out

    ffprobe = _resolve_ffprobe()
    if ffprobe:
        streams = _ffprobe_streams(source, ffprobe)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
    else:
        has_audio = _has_audio_stream_ffmpeg(source)

    ffmpeg_exe = _get_ffmpeg_executable()
    # Suffix must end in .mp4 so ffmpeg picks the MP4 muxer (not .part.<pid>).
    tmp = cache_dir / f"{key}.{os.getpid()}.transcoding.mp4"
    vf = (
        f"scale=w=min(iw\\,{_PREVIEW_MAX_W}):h=min(ih\\,{_PREVIEW_MAX_H}):"
        "force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    cmd: list[str] = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        _PREVIEW_CRF,
        "-profile:v",
        "high",
        "-level",
        "4.0",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd.append("-an")
    cmd.append(str(tmp))

    logger.info(
        "browser_video: transcoding preview (first play may take a while) source=%s -> %s",
        source,
        out,
    )
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            timeout=7200,
            text=True,
        )
        if not tmp.is_file() or tmp.stat().st_size < 1024:
            logger.error(
                "browser_video: transcode produced empty output tmp=%s", tmp
            )
            tmp.unlink(missing_ok=True)
            return source
        os.replace(tmp, out)
        if out.is_file():
            logger.info(
                "browser_video: transcode done bytes=%s path=%s",
                out.stat().st_size,
                out,
            )
            return out
        logger.error("browser_video: replace succeeded but output missing: %s", out)
        return source
    except subprocess.CalledProcessError as e:
        err_tail = (e.stderr or "")[-4000:]
        logger.error(
            "browser_video: ffmpeg failed rc=%s source=%s stderr_tail=%r",
            e.returncode,
            source,
            err_tail,
        )
        tmp.unlink(missing_ok=True)
        return source
    except Exception:
        logger.exception("browser_video: transcode failed source=%s", source)
        tmp.unlink(missing_ok=True)
        return source
