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

def best_window_for_dur(clip, want_dur):
    """Find the best N-second window from per-frame scores."""
    fs = clip.get("frame_scores", [])
    if not fs:
        return clip.get("best_window_start_sec", 0), want_dur
    scores = [f.get("score", 0) if f.get("usable", True) else 0 for f in fs]
    n = len(scores)
    w = max(1, min(round(want_dur), n))
    best_sum, best_i = -1, 0
    for i in range(n - w + 1):
        s = sum(scores[i:i+w])
        if s > best_sum:
            best_sum, best_i = s, i
    return best_i, want_dur

def best_start(clip, proxy_dur, want_dur):
    ws, use_dur = best_window_for_dur(clip, want_dur)
    use_dur = min(use_dur, proxy_dur)
    start = min(ws, max(0.0, proxy_dur - use_dur))
    return start, use_dur

def clip_tags(clip):
    tags = set(t.lower() for t in clip.get("tags", []))
    for car in clip.get("cars", []):
        if car.get("model"):  tags.add(car["model"].lower())
        if car.get("colour"): tags.add(car["colour"].lower())
    return tags

def normalise_model(model):
    """Collapse model variants to a canonical name."""
    m = model.lower()
    for classic in ("356",):
        if classic in m: return "356"
    for code in ("993","996","997","991","992","918","928","914","912","718"):
        if code in m: return f"911-{code}" if code not in ("918","928","914","912","718") else code
    if "targa" in m: return "targa"
    if "speedster" in m: return "speedster"
    if "carrera rs" in m: return "carrera-rs"
    if "911" in m: return "911-classic"
    return m.split()[0] if m else "porsche"

# Rarer colours score lower = appear earlier in sorted order
COLOUR_RARITY = {
    "lime":1, "gold":2, "sage":3, "forest":4, "cream":5, "tan":6,
    "pale":7, "yellow":8, "green":9, "red":10, "orange":11,
    "light":12, "blue":13, "navy":14, "white":15, "beige":16,
    "dark":17, "grey":18, "black":19, "silver":20,
}

def colour_score(colour):
    first = colour.lower().split()[0] if colour else "silver"
    return COLOUR_RARITY.get(first, 15)

def car_key(clip):
    """Pick the most visually distinctive car in the clip as the key."""
    cars = [c for c in clip.get("cars", []) if c.get("colour") or c.get("model")]
    if not cars: return None
    # Pick rarest colour
    best = min(cars, key=lambda c: colour_score(c.get("colour", "silver")))
    colour = best.get("colour", "").lower().split()[0]
    model  = normalise_model(best.get("model", ""))
    return f"{colour}-{model}" if colour else model

# ── FCPXML writer ─────────────────────────────────────────────────────────────
def write_fcpxml(pass_name, timeline_clips, out_dir, verbose=False):
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
        # want_dur=0 means use full clip
        if want_dur == 0:
            start_in, use_dur = 0.0, src_dur
        else:
            start_in, use_dur = best_start(clip, src_dur, want_dur)
        if verbose:
            cars = " | ".join([f"{c.get('colour','')} {c.get('model','')}" for c in clip.get("cars",[]) if c.get("colour")])
            print(f"    {clip_num(clip)} {clip.get('shot_type','')[:4]} {use_dur:.1f}s  {cars[:50]}")
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
# City/road establishing — no cars, wide
CITY_CLIPS      = {"6365", "6361", "6364", "6312"}

# People only — no cars visible, gathering/reaction shots
PEOPLE_ONLY     = {"6326", "6319", "6327", "6317", "6318", "6320"}

# People WITH cars — gathering around the lineup
PEOPLE_CARS     = {"6341", "6332", "6349", "6329", "6351", "6359",
                   "6360", "6366", "6330"}

# Static cars, no people — the clean lineup shots
STATIC_WIDE     = {"6323", "6340", "6348", "6376", "6380", "6375",
                   "6386", "6357", "6388", "6378", "6336", "6311",
                   "6362", "6363", "6347", "6353", "6355", "6372",
                   "6377", "6352", "6337", "6385", "6344", "6345",
                   "6338", "6315", "6314", "6350", "6339", "6367",
                   "6387", "6383"}

# Detail shots — close-ups, no people
DETAIL_CLIPS    = {"6325", "6334", "6335", "6343", "6384", "6324",
                   "6333", "6381", "6328", "6316", "6385"}

