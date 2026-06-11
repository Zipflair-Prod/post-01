"""
Installs POST-01 as a macOS Login Item so it starts automatically on boot.
Run once on TFCPOST01.
"""

import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).parent.parent / "app"
LAUNCH_SCRIPT = APP_DIR / "launch_post01.sh"

def install():
    # Make launch script executable
    LAUNCH_SCRIPT.chmod(0o755)

    # Create a minimal .app wrapper so it shows nicely in Login Items
    app_path = Path("/Applications/POST-01.app")
    contents = app_path / "Contents" / "MacOS"
    contents.mkdir(parents=True, exist_ok=True)

    # Info.plist
    plist = app_path / "Contents" / "Info.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>POST-01</string>
    <key>CFBundleIdentifier</key><string>co.thefilmcrew.post01</string>
    <key>CFBundleName</key><string>POST-01</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
""")

    # Executable stub
    stub = contents / "POST-01"
    stub.write_text(f"""#!/bin/bash
exec "{LAUNCH_SCRIPT}"
""")
    stub.chmod(0o755)

    # Add to Login Items via osascript
    script = f'tell application "System Events" to make login item at end with properties {{path:"{app_path}", hidden:false}}'
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✓  POST-01 installed as Login Item")
        print(f"   App: {app_path}")
        print(f"   Will start automatically on login")
        print()
        print("To start now:")
        print(f"   open {app_path}")
    else:
        print(f"App bundle created at {app_path}")
        print("Add it manually: System Settings → General → Login Items → + → POST-01")
        print(f"(osascript error: {result.stderr})")


if __name__ == "__main__":
    install()
