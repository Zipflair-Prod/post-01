"""
GT World Challenge Asia — GT Academy Contender Scanner.
Finds all 7 contenders by car livery/number AND by face/helmet.

Phase 1: One frame per clip → detect car or driver → tag clip
Phase 2: 1fps on matched clips → find exact moments → trimmed FCPXML per driver

Usage:
  python3 silver_scan.py sepang
  python3 silver_scan.py mandalika

Output: /Volumes/SSD12/Silver_Selects/<Event>/<Driver_Name>/
        /Volumes/SSD12/Silver_Selects/<Event>/<Event>_<Driver>.fcpxml
"""
import json, base64, time, subprocess, tempfile, shutil, sys
from pathlib import Path
from urllib.parse import quote
import anthropic

MODEL  = "claude-haiku-4-5-20251001"
client = anthropic.Anthropic()

REF_PHOTO      = Path("/Volumes/SSD12/Silver_Selects/drivers_reference.jpg")
REF_PHOTO_NAMES = Path("/Volumes/SSD12/Silver_Selects/Names_drivers_reference.jpg")

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

# 7 GT Academy contenders — car number, make, livery, driver name(s)
CONTENDERS = [
    {"number": 10,  "make": "BMW M4 GT3",              "livery": "blue & gold",         "drivers": ["Maxime Oosten", "Brian Lee"]},
    {"number": 16,  "make": "Audi R8 LMS GT3 EVO II",  "livery": "blue & yellow",       "drivers": ["James Yu Kuai", "Cheng Congfu"]},
    {"number": 27,  "make": "Mercedes-AMG GT3",         "livery": "black & orange",      "drivers": ["Elias Seppanen", "Li Lichao"]},
    {"number": 29,  "make": "Lamborghini Huracan GT3",  "livery": "dark navy",           "drivers": ["Akash Neil Nandy"]},
    {"number": 96,  "make": "Ferrari 296 GT3",          "livery": "anime character wrap","drivers": ["Deng Yi", "Kaishun Liu"]},
    {"number": 500, "make": "Nissan GT-R NISMO GT3",    "livery": "white & black Team Szigen", "drivers": ["Atsushi Miyake"]},
]

ACADEMY_DRIVERS = [
    "James Yu Kuai", "Kaishun Liu", "Maxime Oosten",
    "Akash Neil Nandy", "Elias Seppanen", "Atsushi Miyake", "Deng Yi"
]

ROSTER_TEXT = "\n".join(
    f"  #{c['number']}  {c['make']} — {c['livery']} — {', '.join(c['drivers'])}"
    for c in CONTENDERS
)

P1_CAR_PROMPT = f"""You are logging GT3 motorsport footage for a video editor.

GT Academy contender cars:
{ROSTER_TEXT}

Look at this frame. Do you see any of these contender cars, OR any of the named drivers (helmet on or off)?

I am also sending you a reference photo of all 7 drivers together so you can match faces.

Return ONLY valid JSON:
{{
  "detected": true or false,
  "cars": [{{"number": <int or null>, "make": "<make>", "confidence": <0.0-1.0>, "notes": "<brief>"}}],
  "drivers": [{{"name": "<full name or null>", "helmet_on": true/false, "confidence": <0.0-1.0>, "notes": "<e.g. helmet on, likely Nandy based on white suit>"}}],
  "scene": "<track|pitlane|paddock|podium|interview|other>"
}}

For helmet-on shots: use suit colour, car context, and body build to make your best guess — flag confidence below 0.6 as uncertain.
If nothing detected, return detected: false, cars: [], drivers: [].
"""

