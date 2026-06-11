"""
Frame extractor — uses ffmpeg to pull frames from video clips.
Stores frames temporarily with timecode metadata for vision scoring.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def extract_frames(
    video_path: Path,
    output_dir: Path,
    fps: float = 1.0,
    max_frames: int = 300,
    keyframes_only: bool = False
) -> list[dict]:
    """
    Extract frames from a video file.
    Returns list of {frame_path, timecode_sec, timecode_str}

    fps=1.0 means 1 frame per second — good balance of coverage vs API cost.
    keyframes_only=True is faster but less precise.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = Path(video_path)

    if not video_path.exists():
        print(f"  [WARN] Video not found: {video_path}")
        return []

    # Get video duration first
    probe = subprocess.run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", str(video_path)
    ], capture_output=True, text=True)

    duration = None
    try:
        info = json.loads(probe.stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                duration = float(stream.get("duration", 0))
                break
    except Exception:
        pass

    # Build ffmpeg filter
    if keyframes_only:
        vf = "select=eq(pict_type\\,I)"
        vsync = "vfr"
    else:
        vf = f"fps={fps}"
        vsync = "vfr"

    frame_pattern = str(output_dir / "frame_%06d.jpg")

    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", vf,
        "-vsync", vsync,
        "-q:v", "3",           # quality 1-31, lower is better
        "-frames:v", str(max_frames),
        frame_pattern,
        "-y", "-loglevel", "error"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [WARN] ffmpeg error on {video_path.name}: {result.stderr[:200]}")
        return []

    # Build frame list with timecodes
    frames = sorted(output_dir.glob("frame_*.jpg"))
    frame_list = []
    for i, frame in enumerate(frames):
        if keyframes_only:
            # Can't reliably derive timecode from keyframe index — mark as approx
            tc_sec = None
        else:
            tc_sec = i / fps
        frame_list.append({
            "frame_path": str(frame),
            "frame_index": i,
            "timecode_sec": tc_sec,
            "timecode_str": _sec_to_tc(tc_sec) if tc_sec is not None else "unknown",
            "source_video": str(video_path),
        })

    return frame_list


def extract_frames_for_clip(
    video_path: Path,
    tc_in_sec: float,
    tc_out_sec: float,
    output_dir: Path,
    n_frames: int = 3
) -> list[dict]:
    """
    Extract N frames evenly spread across a specific clip range.
    Used for scoring individual clips rather than whole files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = tc_out_sec - tc_in_sec
    if duration <= 0:
        return []

    # Sample evenly through the clip
    interval = duration / (n_frames + 1)
    frames = []
    for i in range(1, n_frames + 1):
        tc = tc_in_sec + interval * i
        frame_path = output_dir / f"clip_frame_{i:02d}.jpg"
        cmd = [
            "ffmpeg", "-ss", str(tc),
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(frame_path),
            "-y", "-loglevel", "error"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and frame_path.exists():
            frames.append({
                "frame_path": str(frame_path),
                "timecode_sec": tc,
                "timecode_str": _sec_to_tc(tc),
                "source_video": str(video_path),
            })

    return frames


def _sec_to_tc(seconds: float, fps: int = 25) -> str:
    """Convert seconds float to HH:MM:SS:FF timecode string."""
    if seconds is None:
        return "00:00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    f = int((seconds % 1) * fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def cleanup_frames(frame_dir: Path):
    """Remove extracted frames to free QNAP space."""
    import shutil
    if frame_dir.exists():
        shutil.rmtree(frame_dir)
