"""
GT World Challenge Asia — Two-phase Silver class scanner.

Phase 1: One frame per clip → detect Silver cars present (~$3/event, fast)
Phase 2: 1fps on matched clips only → find exact moments → trimmed FCPXML

Usage:
  python3 silver_scan.py sepang
  python3 silver_scan.py mandalika

Output:
  /Volumes/SSD12/Silver_Selects/<Event>/Car_XX/   — full clip copies
  /Volumes/SSD12/Silver_Selects/<Event>/<Event>_Silver_Car_XX.fcpxml
"""
import json, base64, time, subprocess, tempfile, shutil, sys
from pathlib import Path
from datetime import datetime
import anthropic

MODEL = "claude-haiku-4-5-20251001"
client = anthropic.Anthropic()

EVENTS = {
    "sepang": {
        "base":    Path("/Volumes/SSD8/260401 - GTWCA SEPANG/2. ASSETS/2. VIDEOS"),
        "out":     Path("/Volumes/SSD12/Silver_Selects/Sepang"),
        "log":     Path("/Volumes/SSD12/Silver_Selects/sepang_log.json"),
        "folders": [
            "CARD 1", "CARD 2", "CARD 3", "CARD 4",
            "GTWCA - BOBBY QUALI",
            "GTWCA - BOBBY DAY 1", "GTWCA - BOBBY DAY 2",
            "GTWCA - BOBBY DAY 3", "GTWCA - BOBBY DAY 4",
            "ONBOARDS",
            "RACE 1 - BOBBY", "RACE 1 - HR",
            "RACE 2 - BOBBY", "RACE 2 - HR",
        ],
    },
    "mandalika": {
        "base":    Path("/Volumes/SSD12/260509 - GTWCA MANDALIKA/3. MEDIA/VIDEO/FOOTAGE FILMED"),
        "out":     Path("/Volumes/SSD12/Silver_Selects/Mandalika"),
        "log":     Path("/Volumes/SSD12/Silver_Selects/mandalika_log.json"),
        "folders": [
            "CARD 3 - PP1", "CARD 4 - PP2", "CARD 5 - PP3", "CARD 9 - FP1",
            "CARD 11.2 - PRE-QUALI", "CARD 14 - QUALI : PIT WALK",
            "CARD 16 - RACE 1", "CARD 18 - RACE 1 DRONE",
            "CARD 19 - RACE 2", "CARD 21 - RACE 2 DRONE",
        ],
    },
}

SILVER_ROSTER = """Silver class cars — GT World Challenge Asia 2026:
#10  BMW M4 GT3 EVO          — blue & gold livery
#13  Ferrari 296 GT3 EVO     — white/grey/black livery
#16  Audi R8 LMS GT3 EVO II  — blue & yellow livery
#25  Porsche 911 GT3 R EVO   — teal & black livery
#27  Mercedes-AMG GT3 EVO    — black & orange livery
#29  Lamborghini Huracan GT3 — dark navy livery (driver: Akash Nandy)
#77  Mercedes-AMG GT3 EVO    — green & yellow ROWE livery
#96  Ferrari 296 GT3         — anime character wrap livery"""

P1_PROMPT = f"""You are logging GT3 motorsport footage for a video editor.

{SILVER_ROSTER}

Look at this frame. Do you see any Silver class car listed above?

Return ONLY valid JSON:
{{
  "silver_detected": true or false,
  "cars": [{{"number": <int or null>, "make": "<make>", "confidence": <0.0-1.0>}}],
  "scene": "<track|pitlane|paddock|podium|interview|other>"
}}
Only include cars you can actually see. If none, silver_detected: false, cars: []."""

P2_PROMPT = f"""You are logging GT3 motorsport footage frame by frame.

{SILVER_ROSTER}

I am sending you multiple frames (1 per second) from a single clip. For EACH frame tell me which Silver class cars are visible.

Return ONLY a JSON array, one entry per frame in order:
[{{"second": <int>, "silver_detected": true/false, "cars": [{{"number": <int or null>, "make": "<make>", "confidence": <0.0-1.0>}}]}}]"""

# ── Utilities ──────────────────────────────────────────────────────────────────

def clip_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 30.0

def extract_frame(path, at_sec, scale=960):
    with tempfile.TemporaryDirectory() as tmp:
        out = f"{tmp}/f.jpg"
        for args in [
            ["ffmpeg", "-ss", str(at_sec), "-i", str(path), "-vframes", "1", "-q:v", "4", "-vf", f"scale={scale}:-1", out],
            ["ffmpeg", "-i", str(path), "-ss", str(at_sec), "-vframes", "1", "-q:v", "4", "-vf", f"scale={scale}:-1", out],
            ["ffmpeg", "-i", str(path), "-vframes", "1", "-q:v", "4", "-vf", f"scale={scale}:-1", out],
        ]:
            subprocess.run(args, capture_output=True)
            p = Path(out)
            if p.exists():
                return p.read_bytes()
    return None

