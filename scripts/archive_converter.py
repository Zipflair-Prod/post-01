"""
Brief archive converter — batch converts TFC's existing PDF brief archive
to POST-01 JSON format for use as few-shot training examples.

Usage:
    python3 scripts/archive_converter.py --input /path/to/brief/pdfs --output config/briefs/archive
    python3 scripts/archive_converter.py --input /path/to/brief/pdfs --limit 10  # test run
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SCHEMA_PATH = Path(__file__).parent.parent / "config" / "briefs" / "brief_schema.json"


def convert_brief_pdf(pdf_path: Path, schema: str, existing_ids: set) -> dict | None:
    """Convert a single brief PDF to POST-01 JSON format."""
    import base64

    with open(pdf_path, "rb") as f:
        pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=3000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""Convert this TFC production brief to POST-01 JSON format.

Schema to follow:
{schema}

Rules:
- brief_id: derive from filename or date+client+project e.g. 250424_PCCA_SIZZLE
- If you can't determine a field from the brief, use a sensible default or empty value
- style_preset: infer from content — "topgear" for dry/presenter-led, use "corporate" for talking-head, "sport" for action/racing
- outcome_tag: "pending" unless you can infer from context
- assets: leave footage_folders and transcript_files as empty arrays
- few_shot_examples: empty array
- created_at: today's date if not determinable: {datetime.now().isoformat()}
- project_folder: ""

Return ONLY valid JSON matching the schema. No commentary, no markdown fences."""
                    }
                ]
            }]
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()

        brief = json.loads(text)

        # Ensure unique brief_id
        bid = brief.get("brief_id", pdf_path.stem)
        if bid in existing_ids:
            bid = f"{bid}_{pdf_path.stem[-6:]}"
            brief["brief_id"] = bid
        existing_ids.add(bid)

        brief["_source_pdf"] = pdf_path.name
        return brief

    except json.JSONDecodeError as e:
        print(f"  [FAIL] JSON parse error on {pdf_path.name}: {e}")
        return None
    except Exception as e:
        print(f"  [FAIL] {pdf_path.name}: {e}")
        return None


def tag_brief(brief: dict) -> dict:
    """Tag brief with outcome and style info for few-shot selection."""
    # These can be manually updated later — start with pending
    brief.setdefault("outcome_tag", "pending")

    # Auto-tag client type from content
    client_name = brief.get("client", {}).get("name", "").lower()
    if any(w in client_name for w in ["porsche", "ferrari", "mclaren", "motorsport", "racing"]):
        brief["client"]["type"] = "automotive"
    elif any(w in client_name for w in ["charity", "foundation", "trust"]):
        brief["client"]["type"] = "charity"

    return brief


def run_conversion(
    input_dir: Path,
    output_dir: Path,
    limit: int = None,
    delay: float = 0.5  # seconds between API calls
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text()

    pdf_files = sorted(input_dir.glob("**/*.pdf"))
    if limit:
        pdf_files = pdf_files[:limit]

    print(f"\nBrief Archive Converter — POST-01")
    print(f"Input:  {input_dir}")
    print(f"Output: {output_dir}")
    print(f"Briefs: {len(pdf_files)}")
    print("=" * 50)

    results = {"converted": [], "failed": [], "skipped": []}
    existing_ids = set()

    # Load already-converted IDs
    for existing in output_dir.glob("*.json"):
        try:
            d = json.loads(existing.read_text())
            existing_ids.add(d.get("brief_id", existing.stem))
        except Exception:
            pass

    for i, pdf_path in enumerate(pdf_files):
        print(f"\n[{i+1}/{len(pdf_files)}] {pdf_path.name}")

        # Skip if already converted
        output_path = output_dir / f"{pdf_path.stem}.json"
        if output_path.exists():
            print(f"  → Already converted, skipping")
            results["skipped"].append(pdf_path.name)
            continue

        brief = convert_brief_pdf(pdf_path, schema, existing_ids)

        if brief:
            brief = tag_brief(brief)
            output_path.write_text(json.dumps(brief, indent=2))
            print(f"  ✓ {brief.get('brief_id')} → {output_path.name}")
            results["converted"].append({
                "file": pdf_path.name,
                "brief_id": brief.get("brief_id"),
                "client": brief.get("client", {}).get("name"),
                "style_preset": brief.get("style_preset")
            })
        else:
            results["failed"].append(pdf_path.name)

        # Rate limiting
        if i < len(pdf_files) - 1:
            time.sleep(delay)

    # Summary
    print(f"\n{'=' * 50}")
    print(f"Converted: {len(results['converted'])}")
    print(f"Skipped:   {len(results['skipped'])}")
    print(f"Failed:    {len(results['failed'])}")

    if results["failed"]:
        print(f"\nFailed files:")
        for f in results["failed"]:
            print(f"  ✗ {f}")

    # Save index
    index_path = output_dir / "_index.json"
    index_path.write_text(json.dumps(results, indent=2))
    print(f"\nIndex saved: {index_path}")

    # Update topgear.json training_examples
    _update_preset_examples(output_dir)

    return results


def _update_preset_examples(archive_dir: Path):
    """Add converted briefs as training examples in relevant presets."""
    presets_dir = Path(__file__).parent.parent / "config" / "presets"
    if not presets_dir.exists():
        return

    # Group converted briefs by style_preset
    by_preset: dict[str, list] = {}
    for f in archive_dir.glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            d = json.loads(f.read_text())
            preset = d.get("style_preset", "topgear")
            outcome = d.get("outcome_tag", "pending")
            by_preset.setdefault(preset, []).append({
                "path": str(f.relative_to(Path(__file__).parent.parent)),
                "outcome": outcome
            })
        except Exception:
            pass

    for preset_name, examples in by_preset.items():
        preset_file = presets_dir / f"{preset_name}.json"
        if not preset_file.exists():
            continue
        preset = json.loads(preset_file.read_text())
        # Prefer successful outcomes first
        prioritised = sorted(examples, key=lambda x: (
            0 if x["outcome"] in ("client_loved_it", "award_winning") else
            1 if x["outcome"] == "delivered_clean" else 2
        ))
        preset["training_examples"] = [e["path"] for e in prioritised[:10]]
        preset_file.write_text(json.dumps(preset, indent=2))
        print(f"  Updated {preset_name}.json with {len(preset['training_examples'])} training examples")


def main():
    parser = argparse.ArgumentParser(description="POST-01 Brief Archive Converter")
    parser.add_argument("--input", required=True, help="Directory containing PDF briefs")
    parser.add_argument("--output", default="config/briefs/archive",
                        help="Output directory for JSON briefs")
    parser.add_argument("--limit", type=int, help="Max briefs to convert (for testing)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between API calls (default 0.5)")
    args = parser.parse_args()

    root = Path(__file__).parent.parent
    run_conversion(
        input_dir=Path(args.input),
        output_dir=root / args.output,
        limit=args.limit,
        delay=args.delay
    )


if __name__ == "__main__":
    main()
