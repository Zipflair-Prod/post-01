"""
FCPXML rough assembly generator.
Takes the scored clip list and generates a Final Cut XML file for DaVinci import.

STATUS: PROTOTYPE — validate against real source timecodes on TFCPOST01 before relying on this.
The clip placement logic is correct in principle; in/out offset accuracy depends on your
transcript timecode format matching what's in this parser.
"""

import json
import re
from pathlib import Path
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

FRAME_RATE = 25  # TFC default — change to 29.97 if shooting NTSC


def tc_to_frames(tc: str, fps: int = FRAME_RATE) -> int:
    """Parse timecode string to frame count. Handles HH:MM:SS:FF and HH:MM:SS.mmm"""
    tc = tc.strip()
    # Drop-frame notation (for 29.97) — simplified, use proper library for broadcast
    if re.match(r"\d{2}:\d{2}:\d{2}[;:]\d{2}", tc):
        parts = re.split(r"[:;]", tc)
        h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        return (h * 3600 + m * 60 + s) * fps + f
    # Milliseconds format HH:MM:SS.mmm
    if re.match(r"\d{2}:\d{2}:\d{2}\.\d+", tc):
        parts = tc.split(":")
        h, m = int(parts[0]), int(parts[1])
        s_parts = parts[2].split(".")
        s = int(s_parts[0])
        ms = int(s_parts[1].ljust(3, "0")[:3])
        total_frames = (h * 3600 + m * 60 + s) * fps + round(ms * fps / 1000)
        return total_frames
    # Seconds as float "123.456"
    if re.match(r"^\d+\.\d+$", tc):
        return int(float(tc) * fps)
    # Plain seconds integer
    if re.match(r"^\d+$", tc):
        return int(tc) * fps
    raise ValueError(f"Unrecognised timecode format: {tc}")


def frames_to_rational(frames: int, fps: int = FRAME_RATE) -> str:
    """Convert frames to FCP rational time string e.g. '100/2500s'"""
    return f"{frames * 100}/{fps * 100}s"


def build_fcpxml(clips: list[dict], project_title: str, fps: int = FRAME_RATE) -> str:
    """Generate FCPXML 1.9 document from scored clip list."""
    root = Element("fcpxml", version="1.9")

    resources = SubElement(root, "resources")
    format_el = SubElement(resources, "format",
                           id="r1",
                           name=f"FFVideoFormat{1080}p{fps}",
                           frameDuration=f"100/{fps * 100}s",
                           width="1920", height="1080",
                           colorSpace="1-1-1 (Rec. 709)")

    # Group assets by source file
    asset_map = {}
    for clip in clips:
        src = clip.get("source_file", "unknown")
        if src not in asset_map:
            asset_id = f"r{len(asset_map) + 2}"
            asset_map[src] = asset_id
            SubElement(resources, "asset",
                       id=asset_id,
                       name=Path(src).stem,
                       src=f"file:///TFCPOST01/Projects/_REPLACE_WITH_PROJECT/Footage/{src}",
                       hasVideo="1", hasAudio="1",
                       format="r1",
                       duration=frames_to_rational(36000, fps))

    library = SubElement(root, "library")
    event = SubElement(library, "event", name=project_title)
    project = SubElement(event, "project", name=f"{project_title} — POST-01 Assembly")

    sequence = SubElement(project, "sequence",
                          format="r1",
                          duration="0s",
                          tcStart="0s",
                          tcFormat="NDF",
                          audioLayout="stereo",
                          audioRate="48k")

    spine = SubElement(sequence, "spine")

    offset_frames = 0
    for i, clip in enumerate(clips):
        try:
            in_frames = tc_to_frames(clip.get("timecode_in", "0"), fps)
            out_frames = tc_to_frames(clip.get("timecode_out", "0"), fps)
        except ValueError as e:
            print(f"  [WARN] Timecode parse error on clip {clip.get('clip_id')}: {e} — skipping")
            continue

        duration_frames = max(out_frames - in_frames, 1)
        src = clip.get("source_file", "unknown")
        asset_id = asset_map.get(src, "r2")

        clip_el = SubElement(spine, "clip",
                             name=clip.get("clip_id", f"clip_{i}"),
                             ref=asset_id,
                             duration=frames_to_rational(duration_frames, fps),
                             start=frames_to_rational(in_frames, fps),
                             offset=frames_to_rational(offset_frames, fps))

        note = clip.get("editor_note", "")
        flags = " | ".join(clip.get("flags", []))
        if note or flags:
            SubElement(clip_el, "note").text = f"{flags} {note}".strip()

        offset_frames += duration_frames

    sequence.set("duration", frames_to_rational(offset_frames, fps))

    raw = tostring(root, encoding="unicode")
    dom = minidom.parseString(raw)
    pretty = dom.toprettyxml(indent="  ")
    lines = [l for l in pretty.split("\n") if l.strip()]
    lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    lines.insert(1, '<!DOCTYPE fcpxml>')
    return "\n".join(lines)


def run_fcpxml_generator(brief_path: str, scored_clips: dict,
                         output_dir: str = None, fps: int = FRAME_RATE) -> Path:
    with open(brief_path) as f:
        brief = json.load(f)

    clips = scored_clips.get("clips", [])
    if not clips:
        print("  [WARN] No clips to assemble — skipping FCPXML generation")
        return None

    # Sort by beat order then score descending, take top clip per beat
    beats = [b["id"] for b in brief.get("beats", [])]
    best_per_beat = {}
    for clip in clips:
        bid = clip.get("beat_id", "unassigned")
        if bid not in best_per_beat or clip.get("composite_score", 0) > best_per_beat[bid].get("composite_score", 0):
            best_per_beat[bid] = clip

    ordered_clips = []
    for bid in beats:
        if bid in best_per_beat:
            ordered_clips.append(best_per_beat[bid])
    for bid, clip in best_per_beat.items():
        if bid not in beats:
            ordered_clips.append(clip)

    xml_content = build_fcpxml(ordered_clips, brief.get("project_title", "Untitled"), fps)

    out_dir = Path(output_dir) if output_dir else Path(brief_path).parent.parent.parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    brief_id = brief.get("brief_id", "unknown").replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = out_dir / f"{brief_id}_rough_assembly_{ts}.fcpxml"
    output_path.write_text(xml_content)
    print(f"  FCPXML written: {output_path} ({len(ordered_clips)} clips)")
    return output_path