P2_PROMPT = f"""You are logging GT3 motorsport footage frame by frame.

GT Academy contender cars:
{ROSTER_TEXT}

I am sending multiple frames (1 per second) from one clip, plus a reference photo of all 7 drivers.
For EACH frame identify any contender cars or drivers visible.

Return ONLY a JSON array, one entry per frame in order:
[{{
  "second": <int>,
  "detected": true/false,
  "cars": [{{"number": <int or null>, "make": "<make>", "confidence": <0.0-1.0>}}],
  "drivers": [{{"name": "<name or null>", "helmet_on": true/false, "confidence": <0.0-1.0>}}]
}}]
"""

# ── Utilities ──────────────────────────────────────────────────────────────────

def load_ref_images():
    imgs = []
    for p in [REF_PHOTO_NAMES, REF_PHOTO]:
        if p.exists():
            imgs.append(p.read_bytes())
    return imgs

def img_block(jpg_bytes):
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(jpg_bytes).decode()
    }}

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
            if Path(out).exists():
                return Path(out).read_bytes()
    return None

def extract_frames_1fps(path, scale=640):
    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([
            "ffmpeg", "-i", str(path),
            "-vf", f"fps=1,scale={scale}:-1", "-q:v", "5", f"{tmp}/f_%04d.jpg"
        ], capture_output=True)
        for p in sorted(Path(tmp).glob("f_*.jpg")):
            sec = int(p.stem.split("_")[1]) - 1
            frames.append((sec, p.read_bytes()))
    return frames

def ask_with_retry(content, max_tokens=500):
    for attempt in range(5):
        try:
            time.sleep(2 + attempt * 3)
            r = client.messages.create(model=MODEL, max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}])
            t = r.content[0].text.strip()
            if "```" in t: t = t.split("```")[1].lstrip("json").strip()
            return json.loads(t)
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                wait = 15 * (attempt + 1)
                print(f"  rate limit — waiting {wait}s...", flush=True)
                time.sleep(wait)
            else:
                print(f"  err: {e}")
                return None
    return None

def ask_p1(frame_jpg, ref_images):
    content = []
    for ref in ref_images:
        content.append(img_block(ref))
    content.append(img_block(frame_jpg))
    content.append({"type": "text", "text": P1_CAR_PROMPT})
    result = ask_with_retry(content, max_tokens=500)
    if result is None:
        return {"detected": False, "cars": [], "drivers": [], "scene": "unknown"}
    return result

def ask_p2_batch(batch, ref_images):
    content = []
    for ref in ref_images:
        content.append(img_block(ref))
    for sec, jpg in batch:
        content.append(img_block(jpg))
        content.append({"type": "text", "text": f"[Frame at {sec}s]"})
    content.append({"type": "text", "text": P2_PROMPT})
    result = ask_with_retry(content, max_tokens=1000)
    if result is None or not isinstance(result, list):
        return [{"second": s, "detected": False, "cars": [], "drivers": []} for s, _ in batch]
    return result

def find_windows(frame_results, key_fn, min_conf=0.5):
    hot = set()
    for f in frame_results:
        if not f.get("detected"):
            continue
        if key_fn(f, min_conf):
            hot.add(f["second"])
    if not hot:
        return []
    secs = sorted(hot)
    windows, start, prev = [], secs[0], secs[0]
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

def clip_tc_start(path):
    """Read embedded start timecode from file, return as seconds. Falls back to 0."""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet",
         "-show_entries", "format_tags=timecode:stream_tags=timecode",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    for line in r.stdout.strip().splitlines():
        line = line.strip()
        if ":" in line and len(line) == 11:
            try:
                h, m, s, f = [int(x) for x in line.replace(";", ":").split(":")]
                return h * 3600 + m * 60 + s + f / 24.0
            except:
                pass
    return 0.0

