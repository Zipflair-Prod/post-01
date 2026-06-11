"""
QNAP project folder creation script.
Creates the standardised TFC folder structure for a new project.
Run this when a new brief comes in.
"""

import json
import os
from pathlib import Path

FOLDER_STRUCTURE = [
    "Footage/Camera_A",
    "Footage/Camera_B",
    "Footage/GoPro",
    "Footage/Phone",
    "Transcripts",
    "Audio/Music",
    "Audio/SFX",
    "Audio/VO",
    "Graphics/Logos",
    "Graphics/Lower_Thirds",
    "Graphics/End_Cards",
    "Assets/Photos",
    "Assets/Documents",
    "Edit/Proxies",
    "Edit/Exports",
    "Edit/Deliverables",
    "POST01_Output/ClipList",
    "POST01_Output/AIPrompts",
    "POST01_Output/BriefingDocs",
    "POST01_Output/FCPXML",
    "Briefs",
]

README_TEMPLATE = """# {project_title}
Client: {client_name}
Brief ID: {brief_id}
Created: {created_at}
Style preset: {style_preset}

## Deliverables
{deliverables}

## POST-01 Outputs
All POST-01 generated files are in POST01_Output/.
- ClipList/       — scored JSON clip list
- AIPrompts/      — prompt pack for Higgsfield / Kling / Nano Banana
- BriefingDocs/   — editor PDF briefing doc
- FCPXML/         — rough assembly for DaVinci import

## Notes
- Source of truth for scoring is the brief JSON in Briefs/
- Do not move or rename transcript files — POST-01 paths are absolute
"""


def create_project_folders(brief: dict, base_path: str = None) -> Path:
    project_folder = brief.get("project_folder")
    if base_path:
        root = Path(base_path) / brief.get("brief_id", "unnamed_project")
    elif project_folder:
        root = Path(project_folder)
    else:
        raise ValueError("brief must have 'project_folder' or provide base_path")

    print(f"  Creating project structure at: {root}")
    for folder in FOLDER_STRUCTURE:
        (root / folder).mkdir(parents=True, exist_ok=True)

    # Write README
    deliverables_text = "\n".join(
        f"- {d['label']} ({d['format']}, {d['duration_sec']}s) — {d['platform']}"
        for d in brief.get("deliverables", [])
    )
    readme = README_TEMPLATE.format(
        project_title=brief.get("project_title", ""),
        client_name=brief.get("client", {}).get("name", ""),
        brief_id=brief.get("brief_id", ""),
        created_at=brief.get("created_at", ""),
        style_preset=brief.get("style_preset", ""),
        deliverables=deliverables_text or "See brief JSON"
    )
    (root / "README.md").write_text(readme)

    # Write brief JSON into Briefs/
    brief_dest = root / "Briefs" / f"{brief.get('brief_id', 'brief')}.json"
    brief_dest.write_text(json.dumps(brief, indent=2))

    print(f"  Created {len(FOLDER_STRUCTURE)} folders + README + brief copy")
    return root


def run_qnap_folders(brief_path: str, base_path: str = None) -> Path:
    with open(brief_path) as f:
        brief = json.load(f)
    return create_project_folders(brief, base_path)
