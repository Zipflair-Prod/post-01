"""
Visual search — find specific subjects across all footage.
Query modes:
  1. Text:       "Porsche #36", "driver helmet red", "pit lane"
  2. Screenshot: drop an image → find matching frames across footage
"""

import base64
import json
import os
import tempfile
from pathlib import Path

import anthropic

from vision.frame_extractor import extract_frames, cleanup_frames, _sec_to_tc

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SEARCH_SYSTEM = """You are POST-01's visual search engine for The Film Crew.
You analyse video frames to find specific subjects, cars, people, or moments.
Be precise about what you can and cannot see. Return only valid JSON."""


def search_footage_text(
    query: str,
    footage_paths: list[str],
    output_dir: Path,
    fps: float = 0.5,
    max_frames_per_file: int = 200,
    confidence_threshold: float = 6.0
) -> dict:
    """
    Search footage for a text query.
    e.g. query = "Porsche #36" or "driver removing helmet"
    Returns matching clips with timecodes.
    """
    all_matches = []
    frames_dir = output_dir / "_search_frames"

    print(f"\n  Visual search: '{query}'")
    print(f"  Scanning {len(footage_paths)} file(s) at {fps}fps...")

    for video_path in footage_paths:
        vp = Path(video_path)
        if not vp.exists():
            print(f"  [SKIP] Not found: {vp.name}")
            continue

        clip_frames_dir = frames_dir / vp.stem
        frames = extract_frames(vp, clip_frames_dir, fps=fps,
                                max_frames=max_frames_per_file)

        if not frames:
            continue

        print(f"  Searching {vp.name} ({len(frames)} frames)...")
        matches = _search_frames_text(query, frames, vp)
        all_matches.extend(matches)
        print(f"    → {len(matches)} match(es)")

    # Clean up temp frames
    cleanup_frames(frames_dir)

    # Filter by confidence and sort
    strong = [m for m in all_matches if m["confidence"] >= confidence_threshold]
    strong.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "query": query,
        "query_type": "text",
        "total_matches": len(strong),
        "matches": strong
    }


def search_footage_screenshot(
    reference_image_path: str,
    footage_paths: list[str],
    output_dir: Path,
    fps: float = 0.5,
    max_frames_per_file: int = 200,
    confidence_threshold: float = 6.0
) -> dict:
    """
    Search footage for frames visually similar to a reference screenshot.
    Drop a frame of Porsche #36 → find every appearance in the footage.
    """
    ref_path = Path(reference_image_path)
    if not ref_path.exists():
        return {"error": f"Reference image not found: {reference_image_path}"}

    with open(ref_path, "rb") as f:
        ref_data = base64.standard_b64encode(f.read()).decode("utf-8")

    # First, describe the reference image
    desc_response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": ref_data}
                },
                {
                    "type": "text",
                    "text": "Describe this reference image in precise detail for a visual search. "
                            "Focus on: specific identifiers (numbers, colours, logos), "
                            "subject (person, vehicle, object), distinctive features. "
                            "Be specific enough that the same subject could be found in different footage."
                }
            ]
        }]
    )
    subject_description = desc_response.content[0].text.strip()
    print(f"  Reference image: {subject_description[:120]}...")

    all_matches = []
    frames_dir = output_dir / "_search_frames"

    for video_path in footage_paths:
        vp = Path(video_path)
        if not vp.exists():
            continue

        clip_frames_dir = frames_dir / vp.stem
        frames = extract_frames(vp, clip_frames_dir, fps=fps,
                                max_frames=max_frames_per_file)
        if not frames:
            continue

        print(f"  Searching {vp.name} ({len(frames)} frames)...")
        matches = _search_frames_reference(ref_data, subject_description, frames, vp)
        all_matches.extend(matches)
        print(f"    → {len(matches)} match(es)")

    cleanup_frames(frames_dir)

    strong = [m for m in all_matches if m["confidence"] >= confidence_threshold]
    strong.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "query": f"Screenshot: {ref_path.name}",
        "subject_description": subject_description,
        "query_type": "screenshot",
        "total_matches": len(strong),
        "matches": strong
    }


