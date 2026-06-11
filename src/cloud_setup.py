"""
Blackmagic Cloud project setup — creates a new DaVinci Resolve Cloud project,
sets the library structure, and invites the assigned editor.

Called automatically by POST-01 when a new brief is processed.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
EDITORS_PATH = PROJECT_ROOT / "config" / "editors.json"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

DAVINCI_SCRIPT_PATH = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"


def load_editors() -> dict:
    if EDITORS_PATH.exists():
        with open(EDITORS_PATH) as f:
            return json.load(f)
    return {}


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    return {}


def get_resolve():
    """Connect to running DaVinci Resolve instance."""
    if DAVINCI_SCRIPT_PATH not in sys.path:
        sys.path.insert(0, DAVINCI_SCRIPT_PATH)
    try:
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if not resolve:
            raise RuntimeError("DaVinci Resolve is not open")
        return resolve
    except ImportError:
        raise RuntimeError(
            "DaVinci Resolve scripting module not found. "
            "Enable in Preferences → System → General → Enable scripting using local network"
        )


def get_or_create_library(project_manager, library_name: str):
    """Get existing library or create new one."""
    # Switch to the library if it exists
    databases = project_manager.GetDatabaseList()
    for db in databases:
        if db.get("DbName") == library_name:
            project_manager.SetCurrentDatabase(db)
            print(f"  Using existing library: {library_name}")
            return True

    # Create new cloud library
    success = project_manager.CreateCloudDatabase({
        "DbName": library_name,
        "DbType": "Cloud"
    })
    if success:
        print(f"  Created library: {library_name}")
    else:
        print(f"  [WARN] Could not create library '{library_name}' — using default")
    return success


def create_davinci_project(brief: dict, editor_ids: list[str] = None) -> dict:
    """
    Create a DaVinci Resolve Cloud project from a brief.
    Returns dict with project_name, library, status.
    """
    editors_config = load_editors()
    brief_id = brief.get("brief_id", "UNKNOWN")
    client_name = brief.get("client", {}).get("name", "CLIENT").upper().replace(" ", "_")
    project_name = brief_id

    # Library = one per client
    library_name = f"TFC_{client_name}"

    result = {
        "project_name": project_name,
        "library": library_name,
        "status": "pending",
        "editors_invited": []
    }

    try:
        resolve = get_resolve()
        pm = resolve.GetProjectManager()

        # Set up library
        get_or_create_library(pm, library_name)

        # Create project
        existing = pm.LoadProject(project_name)
        if existing:
            print(f"  Project already exists: {project_name}")
            project = existing
        else:
            project = pm.CreateProject(project_name)
            if not project:
                raise RuntimeError(f"Failed to create project: {project_name}")
            print(f"  Created project: {project_name}")

        # Set timeline resolution from deliverables
        deliverables = brief.get("deliverables", [])
        if deliverables:
            d = deliverables[0]
            fmt = d.get("format", "16:9")
            w, h = (1920, 1080) if fmt == "16:9" else (1080, 1920) if fmt == "9:16" else (1920, 1080)
            project.SetSetting("timelineResolutionWidth", str(w))
            project.SetSetting("timelineResolutionHeight", str(h))
            fps = load_settings().get("frame_rate", 25)
            project.SetSetting("timelineFrameRate", str(fps))
            print(f"  Timeline: {w}x{h} @ {fps}fps")

        # Invite editors
        invited = []
        editors_to_invite = editor_ids or []

        # Always invite editors with always_invite: true
        for eid, edata in editors_config.get("editors", {}).items():
            if edata.get("always_invite") and eid not in editors_to_invite:
                editors_to_invite.append(eid)

        for eid in editors_to_invite:
            editor = editors_config.get("editors", {}).get(eid)
            if not editor:
                print(f"  [WARN] Editor '{eid}' not in roster")
                continue
            bm_email = editor.get("bm_cloud_email", "")
            if not bm_email:
                print(f"  [WARN] No BM Cloud email for {editor.get('name')} — add to editors.json")
                continue
            # DaVinci Cloud collaboration invite
            # Note: API method depends on Resolve version — test on TFCPOST01
            try:
                project.AddCollaborator(bm_email)
                print(f"  Invited: {editor.get('name')} ({bm_email})")
                invited.append(editor.get("name"))
            except AttributeError:
                print(f"  [NOTE] AddCollaborator not available in this Resolve version")
                print(f"         Invite manually: {bm_email}")

        result["status"] = "created"
        result["editors_invited"] = invited

        # Save project
        pm.SaveProject()

    except RuntimeError as e:
        print(f"  [WARN] DaVinci not available: {e}")
        print(f"  Create project manually: {library_name} → {project_name}")
        result["status"] = "manual_required"
        result["error"] = str(e)

    return result


def assign_editor(brief: dict) -> list[str]:
    """
    Auto-assign best-fit editor(s) from roster based on project type.
    Returns list of editor IDs.
    """
    editors_config = load_editors()
    client_type = brief.get("client", {}).get("type", "")
    style_preset = brief.get("style_preset", "")

    # Map client type / preset to specialty tags
    specialty_map = {
        "automotive": "automotive",
        "sport": "sport",
        "topgear": "automotive",
        "cinematic_milestone": "automotive",
        "corporate": "corporate",
        "lifestyle": "lifestyle",
        "charity": "charity",
    }

    required_specialty = specialty_map.get(client_type) or specialty_map.get(style_preset)

    # Find editors with matching specialty
    candidates = []
    for eid, edata in editors_config.get("editors", {}).items():
        if edata.get("role") != "editor":
            continue
        specialties = edata.get("specialties", [])
        if not specialties:
            continue
        if "all" in specialties or not required_specialty:
            candidates.append(eid)
        elif required_specialty in specialties:
            candidates.append(eid)

    if candidates:
        print(f"  Auto-assigned editors: {', '.join(candidates)}")
    else:
        print(f"  No matching editors found — assign manually in editors.json")

    return candidates


def send_editor_notification(
    editor_id: str,
    brief: dict,
    briefing_doc_path: str,
    bm_project_result: dict
):
    """
    Notify assigned editor via email with everything they need.
    Requires SMTP config or can use osascript on macOS.
    """
    editors_config = load_editors()
    editor = editors_config.get("editors", {}).get(editor_id, {})
    email = editor.get("email", "")

    if not email:
        print(f"  [WARN] No email for {editor_id} — add to editors.json")
        return

    brief_id = brief.get("brief_id", "")
    client = brief.get("client", {}).get("name", "")
    project_name = bm_project_result.get("project_name", brief_id)
    library = bm_project_result.get("library", "")
    workspace = editors_config.get("workspace_url", "")

    subject = f"POST-01 — New project ready: {brief_id}"
    body = f"""Hi {editor.get('name', 'there')},

