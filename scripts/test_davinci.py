"""
Test DaVinci Resolve scripting connection.
Run after enabling scripting in DaVinci Preferences.
"""

import sys

def test_davinci():
    try:
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if resolve:
            print("✓  DaVinci Resolve scripting working")
            pm = resolve.GetProjectManager()
            print(f"✓  Project Manager: {pm}")
            return True
        else:
            print("✗  Could not connect to Resolve — is it open?")
            return False
    except ImportError:
        # Try the standard script module path
        import os
        resolve_script_path = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if os.path.exists(resolve_script_path):
            sys.path.insert(0, resolve_script_path)
            try:
                import DaVinciResolveScript as dvr
                resolve = dvr.scriptapp("Resolve")
                if resolve:
                    print("✓  DaVinci Resolve scripting working")
                    return True
            except Exception as e:
                print(f"✗  Import error: {e}")
        print("✗  DaVinci scripting module not found")
        print("   Make sure DaVinci Resolve is installed and scripting is enabled:")
        print("   Preferences → System → General → Enable scripting using local network")
        return False
    except Exception as e:
        print(f"✗  Error: {e}")
        return False


if __name__ == "__main__":
    ok = test_davinci()
    sys.exit(0 if ok else 1)
