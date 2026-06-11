#!/bin/bash
# POST-01 launcher — sets env and starts the menu bar app
# Add this as a Login Item on TFCPOST01 so it starts automatically

# Load API key from zshrc if not already set
if [ -z "$ANTHROPIC_API_KEY" ]; then
    source ~/.zshrc 2>/dev/null
fi

# Use homebrew Python (has rumps + watchdog)
PYTHON=/opt/homebrew/bin/python3
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

exec "$PYTHON" "$APP_DIR/post01_app.py"
