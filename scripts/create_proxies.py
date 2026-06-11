"""
Proxy generator — creates H.264 1080p proxies from 4K source footage.
Run this overnight before POST-01 vision scoring or editing.

Usage:
    python3 scripts/create_proxies.py --footage /path/to/footage --output /path/to/proxies
"""

import argparse
import subprocess
import sys
from pathlib import Path

VIDEO_EXTENSIONS = ("*.mp4", "*.MP4", "*.mov", "*.MOV", "*.mxf", "*.MXF")


def create_proxy(source: Path, output_dir: Path) -> bool:
    output_dir.mkdir(parents=True, exist_ok=True)
    proxy_path = output_dir / f"{source.stem}_proxy.mp4"

    if proxy_path.exists():
        print(f"  [SKIP] {source.name} — proxy exists")
        return True

    print(f"  → {source.name}")
    cmd = [
        "ffmpeg",
        "-i", str(source),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        "-loglevel", "error",
        "-stats",
        str(proxy_path)
    ]

    result = subprocess.run(cmd)
    if result.returncode == 0:
        size_mb = proxy_path.stat().st_size / (1024 * 1024)
        print(f"     ✓ {proxy_path.name} ({size_mb:.0f}MB)")
        return True
    else:
        print(f"     ✗ Failed: {source.name}")
        proxy_path.unlink(missing_ok=True)
        return False


def run(footage_dir: Path, output_dir: Path):
    videos = []
    for ext in VIDEO_EXTENSIONS:
        videos.extend(sorted(footage_dir.rglob(ext)))

    if not videos:
        print(f"No video files found in {footage_dir}")
        sys.exit(1)

    print(f"\nProxy Generator — POST-01")
    print(f"Source:  {footage_dir} ({len(videos)} files)")
    print(f"Output:  {output_dir}")
    print("=" * 50)

    done, failed = 0, 0
    for v in videos:
        ok = create_proxy(v, output_dir)
        if ok:
            done += 1
        else:
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Done: {done}  Failed: {failed}")
    print(f"\nIn DaVinci: Playback → Proxy Media → Link Proxy Media → {output_dir}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--footage", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(Path(args.footage), Path(args.output))


if __name__ == "__main__":
    main()
