"""Workflow contracts with actual FFmpeg decoding and known synthetic timing."""

import asyncio
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

import server
from pipeline.timeline import analyze_timeline
from pipeline.workflow import create_job_directory, run_media_workflow, validate_job_id, write_json


@pytest.fixture
def click_track(tmp_path):
    sr, duration = 22050, 90
    y = np.zeros(sr * duration, dtype=np.float32)
    rng = np.random.default_rng(41)
    hit = rng.normal(size=441).astype(np.float32) * np.hanning(441) * 0.2
    for t in np.arange(0.5, duration - 0.25, 0.5):
        start = int(t * sr)
        y[start:start + len(hit)] += hit
    path = tmp_path / "click120.wav"
    sf.write(path, y, sr)
    return path


def test_full_track_timestamps_follow_known_beats(click_track):
    timeline = analyze_timeline(click_track, fps=25)
    assert timeline["analyzed_start_sec"] == 0
    assert timeline["analyzed_end_sec"] == 90
    beats = np.array([b["time_sec"] for b in timeline["beats"]])
    assert beats.max() > 85  # catches the upstream representative-window cropping
    assert 116 < timeline["bpm"] < 124
    expected = np.arange(0.5, 89.75, 0.5)
    error = np.min(np.abs(beats[:, None] - expected[None, :]), axis=1)
    assert np.quantile(error, 0.95) < 0.07
    assert timeline["downbeats"] == []  # never guess bar starts from 4/4
    assert timeline["energy_curve"][-1]["time_sec"] > 89
    assert all(b["frame"] == int(b["time_sec"] * 25 + 0.5) for b in timeline["beats"])
    json.dumps(timeline, allow_nan=False)


def test_silence_has_no_false_events(tmp_path):
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(22050 * 2), 22050)
    result = analyze_timeline(path)
    assert result["bpm"] == 0
    for key in ("beats", "onsets", "downbeats", "energy_peaks", "cut_candidates"):
        assert result[key] == []
    assert all(p["relative_energy"] == 0 for p in result["energy_curve"])


def test_requested_model_is_not_silently_replaced(synthetic_stereo_wav):
    with patch("pipeline.timeline._model_beats", side_effect=RuntimeError("model unavailable")):
        with pytest.raises(RuntimeError, match="model unavailable"):
            analyze_timeline(synthetic_stereo_wav, beat_engine="beat-this")


def test_model_timing_and_sections_are_preserved(synthetic_stereo_wav):
    # Adapter contract: the external model owns bar/section inference.
    sections = [{"start_sec": 0, "end_sec": 2, "label": "intro"},
                {"start_sec": 2, "end_sec": 4, "label": "chorus"}]
    with patch("pipeline.timeline._model_beats", return_value=([0.5, 1, 1.5, 2, 2.5, 3, 3.5], [0.5, 2.5], sections)):
        result = analyze_timeline(synthetic_stereo_wav, beat_engine="all-in-one")
    assert [p["time_sec"] for p in result["downbeats"]] == [0.5, 2.5]
    assert result["segments"][1]["label"] == "chorus"
    assert any("section_boundary" in p["reasons"] and p["time_sec"] == 2 for p in result["cut_candidates"])


def test_extract_does_not_run_analyzers_and_keeps_audio(synthetic_stereo_wav, tmp_path):
    job_dir = create_job_directory(tmp_path / "jobs", "extract_only")
    with patch("pipeline.workflow.analyze_timeline", side_effect=AssertionError("must not analyze")):
        result = run_media_workflow(str(synthetic_stereo_wav), job_dir, mode="extract")
    assert "timeline" not in result and "sonic_signature" not in result
    assert Path(result["artifacts"]["audio_path"]).is_file()
    assert json.loads((job_dir / "result.json").read_text(encoding="utf-8")) == result