def extract_frames_1fps(path, duration, scale=640):
    """Extract one frame per second, return list of (second, jpg_bytes)."""
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([
            "ffmpeg", "-i", str(path),
            "-vf", f"fps=1,scale={scale}:-1",
            "-q:v", "5", f"{tmp}/f_%04d.jpg"
        ], capture_output=True)
        for p in sorted(Path(tmp).glob("f_*.jpg")):
            sec = int(p.stem.split("_")[1]) - 1
            frames.append((sec, p.read_bytes()))
    return frames

def img_block(jpg_bytes):
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(jpg_bytes).decode()
    }}

def ask_p1(jpg_bytes):
    time.sleep(1.5)
    try:
        r = client.messages.create(model=MODEL, max_tokens=300, messages=[{
            "role": "user", "content": [img_block(jpg_bytes), {"type": "text", "text": P1_PROMPT}]
        }])
        t = r.content[0].text.strip()
        if "```" in t: t = t.split("```")[1].lstrip("json").strip()
        return json.loads(t)
    except Exception as e:
        print(f"  p1 err: {e}")
        return {"silver_detected": False, "cars": [], "scene": "unknown"}

def ask_p2_batch(frames_batch):
    """Send up to 5 frames at once, get per-frame Silver detection."""
    time.sleep(2)
    content = []
    for sec, jpg in frames_batch:
        content.append(img_block(jpg))
        content.append({"type": "text", "text": f"[Frame at {sec}s]"})
    content.append({"type": "text", "text": P2_PROMPT})
    try:
        r = client.messages.create(model=MODEL, max_tokens=800, messages=[{"role": "user", "content": content}])
        t = r.content[0].text.strip()
        if "```" in t: t = t.split("```")[1].lstrip("json").strip()
        return json.loads(t)
    except Exception as e:
        print(f"  p2 err: {e}")
        return [{"second": s, "silver_detected": False, "cars": []} for s, _ in frames_batch]

def find_silver_windows(frame_results, car_number=None, min_conf=0.5):
    """Find contiguous windows where a specific car (or any Silver) is visible."""
    hot = set()
    for f in frame_results:
        if not f.get("silver_detected"):
            continue
        for c in f.get("cars", []):
            if c.get("confidence", 0) >= min_conf:
                if car_number is None or c.get("number") == car_number:
                    hot.add(f["second"])
    if not hot:
        return []
    # merge into contiguous windows with 1s padding
    secs = sorted(hot)
    windows = []
    start = secs[0]
    prev = secs[0]
    for s in secs[1:]:
        if s - prev > 2:
            windows.append((max(0, start - 1), prev + 2))
            start = s
        prev = s
    windows.append((max(0, start - 1), prev + 2))
    return windows

# ── FCPXML ─────────────────────────────────────────────────────────────────────

def fcpt(secs):
    f = round(secs * 24000 / 1001)
    return f"{f * 1001}/24000s"

