"""
Rescore all clips at 1fps using proxies.
Stores per-frame scores and best usable window in clip JSON.
Run on TFCPOST01: python3 rescore_frames.py
"""
import json, os, base64, time, subprocess, tempfile
from pathlib import Path
import anthropic

CLIP_LIST  = "/Volumes/TFC/POST01_Output/260609_PORSCHE_CLASSICS_SHANGHAI_clip_list_v2.json"
PROXY_DIR  = "/Volumes/TFC/1) WORK IN PROGRESS/260609_PORSCHE_CLASSICS_SHANGHAI/4. RUSHES/Proxies"
OUT_FILE   = "/Volumes/TFC/POST01_Output/260609_PORSCHE_CLASSICS_SHANGHAI_clip_list_v3.json"
MODEL      = "claude-haiku-4-5-20251001"
SLEEP_BETWEEN = 4  # seconds between API calls

client = anthropic.Anthropic()

PROMPT = """Score this single frame from a video clip for editorial use in a Porsche brand film shot in Shanghai.
Return ONLY valid JSON, no other text:
{
  "score": <1-10 float, overall editorial value>,
  "focus": <1-10, sharpness>,
  "exposure": <1-10, 10=perfect>,
  "motion": <"static"|"smooth"|"blur"|"shake">,
  "content": <"car"|"people"|"road"|"city"|"detail"|"mixed">,
  "usable": <true|false>
}"""

def extract_frames_1fps(proxy_path):
    """Extract all frames at 1fps from proxy, return list of (second, jpeg_bytes)."""
    frames = []
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run([
            "ffmpeg", "-i", str(proxy_path),
            "-vf", "fps=1",
            "-q:v", "3",
            f"{tmpdir}/frame_%04d.jpg"
        ], capture_output=True)
        for i, f in enumerate(sorted(Path(tmpdir).glob("frame_*.jpg"))):
            frames.append((i, f.read_bytes()))
    return frames

def score_frame(jpeg_bytes):
    """Score a single frame with Haiku."""
    time.sleep(SLEEP_BETWEEN)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(jpeg_bytes).decode()
                    }},
                    {"type": "text", "text": PROMPT}
                ]
            }]
        )
        text = resp.content[0].text.strip()
        # strip markdown if present
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except Exception as e:
        print(f"    frame error: {e}")
        return {"score": 5.0, "usable": True, "motion": "unknown", "content": "unknown"}

def best_window(frame_scores, window_sec=5):
    """Find the best N-second window using sliding window on usable frames."""
    if not frame_scores:
        return 0, len(frame_scores)
    scores = [f["score"] if f.get("usable", True) else 0 for f in frame_scores]
    n = len(scores)
    w = min(window_sec, n)
    best_sum, best_start = -1, 0
    for i in range(n - w + 1):
        s = sum(scores[i:i+w])
        if s > best_sum:
            best_sum, best_start = s, i
    return best_start, best_start + w

def main():
    data = json.load(open(CLIP_LIST))
    clips = data["clips"]
    print(f"Rescoring {len(clips)} clips at 1fps from proxies\n")

    for i, clip in enumerate(clips):
        stem = clip["clip_id"].replace("_full", "")
        proxy = Path(PROXY_DIR) / f"{stem}_proxy.mp4"

        if not proxy.exists():
            print(f"[{i+1}/{len(clips)}] {stem} — no proxy, skipping")
            continue

        if clip.get("frame_scores"):
            print(f"[{i+1}/{len(clips)}] {stem} — already scored ({len(clip['frame_scores'])} frames), skipping")
            continue

        print(f"[{i+1}/{len(clips)}] {stem} — extracting frames...")
        frames = extract_frames_1fps(proxy)
        print(f"  {len(frames)} frames, scoring...")

        frame_scores = []
        for sec, jpeg_bytes in frames:
            result = score_frame(jpeg_bytes)
            result["second"] = sec
            frame_scores.append(result)
            usable = "✓" if result.get("usable") else "✗"
            print(f"  {sec:3d}s {usable} score={result.get('score',0):.1f} {result.get('content','')} {result.get('motion','')}")

        clip["frame_scores"] = frame_scores

        # best 5s window
        win_start, win_end = best_window(frame_scores, window_sec=5)
        clip["best_window_start_sec"] = win_start
        clip["best_window_end_sec"] = win_end
        clip["best_window_score"] = sum(
            f["score"] for f in frame_scores[win_start:win_end]
        ) / max(win_end - win_start, 1)

        print(f"  best window: {win_start}s–{win_end}s (avg {clip['best_window_score']:.1f})")

        # save after every clip so we don't lose progress
        json.dump(data, open(OUT_FILE, "w"), indent=2)
        print(f"  saved → v3 JSON\n")

    print(f"Done. Full results: {OUT_FILE}")

if __name__ == "__main__":
    main()
