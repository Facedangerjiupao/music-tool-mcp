"""
Stage 3 — WAV Conversion
Standardizes raw audio to 44.1kHz / 16-bit stereo WAV for downstream analysis.
"""

import logging
import subprocess
from subprocess import DEVNULL
from pathlib import Path

from pipeline.runtime import configure_runtime

configure_runtime()

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 44100
TARGET_CHANNELS = 2  # stereo
TARGET_BIT_DEPTH = "s16"  # signed 16-bit PCM


def convert_to_wav(raw_audio_path: Path, out_dir: Path | None = None) -> Path:
    """
    Converts the raw downloaded file to a normalized WAV.
    Uses FFmpeg with high-quality resampling (soxr engine).

    Returns the path to the .wav output file.
    """
    if not raw_audio_path.exists():
        raise FileNotFoundError(f"Raw audio not found: {raw_audio_path}")

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        wav_path = out_dir / "input.wav"
    else:
        wav_path = raw_audio_path.parent / "input.wav"

    if wav_path.exists():
        logger.info("WAV already exists, skipping conversion: %s", wav_path)
        return wav_path

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",  # overwrite output
        "-i",
        str(raw_audio_path),  # input file
        "-vn",  # strip any video stream
        "-acodec",
        "pcm_s16le",  # PCM 16-bit little-endian
        "-ar",
        str(TARGET_SAMPLE_RATE),  # 44.1kHz
        "-ac",
        str(TARGET_CHANNELS),  # stereo
        "-af",
        "aresample=resampler=soxr",  # high-quality resampler
        "-map_metadata",
        "-1",  # strip metadata (clean slate)
        str(wav_path),
    ]

    logger.info(
        "Converting %s → %s @ %dHz stereo …",
        raw_audio_path.name,
        wav_path.name,
        TARGET_SAMPLE_RATE,
    )

    result = subprocess.run(
        cmd,
        stdin=DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    if result.returncode != 0 and "Requested resampling engine is unavailable" in result.stderr:
        logger.info("FFmpeg build has no libsoxr; using its built-in swresample engine")
        fallback_cmd = cmd.copy()
        fallback_cmd[fallback_cmd.index("-af") + 1] = "aresample=resampler=swr"
        result = subprocess.run(fallback_cmd, stdin=DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=120)

    if result.returncode != 0:
        wav_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"FFmpeg conversion failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"stderr: {result.stderr[-2000:]}"
        )

    size_mb = wav_path.stat().st_size / 1e6
    logger.info("WAV ready: %s (%.1f MB)", wav_path, size_mb)
    return wav_path
