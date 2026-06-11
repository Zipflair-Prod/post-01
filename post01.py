"""
POST-01 — Automated Post-Production Pipeline
Main orchestrator. Run this with a brief JSON to fire all outputs.

Usage:
    python post01.py --brief config/briefs/example_cc_insurance_2026.json
    python post01.py --brief path/to/brief.json --output-dir /QNAP/outputs
    python post01.py --brief path/to/brief.json --skip-fcpxml
    python post01.py --brief path/to/brief.json --no-vision
    python post01.py --brief path/to/brief.json --hedge /path/to/hedge/export
    python post01.py --setup-folders --brief path/to/brief.json
    python post01.py --search "Porsche #36" --footage /path/to/footage
    python post01.py --search-ref /path/to/screenshot.jpg --footage /path/to/footage
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from prompt_generator import run_prompt_generator
from briefing_doc import run_briefing_doc
from fcpxml_generator import run_fcpxml_generator
from qnap_folders import run_qnap_folders
from enhanced_scorer import run_enhanced_scorer
from vision.visual_search import (
    search_footage_text, search_footage_screenshot, search_results_to_clips
)
from cloud_setup import run_cloud_setup


def load_settings() -> dict:
    settings_path = Path(__file__).parent / "config" / "settings.json"
    if settings_path.exists():
        with open(settings_path) as f:
            return json.load(f)
    return {}


def save_json(data: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    print(f"  Saved: {path}")


def run(
    brief_path: str,
    output_dir: str = None,
    skip_fcpxml: bool = False,
    setup_folders: bool = False,
    qnap_base: str = None,
    use_vision: bool = True,
    hedge_export: str = None,
    skip_cloud: bool = False,
    editor_ids: list = None,
):
    brief_p = Path(brief_path)
    if not brief_p.exists():
        print(f"ERROR: Brief not found: {brief_path}")
        sys.exit(1)

    with open(brief_p) as f:
        brief = json.load(f)

    root = Path(__file__).parent
    out_dir = Path(output_dir) if output_dir else root / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    brief_id = brief.get("brief_id", "unknown").replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\nPOST-01 — {brief.get('project_title')}")
    print(f"Client:  {brief.get('client', {}).get('name')}")
    print(f"Brief:   {brief_id}")
    print(f"Preset:  {brief.get('style_preset')}")
    print(f"Vision:  {'ON' if use_vision else 'OFF'}")
    print(f"Hedge:   {hedge_export or 'not provided'}")
    print("=" * 60)

    # Step 0 — Folder setup
    if setup_folders:
        print("\n[0] Creating project folders...")
        project_root = run_qnap_folders(brief_path, base_path=qnap_base)
        print(f"  {project_root}")

    # Step 1 — Enhanced scoring (transcript + vision + hedge + selects)
    print("\n[1] Scoring clips...")
    scored = run_enhanced_scorer(
        brief_path,
        config_dir=str(root),
        use_vision=use_vision,
        hedge_export_path=hedge_export,
    )
    clips_path = out_dir / f"{brief_id}_clip_list_{ts}.json"
    save_json(scored, clips_path)

    n_total  = len(scored.get("clips", []))
    n_select = len(scored.get("selects", []))
    print(f"  {n_total} clips scored · {n_select} auto-selected")

    # Step 2 — AI prompt pack
    print("\n[2] Generating AI prompt pack...")
    prompts = run_prompt_generator(brief_path, config_dir=str(root))
    prompts_path = out_dir / f"{brief_id}_ai_prompts_{ts}.json"
    save_json(prompts, prompts_path)
    print(f"  {len(prompts.get('prompt_packs', []))} beat(s) with AI prompts")

    # Step 3 — Editor briefing doc
    print("\n[3] Building editor briefing doc...")
    brief_doc_path = run_briefing_doc(
        brief_path, scored, prompts, output_dir=str(out_dir)
    )

    # Step 4 — FCPXML rough assembly (selects only)
    if not skip_fcpxml:
        print("\n[4] Generating FCPXML rough assembly...")
        # Use selects only for the assembly — best clip per beat
        selects_only = {**scored, "clips": scored.get("selects", scored.get("clips", []))}
        fcpxml_path = run_fcpxml_generator(
            brief_path, selects_only, output_dir=str(out_dir)
        )
        if fcpxml_path:
            print(f"  Assembly: {len(scored.get('selects', []))} clips")
    else:
        print("\n[4] FCPXML skipped")
        fcpxml_path = None

    # Step 5 — Blackmagic Cloud project setup
    bm_result = {}
    if not skip_cloud:
        print("\n[5] Setting up Blackmagic Cloud project...")
        bm_result = run_cloud_setup(
            brief,
            briefing_doc_path=str(brief_doc_path),
            editor_ids=editor_ids,
            notify_editors=True
        )
    else:
        print("\n[5] Cloud setup skipped")

    # Summary
    print("\n" + "=" * 60)
    print("OUTPUTS:")
    print(f"  Clip list + selects:  {clips_path.name}")
    print(f"  AI prompt pack:       {prompts_path.name}")
    print(f"  Editor briefing doc:  {brief_doc_path.name}")
    if fcpxml_path:
        print(f"  FCPXML assembly:      {fcpxml_path.name}")
    print(f"\nAll files → {out_dir}")
    if bm_result.get("status") == "created":
        print(f"\nBlackmagic Cloud:")
        print(f"  Library:  {bm_result.get('library')}")
        print(f"  Project:  {bm_result.get('project_name')}")
        if bm_result.get("editors_invited"):
            print(f"  Invited:  {', '.join(bm_result['editors_invited'])}")
    print(f"\nEditor handoff:")
    print(f"  1. Open DaVinci → {bm_result.get('library', 'Cloud')} → {brief_id}")
    if fcpxml_path:
        print(f"  2. Import {fcpxml_path.name} — {n_select} clip rough assembly")
    print(f"  3. Briefing doc in POST01_Output/BriefingDocs")


def run_search(
    query: str = None,
    reference_image: str = None,
    footage_paths: list = None,
    output_dir: str = None,
):
    out_dir = Path(output_dir) if output_dir else Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    if not footage_paths:
        print("ERROR: --footage required for visual search")
        sys.exit(1)

    if query:
        print(f"\nVisual search: '{query}'")
        results = search_footage_text(query, footage_paths, out_dir)
    elif reference_image:
        print(f"\nScreenshot search: {Path(reference_image).name}")
        results = search_footage_screenshot(reference_image, footage_paths, out_dir)
    else:
        print("ERROR: --search or --search-ref required")
        sys.exit(1)

    # Save results
    search_path = out_dir / f"visual_search_{ts}.json"
    save_json(results, search_path)

    # Print summary
    matches = results.get("matches", [])
    print(f"\n{len(matches)} match(es) found:")
    for m in matches[:10]:
        print(f"  [{m.get('confidence', 0):.1f}] {m.get('source_file')} "
              f"@ {m.get('timecode_str')} — {m.get('description', '')[:60]}")

    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more (see {search_path.name})")

    return results


def main():
    parser = argparse.ArgumentParser(description="POST-01 Automated Post-Production Pipeline")

    # Pipeline mode
    parser.add_argument("--brief", help="Path to brief JSON file")
    parser.add_argument("--output-dir", help="Output directory")
    parser.add_argument("--skip-fcpxml", action="store_true")
    parser.add_argument("--setup-folders", action="store_true")
    parser.add_argument("--qnap-base", help="QNAP base path")
    parser.add_argument("--no-vision", action="store_true", help="Disable vision scoring")
    parser.add_argument("--hedge", help="Path to Hedge export for metadata ingestion")
    parser.add_argument("--skip-cloud", action="store_true", help="Skip Blackmagic Cloud setup")
    parser.add_argument("--editors", nargs="+", help="Editor IDs to assign e.g. --editors ryan jasper")

    # Search mode
    parser.add_argument("--search", help="Visual search query e.g. 'Porsche #36'")
    parser.add_argument("--search-ref", help="Path to reference screenshot")
    parser.add_argument("--footage", nargs="+", help="Footage paths for visual search")

    args = parser.parse_args()

    settings = load_settings()
    if not args.output_dir and settings.get("qnap_base"):
        args.output_dir = str(Path(settings["qnap_base"]) / "POST01_Output")

    if "ANTHROPIC_API_KEY" not in os.environ:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    if args.search or args.search_ref:
        run_search(
            query=args.search,
            reference_image=args.search_ref,
            footage_paths=args.footage,
            output_dir=args.output_dir,
        )
    elif args.brief:
        run(
            brief_path=args.brief,
            output_dir=args.output_dir,
            skip_fcpxml=args.skip_fcpxml,
            setup_folders=args.setup_folders,
            qnap_base=args.qnap_base,
            use_vision=not args.no_vision,
            hedge_export=args.hedge,
            skip_cloud=args.skip_cloud,
            editor_ids=args.editors,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
