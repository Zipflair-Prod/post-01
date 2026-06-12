"""
Re-run describe-only pass on all clips that have frame_scores but no cars array.
Sends the best frame image to Haiku to extract car models and colours.
~1 API call per clip = ~15 mins for 78 clips.
"""
import json, base64, time, subprocess, tempfile
from pathlib import Path
import anthropic

CLIP_LIST = "/Volumes/TFC/POST01_Output/260609_PORSCHE_CLASSICS_SHANGHAI_clip_list_v3.json"
PROXY_DIR = "/Volumes/TFC/1) WORK IN PROGRESS/260609_PORSCHE_CLASSICS_SHANGHAI/4. RUSHES/Proxies"
MODEL     = "claude-haiku-4-5-20251001"

client = anthropic.Anthropic()

PROMPT = '''You are logging a Porsche brand film clip for an editor. Look at this frame carefully.
Return ONLY valid JSON:
{
  "description": "<one sentence — include car colours and models if visible>",
  "shot_type": "wide|mid|close|detail|drone|hyperlapse",
  "tags": ["tag1","tag2"],
  "people": false,
  "cars": [{"model":"<e.g. 911 Carrera, 993, 996, 718, Cayenne — best guess>","colour":"<silver|yellow|red|black|white|blue|green|orange|grey>"}],
  "city_visible": false,
  "best_moment": "<what makes the best window good>"
}
List every car visible. Be specific on colours — not dark but navy, not light but silver.
If model is unclear write "Porsche classic". Porsche models here are likely 911 variants (993, 996, 997, 991, 992), 356, 718, Boxster, Cayman, Cayenne, Macan.'''

def extract_best_frame(proxy_path, best_sec):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([
            "ffmpeg", "-i", str(proxy_path),
            "-vf", f"select=eq(n\\,{best_sec}),scale=640:-1",
            "-vsync", "0", "-q:v", "5", f"{tmp}/best.jpg"
        ], capture_output=True)
        p = Path(tmp) / "best.jpg"
        if p.exists():
            return p.read_bytes()
    # fallback: extract at 1fps and grab the right second
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([
            "ffmpeg", "-i", str(proxy_path),
            "-vf", f"fps=1,scale=640:-1", "-q:v", "5",
            f"{tmp}/f_%04d.jpg"
        ], capture_output=True)
        frames = sorted(Path(tmp).glob("f_*.jpg"))
        idx = min(best_sec, len(frames) - 1)
        if frames:
            return frames[idx].read_bytes()
    return None

def describe(jpg_bytes, frame_summary):
    time.sleep(3)
    content = []
    if jpg_bytes:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpg_bytes).decode()
        }})
    content.append({"type": "text", "text": PROMPT + "\n\nFrame scores summary:\n" + frame_summary})
    try:
        r = client.messages.create(model=MODEL, max_tokens=500,
            messages=[{"role": "user", "content": content}])
        t = r.content[0].text.strip()
        if "```" in t:
            t = t.split("```")[1].lstrip("json").strip()
        return json.loads(t)
    except Exception as e:
        print(f"  err: {e}")
        return {"description": "", "shot_type": "", "tags": [], "cars": [], "people": False, "city_visible": False}

def main():
    data  = json.load(open(CLIP_LIST))
    clips = data["clips"]
    need  = [c for c in clips if c.get("frame_scores") and c.get("cars") is None]
    print(f"Clips needing car data: {len(need)}/{len(clips)}")

    for i, clip in enumerate(need):
        stem  = clip["clip_id"].replace("_full", "")
        px    = Path(PROXY_DIR) / f"{stem}_proxy.mp4"
        if not px.exists():
            print(f"[{i+1}/{len(need)}] no proxy: {stem}")
            continue

        print(f"[{i+1}/{len(need)}] {stem}")
        fs      = clip["frame_scores"]
        best_s  = clip.get("best_window_start_sec", 0)
        summary = "\n".join([
            f"{f['second']}s score={f.get('score','?')} {f.get('content','')} {f.get('motion','')}"
            for f in fs
        ])

        jpg = extract_best_frame(px, best_s)
        meta = describe(jpg, summary)

        clip["description"]  = meta.get("description", clip.get("description", ""))
        clip["shot_type"]    = meta.get("shot_type",   clip.get("shot_type", ""))
        clip["tags"]         = meta.get("tags",        clip.get("tags", []))
        clip["people"]       = meta.get("people",      clip.get("people", False))
        clip["cars"]         = meta.get("cars",        [])
        clip["city_visible"] = meta.get("city_visible",clip.get("city_visible", False))
        clip["best_moment"]  = meta.get("best_moment", clip.get("best_moment", ""))

        car_str = " | ".join([f"{c.get('colour','')} {c.get('model','')}" for c in clip["cars"]]) or "no cars id'd"
        print(f"  {clip['shot_type']} | {clip['description'][:70]}")
        print(f"  cars: {car_str}")

        json.dump(data, open(CLIP_LIST, "w"), indent=2)
        print(f"  saved")

    print(f"\nDone — all clips have car data now")

if __name__ == "__main__":
    main()
