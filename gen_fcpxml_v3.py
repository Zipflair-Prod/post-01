"""
Multi-pass FCPXML generator using v3 per-frame scored clip data.

P1  : Every clip at its best window — full selects in shoot order
P2  : ~5-7 min selects, variety-balanced by shot type
P3_DOC  : 30s documentary cut (Michael's brief — understated, chronological)
P3_REEL : 30s social reel (director's cut — city cold open, reactions, statics, road)

RWB exclusion: blue 997 Speedster blacklisted from all passes except P1.
"""
import json, os, subprocess
from pathlib import Path
from datetime import datetime

CLIP_LIST = "/Volumes/TFC/POST01_Output/260609_PORSCHE_CLASSICS_SHANGHAI_clip_list_v3.json"
PROXY_DIR = "/Volumes/TFC/1) WORK IN PROGRESS/260609_PORSCHE_CLASSICS_SHANGHAI/4. RUSHES/Proxies"
OUT_DIR   = "/Volumes/TFC/1) WORK IN PROGRESS/260609_PORSCHE_CLASSICS_SHANGHAI/2. PROJECT/AI"

# ── RWB exclusion ─────────────────────────────────────────────────────────────
def is_rwb(clip):
    """Blue 997 Speedster with RWB bodykit — exclude from all curated passes."""
    for car in clip.get("cars", []):
        model = car.get("model", "").lower()
        colour = car.get("colour", "").lower()
        if "blue" in colour and ("997" in model or "speedster" in model):
            return True
    tags = [t.lower() for t in clip.get("tags", [])]
    return "rwb" in tags or "rauh-welt" in tags

# ── Utilities ──────────────────────────────────────────────────────────────────
def proxy_path(clip):
    stem = clip["clip_id"].replace("_full", "")
    p = Path(PROXY_DIR) / f"{stem}_proxy.mp4"
    return p if p.exists() else None

def proxy_duration(p):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 5.0

def fcpt(secs):
    f = round(secs * 24000 / 1001)
    return f"{f * 1001}/24000s"

def best_start(clip, proxy_dur, want_dur):
    ws = clip.get("best_window_start_sec", 0)
    we = clip.get("best_window_end_sec", want_dur)
    use_dur = min(want_dur, we - ws, proxy_dur)
    start = min(ws, max(0.0, proxy_dur - use_dur))
    return start, use_dur

def clip_tags(clip):
    tags = set(t.lower() for t in clip.get("tags", []))
    for car in clip.get("cars", []):
        if car.get("model"):  tags.add(car["model"].lower())
        if car.get("colour"): tags.add(car["colour"].lower())
    return tags

def car_key(clip):
    """Unique key for the primary car in a clip — used to enforce variety."""
    cars = clip.get("cars", [])
    if not cars:
        return None
    c = cars[0]
    return f"{c.get('colour','')} {c.get('model','')}".strip().lower()

# ── FCPXML writer ─────────────────────────────────────────────────────────────
def write_fcpxml(pass_name, timeline_clips, out_dir):
    if not timeline_clips:
        print(f"  [{pass_name}] no clips — skipped")
        return

    assets_xml, clips_xml = [], []
    offset_s = 0.0
    used_assets = {}

    for clip, want_dur in timeline_clips:
        px = proxy_path(clip)
        if not px:
            print(f"  MISSING proxy: {clip['clip_id']}")
            continue
        src_dur = proxy_duration(px)
        start_in, use_dur = best_start(clip, src_dur, want_dur)
        aid = f"r{abs(hash(clip['clip_id'])) % 99991}"
        url = "file://" + str(px).replace(" ", "%20")

        if aid not in used_assets:
            used_assets[aid] = True
            assets_xml.append(
                f'    <asset id="{aid}" name="{px.stem}" uid="{aid}" src="{url}"\n'
                f'           start="0s" duration="{fcpt(src_dur)}" hasVideo="1" hasAudio="1"\n'
                f'           videoSources="1" audioSources="1" audioChannels="2">\n'
                f'      <media-rep kind="original-media" src="{url}"/>\n'
                f'    </asset>'
            )
        clips_xml.append(
            f'      <asset-clip name="{px.stem}" ref="{aid}"\n'
            f'                  offset="{fcpt(offset_s)}" duration="{fcpt(use_dur)}"\n'
            f'                  start="{fcpt(start_in)}" tcFormat="NDF"/>'
        )
        offset_s += use_dur

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path(out_dir) / f"PCS_{pass_name}_{ts}.fcpxml"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
        '<fcpxml version="1.10">\n  <resources>\n'
        '    <format id="r0" name="FFVideoFormat1080p2398" frameDuration="1001/24000s"'
        ' width="1920" height="1080" colorSpace="1-1-1 (Rec. 709)"/>\n'
        + "\n".join(assets_xml) +
        '\n  </resources>\n  <library>\n'
        f'    <event name="PCS {pass_name}">\n      <project name="PCS_{pass_name}">\n'
        f'        <sequence duration="{fcpt(offset_s)}" format="r0" tcStart="0s"'
        f' tcFormat="NDF" audioLayout="stereo" audioRate="48k">\n          <spine>\n'
        + "\n".join(clips_xml) +
        '\n          </spine>\n        </sequence>\n      </project>\n'
        '    </event>\n  </library>\n</fcpxml>'
    )
    out_path.write_text(xml)
    print(f"  [{pass_name}] {len(clips_xml)} clips, {offset_s/60:.1f} mins → {out_path.name}")
    return out_path