def build_xml(clips, event_name, label):
    total = 0.0
    assets, spine = [], []
    for i, c in enumerate(clips):
        aid = f"a{i}"
        dur = c["full_dur"]
        encoded_path = quote(c["path"], safe="/:")
        assets.append(
            f'    <asset id="{aid}" name="{c["name"]}" src="file://{encoded_path}" '
            f'start="0s" duration="{fcpt(dur)}" hasVideo="1" hasAudio="1"/>'
        )
        spine.append(
            f'      <clip name="{c["name"]}" offset="{fcpt(total)}" '
            f'duration="{fcpt(dur)}" start="0s">'
            f'<video ref="{aid}"/></clip>'
        )
        total += dur
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
        '<fcpxml version="1.10">\n  <resources>\n'
        '    <format id="r1" name="FFVideoFormat1080p24" frameDuration="1001/24000s" width="1920" height="1080"/>\n'
        + "\n".join(assets) +
        '\n  </resources>\n  <library>\n'
        f'    <event name="{event_name} — {label}">\n'
        f'      <project name="{event_name}_{label}">\n'
        f'        <sequence format="r1" duration="{fcpt(total)}" tcStart="0s">\n'
        '          <spine>\n' + "\n".join(spine) +
        '\n          </spine>\n        </sequence>\n      </project>\n    </event>\n  </library>\n</fcpxml>'
    ), total

def write_fcpxml(out_dir, event_name, clips_by_subject):
    # Per-driver timelines
    for label, clips in clips_by_subject.items():
        if not clips:
            continue
        xml_path = out_dir / f"{event_name}_{label.replace(' ','_')}.fcpxml"
        xml, total = build_xml(clips, event_name, label)
        xml_path.write_text(xml)
        print(f"  FCPXML: {xml_path.name} ({len(clips)} clips, {total:.0f}s)")

    # Combined timeline — grouped by driver in ACADEMY_DRIVERS order, deduplicated
    seen_paths = set()
    all_clips_combined = []
    for driver in ACADEMY_DRIVERS:
        label = driver.replace(" ", "_")
        driver_clips = sorted(clips_by_subject.get(label, []), key=lambda c: c["name"])
        for c in driver_clips:
            if c["path"] not in seen_paths:
                seen_paths.add(c["path"])
                all_clips_combined.append(c)
    if all_clips_combined:
        xml_path = out_dir / f"{event_name}_ALL_DRIVERS.fcpxml"
        xml, total = build_xml(all_clips_combined, event_name, "ALL_DRIVERS")
        xml_path.write_text(xml)
        print(f"  FCPXML: {xml_path.name} ({len(all_clips_combined)} clips, {total:.0f}s) ← combined")

# ── Main ───────────────────────────────────────────────────────────────────────

def all_clips(event):
    clips = []
    for folder_name in event["folders"]:
        folder = event["base"] / folder_name
        if not folder.exists():
            print(f"  [skip] not found: {folder_name}")
            continue
        found = sorted(
            p for p in folder.rglob("*")
            if p.suffix.lower() == ".mp4" and not p.name.startswith("._")
        )
        clips.extend((folder_name, p) for p in found)
    return clips

# Cars shared between two Academy drivers — attribution by face only, never car alone
SHARED_CAR_NUMBERS = {
    num for cont in CONTENDERS
    if sum(1 for d in cont["drivers"] if d in ACADEMY_DRIVERS) > 1
    for num in [cont["number"]]
}

def subjects_for_clip(frame_results):
    """
    Return set of Academy driver names this clip should be attributed to.
    Rules:
      - Driver face: name in ACADEMY_DRIVERS, confidence >= 0.6
      - Car (solo car): number matches exactly one Academy driver, confidence >= 0.75,
        car number not in SHARED_CAR_NUMBERS
      - Shared car (#96 etc.): ONLY via face detection above
    """
    subjects = set()
    for f in frame_results:
        if not f.get("detected"):
            continue
        # Named face detections
        for d in f.get("drivers", []):
            if d.get("name") in ACADEMY_DRIVERS and d.get("confidence", 0) >= 0.6:
                subjects.add(d["name"])
        # Car detections — solo-car only, high confidence
        for c in f.get("cars", []):
            num = c.get("number")
            if num is None or c.get("confidence", 0) < 0.75:
                continue
            if num in SHARED_CAR_NUMBERS:
                continue  # need face detection for shared cars
            for cont in CONTENDERS:
                if cont["number"] == num:
                    academy_in_car = [d for d in cont["drivers"] if d in ACADEMY_DRIVERS]
                    if len(academy_in_car) == 1:
                        subjects.add(academy_in_car[0])
    return subjects

