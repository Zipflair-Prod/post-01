"""
AI prompt pack generator — takes the brief beat list and outputs
optimised prompts for Higgsfield, Kling, and Nano Banana.
"""

import json
import os
from pathlib import Path
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

PROMPT_SYSTEM = """You are POST-01, a post-production assistant for The Film Crew (TFC).
You generate AI image/video prompts for beats that require generated shots.
TFC's style: dry, British, filmic, authentic over polished.
Never use words like 'epic', 'stunning', 'cinematic', 'powerful', or 'emotional journey'.
Return only valid JSON. No commentary."""

TOOLS = ["Higgsfield", "Kling", "NanoBanana"]

TOOL_NOTES = {
    "Higgsfield": "Best for photorealistic video with motion. Strong on faces and environments.",
    "Kling": "Strong on dynamic motion, action, vehicles. Good for product and movement shots.",
    "NanoBanana": "Best for stylised / graphic / illustrative looks. Good for title cards and abstract."
}


def generate_prompts(brief: dict, preset: dict) -> dict:
    ai_beats = [b for b in brief.get("beats", []) if b.get("type") in ("ai_generated", "mixed")]

    if not ai_beats:
        return {"prompts": [], "note": "No AI-generated beats in this brief."}

    ai_style = preset.get("ai_prompt_style", {})
    avoid_words = ai_style.get("avoid_in_prompts", [])

    beats_text = json.dumps(ai_beats, indent=2)

    prompt = f"""BRIEF:
Project: {brief.get('project_title')}
Client: {brief.get('client', {}).get('name')}
Concept: {brief.get('concept', {}).get('logline')}
Tone: {brief.get('concept', {}).get('tone')}

AI PROMPT STYLE GUIDE:
- Tone: {ai_style.get('tone', 'dry, British, slightly absurdist')}
- Aesthetic: {ai_style.get('aesthetic', 'filmic over viral')}
- Never use these words in prompts: {', '.join(avoid_words)}

TOOLS AVAILABLE:
{json.dumps(TOOL_NOTES, indent=2)}

BEATS REQUIRING AI-GENERATED SHOTS:
{beats_text}

For each beat, output a prompt pack with:
- beat_id: from the brief
- beat_label: from the brief
- shots: array of shot objects, each with:
  - shot_id: "{'{'}beat_id{'}'}_shot_{'{'}index{'}'}"
  - tool: best tool for this shot (Higgsfield / Kling / NanoBanana)
  - prompt: the actual text prompt to paste into the tool
  - negative_prompt: what to avoid (optional but useful)
  - intent_note: one line on what this shot achieves in the edit
  - duration_sec: target duration if video

Generate 2–3 shot options per beat. Vary the tool where appropriate.

Return as JSON: {{"prompt_packs": [...]}}"""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4096,
        system=PROMPT_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def run_prompt_generator(brief_path: str, config_dir: str = None) -> dict:
    brief_p = Path(brief_path)
    with open(brief_p) as f:
        brief = json.load(f)

    root = Path(config_dir) if config_dir else brief_p.parent.parent.parent
    presets_dir = root / "config" / "presets"

    preset_name = brief.get("style_preset", "topgear")
    preset_path = presets_dir / f"{preset_name}.json"
    preset = {}
    if preset_path.exists():
        with open(preset_path) as f:
            preset = json.load(f)

    ai_beats = [b for b in brief.get("beats", []) if b.get("type") in ("ai_generated", "mixed")]
    print(f"  Generating AI prompts for {len(ai_beats)} beat(s)...")

    result = generate_prompts(brief, preset)
    result["brief_id"] = brief.get("brief_id")
    return result