# ── Story beat classification ─────────────────────────────────────────────────
# Based on clip descriptions + shoot order (clip number = roughly chronological)
CITY_CLIPS      = {"6364", "6365", "6313"}
PEOPLE_CLIPS    = {"6316", "6319", "6320", "6326", "6327", "6385", "6382"}
STATIC_CLIPS    = {"6362", "6363", "6341", "6336", "6340", "6332", "6311",
                   "6323", "6322", "6331", "6338", "6344", "6345", "6376",
                   "6317", "6318", "6321", "6348", "6349", "6359"}
DETAIL_CLIPS    = {"6325", "6329", "6334", "6335", "6337", "6343", "6333",
                   "6381", "6328", "6330", "6324", "6384", "6388", "6386"}
DRIVING_CLIPS   = {"6371", "6360", "6356", "6366", "6370", "6361", "6368",
                   "6373", "6378", "6353", "6357", "6354", "6346", "6347",
                   "6355", "6367", "6375", "6374", "6358", "6369", "6312",
                   "6372", "6380"}
FINALE_CLIPS    = {"6379", "6383", "6377", "6350", "6352", "6387", "6386",
                   "6315", "6314", "6342", "6351"}

def clip_num(clip):
    return clip["clip_id"].replace("B-Cam20260602_", "").replace("_full", "")

# ── Beat scorers ───────────────────────────────────────────────────────────────
def score_beat(clip, beat):
    bws = clip.get("best_window_score", clip.get("composite_score", 5))
    num = clip_num(clip)
    tags = clip_tags(clip)
    st   = clip.get("shot_type", "")
    people = clip.get("people", False)
    moving = any(t in tags for t in ("driving","tunnel","road","moving","convoy","motion"))

    # ── Documentary beats (hard-mapped from clip descriptions) ───────────────
    if beat == "doc_city":
        return bws + (10 if num in CITY_CLIPS else 0)

    if beat == "doc_people":
        return bws + (10 if num in PEOPLE_CLIPS else 0) + (-5 if moving else 0)

    if beat == "doc_static":
        return bws + (8 if num in STATIC_CLIPS else 0) + (4 if num in DETAIL_CLIPS else 0) + (-5 if people else 0)

    if beat == "doc_detail":
        return bws + (10 if num in DETAIL_CLIPS else 0) + (-5 if people else 0)

    if beat == "doc_drive":
        return bws + (10 if num in DRIVING_CLIPS else 0) + (-3 if people else 0)

    if beat == "doc_finale":
        return bws + (10 if num in FINALE_CLIPS else 0) + (-3 if moving else 0)

    if beat == "doc_hero":
        return bws + (6 if num in STATIC_CLIPS and not people else 0) + (4 if st == "wide" else 0)

    # ── Reel beats ───────────────────────────────────────────────────────────
    if beat == "reel_city":
        return bws + (10 if num in CITY_CLIPS else 0)

    if beat == "reel_reaction":
        return bws + (10 if num in PEOPLE_CLIPS else 0) + (-3 if moving else 0)

    if beat == "reel_hyperzoom":
        return bws + (6 if st == "hyperlapse" else 0) + (3 if "hyperzoom" in tags or "zoom" in tags else 0)

    if beat == "reel_static":
        return bws + (8 if num in STATIC_CLIPS else 0) + (6 if num in DETAIL_CLIPS else 0) + (-5 if people else 0)

    if beat == "reel_road":
        return bws + (10 if num in DRIVING_CLIPS else 0) + (2 if "tunnel" in tags else 0)

    if beat == "reel_hero":
        return bws + (8 if num in STATIC_CLIPS and not people else 0) + (4 if st == "wide" else 0)

    return bws