# Driving / motion through Shanghai
DRIVING_CLIPS   = {"6371", "6356", "6370", "6373", "6374", "6358",
                   "6372", "6366", "6354", "6346", "6342", "6368",
                   "6369"}

# Finale — end of day, glass building event area (later clip numbers, people + cars)
FINALE_CLIPS    = {"6379", "6382", "6383", "6387", "6381", "6344",
                   "6350", "6345", "6367", "6377", "6378", "6380"}

# Convenience alias
PEOPLE_CLIPS    = PEOPLE_ONLY | PEOPLE_CARS

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
        return bws + (10 if num in PEOPLE_ONLY else 0) + (5 if num in PEOPLE_CARS else 0)

    if beat == "doc_static":
        return bws + (10 if num in STATIC_WIDE and not people else 0) + (6 if num in DETAIL_CLIPS else 0)

    if beat == "doc_detail":
        return bws + (10 if num in DETAIL_CLIPS and not people else 0)

    if beat == "doc_drive":
        return bws + (10 if num in DRIVING_CLIPS else 0)

    if beat == "doc_finale":
        return bws + (10 if num in FINALE_CLIPS else 0)

    if beat == "doc_hero":
        return bws + (8 if num in STATIC_WIDE and not people else 0) + (3 if st == "wide" else 0)

    # ── Reel beats ───────────────────────────────────────────────────────────
    if beat == "reel_city":
        return bws + (10 if num in CITY_CLIPS else 0)

    if beat == "reel_reaction":
        return bws + (10 if num in PEOPLE_ONLY else 0) + (5 if num in PEOPLE_CARS else 0)

    if beat == "reel_hyperzoom":
        return bws + (6 if st == "hyperlapse" else 0) + (3 if "hyperzoom" in tags or "zoom" in tags else 0)

    if beat == "reel_static":
        return bws + (10 if num in STATIC_WIDE and not people else 0) + (8 if num in DETAIL_CLIPS else 0)

    if beat == "reel_road":
        return bws + (10 if num in DRIVING_CLIPS else 0)

    if beat == "reel_hero":
        return bws + (8 if num in STATIC_WIDE and not people else 0) + (3 if st == "wide" else 0)

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

    # ── P2: SELECTS — every clip, chronological, best 5s window each ────────
    p2 = [(c, 5.0) for c in sorted(usable, key=lambda c: int(clip_num(c)))]
    write_fcpxml("P2_SELECTS", p2, OUT_DIR)

    from collections import defaultdict

    all_clip_nums = {clip_num(c) for c in usable}

    def pick(pool_ids, n, used, dur):
        """Pick n clips from pool in shoot order, no repeats."""
        pool = sorted(
            [c for c in usable if clip_num(c) in pool_ids and c["clip_id"] not in used],
            key=lambda c: int(clip_num(c))
        )
        result = []
        for c in pool:
            if len(result) >= n: break
            result.append((c, dur))
            used.add(c["clip_id"])
        return result

    def pick_remaining(used, dur):
        """Pick all clips not yet used, in shoot order."""
        return [
            (c, dur) for c in sorted(usable, key=lambda c: int(clip_num(c)))
            if c["clip_id"] not in used
        ]

    used = set()

    # ── SCENE 1: Drive cold open (~8s) ───────────────────────────────────────
    # Open on motion — Shanghai streets, mystery, no context yet
    scene1 = pick(DRIVING_CLIPS, 3, used, 2.5)

    # ── SCENE 2: Each car introduced — wide + detail back to back ────────────
    car_clips = [c for c in usable
                 if not c.get("people", False)
                 and c["clip_id"] not in used]

    by_car = defaultdict(lambda: {"wide": [], "detail": []})
    for c in car_clips:
        ck = car_key(c)
        if not ck: continue
        st = c.get("shot_type", "mid")
        bucket = "detail" if st in ("detail", "close") else "wide"
        by_car[ck][bucket].append(c)

    # Sort each bucket chronologically (earlier = first filmed)
    for ck in by_car:
        by_car[ck]["wide"].sort(key=lambda c: int(clip_num(c)))
        by_car[ck]["detail"].sort(key=lambda c: int(clip_num(c)))

    car_order = sorted(by_car.keys(), key=lambda k: colour_score(k.split("-")[0]))

    scene2 = []
    for ck in car_order:
        # Wide of this car (3s)
        for c in by_car[ck]["wide"]:
            if c["clip_id"] not in used:
                scene2.append((c, 3.0)); used.add(c["clip_id"]); break
        # Detail of same car immediately after (1.5s)
        for c in by_car[ck]["detail"]:
            if c["clip_id"] not in used:
                scene2.append((c, 1.5)); used.add(c["clip_id"]); break

    # ── SCENE 3: Multi-car wides — the full lineup together (~8s) ────────────
    # Shots with multiple cars visible, no people
    multi_car = [c for c in usable
                 if not c.get("people", False)
                 and len(c.get("cars", [])) >= 2
                 and c["clip_id"] not in used
                 and clip_num(c) in STATIC_WIDE]
    multi_car.sort(key=lambda c: int(clip_num(c)))
    scene3 = []
    for c in multi_car[:3]:
        scene3.append((c, 2.5)); used.add(c["clip_id"])

    # ── SCENE 4: People — the crowd reacting (~8s) ───────────────────────────
    scene4 = pick(PEOPLE_ONLY, 2, used, 2.0) + pick(PEOPLE_CARS, 2, used, 2.0)

    # ── SCENE 5: Back on the road — payoff (~10s) ────────────────────────────
    scene5 = pick(DRIVING_CLIPS, 4, used, 2.5)

    # ── SCENE 6: Hero hold — single best wide, no people (~4s) ───────────────
    scene6 = pick(STATIC_WIDE, 1, used, 4.0)

    # Catch-all — any clip not yet used goes in shoot order after road
    scene_remainder = pick_remaining(used, 3.0)

    story = scene1 + scene2 + scene3 + scene4 + scene5 + scene6 + scene_remainder
    print(f"\nPCS_STORY breakdown:")
    print(f"  S1 drive open:  {len(scene1)} clips")
    print(f"  S2 car intros:  {len(scene2)} clips ({len(car_order)} unique cars)")
    print(f"  S3 multi-car:   {len(scene3)} clips")
    print(f"  S4 people:      {len(scene4)} clips")
    print(f"  S5 road:        {len(scene5)} clips")
    print(f"  S6 hero hold:   {len(scene6)} clips")
    print(f"  Remainder:      {len(scene_remainder)} clips")
    write_fcpxml("PCS_STORY", story, OUT_DIR, verbose=True)

    # ── TALKING CUT — all people clips in shoot order ────────────────────────
    talking = sorted(
        [c for c in usable if c.get("people", False)],
        key=lambda c: int(clip_num(c))
    )
    # want_dur=0 → full clip
    talking_tl = [(c, 0) for c in talking]
    write_fcpxml("TALKING_ALL", talking_tl, OUT_DIR, verbose=True)

    # Also generate the 30s documentary cut for Michael
    used_doc = set()
    doc = (
        pick(CITY_CLIPS,    1, used_doc, 3.0) +
        pick(PEOPLE_ONLY,   1, used_doc, 2.5) +
        pick(PEOPLE_CARS,   1, used_doc, 2.5) +
        pick(STATIC_WIDE,   3, used_doc, 2.0) +
        pick(DETAIL_CLIPS,  1, used_doc, 1.5) +
        pick(DRIVING_CLIPS, 3, used_doc, 2.0) +
        pick(FINALE_CLIPS,  1, used_doc, 2.0) +
        pick(STATIC_WIDE,   1, used_doc, 3.0)
    )
    acc, doc_trim = 0.0, []
    for c, d in doc:
        if acc >= 30.0: break
        d = min(d, 30.0 - acc)
        doc_trim.append((c, d)); acc += d
    write_fcpxml("P3_DOC", doc_trim, OUT_DIR)

    # ── Car inventory ─────────────────────────────────────────────────────────
    print(f"\nCar inventory (curated clips only):")
    car_counts = {}
    for c in usable:
        for car in c.get("cars", []):
            key = f"{car.get('colour','?')} {car.get('model','Porsche')}".strip()
            car_counts[key] = car_counts.get(key, 0) + 1
    for car, count in sorted(car_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {car}: {count} clips")

    print(f"\nAll done — FCPXMLs in AI folder")
    print(f"  P1_ALL     — full selects, shoot order (includes RWB)")
    print(f"  P2_SELECTS — ~6 min variety selects")
    print(f"  PCS_STORY  — main story edit: drive open → each car → lineup → people → road → hero")
    print(f"  P3_DOC     — 30s documentary cut for Michael")
    print(f"  P3_DOC    — 30s documentary (Michael's brief)")
    print(f"  P3_REEL   — 30s social/director's cut")


if __name__ == "__main__":
    main()
