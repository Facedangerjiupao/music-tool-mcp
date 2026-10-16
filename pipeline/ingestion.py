"""
Stage 1 — Ingestion & Validation
Validates that the URL is a supported source and reachable.
"""

import os
import re
import json
import subprocess
import math
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from pipeline.runtime import configure_runtime

configure_runtime()


SUPPORTED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "music.youtube.com",
    "m.youtube.com",
}

SUPPORTED_AUDIO_EXTS = {"mp3", "wav", "flac", "ogg", "m4a", "aac", "opus", "aiff", "aif", "wma"}
SUPPORTED_VIDEO_EXTS = {"mp4", "mov", "mkv", "webm", "avi", "m4v", "ts", "mpeg", "mpg"}


def validate_url_format(url: str) -> None:
    """
    Synchronous HTTP(S) URL check — actual site support is resolved by yt-dlp.
    Raises ValueError for malformed URLs or unsupported protocols.
    Called upfront by get_sonic_signature before spawning the pipeline task.
    """
    parsed = urlparse(url)

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid URL format: {url!r}")
    if parsed.username or parsed.password or any(c.isspace() for c in parsed.netloc):
        raise ValueError("Invalid URL format: credentials and whitespace in host are unsupported")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL format: {url!r}") from exc


def validate_source(url: str) -> dict:
    """
    Validates the URL and probes metadata via yt-dlp.
    Returns a dict with title, duration, uploader, and thumbnail.
    Raises ValueError for unsupported or unreachable sources.
    """
    validate_url_format(url)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "socket_timeout": 30,
        # Resolve watch?v=X&list=RD...&start_radio=1 to the single video X instead
        # of following the playlist/radio (which lands on a different, often
        # unavailable, track). Browser-copied YouTube URLs usually carry these.
        "noplaylist": True,
        "skip_download": True,
    }

    proxy_url = os.environ.get("YTDLP_PROXY")
    if proxy_url:
        ydl_opts["proxy"] = proxy_url

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            meta = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"yt-dlp failed to probe '{url}': {exc}") from exc

    if meta is None:
        raise RuntimeError(f"yt-dlp returned no metadata for '{url}'")
    if meta.get("_type") in {"playlist", "multi_video"}:
        raise ValueError("Use a single media item URL, not a playlist.")
    if meta.get("is_live"):
        raise ValueError("Live streams are unsupported; use a finished recording.")

    duration = meta.get("duration", 0)
    if duration and duration > 3600:
        raise ValueError(
            f"Track duration {duration}s exceeds 60-minute limit. "
            "Please use a shorter clip."
        )

    categories = meta.get("categories") or []
    genre_hint = categories[0] if categories else None

    return {
        "title": meta.get("title", "Unknown"),
        "duration_sec": duration,
        "uploader": meta.get("uploader", "Unknown"),
        "thumbnail": meta.get("thumbnail"),
        "webpage_url": meta.get("webpage_url", url),
        "extractor": meta.get("extractor", "unknown"),
        "genre_hint": genre_hint,
        "source_type": "url",
    }


def validate_file_path(path: str) -> None:
    p = Path(path)
    if not p.exists():
        raise ValueError(f"File does not exist: {path}")
    if not p.is_file():
        raise ValueError(f"Not a regular file: {path}")
    
    ext = p.suffix.lower().lstrip(".")
    if ext not in SUPPORTED_AUDIO_EXTS | SUPPORTED_VIDEO_EXTS:
        raise ValueError(f"Unsupported media extension: {ext}")


def validate_file_source(path: str) -> dict:
    validate_file_path(path)
    try:
        max_dur = float(os.environ.get("FILE_MAX_DURATION_SEC", "600"))
    except ValueError:
        max_dur = 600.0

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=codec_type",
        "-of", "json",
        str(Path(path).resolve())
    ]
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                             check=True, timeout=30)
        probe = json.loads(res.stdout)
        duration = float(probe.get("format", {}).get("duration", 0))
    except Exception as exc:
        raise RuntimeError(f"Failed to probe file duration via ffprobe: {exc}") from exc

    if not any(s.get("codec_type") == "audio" for s in probe.get("streams", [])):
        raise ValueError("Media contains no audio stream.")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Media duration must be finite and greater than zero.")
    if duration > max_dur:
        raise ValueError(f"Duration {duration}s exceeds {max_dur}s limit.")

    return {
        "title": Path(path).stem,
        "uploader": "local file",
        "duration_sec": duration,
        "thumbnail": None,
        "webpage_url": None,
        "source_path": str(Path(path).resolve()),
        "extractor": "local",
        "source_type": "file",
        "genre_hint": None,
    }


def save_metadata(job_dir: Path, info: dict) -> None:
    """
    Atomically persists source metadata to disk alongside the stems.
    Uses a .tmp → rename pattern to prevent partial-write corruption.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / "metadata.json"
    tmp = job_dir / "metadata.json.tmp"
    try:
        tmp.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)  # atomic on POSIX; near-atomic on Windows NTFS
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def load_metadata(job_dir: Path) -> dict | None:
    """
    Loads cached source metadata from disk.
    Returns None if the file does not exist or is malformed.
    """
    path = job_dir / "metadata.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _is_direct_audio(url: str) -> bool:
    """Allow direct .mp3/.wav/.flac/.ogg URLs."""
    return bool(re.search(r"\.(mp3|wav|flac|ogg|m4a|aac)(\?.*)?$", url, re.IGNORECASE))
