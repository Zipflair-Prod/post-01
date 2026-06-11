import json, os, subprocess
from pathlib import Path
from datetime import datetime

CLIP_LIST  = "/Volumes/TFC/POST01_Output/260609_PORSCHE_CLASSICS_SHANGHAI_clip_list_v2.json"
PROXY_DIR  = "/Volumes/TFC/1) WORK IN PROGRESS/260609_PORSCHE_CLASSICS_SHANGHAI/4. RUSHES/Proxies"
OUT_DIR    = "/Volumes/TFC/1) WORK IN PROGRESS/260609_PORSCHE_CLASSICS_SHANGHAI/2. PROJECT/AI"
SOURCE_DIR = "/Volumes/TFC/1) WORK IN PROGRESS/260609_PORSCHE_CLASSICS_SHANGHAI/3. MEDIA/VIDEO/CLIENT PROVIDED/PORSCHE-CLASSICS-SHANGHAI"


def get_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 5.0


def to_fcptime(secs):
    frames = round(secs * 24000 / 1001)
    return f"{frames * 1001}/24000s"


def find_proxy(clip_id):
    stem = clip_id.replace("_full", "")
    p = Path(PROXY_DIR) / f"{stem}_proxy.mp4"
    if p.exists():
        return p
    for f in Path(SOURCE_DIR).rglob(f"{stem}.*"):
        if f.suffix.lower() in ('.mp4', '.mov'):
            return f
    return None


def load_clips():
    with open(CLIP_LIST) as f:
        data = json.load(f)
    clips = data["clips"]

    good = [c for c in clips if c.get("composite_score", c.get("final_composite", 0)) > 0]
    bad  = []
    return good, bad


def categorise(clips):
    gathering, details, motion, road = [], [], [], []
    for c in clips:
        flags = c.get("flags", [])
        score = c.get("final_composite", c.get("composite_score", 0))
        entry = (score, c)
        if "PRESENTER_ENERGY" in flags or "GOOD_REACTION" in flags:
            gathering.append(entry)
        elif "MOTION_BLUR" in flags:
            motion.append(entry)
        elif "LANDMARK_VISIBILITY" in flags or "URBAN_CONTEXT" in flags:
            road.append(entry)
        else:
            details.append(entry)
    for lst in [gathering, details, motion, road]:
        lst.sort(key=lambda x: x[0], reverse=True)
    return gathering, details, motion, road


STYLES = {
    "quiet_epic": {
        "gathering_count": 3, "gathering_dur": 2.5,
        "detail_count":    3, "detail_dur":    2.0,
        "motion_count":    1, "motion_dur":    1.0,
        "road_count":      4, "road_dur":      2.5,
        "end_dur":         5.0,
    },
    "fast_cut_to_hold": {
        "gathering_count": 4, "gathering_dur": 1.0,
        "detail_count":    6, "detail_dur":    0.5,
        "motion_count":    2, "motion_dur":    0.5,
        "road_count":      6, "road_dur":      1.0,
        "end_dur":         4.0,
    },
    "city_context_led": {
        "gathering_count": 2, "gathering_dur": 3.0,
        "detail_count":    3, "detail_dur":    2.0,
        "motion_count":    1, "motion_dur":    1.0,
        "road_count":      5, "road_dur":      2.0,
        "end_dur":         4.0,
    },
    "sound_design_first": {
        "gathering_count": 3, "gathering_dur": 2.5,
        "detail_count":    4, "detail_dur":    1.5,
        "motion_count":    2, "motion_dur":    0.75,
        "road_count":      5, "road_dur":      1.5,
        "end_dur":         4.0,
    },
    "single_take_feel": {
        "gathering_count": 2, "gathering_dur": 3.5,
        "detail_count":    3, "detail_dur":    2.5,
        "motion_count":    1, "motion_dur":    1.5,
        "road_count":      4, "road_dur":      2.5,
        "end_dur":         4.0,
    },
}


