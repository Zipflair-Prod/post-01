"""
Enhanced scorer — combines transcript scoring + vision scoring + Hedge metadata.
Produces a fully ranked clip list with auto-selects marked.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from scorer import run_scorer, load_transcripts, load_preset
from vision.frame_extractor import extract_frames_for_clip, cleanup_frames, _sec_to_tc
from vision.vision_scorer import score_frames_for_clip, merge_scores
from vision.hedge_ingest import load_hedge_metadata, apply_hedge_metadata


def tc_to_sec(tc: str) -> float:
    """Convert timecode string to seconds float."""
    import re
    tc = tc.strip()
    if re.match(r"\d{2}:\d{2}:\d{2}[;:]\d{2}", tc):
        parts = re.split(r"[:;]", tc)
        h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        return h * 3600 + m * 60 + s + f / 25.0
    if re.match(r"\d{2}:\d{2}:\d{2}\.\d+", tc):
        parts = tc.split(":")
        h, m = int(parts[0]), int(parts[1])
        s_parts = parts[2].split(".")
        return h * 3600 + m * 60 + int(s_parts[0]) + int(s_parts[1].ljust(3, "0")[:3]) / 1000.0
    if re.match(r"^\d+\.\d+$", tc):
        return float(tc)
    return 0.0


def run_enhanced_scorer(
    brief_path: str,
    config_dir: str = None,
    use_vision: bool = True,
    hedge_export_path: str = None,
    n_vision_frames: int = 3
) -> dict:
    """
    Full enhanced scoring pipeline.

    1. Transcript scoring (always)
    2. Vision scoring per clip (if use_vision and footage paths available)
    3. Hedge metadata merge (if hedge_export_path provided)
    4. Auto-selects: best clip per beat marked as SELECTED
    """
    brief_p = Path(brief_path)
    with open(brief_p) as f:
        brief = json.load(f)

    root = Path(config_dir) if config_dir else brief_p.parent.parent.parent
    preset = load_preset(brief.get("style_preset", "topgear"), root / "config" / "presets")

    # Step 1: Transcript scoring
    print("\n[1] Transcript scoring...")
    transcript_result = run_scorer(brief_path, config_dir)
    clips = transcript_result.get("clips", [])
    print(f"    {len(clips)} clips scored from transcripts")

    # Step 2: Vision scoring
    footage_folders = brief.get("assets", {}).get("footage_folders", [])

    # B-roll project — no transcripts, build clip list from footage files directly
    if not clips and footage_folders:
        print("\n[1b] No transcripts — building clip list from footage files...")
        clips = _build_clips_from_footage(footage_folders)
        print(f"     {len(clips)} clips found in footage folders")
        transcript_result["clips"] = clips

    if use_vision and footage_folders:
        print("\n[2] Vision scoring clips...")
        clips = _run_vision_scoring(clips, brief, preset, root)
    elif use_vision and not footage_folders:
        print("\n[2] Vision scoring skipped — no footage_folders in brief")
    else:
        print("\n[2] Vision scoring disabled")

    # Step 3: Hedge metadata
    if hedge_export_path:
        print("\n[3] Applying Hedge metadata...")
        hedge_data = load_hedge_metadata(Path(hedge_export_path))
        clips = apply_hedge_metadata(clips, hedge_data)
    else:
        print("\n[3] No Hedge export — skipping")

    # Step 4: Auto-selects
    print("\n[4] Selecting best clips per beat...")
    clips = _mark_selects(clips, brief)

    result = transcript_result.copy()
    result["clips"] = clips
    result["vision_scoring_used"] = use_vision and bool(footage_folders)
    result["hedge_used"] = bool(hedge_export_path)
    result["selects"] = [c for c in clips if "SELECTED" in c.get("flags", [])]

    n_selects = len(result["selects"])
    print(f"\n    {n_selects} clip(s) auto-selected ({len(clips)} total scored)")

    return result


def _build_clips_from_footage(footage_folders: list[str]) -> list[dict]:
    """
    Build a clip list directly from footage files for b-roll projects with no transcripts.
    Gets video duration via ffprobe and creates one clip entry per file.
    """
    import subprocess
    VIDEO_EXTS = ("*.mp4", "*.MP4", "*.mov", "*.MOV", "*.mxf", "*.MXF")
    clips = []

    for folder in footage_folders:
        fp = Path(folder)
        if not fp.exists():
            print(f"    [WARN] Footage folder not found: {fp}")
            continue
        videos = []
        for ext in VIDEO_EXTS:
            videos.extend(fp.rglob(ext))

        for i, vid in enumerate(sorted(videos)):
            # Get duration via ffprobe
            duration = _get_video_duration(vid)
            clips.append({
                "clip_id": f"{vid.stem}_full",
                "source_file": vid.name,
                "source_path": str(vid),
                "timecode_in": "00:00:00:00",
                "timecode_out": _sec_to_tc(duration) if duration else "00:00:10:00",
                "duration_sec": duration or 10.0,
                "transcript_text": "",
                "beat_id": "unassigned",
                "relevance_score": 5.0,
                "energy_score": 5.0,
                "humour_score": 0.0,
                "usability_score": 8.0,
                "composite_score": 5.0,
                "flags": [],
                "editor_note": ""
            })

    return clips


def _get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds via ffprobe."""
    import subprocess, json as _json
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", str(video_path)
        ], capture_output=True, text=True, timeout=10)
        info = _json.loads(result.stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                return float(stream.get("duration", 10.0))
    except Exception:
        pass
    return 10.0


def _run_vision_scoring(
    clips: list[dict],
    brief: dict,
    preset: dict,
    root: Path
) -> list[dict]:
    """Score each clip visually by extracting frames from source footage."""
    footage_folders = brief.get("assets", {}).get("footage_folders", [])

    # Build a lookup: source_file_stem → full video path
    video_map = {}
    for folder in footage_folders:
        fp = Path(folder)
        if fp.exists():
            for ext in ("*.mp4", "*.mov", "*.MP4", "*.MOV", "*.mxf", "*.MXF", "*.r3d"):
                for vid in fp.rglob(ext):
                    video_map[vid.stem.lower()] = vid

    frames_tmp = root / "outputs" / "_vision_tmp"
    enriched = []
    vision_count = 0

    for clip in clips:
        # Use direct path if available (b-roll clips), otherwise look up by stem
        source_path = clip.get("source_path")
        if source_path and Path(source_path).exists():
            video_path = Path(source_path)
        else:
            source_stem = Path(clip.get("source_file", "")).stem.lower()
            video_path = video_map.get(source_stem)

        if not video_path:
            # Can't find the video — skip vision for this clip
            enriched.append(clip)
            continue

        try:
            tc_in = tc_to_sec(clip.get("timecode_in", "0"))
            tc_out = tc_to_sec(clip.get("timecode_out", "0"))
            clip_frames_dir = frames_tmp / clip.get("clip_id", f"clip_{vision_count}")

            frames = extract_frames_for_clip(
                video_path, tc_in, tc_out, clip_frames_dir, n_frames=3
            )

            if frames:
                vision_scores = score_frames_for_clip(frames, clip, brief, preset)
                merged_clip = merge_scores(clip, vision_scores, preset)
                enriched.append(merged_clip)
                vision_count += 1
            else:
                enriched.append(clip)

            # Clean up frames for this clip immediately
            cleanup_frames(clip_frames_dir)

        except Exception as e:
            print(f"  [WARN] Vision scoring failed for {clip.get('clip_id')}: {e}")
            enriched.append(clip)

    print(f"    Vision scored: {vision_count}/{len(clips)} clips")
    return enriched


def _mark_selects(clips: list[dict], brief: dict) -> list[dict]:
    """
    Mark the best clip per beat as SELECTED.
    Rules:
    - Highest final_composite / composite_score
    - Not flagged UNUSABLE or PERSONAL_DATA
    - Not flagged with NO_GO violations
    """
    beats = [b["id"] for b in brief.get("beats", [])]
    disqualify_flags = {"UNUSABLE", "PERSONAL_DATA"}

    # Group by beat
    by_beat: dict[str, list] = {}
    for clip in clips:
        bid = clip.get("beat_id", "unassigned")
        by_beat.setdefault(bid, []).append(clip)

    # Pick best per beat
    selected_ids = set()
    for bid in beats:
        beat_clips = by_beat.get(bid, [])
        eligible = [
            c for c in beat_clips
            if not set(c.get("flags", [])) & disqualify_flags
            and not any(f.startswith("NO_GO:") for f in c.get("flags", []))
        ]
        if eligible:
            best = max(eligible, key=lambda c: c.get("final_composite",
                                                      c.get("composite_score", 0)))
            selected_ids.add(best.get("clip_id"))

    # Apply SELECTED flag
    result = []
    for clip in clips:
        c = dict(clip)
        if c.get("clip_id") in selected_ids:
            flags = list(c.get("flags", []))
            if "SELECTED" not in flags:
                flags.insert(0, "SELECTED")
            c["flags"] = flags
        result.append(c)

    # Re-sort: selects first, then by beat order, then by score
    beat_order = {bid: i for i, bid in enumerate(beats)}

    def sort_key(c):
        is_selected = "SELECTED" in c.get("flags", [])
        beat_pos = beat_order.get(c.get("beat_id", ""), 999)
        score = -(c.get("final_composite", c.get("composite_score", 0)))
        return (not is_selected, beat_pos, score)

    result.sort(key=sort_key)
    return result
