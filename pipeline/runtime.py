"""Locate command-line dependencies without requiring an activated shell."""

import os
import sys
from pathlib import Path


def default_jobs_root() -> Path:
    """Keep installed-package output in a writable user directory."""
    return Path(os.environ.get("JOBS_ROOT", Path.home() / ".cache" / "music-tool" / "jobs"))


def configure_runtime() -> None:
    root = Path(__file__).resolve().parent.parent
    paths = [Path(sys.executable).parent]
    configured = os.environ.get("FFMPEG_BIN")
    if configured:
        candidate = Path(configured)
        paths.append(candidate.parent if candidate.is_file() else candidate)
    paths.append(root / ".runtime" / "ffmpeg")
    current = os.environ.get("PATH", "").split(os.pathsep)
    for path in reversed(paths):
        value = str(path)
        if path.is_dir() and value not in current:
            current.insert(0, value)
    os.environ["PATH"] = os.pathsep.join(current)
