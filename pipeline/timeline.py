"""Full-file editing timestamps, using librosa or optional upstream models.

Scores rank events within one track; they are not model confidence values.
No prose, shot descriptions, drum-instrument labels, or invented bar grids.
"""

import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np


BEAT_ENGINES = {"librosa", "beat-this", "all-in-one"}


def validate_timeline_options(beat_engine: str, fps: float) -> None:
    if beat_engine not in BEAT_ENGINES:
        raise ValueError(f"beat_engine must be one of {sorted(BEAT_ENGINES)}")
    if not math.isfinite(fps) or not 0 < fps <= 240:
        raise ValueError("fps must be finite and between 0 and 240")


@lru_cache(maxsize=2)
def _beat_this_model(device: str, checkpoint: str):
    try:
        from beat_this.inference import File2Beats
    except ImportError as exc:
        raise RuntimeError('Beat This! is not installed. Install with: pip install ".[timeline]"') from exc
    return File2Beats(checkpoint_path=checkpoint, device=device, dbn=False)


def _model_beats(wav_path: Path, engine: str) -> tuple:
    device = os.environ.get("AUDIO_DEVICE", "cpu")
    if engine == "beat-this":
        tracker = _beat_this_model(device, os.environ.get("BEAT_THIS_CHECKPOINT", "final0"))
        beats, downbeats = tracker(str(wav_path))
        return beats, downbeats, []
    try:
        import allin1_infer
    except ImportError as exc:
        raise RuntimeError(
            "All-In-One-Infer is not installed. See WORKFLOW.zh-CN.md for the optional installation."
        ) from exc
    result = allin1_infer.analyze(
        str(wav_path), device=device,
        demix_dir=wav_path.parent / "structure_stems",
        spec_dir=wav_path.parent / "structure_specs",
    )
    segments = [{"start_sec": float(s.start), "end_sec": float(s.end), "label": s.label}
                for s in result.segments]
    return result.beats, result.downbeats, segments


def _clean_times(values, duration: float) -> np.ndarray:
    return np.array(sorted({round(float(t), 6) for t in values
                            if math.isfinite(float(t)) and 0 <= float(t) < duration}))


def _relative(values: np.ndarray) -> np.ndarray:
    peak = float(np.max(values)) if len(values) else 0.0
    return np.clip(values / peak, 0, 1) if peak > 1e-8 else np.zeros_like(values)