def repack(event_name, event):
    """Clear driver folders and rebuild cleanly from the Phase 2 log."""
    log_path = event["log"]
    out_dir = event["out"]
    if not log_path.exists():
        print("No log found — run the full scan first.")
        return
    log = json.load(open(log_path))

    # Wipe existing driver folders (leave FCPXMLs for now)
    for d in out_dir.iterdir():
        if d.is_dir():
            shutil.rmtree(d)
            print(f"  cleared: {d.name}/")

    clips_by_subject = {}
    for key, val in sorted(log.items()):
        if not key.startswith("p2:"):
            continue
        clip_path = Path(key[3:])
        if not clip_path.exists():
            continue
        frame_results = val.get("frame_results", [])
        full_dur = clip_duration(clip_path)
        subjects = subjects_for_clip(frame_results)
        if not subjects:
            continue

        for subject in subjects:
            label = subject.replace(" ", "_")
            dest_dir = out_dir / label
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / clip_path.name
            if not dest.exists():
                shutil.copy2(clip_path, dest)
            if label not in clips_by_subject:
                clips_by_subject[label] = []
            clips_by_subject[label].append({
                "name": clip_path.stem,
                "path": str(clip_path),
                "start": 0.0,
                "end": full_dur,
                "full_dur": full_dur,
            })
        print(f"  {clip_path.name} → {', '.join(sorted(subjects))}")

    print(f"\nGenerating FCPXMLs for {event_name}...")
    write_fcpxml(out_dir, event_name, clips_by_subject)
    total = sum(len(v) for v in clips_by_subject.values())
    print(f"\nDone — {total} clips across {len(clips_by_subject)} subjects")

