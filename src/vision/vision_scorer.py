"""
Vision scorer — sends frames to Claude vision API and scores clips
on visual quality, content, and style preset rules.
"""

import base64
import json
import os
from pathlib import Path
from typing import Optional

import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

VISION_SYSTEM = """You are POST-01, a post-production assistant for The Film Crew (TFC).
You score video frames for editorial quality — not generic image quality.
You understand the difference between a technically perfect frame and an
editorially useful one. Return only valid JSON."""


def _encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def score_frames_for_clip(
    frames: list[dict],
    clip: dict,
    brief: dict,
    preset: dict,
) -> dict:
    """
    Score a set of frames for a single clip.
    Returns vision scores to merge with transcript scores.
    """
    if not frames:
        return _empty_vision_score()

    beat_id = clip.get("beat_id", "")
    beats = {b["id"]: b for b in brief.get("beats", [])}
    beat = beats.get(beat_id, {})
    no_go = preset.get("no_go_rules", [])
    clip_rules = preset.get("clip_selection_rules", [])

    # Build image content blocks (max 5 frames to control cost)
    image_blocks = []
    for frame in frames[:5]:
        image_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": _encode_image(frame["frame_path"])
            }
        })

    image_blocks.append({
        "type": "text",
        "text": f"""Score these frames from a video clip for editorial use.

CLIP INFO:
- Assigned beat: {beat.get('label', beat_id)} — {beat.get('description', '')}
- Transcript: "{clip.get('transcript_text', '')[:200]}"
- Style preset no-go rules: {', '.join(no_go)}
- Clip selection preferences: {', '.join(clip_rules)}

Score each dimension 0-10:
- composition: framing, headroom, subject placement
- technical: focus, exposure, stability (not motion blur from action — that can be good)
- presenter_performance: energy, eye contact, authenticity (0 if no presenter)
- visual_interest: does this frame make you want to watch more?
- style_fit: how well it matches the preset tone (dry, authentic, not over-produced)

Also output:
- no_go_violations: list any preset no-go rules violated (e.g. "slow_motion_detected")
- visual_flags: list any of ["BEST_FRAME", "CLEAN_WIDE", "GOOD_REACTION",
                              "PRESENTER_ENERGY", "MOTION_BLUR", "OUT_OF_FOCUS",
                              "OVER_EXPOSED", "TECHNICAL_ISSUE", "DRONE_SHOT"]
- one_line_note: brief editorial note for the editor

Return JSON:
{{
  "composition": 0-10,
  "technical": 0-10,
  "presenter_performance": 0-10,
  "visual_interest": 0-10,
  "style_fit": 0-10,
  "vision_composite": 0-10,
  "no_go_violations": [],
  "visual_flags": [],
  "one_line_note": ""
}}"""
    })

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=512,
        system=VISION_SYSTEM,
        messages=[{"role": "user", "content": image_blocks}]
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:].strip()

    result = json.loads(text)

    # Calculate composite if not provided
    if "vision_composite" not in result:
        result["vision_composite"] = round(
            result.get("composition", 5) * 0.2 +
            result.get("technical", 5) * 0.2 +
            result.get("presenter_performance", 5) * 0.2 +
            result.get("visual_interest", 5) * 0.2 +
            result.get("style_fit", 5) * 0.2, 1
        )

    return result


def _empty_vision_score() -> dict:
    return {
        "composition": 5.0,
        "technical": 5.0,
        "presenter_performance": 5.0,
        "visual_interest": 5.0,
        "style_fit": 5.0,
        "vision_composite": 5.0,
        "no_go_violations": [],
        "visual_flags": [],
        "one_line_note": "No frames available for vision scoring"
    }


def merge_scores(transcript_clip: dict, vision_scores: dict, preset: dict) -> dict:
    """
    Merge transcript score and vision score into final composite.
    Weighting: 60% transcript (content), 40% vision (delivery + quality).
    """
    weights = preset.get("scoring_weights", {
        "relevance_to_brief": 0.4,
        "energy_level": 0.2,
        "humour_potential": 0.2,
        "usability": 0.2
    })

    transcript_composite = transcript_clip.get("composite_score", 5.0)
    vision_composite = vision_scores.get("vision_composite", 5.0)

    # Penalise no-go violations
    violations = vision_scores.get("no_go_violations", [])
    penalty = len(violations) * 1.5

    final_composite = round(
        transcript_composite * 0.6 + vision_composite * 0.4 - penalty, 1
    )
    final_composite = max(0.0, min(10.0, final_composite))

    merged = {**transcript_clip}
    merged["vision_scores"] = vision_scores
    merged["transcript_composite"] = transcript_composite
    merged["vision_composite"] = vision_composite
    merged["final_composite"] = final_composite
    merged["composite_score"] = final_composite  # override for downstream compatibility

    # Merge flags
    existing_flags = set(transcript_clip.get("flags", []))
    visual_flags = set(vision_scores.get("visual_flags", []))
    if violations:
        existing_flags.update([f"NO_GO:{v}" for v in violations])
    merged["flags"] = list(existing_flags | visual_flags)

    # Merge notes
    vision_note = vision_scores.get("one_line_note", "")
    existing_note = transcript_clip.get("editor_note", "")
    if vision_note and vision_note != existing_note:
        merged["editor_note"] = f"{existing_note} | {vision_note}".strip(" |")

    return merged