def gen_fcpxml(style_name, s, gathering, details, motion, road, all_clips):
    def pick(pool, count, fallback_offset=0):
        if len(pool) >= count:
            return pool[:count]
        need = count - len(pool)
        extra = [e for e in all_clips if e not in pool][fallback_offset:fallback_offset + need]
        return pool + extra

    g = pick(gathering, s["gathering_count"])
    d = pick(details,   s["detail_count"])
    m = pick(motion,    s["motion_count"])
    r = pick(road,      s["road_count"])

    timeline = (
        [(c, s["gathering_dur"]) for _, c in g] +
        [(c, s["detail_dur"])    for _, c in d] +
        [(c, s["motion_dur"])    for _, c in m] +
        [(c, s["road_dur"])      for _, c in r]
    )

    # Deduplicate
    seen, unique_tl = set(), []
    for c, d_ in timeline:
        if c["clip_id"] not in seen:
            seen.add(c["clip_id"])
            unique_tl.append((c, d_))

    # Last clip gets end_dur
    if unique_tl:
        c, _ = unique_tl[-1]
        unique_tl[-1] = (c, s["end_dur"])

    assets_xml, clips_xml = [], []
    offset_s = 0.0
    used_asset_ids = {}

    for clip, dur_s in unique_tl:
        proxy = find_proxy(clip["clip_id"])
        if not proxy:
            print(f"    MISSING proxy for {clip['clip_id']}, skipping")
            continue

        src_dur  = get_duration(proxy)
        dur_s    = min(dur_s, src_dur)
        start_in = max(0.0, src_dur / 2 - dur_s / 2)
        if start_in + dur_s > src_dur:
            start_in = max(0.0, src_dur - dur_s)

        cid       = clip["clip_id"]
        asset_id  = f"r{abs(hash(cid)) % 100000}"
        file_url  = "file://" + str(proxy).replace(" ", "%20")

        if asset_id not in used_asset_ids:
            used_asset_ids[asset_id] = True
            assets_xml.append(
                f'    <asset id="{asset_id}" name="{proxy.stem}" uid="{asset_id}"'
                f' src="{file_url}"\n'
                f'           start="0s" duration="{to_fcptime(src_dur)}"'
                f' hasVideo="1" hasAudio="1" videoSources="1" audioSources="1" audioChannels="2">\n'
                f'      <media-rep kind="original-media" src="{file_url}"/>\n'
                f'    </asset>'
            )

        clips_xml.append(
            f'      <asset-clip name="{proxy.stem}" ref="{asset_id}"\n'
            f'                  offset="{to_fcptime(offset_s)}" duration="{to_fcptime(dur_s)}"\n'
            f'                  start="{to_fcptime(start_in)}" tcFormat="NDF">\n'
            f'      </asset-clip>'
        )
        offset_s += dur_s

    total_dur = to_fcptime(offset_s)
    ts        = datetime.now().strftime("%Y%m%d_%H%M")
    out_name  = f"PCS_{style_name}_{ts}.fcpxml"
    out_path  = Path(OUT_DIR) / out_name

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r0" name="FFVideoFormat1080p2398" frameDuration="1001/24000s"
            width="1920" height="1080" colorSpace="1-1-1 (Rec. 709)"/>
{chr(10).join(assets_xml)}
  </resources>
  <library>
    <event name="Porsche Classics Shanghai — {style_name}">
      <project name="PCS_{style_name}">
        <sequence duration="{total_dur}" format="r0" tcStart="0s" tcFormat="NDF"
                  audioLayout="stereo" audioRate="48k">
          <spine>
{chr(10).join(clips_xml)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>'''

    out_path.write_text(xml)
    print(f"  Written ({len(unique_tl)} clips, {offset_s:.1f}s): {out_path.name}")
    return out_path


def main():
    print("Loading clips...")
    good, bad = load_clips()
    print(f"  {len(good)} usable  |  {len(bad)} filtered (NO_GO)")

    all_by_score = sorted(
        [(c.get("final_composite", c.get("composite_score", 0)), c) for c in good],
        key=lambda x: x[0],
        reverse=True
    )

    gathering, details, motion, road = categorise(good)
    print(f"  Gathering:{len(gathering)}  Details:{len(details)}  Motion:{len(motion)}  Road:{len(road)}")

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"\nOutput → {OUT_DIR}\n")

    for style_name, style in STYLES.items():
        print(f"[{style_name}]")
        gen_fcpxml(style_name, style, gathering, details, motion, road, all_by_score)

    print("\nAll done — 5 FCPXMLs ready.")


if __name__ == "__main__":
    main()