def write_fcpxml(out_dir, event_name, clips_by_car):
    """Generate one FCPXML per car number with trimmed clips."""
    for car_label, clips in clips_by_car.items():
        if not clips:
            continue
        xml_path = out_dir / f"{event_name}_{car_label}.fcpxml"
        total = 0.0
        asset_lines = []
        clip_lines = []
        for i, c in enumerate(clips):
            aid = f"a{i}"
            dur = c["end"] - c["start"]
            asset_lines.append(
                f'    <asset id="{aid}" name="{c["name"]}" '
                f'src="file://{c["path"]}" start="0s" '
                f'duration="{fcpt(c["full_dur"])}" hasVideo="1" hasAudio="1"/>'
            )
            clip_lines.append(
                f'      <clip name="{c["name"]}" offset="{fcpt(total)}" '
                f'duration="{fcpt(dur)}" start="{fcpt(c["start"])}">'
                f'<video ref="{aid}"/></clip>'
            )
            total += dur

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat1080p24" frameDuration="1001/24000s" width="1920" height="1080"/>
{"".join(chr(10) + l for l in asset_lines)}
  </resources>
  <library>
    <event name="{event_name} — {car_label}">
      <project name="{event_name}_{car_label}">
        <sequence format="r1" duration="{fcpt(total)}" tcStart="0s">
          <spine>
{"".join(chr(10) + l for l in clip_lines)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>"""
        xml_path.write_text(xml)
        print(f"  FCPXML: {xml_path.name} ({len(clips)} clips, {total:.0f}s)")

# ── Main ───────────────────────────────────────────────────────────────────────

def all_clips(event):
    clips = []
    for folder_name in event["folders"]:
        folder = event["base"] / folder_name
        if not folder.exists():
            continue
        for p in sorted(folder.rglob("*.MP4")):
            if not p.name.startswith("._"):
                clips.append((folder_name, p))
    return clips

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in EVENTS:
        print("Usage: python3 silver_scan.py sepang|mandalika")
        sys.exit(1)

    event_name = sys.argv[1].capitalize()
    event = EVENTS[sys.argv[1]]
    out_dir = event["out"]
    log_path = event["log"]

    out_dir.mkdir(parents=True, exist_ok=True)
    log = json.load(open(log_path)) if log_path.exists() else {}

    clips = all_clips(event)
    print(f"\n{event_name}: {len(clips)} clips total\n")

    # ── Phase 1: quick scan ────────────────────────────────────────────────────
    print("="*60)
    print("PHASE 1 — Quick scan (1 frame per clip)")
    print("="*60)

    p1_matches = []
    for i, (folder_name, clip_path) in enumerate(clips):
        key = f"p1:{clip_path}"
        if key in log:
            if log[key].get("silver_detected"):
                p1_matches.append(clip_path)
            continue

        dur = clip_duration(clip_path)
        at_sec = min(10.0, dur * 0.3)
        print(f"[{i+1}/{len(clips)}] {clip_path.name} ({dur:.0f}s)", end=" ", flush=True)

        jpg = extract_frame(clip_path, at_sec)
        if not jpg:
            print("— no frame")
            log[key] = {"silver_detected": False, "error": "no_frame"}
            json.dump(log, open(log_path, "w"), indent=2)
            continue

        result = ask_p1(jpg)
        result["folder"] = folder_name
        log[key] = result

        if result.get("silver_detected"):
            p1_matches.append(clip_path)
            cars = ", ".join(f"#{c.get('number')} {c.get('make')}" for c in result.get("cars", []))
            print(f"✓ {cars}")
        else:
            print(f"— no Silver ({result.get('scene','?')})")

        json.dump(log, open(log_path, "w"), indent=2)

    print(f"\nPhase 1 done — {len(p1_matches)} clips with Silver cars\n")

    # ── Phase 2: 1fps detail scan on matches ───────────────────────────────────
    print("="*60)
    print(f"PHASE 2 — 1fps detail scan ({len(p1_matches)} clips)")
    print("="*60)

    clips_by_car = {}

    for i, clip_path in enumerate(p1_matches):
        key = f"p2:{clip_path}"
        if key in log:
            frame_results = log[key]["frame_results"]
        else:
            dur = clip_duration(clip_path)
            print(f"[{i+1}/{len(p1_matches)}] {clip_path.name} ({dur:.0f}s) — extracting frames...")
            frames = extract_frames_1fps(clip_path, dur)
            if not frames:
                print("  no frames")
                continue

            frame_results = []
            batch_size = 4
            for b in range(0, len(frames), batch_size):
                batch = frames[b:b+batch_size]
                results = ask_p2_batch(batch)
                frame_results.extend(results if isinstance(results, list) else [])
                detected = [r for r in results if r.get("silver_detected")]
                print(f"  frames {b}-{b+len(batch)-1}: {len(detected)} with Silver", flush=True)

            log[key] = {"frame_results": frame_results}
            json.dump(log, open(log_path, "w"), indent=2)

        # find all car numbers detected in this clip
        car_numbers = set()
        for f in frame_results:
            for c in f.get("cars", []):
                if c.get("confidence", 0) >= 0.5 and f.get("silver_detected"):
                    num = c.get("number")
                    make = c.get("make", "unknown")
                    car_numbers.add((num, make))

        full_dur = clip_duration(clip_path)

        for num, make in car_numbers:
            windows = find_silver_windows(frame_results, car_number=num)
            label = f"Car_{num:02d}" if num else f"{make}_unknown"

            if label not in clips_by_car:
                clips_by_car[label] = []

            for start, end in windows:
                end = min(end, full_dur)
                clips_by_car[label].append({
                    "name": clip_path.stem,
                    "path": str(clip_path),
                    "start": start,
                    "end": end,
                    "full_dur": full_dur,
                })
                # copy full clip to folder
                dest_dir = out_dir / label
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / clip_path.name
                if not dest.exists():
                    shutil.copy2(clip_path, dest)

            print(f"  → {label}: {len(windows)} windows")

    # ── FCPXML ────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Generating FCPXMLs...")
    write_fcpxml(out_dir, event_name, clips_by_car)

    total_clips = sum(len(v) for v in clips_by_car.values())
    print(f"\nDone — {total_clips} trimmed clips across {len(clips_by_car)} cars")
    print(f"Output: {out_dir}")

if __name__ == "__main__":
    main()
