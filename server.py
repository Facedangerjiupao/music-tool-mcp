"""
Audio Sonic Signature MCP Server
Analyzes a YouTube URL and returns a structured sonic signature JSON.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from pipeline.ingestion import validate_url_format, validate_source, save_metadata, load_metadata
from pipeline.downloader import download_audio
from pipeline.converter import convert_to_wav
from pipeline.separator import separate_stems
from pipeline.analyzer import analyze_audio
from pipeline.vectorizer import generate_vibe_vector
from pipeline.assembler import assemble_payload
from pipeline.runtime import default_jobs_root
from pipeline.workflow import (create_job_directory, run_media_workflow, validate_job_id,
                               validate_request, write_json)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("audio-sonic-mcp")

# Ensure CLI tools installed alongside this Python (yt-dlp) are
# findable by subprocess calls even when the venv is not "activated".
_venv_bin = str(Path(sys.executable).parent)
if _venv_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _venv_bin + os.pathsep + os.environ.get("PATH", "")
    logger.info("Added venv bin to PATH: %s", _venv_bin)

# Prevent torchaudio from attempting to load FFmpeg/torchcodec DLLs
os.environ["TORCHAUDIO_USE_BACKEND_PREFERENCE"] = "soundfile"

# ── Config ────────────────────────────────────────────────────────────────────
JOBS_ROOT = default_jobs_root()
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

app = FastMCP("audio-sonic-mcp")

# ── Concurrency Control ───────────────────────────────────────────────────────
# Serializes ML jobs to prevent OOM on constrained hardware
CONCURRENCY_LOCK = threading.Lock()

# ── In-memory job store ───────────────────────────────────────────────────────
JOB_STORE: dict[str, dict] = {}


class JobStatus:
    """Lifecycle states stored under JOB_STORE[job_id]["status"]."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


def _cleanup_job_artifacts(job_dir: Path) -> None:
    """Remove downloaded audio and stem WAVs after a successful run.

    Keeps metadata.json for traceability. Disabled when KEEP_JOB_FILES=1
    (useful for debugging or to preserve fast-resume).
    """
    if os.environ.get("KEEP_JOB_FILES", "").lower() in ("1", "true", "yes"):
        logger.info("cleanup: skipped (KEEP_JOB_FILES set) for %s", job_dir.name)
        return

    import shutil

    removed_bytes = 0
    for path in job_dir.iterdir():
        if path.name in {"metadata.json", "result.json", "job.json"}:
            continue
        try:
            if path.is_dir():
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                shutil.rmtree(path)
                removed_bytes += size
            else:
                removed_bytes += path.stat().st_size
                path.unlink()
        except Exception as e:
            logger.warning("cleanup: failed on %s: %s", path, e)

    logger.info(
        "cleanup: freed %.1f MB from %s", removed_bytes / 1_048_576, job_dir.name
    )


def _maybe_warn_non_music(payload: dict) -> None:
    """Inject a non_music_warning when confidence_score is below the music threshold."""
    if payload.get("header", {}).get("confidence_score", 1.0) < 0.35:
        payload["non_music_warning"] = (
            "Low confidence score — this audio may not be music. "
            "BPM, key, and vibe vector results may be unreliable."
        )


# ── Background pipeline ───────────────────────────────────────────────────────