A new project is ready for you in DaVinci Resolve.

PROJECT: {project_name}
CLIENT: {client}
LIBRARY: {library}
DEADLINE: {brief.get('deliverables', [{}])[0].get('notes', 'See briefing doc')}

Open in DaVinci Resolve → Blackmagic Cloud → {library} → {project_name}
Workspace: {workspace}

Briefing doc attached — includes scored clip list, beat breakdown, and AI prompt pack.

TFC POST-01
"""

    # Use macOS Mail via osascript
    script = f'''
tell application "Mail"
    set newMessage to make new outgoing message with properties {{
        subject: "{subject}",
        content: "{body}",
        visible: true
    }}
    tell newMessage
        make new to recipient at end of to recipients with properties {{address: "{email}"}}
    end tell
    activate
end tell
'''
    try:
        subprocess.run(["osascript", "-e", script], check=True)
        print(f"  Email drafted for {editor.get('name')} ({email}) — check Mail app to send")
    except Exception as e:
        print(f"  [WARN] Could not draft email: {e}")
        print(f"  Send manually to: {email}")


def run_cloud_setup(
    brief: dict,
    briefing_doc_path: str = None,
    editor_ids: list[str] = None,
    notify_editors: bool = True
) -> dict:
    """
    Full cloud setup flow:
    1. Auto-assign editor if not specified
    2. Create DaVinci Cloud project
    3. Notify assigned editors
    """
    print("\n  Setting up Blackmagic Cloud project...")

    # Auto-assign if not specified
    if not editor_ids:
        editor_ids = assign_editor(brief)

    # Create project
    bm_result = create_davinci_project(brief, editor_ids)

    # Notify editors
    if notify_editors and briefing_doc_path:
        for eid in editor_ids:
            send_editor_notification(eid, brief, briefing_doc_path, bm_result)

    return bm_result
