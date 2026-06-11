"""
Transcript scorer — reads transcript JSON files and scores each segment
against the brief using Claude API structured output.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SCORER_SYSTEM = """You are POST-01, a post-production assistant for The Film Crew (TFC).
Your job is to score transcript segments against a production brief and return a ranked clip list.
You do not make creative decisions — you surface the best candidates so the editor can.
Return only valid JSON. No commentary."""

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "source_file": {"type": "string"},
                    "timecode_in": {"type": "string"},
                    "timecode_out": {"type": "string"},
                    "transcript_text": {"type": "string"},
                    "beat_id": {"type": "string"},
                    "relevance_score": {"type": "number", "minimum": 0, "maximum": 10},
                    "energy_score": {"type": "number", "minimum": 0, "maximum": 10},
                    "humour_score": {"type": "number", "minimum": 0, "maximum": 10},
                    "usability_score": {"type": "number", "minimum": 0, "maximum": 10},
                    "composite_score": {"type": "number", "minimum": 0, "maximum": 10},
                    "flags": {"type": "array", "items": {"type": "string"}},
                    "editor_note": {"type": "string"}
                },
                "required": ["clip_id", "source_file", "timecode_in", "timecode_out",
                             "transcript_text", "beat_id", "relevance_score",
                             "energy_score", "usability_score", "composite_score", "flags"]
            }
        },
        "summary": {"type": "object"}
    },
    "required": ["clips"]
}


def load_transcripts(paths: list[str]) -> list[dict]:
    transcripts = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            print(f"  [WARN] Transcript not found: {p}")
            continue
        with open(path) as f:
            data = json.load(f)
        data["_source_file"] = path.name
        transcripts.append(data)
    return transcripts


def load_preset(preset_name: str, presets_dir: Path) -> dict:
    preset_path = presets_dir / f"{preset_name}.json"
    if not preset_path.exists():
        print(f"  [WARN] Style preset '{preset_name}' not found, using defaults")
        return {}
    with open(preset_path) as f:
        return json.load(f)


def score_transcripts(brief: dict, transcripts: list[dict], preset: dict,
                      few_shot_examples: Optional[list] = None) -> dict:
    beats_summary = "\n".join(
        f"  {b['id']}: {b['label']} — {b['description']}" for b in brief.get("beats", [])
    )
    no_go = preset.get("no_go_rules", [])
    scoring_weights = preset.get("scoring_weights", {})
    clip_rules = preset.get("clip_selection_rules", [])

    few_shot_block = ""
    if few_shot_examples:
        examples_text = json.dumps(few_shot_examples[:3], indent=2)
        few_shot_block = f"\n\nFEW-SHOT EXAMPLES (TFC briefs in this style):\n{examples_text}"

    prompt = f"""PRODUCTION BRIEF:
Project: {brief.get('project_title')}
Client: {brief.get('client', {}).get('name')}
Concept: {brief.get('concept', {}).get('logline')}
Tone: {brief.get('concept', {}).get('tone')}
Style preset: {brief.get('style_preset')}

BEATS:
{beats_summary}

STYLE RULES:
- No-go rules: {', '.join(no_go)}
- Clip selection: {', '.join(clip_rules)}
- Scoring weights: {json.dumps(scoring_weights)}
{few_shot_block}

TRANSCRIPTS TO SCORE:
{json.dumps(transcripts, indent=2)}

Score every segment across all transcript files. For each segment output:
- clip_id: "{'{'}source_file_stem{'}'}_seg_{'{'}index{'}'}"
- source_file: filename
- timecode_in / timecode_out: from the transcript (keep original format)
- transcript_text: the spoken text
- beat_id: best matching beat ID from the brief (or "unassigned" if none fits)
- relevance_score: 0–10 (how well it serves the assigned beat)
- energy_score: 0–10 (performance energy, not volume)
- humour_score: 0–10 (dry humour potential per the preset)
- usability_score: 0–10 (deduct for false starts, personal data issues, unusable audio)
- composite_score: weighted average using the scoring weights
- flags: list any of ["MUST_USE", "STRONG_CANDIDATE", "HUMOUR_GOLD", "PERSONAL_DATA",
                      "FALSE_START", "PRESENTER_STUMBLE_WORTH_KEEPING", "UNUSABLE"]
- editor_note: one short note for Ryan if anything notable

Return as JSON matching the schema. Sort clips by composite_score descending within each beat."""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=8096,
        system=SCORER_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def run_scorer(brief_path: str, config_dir: str = None) -> dict:
    brief_p = Path(brief_path)
    with open(brief_p) as f:
        brief = json.load(f)

    root = Path(config_dir) if config_dir else brief_p.parent.parent.parent
    presets_dir = root / "config" / "presets"

    preset = load_preset(brief.get("style_preset", "topgear"), presets_dir)
    transcripts = load_transcripts(brief.get("assets", {}).get("transcript_files", []))

    few_shot_paths = brief.get("few_shot_examples", [])
    few_shot_examples = []
    for p in few_shot_paths:
        fp = root / p
        if fp.exists():
            with open(fp) as f:
                few_shot_examples.append(json.load(f))

    print(f"  Scoring {len(transcripts)} transcript files against {len(brief.get('beats', []))} beats...")
    result = score_transcripts(brief, transcripts, preset, few_shot_examples)
    result["brief_id"] = brief.get("brief_id")
    result["preset_used"] = brief.get("style_preset")
    return result