def _run_pipeline(job_id: str, url: str, job_dir: Path, started_at: float) -> None:
    """Daemon-thread pipeline: acquires CONCURRENCY_LOCK, runs all pipeline stages,
    writes final result (success or error) to JOB_STORE.
    All pipeline stages are synchronous — no event loop needed."""
    t_start = time.perf_counter()
    try:
        with CONCURRENCY_LOCK:
            # Mark as running once we hold the lock — distinguishes "actively
            # processing" from "queued waiting on a prior job's lock" when polling.
            JOB_STORE[job_id] = {"status": JobStatus.RUNNING, "started_at": started_at}

            # Stage 1: Validate + Metadata Cache
            logger.info("[1/5] Validating source …")
            cached_meta = load_metadata(job_dir)
            if cached_meta:
                logger.info("Fast-Resume: Loaded metadata from disk for job=%s", job_id)
                source_info = cached_meta
            else:
                source_info = validate_source(url)
                try:
                    save_metadata(job_dir, source_info)
                    logger.info("Persisted metadata.json for job=%s", job_id)
                except Exception as e:
                    logger.warning("Could not persist metadata for job=%s: %s", job_id, e)

            # Fast Resume Check
            wav_path = job_dir / "input.wav"
            if wav_path.exists():
                logger.info(
                    "Fast-Resume: Found existing WAV at %s. Skipping download & convert.",
                    wav_path,
                )
            else:
                # Stage 2: Download
                logger.info("[2/5] Downloading audio stream …")
                raw_audio_path = download_audio(url, job_id, JOBS_ROOT)

                # Stage 3: Convert
                logger.info("[3/5] Converting to 44.1kHz WAV …")
                wav_path = convert_to_wav(raw_audio_path)

            # Stage 4: Stem Separation
            logger.info("[4/5] Separating stems (Demucs mdx_extra) …")
            stems_dir = separate_stems(wav_path)
            if stems_dir:
                logger.info("[4/5] Stems ready at %s", stems_dir)
            else:
                logger.info("[4/5] Stem separation unavailable — will use HPSS")

            # Stage 5: Analyze + Vectorize
            logger.info("[5/5] Running analysis and vibe vectorization …")
            features = analyze_audio(wav_path, stems_dir)
            logger.info(
                "[5/5] Analysis complete: bpm=%.1f key=%s confidence=%.2f",
                features["bpm"],
                features["key"],
                features.get("mode_confidence", 0),
            )

            vibe_vector = generate_vibe_vector(wav_path)
            logger.info("[5/5] Vectorize complete: dim=%d", len(vibe_vector))

            elapsed = time.perf_counter() - t_start
            payload = assemble_payload(
                job_id=job_id,
                features=features,
                vibe_vector=vibe_vector,
                inference_time=elapsed,
                cpu_samples=[],
                source_info=source_info,
            )
            _maybe_warn_non_music(payload)

            JOB_STORE[job_id] = {"status": JobStatus.SUCCESS, "payload": payload, "started_at": started_at}
            logger.info("✓ _run_pipeline | job=%s elapsed=%.1fs", job_id, elapsed)
            _cleanup_job_artifacts(job_dir)

    except BaseException as exc:
        elapsed = time.perf_counter() - t_start
        error_payload = {
            "header": {"job_id": job_id, "status": JobStatus.ERROR, "confidence_score": 0.0},
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "telemetry": {"inference_time_sec": round(elapsed, 2)},
        }
        JOB_STORE[job_id] = {"status": JobStatus.ERROR, "payload": error_payload, "started_at": started_at}
        logger.error(
            "✗ _run_pipeline | job=%s elapsed=%.1fs error=%s: %s",
            job_id,
            elapsed,
            type(exc).__name__,
            exc,
            exc_info=True,
        )


# ── Tool handlers ─────────────────────────────────────────────────────────────


@app.tool()
async def get_sonic_signature(url: str, job_id: str = None) -> str:
    """
    Submits a YouTube URL for sonic analysis and returns immediately.
    The pipeline runs in the background — poll get_job_status(job_id) for results.
    Typical processing: 20-30s on GPU, ~6-8 min on CPU (Demucs separation dominates).

    Args:
        url: YouTube URL of the track to analyze.
        job_id: Optional custom job ID. Auto-generated if omitted.
    """
    job_id = job_id or f"sig_{uuid.uuid4().hex[:8]}"
    logger.info("▶ get_sonic_signature | job=%s url=%s", job_id, url)

    try:
        validate_job_id(job_id)
        if job_id in JOB_STORE or (JOBS_ROOT / job_id).exists():
            raise ValueError(f"Job '{job_id}' already exists; use a new job_id")
        validate_url_format(url)
    except ValueError as exc:
        error_payload = {
            "header": {"job_id": job_id, "status": JobStatus.ERROR, "confidence_score": 0.0},
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "telemetry": {"inference_time_sec": 0.0},
        }
        logger.warning("✗ get_sonic_signature | job=%s bad URL: %s", job_id, exc)
        return json.dumps(error_payload, indent=2)

    job_dir = JOBS_ROOT / job_id
    started_at = time.time()
    JOB_STORE[job_id] = {"status": JobStatus.QUEUED, "started_at": started_at}
    threading.Thread(
        target=_run_pipeline, args=(job_id, url, job_dir, started_at), daemon=True
    ).start()

    return json.dumps(
        {
            "status": JobStatus.QUEUED,
            "job_id": job_id,
            "message": (
                f"Job {job_id} queued. "
                "Call get_job_status to poll for results."
            ),
        },
        indent=2,
    )


def _run_media_pipeline(job_id: str, source: str, job_dir: Path, started_at: float, options: dict) -> None:
    """Use the existing server queue/lock for the workflow extension."""
    try:
        with CONCURRENCY_LOCK:
            JOB_STORE[job_id] = {"status": JobStatus.RUNNING, "started_at": started_at}
            write_json(job_dir / "job.json", {"job_id": job_id, "status": "running", "started_at": started_at})
            payload = run_media_workflow(source, job_dir, **options)
            JOB_STORE[job_id] = {"status": JobStatus.SUCCESS, "payload": payload, "started_at": started_at}
    except Exception as exc:
        logger.exception("Media workflow failed: %s", job_id)
        payload = {"job_id": job_id, "status": "error",
                   "error": {"type": type(exc).__name__, "message": str(exc)}}
        JOB_STORE[job_id] = {"status": JobStatus.ERROR, "payload": payload, "started_at": started_at}
        try:
            write_json(job_dir / "result.json", payload)
        except OSError:
            logger.exception("Could not persist failed job %s", job_id)


