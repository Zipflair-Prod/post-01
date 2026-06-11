"""
POST-01 Deploy Script
Run this on TFCPOST01 to install and configure the pipeline.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

# Known QNAP mount locations to probe
QNAP_CANDIDATES = [
    "/Volumes/TFCPOST01",
    "/Volumes/POST01",
    "/Volumes/TFC",
    "/Volumes/Media",
    "/Volumes/QNAP",
]

GREEN  = "\033[92m"
AMBER  = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):  print(f"  {GREEN}✓{RESET}  {msg}")
def warn(msg): print(f"  {AMBER}~{RESET}  {msg}")
def fail(msg): print(f"  {RED}✗{RESET}  {msg}")
def head(msg): print(f"\n{BOLD}{msg}{RESET}")


def check_python():
    head("1 / 7  Python version")
    v = sys.version_info
    if v >= (3, 9):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
        return True
    else:
        fail(f"Python {v.major}.{v.minor} — need 3.9+. Install via python.org or brew.")
        return False


HOMEBREW_PYTHON = "/opt/homebrew/bin/python3"

def install_deps():
    head("2 / 7  Python dependencies")
    deps = ["anthropic", "reportlab", "rumps", "watchdog"]
    missing = []
    for dep in deps:
        try:
            __import__(dep)
            ok(dep)
        except ImportError:
            missing.append(dep)

    if missing:
        warn(f"Installing: {', '.join(missing)}")
        # Use homebrew Python for rumps/watchdog (needs pyobjc)
        pip_python = HOMEBREW_PYTHON if Path(HOMEBREW_PYTHON).exists() else sys.executable
        result = subprocess.run(
            [pip_python, "-m", "pip", "install", "--break-system-packages"] + missing,
            capture_output=True, text=True
        )
        if result.returncode == 0:
            ok(f"Installed: {', '.join(missing)}")
        else:
            fail(f"pip install failed:\n{result.stderr}")
            return False
    return True


def find_qnap():
    head("3 / 7  QNAP mount")
    for path in QNAP_CANDIDATES:
        if Path(path).exists():
            ok(f"Found at {path}")
            return path

    # Try listing /Volumes for anything that looks like a NAS
    volumes = list(Path("/Volumes").iterdir()) if Path("/Volumes").exists() else []
    if volumes:
        warn("Could not auto-detect QNAP. Available volumes:")
        for v in volumes:
            print(f"       {v}")
        path = input("\n  Enter QNAP mount path (or press Enter to skip): ").strip()
        if path and Path(path).exists():
            ok(f"Using {path}")
            return path

    warn("QNAP not found — outputs will write to local ./outputs/ instead")
    warn("Mount the QNAP in Finder and re-run deploy to fix this")
    return None


def write_settings(qnap_path):
    head("4 / 7  Writing config/settings.json")

    existing = {}
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            existing = json.load(f)

    settings = {
        "qnap_base": qnap_path or str(PROJECT_ROOT / "outputs"),
        "project_root": str(PROJECT_ROOT),
        "default_preset": existing.get("default_preset", "topgear"),
        "frame_rate": existing.get("frame_rate", 25),
        "davinci_scripting": existing.get("davinci_scripting", False),
    }

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
    ok(f"Written to {SETTINGS_PATH}")
    return settings


def create_output_dirs(settings):
    head("5 / 7  Output directories")
    qnap = Path(settings["qnap_base"])
    dirs = [
        qnap / "POST01_Output",
        qnap / "POST01_Output" / "ClipLists",
        qnap / "POST01_Output" / "AIPrompts",
        qnap / "POST01_Output" / "BriefingDocs",
        qnap / "POST01_Output" / "FCPXML",
        PROJECT_ROOT / "outputs",
    ]
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
            ok(str(d))
        except PermissionError:
            fail(f"Permission denied: {d}")
        except Exception as e:
            warn(f"{d} — {e}")


def check_api_key():
    head("6 / 7  ANTHROPIC_API_KEY")
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key and key != "your_key_here":
        ok(f"Set ({key[:8]}…)")
        return True
    else:
        fail("Not set — POST-01 cannot call Claude without this")
        print()
        print("  Add to ~/.zshrc:")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        print("  Then run: source ~/.zshrc")
        return False


def run_dry_run():
    head("7 / 7  Dry-run validation")
    test_script = f"""
import sys
sys.path.insert(0, '{PROJECT_ROOT}/src')
from scorer import run_scorer
from prompt_generator import run_prompt_generator
from briefing_doc import run_briefing_doc
from fcpxml_generator import tc_to_frames
from qnap_folders import create_project_folders

# Timecode parser
assert tc_to_frames('00:00:10:00') == 250, 'timecode parser failed'
assert tc_to_frames('00:01:00.000') == 1500, 'millisecond timecode parser failed'

print('imports_ok')
"""
    result = subprocess.run([sys.executable, "-c", test_script],
                            capture_output=True, text=True)
    if "imports_ok" in result.stdout:
        ok("All modules import cleanly")
        ok("Timecode parser working")
        return True
    else:
        fail("Dry-run failed:")
        print(result.stderr)
        return False


def check_davinci():
    print(f"\n{BOLD}DaVinci Resolve scripting{RESET}")
    settings_path = SETTINGS_PATH
    settings = {}
    if settings_path.exists():
        with open(settings_path) as f:
            settings = json.load(f)

    if settings.get("davinci_scripting"):
        ok("Previously confirmed enabled")
    else:
        warn("Not yet confirmed")
        print()
        print("  To enable:")
        print("  1. Open DaVinci Resolve on this machine")
        print("  2. Preferences → System → General")
        print("  3. Tick 'Enable scripting using local network'")
        print("  4. Restart DaVinci")
        print()
        confirm = input("  Press Y once you've done this (or Enter to skip): ").strip().lower()
        if confirm == "y":
            settings["davinci_scripting"] = True
            with open(settings_path, "w") as f:
                json.dump(settings, f, indent=2)
            ok("Marked as enabled in settings.json")
            print()
            print("  Test it by running:")
            print(f"  python3 {PROJECT_ROOT}/scripts/test_davinci.py")


def main():
    print(f"\n{BOLD}POST-01 Deploy — The Film Crew{RESET}")
    print(f"Project: {PROJECT_ROOT}")
    print("=" * 50)

    passed = []
    passed.append(check_python())
    passed.append(install_deps())
    qnap_path = find_qnap()
    settings = write_settings(qnap_path)
    create_output_dirs(settings)
    passed.append(check_api_key())
    passed.append(run_dry_run())
    check_davinci()

    print(f"\n{'=' * 50}")
    if all(passed):
        print(f"{GREEN}{BOLD}POST-01 ready.{RESET}")
        print()
        print("Start the app:")
        print(f"  {HOMEBREW_PYTHON} {PROJECT_ROOT}/app/post01_app.py")
        print()
        print("Install as Login Item (auto-start on boot):")
        print(f"  python3 {PROJECT_ROOT}/scripts/install_login_item.py")
        print()
        print("Or first pipeline run:")
        print(f"  cd {PROJECT_ROOT}")
        print(f"  python3 post01.py --brief config/briefs/example_cc_insurance_2026.json --skip-fcpxml")
    else:
        print(f"{AMBER}{BOLD}Setup incomplete — fix the items marked ✗ above.{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