def _search_frames_text(query: str, frames: list[dict], video_path: Path) -> list[dict]:
    """Batch frames and search for text query."""
    matches = []
    batch_size = 8  # frames per API call — balance cost vs coverage

    for i in range(0, len(frames), batch_size):
        batch = frames[i:i + batch_size]
        content = []

        for j, frame in enumerate(batch):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(
                        open(frame["frame_path"], "rb").read()
                    ).decode("utf-8")
                }
            })

        frame_tcs = [f["timecode_str"] for f in batch]
        content.append({
            "type": "text",
            "text": f"""Search query: "{query}"

These {len(batch)} frames are from {video_path.name}.
Frame timecodes: {frame_tcs}

For each frame where the query subject is visible, return a match.
Ignore frames where the subject is not clearly visible.

Return JSON:
{{
  "matches": [
    {{
      "frame_index": 0,
      "timecode_str": "from the list above",
      "timecode_sec": approximate seconds,
      "confidence": 0-10,
      "description": "what you can see",
      "suggested_tc_in": "suggested clip in point (2-3 sec before)",
      "suggested_tc_out": "suggested clip out point (2-3 sec after)"
    }}
  ]
}}

Only include frames with confidence >= 5. Return empty matches array if nothing found."""
        })

        try:
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                system=SEARCH_SYSTEM,
                messages=[{"role": "user", "content": content}]
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:].strip()
            result = json.loads(text)
            for m in result.get("matches", []):
                m["source_file"] = video_path.name
                m["source_path"] = str(video_path)
                matches.append(m)
        except Exception as e:
            print(f"    [WARN] Batch search error: {e}")

    return matches


def _search_frames_reference(ref_data: str, subject_desc: str,
                              frames: list[dict], video_path: Path) -> list[dict]:
    """Search frames for a reference image subject."""
    matches = []
    batch_size = 6  # smaller batches — reference image takes a slot

    for i in range(0, len(frames), batch_size):
        batch = frames[i:i + batch_size]
        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": ref_data}
            },
            {
                "type": "text",
                "text": f"Reference subject: {subject_desc}\n\nNow check these frames:"
            }
        ]

        for frame in batch:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(
                        open(frame["frame_path"], "rb").read()
                    ).decode("utf-8")
                }
            })

        frame_tcs = [f["timecode_str"] for f in batch]
        content.append({
            "type": "text",
            "text": f"""Timecodes for the frames above (in order): {frame_tcs}
Source file: {video_path.name}

Which of these frames contain the same subject as the reference image?
Look for the specific identifiers described above.

Return JSON:
{{
  "matches": [
    {{
      "frame_index_in_batch": 0,
      "timecode_str": "from list",
      "timecode_sec": 0.0,
      "confidence": 0-10,
      "description": "what matches",
      "suggested_tc_in": "suggested in point",
      "suggested_tc_out": "suggested out point"
    }}
  ]
}}"""
        })

        try:
            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                system=SEARCH_SYSTEM,
                messages=[{"role": "user", "content": content}]
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:].strip()
            result = json.loads(text)
            for m in result.get("matches", []):
                m["source_file"] = video_path.name
                m["source_path"] = str(video_path)
                matches.append(m)
        except Exception as e:
            print(f"    [WARN] Reference search error: {e}")

    return matches


def search_results_to_clips(search_results: dict) -> list[dict]:
    """Convert visual search results into POST-01 clip format for the scored list."""
    clips = []
    for i, match in enumerate(search_results.get("matches", [])):
        clips.append({
            "clip_id": f"visual_search_{i:03d}",
            "source_file": match.get("source_file", ""),
            "timecode_in": match.get("suggested_tc_in", match.get("timecode_str", "")),
            "timecode_out": match.get("suggested_tc_out", match.get("timecode_str", "")),
            "transcript_text": f"[VISUAL MATCH] {match.get('description', '')}",
            "beat_id": "unassigned",
            "composite_score": match.get("confidence", 5.0),
            "flags": ["VISUAL_SEARCH_MATCH"],
            "editor_note": f"Visual search: {search_results.get('query', '')}",
            "search_confidence": match.get("confidence", 5.0)
        })
    return clips