def test_video_input_and_cli_json(synthetic_stereo_wav, tmp_path):
    video = tmp_path / "测试视频.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=25",
                    "-i", str(synthetic_stereo_wav), "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(video)], check=True)
    cli = Path(__file__).resolve().parents[1] / "media_cli.py"
    proc = subprocess.run([sys.executable, "-X", "utf8", str(cli), str(video), "--mode", "rhythm",
                           "--fps", "25", "--jobs-root", str(tmp_path / "jobs")],
                          capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == "success"
    assert result["timeline"]["analyzed_end_sec"] >= 4
    assert result["timeline"]["fps"] == 25
    assert Path(result["artifacts"]["audio_path"]).is_file()
    assert video.is_file()


def test_discard_media_never_deletes_original(synthetic_stereo_wav, tmp_path):
    before = synthetic_stereo_wav.read_bytes()
    job_dir = create_job_directory(tmp_path / "jobs", "discard")
    result = run_media_workflow(str(synthetic_stereo_wav), job_dir, mode="extract", keep_media=False)
    assert synthetic_stereo_wav.read_bytes() == before
    assert not (job_dir / "input.wav").exists()
    assert list(result["artifacts"]) == ["result_json"]
    assert (job_dir / "result.json").exists()


def test_direct_http_download_uses_existing_ytdlp_pipeline(synthetic_stereo_wav, tmp_path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(QuietHandler, directory=str(synthetic_stereo_wav.parent))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_port}/{synthetic_stereo_wav.name}"
        job_dir = create_job_directory(tmp_path / "jobs", "download")
        result = run_media_workflow(url, job_dir, mode="extract")
        assert result["source"]["source_type"] == "url"
        assert Path(result["artifacts"]["source_media_path"]).exists()
        assert result["source"]["duration_sec"] == 4
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("job_id", ["../outside", "..", "CON", "a/b", "a\\b", "NUL", "x" * 65])
def test_job_paths_cannot_escape_workspace(job_id):
    with pytest.raises(ValueError):
        validate_job_id(job_id)


def test_duplicate_job_does_not_overwrite(tmp_path):
    root = tmp_path / "jobs"
    job = create_job_directory(root, "existing")
    (job / "result.json").write_text("original")
    with pytest.raises(ValueError, match="already exists"):
        create_job_directory(root, "existing")
    assert (job / "result.json").read_text() == "original"


@pytest.mark.asyncio
async def test_async_job_and_result_survive_store_reset(synthetic_stereo_wav, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOBS_ROOT", tmp_path / "jobs")
    server.JOB_STORE.clear()
    response = json.loads(await server.prepare_media(str(synthetic_stereo_wav), mode="extract", job_id="async_job"))
    assert response["status"] == "queued"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = json.loads(await server.get_job_status("async_job"))
        if result.get("status") in {"success", "error"}:
            break
        await asyncio.sleep(0.05)
    assert result["status"] == "success", result
    server.JOB_STORE.clear()
    assert json.loads(await server.get_job_status("async_job")) == result


@pytest.mark.asyncio
async def test_interrupted_job_reports_error_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOBS_ROOT", tmp_path / "jobs")
    job = create_job_directory(server.JOBS_ROOT, "interrupted")
    write_json(job / "job.json", {"status": "running"})
    result = json.loads(await server.get_job_status("interrupted"))
    assert result["status"] == "error"
    assert result["error"]["type"] == "InterruptedJob"


@pytest.mark.asyncio
async def test_bad_request_rejected_before_job_creation(synthetic_stereo_wav, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOBS_ROOT", tmp_path / "jobs")
    result = json.loads(await server.prepare_media(str(synthetic_stereo_wav), fps=float("nan")))
    assert result["status"] == "error"
    assert not server.JOBS_ROOT.exists()


def test_rest_submit_and_validation(synthetic_stereo_wav, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from app_cloud import app
    monkeypatch.setattr(server, "JOBS_ROOT", tmp_path / "jobs")
    with TestClient(app) as client:
        bad = client.post("/api/v1/media", json={"source": str(synthetic_stereo_wav), "mode": "unknown"})
        assert bad.status_code == 400
        response = client.post("/api/v1/media", json={"source": str(synthetic_stereo_wav), "mode": "extract"})
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.get(f"/api/v1/jobs/{job_id}").json()
            if result.get("status") in {"success", "error"}:
                break
            time.sleep(0.05)
        assert result["status"] == "success", result


@pytest.mark.asyncio
async def test_real_mcp_stdio_tool_call(synthetic_stereo_wav, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import os
    env = {**os.environ, "JOBS_ROOT": str(tmp_path / "mcp_jobs"), "PYTHONUTF8": "1", "TRANSPORT_MODE": "stdio"}
    params = StdioServerParameters(command=sys.executable, args=[str(Path(server.__file__).resolve())], env=env)
    async def exercise():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "prepare_media" in {t.name for t in tools.tools}
                response = await session.call_tool("prepare_media", {"source": str(synthetic_stereo_wav), "mode": "extract"})
                submitted = json.loads(response.content[0].text)
                for _ in range(100):
                    response = await session.call_tool("get_job_status", {"job_id": submitted["job_id"]})
                    result = json.loads(response.content[0].text)
                    if result.get("status") in {"success", "error"}:
                        assert result["status"] == "success", result
                        assert Path(result["artifacts"]["audio_path"]).exists()
                        return
                    await asyncio.sleep(0.1)
                pytest.fail("MCP job did not complete")
    await asyncio.wait_for(exercise(), timeout=90)


@pytest.mark.asyncio
async def test_mcp_sse_mount_supports_real_handshake():
    pytest.importorskip("uvicorn")
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from app_cloud import app
    import socket
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    uv_server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    worker = threading.Thread(target=uv_server.run, kwargs={"sockets": [sock]}, daemon=True)
    worker.start()
    try:
        for _ in range(100):
            if uv_server.started:
                break
            await asyncio.sleep(0.05)
        async def exercise():
            async with sse_client(f"http://127.0.0.1:{port}/mcp/sse") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    assert "prepare_media" in {tool.name for tool in result.tools}
        await asyncio.wait_for(exercise(), timeout=20)
    finally:
        uv_server.should_exit = True
        worker.join(timeout=5)
        sock.close()