def analyze_timeline(wav_path: Path, beat_engine: str = "librosa", fps: float = 30.0,
                     drums_path: Path | None = None) -> dict:
    """Analyze the entire file. Times are relative to decoded audio start, in seconds.

    librosa finds pulses and onsets, not bar positions. Its downbeat list stays
    empty. Model failures propagate so requested engines never silently change.
    """
    import librosa
    import soundfile as sf
    from scipy.ndimage import gaussian_filter1d
    from scipy.signal import butter, find_peaks, sosfilt

    validate_timeline_options(beat_engine, fps)
    sr, hop = 22050, 256
    audio, native_sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    duration = len(audio) / native_sr
    if duration <= 0:
        raise ValueError("Audio is empty")
    y = audio.mean(axis=1)
    if native_sr != sr:
        y = librosa.resample(y, orig_sr=native_sr, target_sr=sr)
    if not np.isfinite(y).all():
        raise ValueError("Audio contains non-finite samples")
    rhythm_y = y
    if drums_path is not None:
        drums, drums_sr = sf.read(str(drums_path), dtype="float32", always_2d=True)
        rhythm_y = drums.mean(axis=1)
        if drums_sr != sr:
            rhythm_y = librosa.resample(rhythm_y, orig_sr=drums_sr, target_sr=sr)

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    energy = _relative(gaussian_filter1d(rms, sigma=max(1, 0.05 * sr / hop)))
    lowpass = sosfilt(butter(4, 150, fs=sr, output="sos"), y)
    low_energy = _relative(librosa.feature.rms(y=lowpass, hop_length=hop)[0])
    onset_env = librosa.onset.onset_strength(y=rhythm_y, sr=sr, hop_length=hop)
    onset_strength = _relative(onset_env)
    silent = float(np.max(rms)) < 1e-6
    beats, downbeats, segments = np.array([]), np.array([]), []
    onset_frames = np.array([], dtype=int)
    if not silent:
        onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop)
        if beat_engine == "librosa":
            _, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop)
            beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
        else:
            beats, downbeats, segments = _model_beats(wav_path, beat_engine)
    beats = _clean_times(beats, duration)
    downbeats = _clean_times(downbeats, duration)
    bpm = round(float(60 / np.median(np.diff(beats))), 3) if len(beats) > 1 else 0.0

    def point(time_sec: float, strength: float) -> dict:
        return {"time_sec": round(float(time_sec), 6),
                "frame": int(math.floor(float(time_sec) * fps + 0.5)),
                "strength": round(float(np.clip(strength, 0, 1)), 4)}

    def at(values: np.ndarray, time_sec: float) -> float:
        return float(values[min(len(values) - 1, int(round(time_sec * sr / hop)))])

    beat_points = [point(t, at(onset_strength, t)) for t in beats]
    downbeat_points = [point(t, at(onset_strength, t)) for t in downbeats]
    onsets = [point(f * hop / sr, onset_strength[f]) for f in onset_frames if f * hop / sr < duration]
    peak_frames, _ = find_peaks(energy, distance=int(0.35 * sr / hop), prominence=0.12)
    peaks = [point(times[f], energy[f]) for f in peak_frames if times[f] < duration] if not silent else []
    stride = max(1, round(0.25 * sr / hop))
    curve = [{"time_sec": round(float(times[f]), 6), "rms": round(float(rms[f]), 6),
              "relative_energy": round(float(energy[f]), 4),
              "relative_low_frequency_energy": round(float(low_energy[f]), 4)}
             for f in range(0, len(rms), stride) if times[f] < duration]
    valid_segments = []
    for segment in segments:
        start, end = max(0.0, segment["start_sec"]), min(duration, segment["end_sec"])
        if math.isfinite(start) and math.isfinite(end) and end > start:
            valid_segments.append({"start_sec": round(start, 6), "end_sec": round(end, 6),
                                   "label": segment["label"]})

    # Combine nearby independent evidence while preserving a measured event time.
    # Do not equate every peak to a musical drop or every onset to a drum hit.
    events = []
    for kind, points, weight in [("beat", beat_points, 0.35), ("downbeat", downbeat_points, 0.75),
                                  ("onset", onsets, 0.45), ("energy_peak", peaks, 0.5)]:
        for p in points:
            events.append({**p, "reason": kind, "score": weight * (0.5 + 0.5 * p["strength"])})
    for segment in valid_segments:
        if segment["start_sec"] > 0:
            events.append({**point(segment["start_sec"], 1.0), "reason": "section_boundary", "score": 0.9})
    clusters = []
    for event in sorted(events, key=lambda e: e["time_sec"]):
        if clusters and event["time_sec"] - clusters[-1][0]["time_sec"] <= 0.07:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    ranked = []
    for cluster in clusters:
        anchor = max(cluster, key=lambda e: e["score"])
        # Multiple measurements of the same kind must not inflate the rank.
        evidence = {}
        for e in cluster:
            evidence[e["reason"]] = max(evidence.get(e["reason"], 0), e["score"])
        ranked.append({"time_sec": anchor["time_sec"], "frame": anchor["frame"],
                       "score": round(min(1.0, sum(evidence.values())), 4),
                       "reasons": sorted(evidence)})
    selected = []
    for event in sorted(ranked, key=lambda e: (-e["score"], e["time_sec"])):
        if all(abs(event["time_sec"] - p["time_sec"]) >= 0.3 for p in selected):
            selected.append(event)
        if len(selected) >= 120:
            break

    notes = ["Event strengths and cut scores are relative rankings within this track, not confidence probabilities.",
             "Onsets can be drums, vocals or other sounds; individual drum instruments are not classified."]
    if beat_engine == "librosa":
        notes.append("librosa does not detect bar starts; downbeats is empty. Tempo may have half/double-time ambiguity.")
    if silent:
        notes.append("Silent audio: no rhythm events detected.")
    return {"time_unit": "seconds", "time_origin": "decoded_audio_start",
            "analyzed_start_sec": 0.0, "analyzed_end_sec": round(duration, 6),
            "fps": fps, "frame_rounding": "nearest_half_up", "beat_engine": beat_engine,
            "onset_source": "drums_stem" if drums_path else "mixture", "bpm": bpm,
            "beats": beat_points, "downbeats": downbeat_points, "onsets": onsets,
            "energy_peaks": peaks, "energy_curve": curve,
            "energy_curve_step_sec": round(stride * hop / sr, 6),
            "segments": valid_segments,
            "cut_candidates": sorted(selected, key=lambda e: e["time_sec"]), "notes": notes}
