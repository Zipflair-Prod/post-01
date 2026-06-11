"""
Hedge metadata ingestion — reads Hedge AI tag exports and folds
visual metadata into POST-01's clip scoring.

STATUS: Stub — wire up once Hedge export format confirmed on TFCPOST01.
Run: python3 -m vision.hedge_ingest --sample /path/to/hedge/export
     to inspect your export format and confirm the field mapping.
"""

import csv
import json
from pathlib import Path


# ── Format detection ───────────────────────────────────────────────────────────
# Hedge can export as JSON sidecar, CSV, or XML depending on version.
# Run --sample to detect your format automatically.

KNOWN_FORMATS = ["json_sidecar", "csv_export", "xml_export"]


def detect_hedge_format(export_path: Path) -> str:
    if export_path.is_dir():
        # Check for sidecar JSON files alongside clips
        json_files = list(export_path.glob("*.json"))
        if json_files:
            sample = json.loads(json_files[0].read_text())
            if "hedgeVersion" in sample or "tags" in sample:
                return "json_sidecar"
    if export_path.suffix.lower() == ".csv":
        return "csv_export"
    if export_path.suffix.lower() in (".xml", ".fcpxml"):
        return "xml_export"
    return "unknown"


def load_hedge_metadata(export_path: Path) -> dict[str, dict]:
    """
    Load Hedge metadata and return a dict keyed by clip filename.
    Value is a dict of visual tags/scores.

    Returns: {"clip_name.mp4": {"tags": [...], "quality": 8, ...}}
    """
    fmt = detect_hedge_format(export_path)
    print(f"  Hedge format detected: {fmt}")

    if fmt == "json_sidecar":
        return _load_json_sidecars(export_path)
    elif fmt == "csv_export":
        return _load_csv_export(export_path)
    else:
        print(f"  [WARN] Unknown Hedge format — check export settings")
        print(f"  Run: python3 -m vision.hedge_ingest --sample {export_path}")
        return {}


def _load_json_sidecars(folder: Path) -> dict[str, dict]:
    result = {}
    for json_file in folder.glob("*.json"):
        try:
            data = json.loads(json_file.read_text())
            clip_name = json_file.stem  # assumes sidecar matches clip name
            result[clip_name] = _normalise_hedge_json(data)
        except Exception as e:
            print(f"  [WARN] Could not read {json_file.name}: {e}")
    return result


def _load_csv_export(csv_path: Path) -> dict[str, dict]:
    result = {}
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                clip_name = Path(row.get("filename", row.get("File", ""))).stem
                if clip_name:
                    result[clip_name] = _normalise_hedge_csv(row)
    except Exception as e:
        print(f"  [WARN] CSV load error: {e}")
    return result


def _normalise_hedge_json(data: dict) -> dict:
    """Map Hedge JSON fields to POST-01 standard visual metadata."""
    return {
        "tags": data.get("tags", data.get("aiTags", [])),
        "quality_score": data.get("quality", data.get("qualityScore", 5)),
        "faces_detected": data.get("faces", data.get("faceCount", 0)) > 0,
        "scene_type": data.get("scene", data.get("sceneType", "")),
        "motion_level": data.get("motion", data.get("motionLevel", "unknown")),
        "focus_quality": data.get("focus", data.get("focusQuality", "unknown")),
        "exposure_quality": data.get("exposure", data.get("exposureQuality", "unknown")),
        "raw": data
    }


def _normalise_hedge_csv(row: dict) -> dict:
    """Map Hedge CSV columns to POST-01 standard visual metadata."""
    # Common Hedge CSV column names — may need adjustment for your version
    tags_str = row.get("Tags", row.get("AI Tags", row.get("tags", "")))
    tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

    return {
        "tags": tags,
        "quality_score": float(row.get("Quality", row.get("quality", 5))),
        "faces_detected": row.get("Faces", "0") not in ("0", ""),
        "scene_type": row.get("Scene", row.get("scene", "")),
        "motion_level": row.get("Motion", row.get("motion", "unknown")),
        "focus_quality": row.get("Focus", row.get("focus", "unknown")),
        "exposure_quality": row.get("Exposure", row.get("exposure", "unknown")),
        "raw": dict(row)
    }


def apply_hedge_metadata(clips: list[dict], hedge_data: dict[str, dict]) -> list[dict]:
    """
    Merge Hedge visual metadata into POST-01 scored clip list.
    Boosts/penalises composite score based on technical quality.
    """
    if not hedge_data:
        return clips

    enriched = []
    for clip in clips:
        source = Path(clip.get("source_file", "")).stem
        hedge = hedge_data.get(source)

        if not hedge:
            enriched.append(clip)
            continue

        clip = dict(clip)
        clip["hedge_metadata"] = hedge

        # Score adjustment based on Hedge quality
        quality = hedge.get("quality_score", 5)
        quality_delta = (quality - 5) * 0.3  # ±1.5 max adjustment

        # Tag-based flags
        tags = [t.lower() for t in hedge.get("tags", [])]
        if any(t in tags for t in ["out of focus", "blurry", "unusable"]):
            clip.setdefault("flags", []).append("HEDGE_UNUSABLE")
            quality_delta -= 2.0
        if any(t in tags for t in ["best take", "hero shot", "featured"]):
            clip.setdefault("flags", []).append("HEDGE_BEST_TAKE")
            quality_delta += 1.0
        if hedge.get("faces_detected"):
            clip.setdefault("flags", []).append("HEDGE_FACES_DETECTED")

        # Apply adjustment
        current = clip.get("composite_score", clip.get("final_composite", 5.0))
        clip["composite_score"] = max(0.0, min(10.0, current + quality_delta))
        clip["hedge_quality_delta"] = round(quality_delta, 2)

        enriched.append(clip)

    matched = sum(1 for c in enriched if "hedge_metadata" in c)
    print(f"  Hedge metadata: {matched}/{len(clips)} clips enriched")
    return enriched


def print_sample(export_path: Path):
    """Inspect a Hedge export to confirm format. Run this first."""
    path = Path(export_path)
    fmt = detect_hedge_format(path)
    print(f"\nHedge export format: {fmt}")
    print(f"Path: {path}")

    if fmt == "json_sidecar":
        files = list(path.glob("*.json"))
        if files:
            print(f"\nSample JSON ({files[0].name}):")
            print(json.dumps(json.loads(files[0].read_text()), indent=2)[:800])
    elif fmt == "csv_export":
        with open(path) as f:
            lines = f.readlines()[:5]
        print(f"\nCSV headers + first row:")
        print("".join(lines))
    else:
        print("\nFormat not recognised. Check Hedge export settings:")
        print("  Hedge → Preferences → Metadata Export")
        print("  Try: JSON Sidecar or CSV Report")


if __name__ == "__main__":
    import sys
    if "--sample" in sys.argv:
        idx = sys.argv.index("--sample")
        if idx + 1 < len(sys.argv):
            print_sample(Path(sys.argv[idx + 1]))
        else:
            print("Usage: python3 -m vision.hedge_ingest --sample /path/to/export")
    else:
        print("Hedge Ingest — POST-01")
        print("Usage: python3 -m vision.hedge_ingest --sample /path/to/hedge/export")
        print("       Inspects your Hedge export format before wiring it in.")