def build_pass3(usable, beats, used_ids=None):
    """
    beats: list of (beat_name, want_dur, max_clips)
    Returns list of (clip, dur).
    Enforces no consecutive same car-key in static beats.
    """
    used = set(used_ids or [])
    timeline = []
    last_car = None

    for beat, want_dur, max_clips in beats:
        candidates = [c for c in usable if c["clip_id"] not in used]
        ranked = sorted(candidates, key=lambda c: score_beat(c, beat), reverse=True)

        added = 0
        for c in ranked:
            if added >= max_clips:
                break
            ck = car_key(c)
            # avoid same car twice in a row during static sections
            if "static" in beat and ck and ck == last_car:
                continue
            timeline.append((c, want_dur))
            used.add(c["clip_id"])
            last_car = ck
            added += 1

    # Trim to 30s
    total = sum(d for _, d in timeline)
    if total > 32:
        out, acc = [], 0.0
        for c, d in timeline[:-1]:
            out.append((c, d))
            acc += d
        last_c, _ = timeline[-1]
        out.append((last_c, max(1.5, 30.0 - acc)))
        timeline = out

    return timeline


def main():
    data  = json.load(open(CLIP_LIST))
    clips = data["clips"]
    print(f"Loaded {len(clips)} clips from v3")

    # Clips with proxy + scoring
    usable_all = [c for c in clips if proxy_path(c) and c.get("frame_scores")]
    rwb_clips  = [c for c in usable_all if is_rwb(c)]
    usable     = [c for c in usable_all if not is_rwb(c)]

    print(f"Usable: {len(usable)}  |  RWB excluded: {len(rwb_clips)}  |  No proxy: {len(clips)-len(usable_all)}")
    if rwb_clips:
        print(f"  RWB clips: {[c['clip_id'] for c in rwb_clips]}")

    usable_sorted = sorted(usable, key=lambda c: c.get("best_window_score", c.get("composite_score", 0)), reverse=True)

    os.makedirs(OUT_DIR, exist_ok=True)

    # ── P1: ALL CLIPS in shoot order ─────────────────────────────────────────
    p1 = sorted(usable_all, key=lambda c: c["clip_id"])  # includes RWB — editor can see everything
    p1_tl = [(c, min(
        c.get("best_window_end_sec", 5) - c.get("best_window_start_sec", 0),
        c.get("duration_sec", 5)
    )) for c in p1]
    write_fcpxml("P1_ALL", p1_tl, OUT_DIR)

    # ── P2: SELECTS 5-7 mins, variety-balanced ───────────────────────────────
    target_secs = 360  # 6 mins
    by_type  = {}
    for c in usable_sorted:
        st = c.get("shot_type", "wide")
        by_type.setdefault(st, []).append(c)

    type_order   = ["wide", "mid", "detail", "close", "hyperlapse", "drone"]
    type_iters   = {t: iter(by_type.get(t, [])) for t in type_order}
    added_ids, p2, total = set(), [], 0.0
    fallback_iter = iter(usable_sorted)

    while total < target_secs:
        found = False
        for t in type_order:
            c = next(type_iters[t], None)
            if c and c["clip_id"] not in added_ids:
                win = c.get("best_window_end_sec", 5) - c.get("best_window_start_sec", 0)
                d   = max(2.0, min(win, 7.0))
                p2.append((c, d)); added_ids.add(c["clip_id"]); total += d
                found = True
                if total >= target_secs: break
        if not found:
            c = next(fallback_iter, None)
            if not c: break
            if c["clip_id"] not in added_ids:
                win = c.get("best_window_end_sec", 5) - c.get("best_window_start_sec", 0)
                d   = max(2.0, min(win, 7.0))
                p2.append((c, d)); added_ids.add(c["clip_id"]); total += d

    write_fcpxml("P2_SELECTS", p2, OUT_DIR)

    # ── P3_DOC: 30s documentary (Michael's brief) ────────────────────────────
    # 0-3s   : Shanghai city establishing (6364/6365)
    # 3-7s   : People gathering at Porsche Centre
    # 7-14s  : Statics outside — cars lined up, one detail cut
    # 14-16s : Detail — badge/wheel close-up
    # 16-24s : On the road through Shanghai
    # 24-27s : Glass building finale
    # 27-30s : Hero hold
    doc_beats = [
        ("doc_city",   3.0, 1),   # 3s  — 1 city wide
        ("doc_people", 2.0, 2),   # 4s  — 2 people shots
        ("doc_static", 2.5, 3),   # 7s  — 3 statics
        ("doc_detail", 1.5, 1),   # 1.5s — 1 detail
        ("doc_drive",  2.0, 4),   # 8s  — 4 driving shots
        ("doc_finale", 1.5, 2),   # 3s  — 2 finale shots
        ("doc_hero",   3.0, 1),   # 3s  — hero hold
    ]
    p3_doc = build_pass3(usable_sorted, doc_beats)
    write_fcpxml("P3_DOC", p3_doc, OUT_DIR)

    # ── P3_REEL: 30s social/director's cut ───────────────────────────────────
    # 0-3s   : Shanghai city cold open
    # 3-8s   : People reactions — amazed, excited (fast cuts, building)
    # 7-8s   : Hyperzoom outside Porsche Centre — the pivot
    # 8-16s  : Statics — every car, every colour, fast cuts, no repeats
    # 16-23s : On the road — convoy, tunnel, motion
    # 23-27s : Shanghai + cars together
    # 27-30s : Hero hold
    reel_beats = [
        ("reel_city",      3.0, 1),   # 3s  — city cold open
        ("reel_reaction",  1.2, 4),   # ~5s — reaction montage
        ("reel_hyperzoom", 1.5, 1),   # 1.5s — hyperzoom pivot
        ("reel_static",    1.0, 8),   # 8s  — fast-cut statics, car variety enforced
        ("reel_road",      1.5, 4),   # 6s  — driving/tunnel
        ("reel_city",      1.5, 2),   # 3s  — Shanghai backdrop
        ("reel_hero",      3.0, 1),   # 3s  — hero hold
    ]
    p3_reel = build_pass3(usable_sorted, reel_beats)
    write_fcpxml("P3_REEL", p3_reel, OUT_DIR)

    # ── P4: 1-MIN BEST OF — cars only, max variety ───────────────────────────
    # Every car model/colour gets its moment. No people. Mix of wide/mid/detail.
    # ~60s target. No repeat of same car key back-to-back.
    car_only = [c for c in usable_sorted
                if not c.get("people", False)
                and c.get("cars") and len(c["cars"]) > 0]

    # Build ordered list ensuring car variety — cycle through unique car keys
    from collections import defaultdict
    by_car = defaultdict(list)
    for c in car_only:
        ck = car_key(c)
        by_car[ck].append(c)

    # Sort each car's clips by best_window_score
    for ck in by_car:
        by_car[ck].sort(key=lambda c: c.get("best_window_score", 0), reverse=True)

    # Round-robin through cars, mix shot types per car (wide then detail)
    car_keys_sorted = sorted(by_car.keys(), key=lambda k: -by_car[k][0].get("best_window_score", 0))
    p4, used_p4, total_p4 = [], set(), 0.0
    target_p4 = 62.0

    # First pass: one best clip per car
    for ck in car_keys_sorted:
        if total_p4 >= target_p4:
            break
        for c in by_car[ck]:
            if c["clip_id"] not in used_p4:
                st = c.get("shot_type", "mid")
                d  = 3.0 if st == "wide" else 2.0 if st == "mid" else 1.5
                p4.append((c, d))
                used_p4.add(c["clip_id"])
                total_p4 += d
                break

    # Second pass: fill remaining time with detail shots of same cars
    for ck in car_keys_sorted:
        if total_p4 >= target_p4:
            break
        for c in by_car[ck]:
            if c["clip_id"] not in used_p4 and c.get("shot_type") in ("detail", "close"):
                p4.append((c, 1.5))
                used_p4.add(c["clip_id"])
                total_p4 += 1.5
                break

    # Trim last clip to hit ~60s
    if p4:
        acc = sum(d for _, d in p4[:-1])
        last_c, _ = p4[-1]
        p4[-1] = (last_c, max(1.5, 60.0 - acc))

    write_fcpxml("P4_BESTOF_1MIN", p4, OUT_DIR)

    # ── Car inventory ─────────────────────────────────────────────────────────
    print(f"\nCar inventory (curated clips only):")
    car_counts = {}
    for c in usable:
        for car in c.get("cars", []):
            key = f"{car.get('colour','?')} {car.get('model','Porsche')}".strip()
            car_counts[key] = car_counts.get(key, 0) + 1
    for car, count in sorted(car_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {car}: {count} clips")

    print(f"\nAll done — 4 FCPXMLs in AI folder")
    print(f"  P1_ALL    — full selects, shoot order (includes RWB for reference)")
    print(f"  P2_SELECTS — ~6 min variety selects")
    print(f"  P3_DOC    — 30s documentary (Michael's brief)")
    print(f"  P3_REEL   — 30s social/director's cut")


if __name__ == "__main__":
    main()
