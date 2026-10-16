"""Workflow extension of Audio Sonic MCP, reusing its ingestion and pipeline stages."""

import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from pipeline.converter import convert_to_wav
from pipeline.downloader import download_audio
from pipeline.ingestion import (save_metadata, validate_file_path, validate_file_source,
                                validate_source, validate_url_format)
from pipeline.timeline import analyze_timeline, validate_timeline_options


def validate_job_id(job_id: str) -> None:
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", job_id) or job_id.upper() in reserved:
        raise ValueError("job_id must be 1-64 letters, digits, underscores or hyphens, starting with a letter/digit; device names are forbidden")


def validate_request(source: str, mode: str, media_type: str, beat_engine: str, fps: float) -> bool:
    if mode not in {"extract", "rhythm", "full"}:
        raise ValueError("mode must be extract, rhythm or full")
    if media_type not in {"audio", "video"}:
        raise ValueError("media_type must be audio or video")
    validate_timeline_options(beat_engine, fps)
    is_url = urlparse(source).scheme.lower() in {"http", "https"}
    if is_url:
        validate_url_format(source)
    else:
        validate_file_path(source)
    return is_url


def create_job_directory(root: Path, job_id: str) -> Path:
    validate_job_id(job_id)
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / job_id
    if path.resolve().parent != root:
        raise ValueError("Job directory must stay inside JOBS_ROOT")
    try:
        path.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"Job '{job_id}' already exists; use a new job_id") from exc
    return path


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def run_media_workflow(source: str, job_dir: Path, mode: str = "rhythm", media_type: str = "audio",
                       beat_engine: str = "librosa", fps: float = 30.0, keep_media: bool = True,
                       separate: bool = False) -> dict:
    """Return raw features and artifact paths. Never generate a prose report.

    Caller reserves a fresh job directory with create_job_directory. Results and
    metadata persist even when intermediate media retention is disabled.
    """
    started = time.perf_counter()
    is_url = validate_request(source, mode, media_type, beat_engine, fps)
    metadata = validate_source(source) if is_url else validate_file_source(source)
    save_metadata(job_dir, metadata)
    raw_path = (download_audio(source, job_dir.name, job_dir.parent, media_type=media_type)
                if is_url else Path(source).resolve())
    wav_path = convert_to_wav(raw_path, out_dir=job_dir)
    # Probe the decoded audio too: URL metadata can be missing or wrong.
    import soundfile as sf
    info = sf.info(str(wav_path))
    duration = info.frames / info.samplerate
    max_duration = float(os.environ.get("WORKFLOW_MAX_DURATION_SEC", "600"))
    if not math.isfinite(max_duration) or max_duration <= 0:
        raise ValueError("WORKFLOW_MAX_DURATION_SEC must be a positive finite number")
    if duration <= 0 or duration > max_duration:
        raise ValueError(f"Decoded duration {duration:.2f}s must be between 0 and {max_duration}s")
    metadata["duration_sec"] = duration
    save_metadata(job_dir, metadata)
    result_path = (job_dir / "result.json").resolve()
    payload = {"schema_version": "1.0", "job_id": job_dir.name, "status": "success", "mode": mode,
               "source": metadata, "artifacts": {"result_json": str(result_path)},
               "engines": {"base_project": "audio-sonic-mcp", "media": "yt-dlp+ffmpeg" if is_url else "ffmpeg"}}
    warnings = []
    stems_dir = None
    if separate:
        from pipeline.separator import separate_stems
        stems_dir = separate_stems(wav_path, full_song=True)
        if stems_dir is None:
            raise RuntimeError("Stem separation was requested but Demucs is unavailable or failed. Install .[clap] or call with separate=false.")
    if mode != "extract":
        drums = stems_dir / "drums.wav" if stems_dir else None
        payload["timeline"] = analyze_timeline(wav_path, beat_engine=beat_engine, fps=fps, drums_path=drums)
        payload["engines"]["timeline"] = beat_engine
    if mode == "full":
        from pipeline.analyzer import analyze_audio
        from pipeline.assembler import assemble_payload
        features = analyze_audio(wav_path, stems_dir=stems_dir, full_song=True)
        signature = assemble_payload(job_dir.name, features, [], 0, [], metadata)["sonic_signature"]
        signature.pop("vibe_vector", None)
        payload["sonic_signature"] = signature
        warnings.append("Key and production descriptors use the upstream heuristic analyzer; no CLAP model was requested.")
    if keep_media:
        payload["artifacts"]["audio_path"] = str(wav_path.resolve())
        payload["artifacts"]["source_media_path"] = str(raw_path.resolve())
        if stems_dir:
            payload["artifacts"]["stems"] = {p.stem: str(p.resolve()) for p in stems_dir.glob("*.wav")}
    else:
        # Only touch artifacts inside this fresh job directory, never the user's input.
        for path in job_dir.iterdir():
            if path.name in {"metadata.json", "job.json", "result.json"}:
                continue
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                if path.resolve().parent != job_dir.resolve():
                    raise ValueError("Refusing to remove an artifact outside the job directory")
                shutil.rmtree(path)
            else:
                path.unlink()
    payload["warnings"] = warnings
    payload["elapsed_sec"] = round(time.perf_counter() - started, 3)
    write_json(result_path, payload)
    return payload
