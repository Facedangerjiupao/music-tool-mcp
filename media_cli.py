"""Raw media/feature CLI extension for the upstream Audio Sonic MCP project."""

import argparse
from contextlib import redirect_stdout
import json
import logging
import os
from pathlib import Path
import sys
import uuid

from pipeline.workflow import create_job_directory, run_media_workflow, validate_request, write_json
from pipeline.runtime import default_jobs_root


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Audio Sonic MCP workflow fork: URL/local media -> files + JSON")
    parser.add_argument("source", help="HTTP(S) URL or local audio/video path")
    parser.add_argument("--mode", choices=["extract", "rhythm", "full"], default="rhythm")
    parser.add_argument("--media-type", choices=["audio", "video"], default="audio", help="URL download: audio only, or retain video too")
    parser.add_argument("--beat-engine", choices=["librosa", "beat-this", "all-in-one"], default="librosa")
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--job-id")
    parser.add_argument("--jobs-root", type=Path, default=default_jobs_root())
    parser.add_argument("--out", type=Path, help="Also write result JSON here")
    parser.add_argument("--discard-media", action="store_true", help="Keep JSON/metadata only; never delete the original input")
    parser.add_argument("--separate", action="store_true", help="Explicitly run optional Demucs on the full file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    job_id = args.job_id or f"media_{uuid.uuid4().hex[:12]}"
    job_dir = None
    try:
        validate_request(args.source, args.mode, args.media_type, args.beat_engine, args.fps)
        job_dir = create_job_directory(args.jobs_root, job_id)
        with redirect_stdout(sys.stderr):
            result = run_media_workflow(args.source, job_dir, mode=args.mode, media_type=args.media_type,
                                        beat_engine=args.beat_engine, fps=args.fps,
                                        keep_media=not args.discard_media, separate=args.separate)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as exc:
        result = {"job_id": job_id, "status": "error", "error": {"type": type(exc).__name__, "message": str(exc)}}
        if job_dir is not None:
            write_json(job_dir / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