def main():
    fcpxml_only = "--fcpxml" in sys.argv
    repack_only = "--repack" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args or args[0] not in EVENTS:
        print("Usage: python3 silver_scan.py sepang|mandalika [--fcpxml|--repack]")
        sys.exit(1)

    event_name = args[0].capitalize()
    event = EVENTS[args[0]]

    if repack_only:
        repack(event_name, event)
        return
    if fcpxml_only:
        repack(event_name, event)  # --fcpxml now also repacks cleanly
        return
    out_dir, log_path = event["out"], event["log"]
    out_dir.mkdir(parents=True, exist_ok=True)

    ref_images = load_ref_images()
    print(f"Reference photos loaded: {len(ref_images)}")

    log = json.load(open(log_path)) if log_path.exists() else {}
    clips = all_clips(event)
    print(f"\n{event_name}: {len(clips)} clips\n")

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    print("="*60)
    print("PHASE 1 — Quick scan (1 frame per clip)")
    print("="*60)

    p1_matches = []
    for i, (folder_name, clip_path) in enumerate(clips):
        key = f"p1:{clip_path}"
        if key in log:
            if log[key].get("detected"):
                p1_matches.append(clip_path)
            continue

        dur = clip_duration(clip_path)
        at_sec = min(10.0, dur * 0.3)
        print(f"[{i+1}/{len(clips)}] {clip_path.name} ({dur:.0f}s)", end=" ", flush=True)

        jpg = extract_frame(clip_path, at_sec)
        if not jpg:
            print("— no frame")
            log[key] = {"detected": False, "error": "no_frame"}
            json.dump(log, open(log_path, "w"), indent=2)
            continue

        result = ask_p1(jpg, ref_images)
        result["folder"] = folder_name
        log[key] = result

        if result.get("detected"):
            p1_matches.append(clip_path)
            cars = [f"#{c.get('number')} {c.get('make')}" for c in result.get("cars", [])]
            drivers = [f"{d.get('name')} ({'helmet' if d.get('helmet_on') else 'face'}, {int(d.get('confidence',0)*100)}%)"
                       for d in result.get("drivers", []) if d.get("name")]
            hits = ", ".join(cars + drivers)
            print(f"✓ {hits or 'detected'} [{result.get('scene','?')}]")
        else:
            print(f"— nothing [{result.get('scene','?')}]")

        json.dump(log, open(log_path, "w"), indent=2)

    print(f"\nPhase 1 done — {len(p1_matches)} clips matched\n")

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print("="*60)
    print(f"PHASE 2 — 1fps detail scan ({len(p1_matches)} clips)")
    print("="*60)

    clips_by_subject = {}

    for i, clip_path in enumerate(p1_matches):
        key = f"p2:{clip_path}"
        if key in log:
            frame_results = log[key]["frame_results"]
        else:
            full_dur = clip_duration(clip_path)
            print(f"[{i+1}/{len(p1_matches)}] {clip_path.name} ({full_dur:.0f}s)")
            frames = extract_frames_1fps(clip_path)
            if not frames:
                print("  no frames")
                continue

            frame_results = []
            for b in range(0, len(frames), 4):
                batch = frames[b:b+4]
                results = ask_p2_batch(batch, ref_images)
                if isinstance(results, list):
                    frame_results.extend(results)
                hits = [r for r in (results if isinstance(results, list) else []) if r.get("detected")]
                print(f"  frames {b}-{b+len(batch)-1}: {len(hits)} hits", flush=True)

            log[key] = {"frame_results": frame_results}
            json.dump(log, open(log_path, "w"), indent=2)

        full_dur = clip_duration(clip_path)

        # collect all subjects (cars by number, drivers by name)
        subjects = set()
        for f in frame_results:
            if not f.get("detected"):
                continue
            for c in f.get("cars", []):
                if c.get("confidence", 0) >= 0.5:
                    num = c.get("number")
                    # map car number to driver names
                    for cont in CONTENDERS:
                        if cont["number"] == num:
                            for d in cont["drivers"]:
                                if d in ACADEMY_DRIVERS:
                                    subjects.add(d)
            for d in f.get("drivers", []):
                if d.get("confidence", 0) >= 0.4 and d.get("name") in ACADEMY_DRIVERS:
                    subjects.add(d["name"])

        for subject in subjects:
            label = subject.replace(" ", "_")

            # windows where this subject appears
            def key_fn(f, min_conf, s=subject):
                for c in f.get("cars", []):
                    if c.get("confidence", 0) >= min_conf:
                        for cont in CONTENDERS:
                            if cont["number"] == c.get("number") and s in cont["drivers"]:
                                return True
                for d in f.get("drivers", []):
                    if d.get("name") == s and d.get("confidence", 0) >= min_conf:
                        return True
                return False

            windows = find_windows(frame_results, key_fn, min_conf=0.4)
            if not windows:
                continue

            if label not in clips_by_subject:
                clips_by_subject[label] = []

            dest_dir = out_dir / label
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / clip_path.name
            if not dest.exists():
                shutil.copy2(clip_path, dest)

            for start, end in windows:
                clips_by_subject[label].append({
                    "name": clip_path.stem,
                    "path": str(clip_path),
                    "start": start,
                    "end": min(end, full_dur),
                    "full_dur": full_dur,
                })

            print(f"  → {subject}: {len(windows)} windows")

    # ── FCPXML ────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}\nGenerating FCPXMLs...")
    write_fcpxml(out_dir, event_name, clips_by_subject)

    total = sum(len(v) for v in clips_by_subject.values())
    print(f"\nDone — {total} clips across {len(clips_by_subject)} subjects")
    print(f"Output: {out_dir}")

if __name__ == "__main__":
    main()
