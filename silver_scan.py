"""
GT World Challenge Asia — Silver class clip scanner.
Walks race folders, extracts one frame per MP4, asks Haiku if any Silver class
car is visible, copies matches into Output/Silver_Selects/Car_XX/.
Resume-safe: skips clips already in the log.
"""
import json, base64, time, subprocess, tempfile, shutil
from pathlib import Path
import anthropic

BASE   = Path("/Volumes/SSD8/260401 - GTWCA SEPANG/2. ASSETS/2. VIDEOS")
OUT    = Path("/Volumes/SSD8/260401 - GTWCA SEPANG/Silver_Selects")
LOG    = Path("/Volumes/SSD8/260401 - GTWCA SEPANG/silver_scan_log.json")
MODEL  = "claude-haiku-4-5-20251001"

FOLDERS = [
    "RACE 1 - BOBBY",
    "RACE 1 - HR",
    "RACE 2 - BOBBY",
    "RACE 2 - HR",
]

SILVER_ROSTER = """
Silver class cars — GT World Challenge Asia, Sepang 2026:

#10  BMW M4 GT3 EVO         — blue & gold livery        — GTO with KRC
#13  Ferrari 296 GT3 EVO    — white/grey/black livery    — 33R Harmony Racing
#16  Audi R8 LMS GT3 EVO II — blue & yellow livery       — FAW Audi Sport Asia Team Phantom
#25  Porsche 911 GT3 R EVO  — teal & black livery        — Porsche Center Okazaki
#27  Mercedes-AMG GT3 EVO   — black & orange livery      — Climax Racing
#29  Lamborghini Huracan GT3— dark navy livery           — Absolute Racing (driver: Akash Nandy)
#77  Mercedes-AMG GT3 EVO   — green & yellow ROWE livery — Craft-Bamboo Racing
#96  Ferrari 296 GT3        — anime character wrap       — Winhere Harmony Racing
"""

PROMPT = f"""You are logging footage from a GT3 motorsport weekend for a video production company.

{SILVER_ROSTER}

Look at this frame carefully. Do you see any of the Silver class cars listed above?

Return ONLY valid JSON:
{{
  "silver_detected": true or false,
  "cars": [
    {{
      "number": <race number as integer, or null if unreadable>,
      "make": "<BMW|Ferrari|Audi|Porsche|Mercedes|Lamborghini|unknown>",
      "confidence": <0.0-1.0>,
      "notes": "<brief note e.g. 'car 27 clearly visible, black/orange livery'>"
    }}
  ],
  "scene": "<track|pitlane|paddock|podium|interview|other>"
}}

If no Silver class cars are visible, return silver_detected: false and cars: [].
Only include cars you can actually see — don't guess from context.
"""

client = anthropic.Anthropic()

def clip_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 30.0

def extract_frame(mp4_path, at_sec):
    with tempfile.TemporaryDirectory() as tmp:
        out = f"{tmp}/frame.jpg"
        subprocess.run([
            "ffmpeg", "-ss", str(at_sec), "-i", str(mp4_path),
            "-vframes", "1", "-q:v", "4", "-vf", "scale=960:-1",
            out
        ], capture_output=True)
        p = Path(out)
        if p.exists():
            return p.read_bytes()
    return None

def ask_haiku(jpg_bytes):
    time.sleep(2)
    try:
        r = client.messages.create(
            model=MODEL, max_tokens=400,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(jpg_bytes).decode()
                }},
                {"type": "text", "text": PROMPT}
            ]}]
        )
        t = r.content[0].text.strip()
        if "```" in t:
            t = t.split("```")[1].lstrip("json").strip()
        return json.loads(t)
    except Exception as e:
        print(f"  API err: {e}")
        return {"silver_detected": False, "cars": [], "scene": "unknown"}

def main():
    log = json.load(open(LOG)) if LOG.exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)

    total = skipped = matched = 0

    for folder_name in FOLDERS:
        folder = BASE / folder_name
        if not folder.exists():
            print(f"Folder not found: {folder_name}")
            continue

        clips = sorted(folder.glob("*.MP4"))
        print(f"\n{'='*60}")
        print(f"{folder_name}: {len(clips)} clips")
        print(f"{'='*60}")

        for clip in clips:
            key = str(clip)
            if key in log:
                skipped += 1
                continue

            total += 1
            dur = clip_duration(clip)
            at_sec = min(10.0, dur * 0.3)

            print(f"[{total}] {clip.name} ({dur:.0f}s)", end=" ", flush=True)

            jpg = extract_frame(clip, at_sec)
            if not jpg:
                print("— no frame extracted")
                log[key] = {"silver_detected": False, "error": "no_frame"}
                json.dump(log, open(LOG, "w"), indent=2)
                continue

            result = ask_haiku(jpg)
            log[key] = {"folder": folder_name, "clip": clip.name, **result}

            if result.get("silver_detected"):
                matched += 1
                for car in result.get("cars", []):
                    num = car.get("number")
                    make = car.get("make", "unknown")
                    conf = car.get("confidence", 0)
                    label = f"Car_{num:02d}" if num else f"{make}_unknown"
                    dest_dir = OUT / label
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / clip.name
                    if not dest.exists():
                        shutil.copy2(clip, dest)
                    print(f"✓ #{num} {make} ({conf:.0%}) → {label}/")
            else:
                print(f"— no Silver cars ({result.get('scene','?')})")

            json.dump(log, open(LOG, "w"), indent=2)

    print(f"\n{'='*60}")
    print(f"Done. {total} scanned, {skipped} skipped, {matched} clips with Silver cars.")
    print(f"Results: {OUT}")
    print(f"Log: {LOG}")

if __name__ == "__main__":
    main()