@app.tool()
async def prepare_media(source: str, mode: str = "rhythm", media_type: str = "audio",
                        beat_engine: str = "librosa", fps: float = 30.0,
                        keep_media: bool = True, separate: bool = False,
                        job_id: str | None = None) -> str:
    """Prepare URL or local audio/video for another AI/workflow. Returns a job ID immediately.

    Poll get_job_status(job_id) until status is success/error. Success returns
    absolute artifact paths and raw JSON features; no written report or script.
    mode: extract (files only), rhythm (full-file timestamps/energy), full (also
    upstream key/production descriptors). media_type=video keeps URL video too.
    beat_engine: librosa (built-in; no downbeats), beat-this (optional model),
    all-in-one (optional structure model with verse/chorus segments).
    keep_media defaults true so subsequent tools can read files. separate=true
    explicitly requests optional full-song Demucs. fps sets nearest frame numbers.
    Site support depends on yt-dlp. Local paths refer to the server machine.
    """
    job_id = job_id or f"media_{uuid.uuid4().hex[:12]}"
    try:
        validate_job_id(job_id)
        validate_request(source, mode, media_type, beat_engine, fps)
        if job_id in JOB_STORE:
            raise ValueError(f"Job '{job_id}' already exists; use a new job_id")
        job_dir = create_job_directory(JOBS_ROOT, job_id)
    except (ValueError, OSError) as exc:
        return json.dumps({"job_id": job_id, "status": "error",
                           "error": {"type": type(exc).__name__, "message": str(exc)}})
    started_at = time.time()
    JOB_STORE[job_id] = {"status": JobStatus.QUEUED, "started_at": started_at}
    write_json(job_dir / "job.json", {"job_id": job_id, "status": "queued", "started_at": started_at})
    options = dict(mode=mode, media_type=media_type, beat_engine=beat_engine, fps=fps,
                   keep_media=keep_media, separate=separate)
    threading.Thread(target=_run_media_pipeline,
                     args=(job_id, source, job_dir, started_at, options), daemon=True).start()
    return json.dumps({"job_id": job_id, "status": "queued", "poll_tool": "get_job_status"})


def _persisted_job(job_id: str) -> dict | None:
    job_dir = JOBS_ROOT / job_id
    for name in ("result.json", "job.json"):
        path = job_dir / name
        if path.is_file():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if name == "job.json":
                return {"job_id": job_id, "status": "error", "error": {
                    "type": "InterruptedJob", "message": "Server stopped before job completion; submit a new job."}}
            return result
    return None


@app.tool()
async def get_job_status(job_id: str) -> str:
    """
    Returns the current status and payload of a previously submitted job.

    Args:
        job_id: The job ID returned by get_sonic_signature.
    """
    logger.info("▶ get_job_status | job=%s", job_id)
    try:
        validate_job_id(job_id)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)})
    job = JOB_STORE.get(job_id)
    if not job:
        result = _persisted_job(job_id) or {"error": f"Job '{job_id}' not found."}
        logger.warning("  get_job_status | job=%s not found", job_id)
    else:
        result = job.get("payload", {"status": job["status"]})
        logger.info("  get_job_status | job=%s status=%s", job_id, job.get("status"))
    return json.dumps(result, indent=2)


@app.tool()
async def list_jobs() -> str:
    """Lists all completed and in-progress analysis jobs."""
    logger.info("▶ list_jobs | total=%d", len(JOB_STORE))
    summary = [
        {
            "job_id": jid,
            "status": info.get("status"),
            "started_at": info.get("started_at"),
        }
        for jid, info in JOB_STORE.items()
    ]
    for path in JOBS_ROOT.iterdir():
        if path.is_dir() and path.name not in JOB_STORE:
            try:
                validate_job_id(path.name)
            except ValueError:
                continue
            persisted = _persisted_job(path.name)
            if persisted:
                summary.append({"job_id": path.name, "status": persisted.get("status"), "persisted": True})
    return json.dumps(summary, indent=2)


@app.tool()
async def check_health() -> str:
    """Verification tool for server dependencies."""
    import subprocess
    import importlib.util

    results = {"status": "ok", "checks": [], "workflow_modes": ["extract", "rhythm", "full"]}
    all_ok = True

    # FFmpeg is a genuine CLI dependency — converter.py shells out to it.
    def _check_ffmpeg():
        return subprocess.run(
            ["ffmpeg", "-version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
        )

    try:
        r = await asyncio.to_thread(_check_ffmpeg)
        res = {"name": "FFmpeg", "status": "ok" if r.returncode == 0 else "error"}
        if r.returncode != 0:
            all_ok = False
    except Exception as e:
        res = {"name": "FFmpeg", "status": "not_found", "error": str(e)}
        all_ok = False
    results["checks"].append(res)

    import shutil
    ffprobe_ok = shutil.which("ffprobe") is not None
    results["checks"].append({"name": "FFprobe", "status": "ok" if ffprobe_ok else "missing"})
    all_ok = all_ok and ffprobe_ok
    results["optional_engines"] = {
        "beat-this": importlib.util.find_spec("beat_this") is not None,
        "all-in-one": importlib.util.find_spec("allin1_infer") is not None,
        "demucs": importlib.util.find_spec("demucs") is not None,
    }

    # yt-dlp is used as a Python library (pipeline imports yt_dlp), not via CLI,
    # so probe it by import — the CLI entry point may be absent/broken yet the
    # library fully functional.
    yt_dlp_ok = importlib.util.find_spec("yt_dlp") is not None
    results["checks"].append(
        {"name": "yt-dlp", "status": "ok" if yt_dlp_ok else "missing"}
    )
    if not yt_dlp_ok:
        all_ok = False

    packages = ["mcp", "librosa", "soundfile", "numpy"]
    for pkg in packages:
        found = importlib.util.find_spec(pkg) is not None
        results["checks"].append(
            {"name": f"python:{pkg}", "status": "ok" if found else "missing"}
        )
        if not found:
            all_ok = False

    # madmom is optional but is the primary BPM engine. Its absence does not
    # break the server, so it does not flip `all_ok` — but it silently degrades
    # BPM accuracy, so report it explicitly rather than letting it pass unseen.
    madmom_ok = importlib.util.find_spec("madmom") is not None
    results["checks"].append(
        {
            "name": "python:madmom (optional)",
            "status": "ok" if madmom_ok else "missing",
            "bpm_engine": "madmom" if madmom_ok else "librosa-fallback",
            **({} if madmom_ok else {
                "note": (
                    "BPM falls back to librosa, which can report a 2:3 or octave "
                    "multiple of the true tempo. Every result carries a "
                    "`bpm_engine` field naming the engine actually used. "
                    "Install with: pip install \".[beats]\""
                )
            }),
        }
    )

    results["checks"].append(
        {
            "name": "jobs_root",
            "status": "ok" if JOBS_ROOT.exists() else "error",
            "path": str(JOBS_ROOT),
        }
    )

    if not all_ok:
        results["status"] = "degraded"

    return json.dumps(results, indent=2)


# ── Entrypoint ────────────────────────────────────────────────────────────────


def _prewarm_librosa() -> None:
    """Trigger numba JIT compilation in the main thread before serving.

    Without this, the first librosa call from a worker thread (asyncio.to_thread)
    deadlocks under FastMCP's task group on Windows.
    """
    import numpy as np
    import librosa

    dummy_stereo = np.zeros((2, 4096), dtype=np.float32)
    dummy_mono = librosa.to_mono(dummy_stereo)
    librosa.resample(dummy_mono, orig_sr=44100, target_sr=22050)
    librosa.effects.hpss(dummy_mono)
    logger.info("librosa pre-warm complete")


def _prewarm_demucs() -> None:
    """Load Demucs model weights into memory before serving.
    Downloads ~400 MB of weights on first run; cached on disk thereafter.
    Download progress bars are redirected to stderr so they don't corrupt
    the MCP stdio JSON-RPC stream.
    """
    import sys
    old_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        from pipeline.separator import load_demucs_model
        load_demucs_model()
    except Exception as exc:
        logger.warning("Demucs pre-warm skipped (stem separation will use HPSS): %s", exc)
    finally:
        sys.stdout = old_stdout


def main():
    _prewarm_librosa()
    _prewarm_demucs()
    
    transport = os.environ.get("TRANSPORT_MODE", "stdio").lower()
    if transport == "sse":
        port = int(os.environ.get("PORT", 8000))
        logger.info("Starting Audio Sonic MCP in SSE mode on port %d...", port)
        app.settings.host = os.environ.get("HOST", "127.0.0.1")
        app.settings.port = port
        app.run(transport="sse")
    elif transport == "hybrid":
        import uvicorn
        port = int(os.environ.get("PORT", 8000))
        logger.info("Starting Audio Sonic Hybrid Server (MCP SSE + REST API) on port %d...", port)
        uvicorn.run("app_cloud:app", host=os.environ.get("HOST", "127.0.0.1"), port=port, reload=False)
    else:
        logger.info("Starting Audio Sonic MCP in Stdio mode...")
        app.run(transport="stdio")


if __name__ == "__main__":
    main()

