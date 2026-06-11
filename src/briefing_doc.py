"""
Editor briefing doc generator — combines scored clip list + AI prompts
into a PDF briefing document for Ryan.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)


TFC_ORANGE = colors.HexColor("#E8500A")
TFC_DARK = colors.HexColor("#1A1A1A")
TFC_MID = colors.HexColor("#555555")
TFC_LIGHT = colors.HexColor("#F5F5F5")
TFC_GREEN = colors.HexColor("#2E7D32")
TFC_AMBER = colors.HexColor("#F57F17")
TFC_RED = colors.HexColor("#C62828")

FLAG_COLOURS = {
    "MUST_USE": TFC_GREEN,
    "STRONG_CANDIDATE": TFC_GREEN,
    "HUMOUR_GOLD": TFC_ORANGE,
    "PRESENTER_STUMBLE_WORTH_KEEPING": TFC_ORANGE,
    "PERSONAL_DATA": TFC_RED,
    "UNUSABLE": TFC_RED,
    "FALSE_START": TFC_AMBER,
}


def build_styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("title", fontSize=22, textColor=TFC_DARK,
                                spaceAfter=2*mm, fontName="Helvetica-Bold"),
        "subtitle": ParagraphStyle("subtitle", fontSize=11, textColor=TFC_ORANGE,
                                   spaceAfter=1*mm, fontName="Helvetica-Bold"),
        "section": ParagraphStyle("section", fontSize=10, textColor=TFC_DARK,
                                  spaceBefore=4*mm, spaceAfter=2*mm,
                                  fontName="Helvetica-Bold"),
        "body": ParagraphStyle("body", fontSize=8.5, textColor=TFC_MID,
                               spaceAfter=1*mm, fontName="Helvetica",
                               leading=13),
        "mono": ParagraphStyle("mono", fontSize=7.5, textColor=TFC_MID,
                               fontName="Courier", spaceAfter=0.5*mm),
        "flag": ParagraphStyle("flag", fontSize=7, fontName="Helvetica-Bold"),
        "note": ParagraphStyle("note", fontSize=7.5, textColor=TFC_MID,
                               fontName="Helvetica-Oblique"),
    }
    return styles


def score_bar(score: float, max_score: float = 10) -> str:
    filled = int((score / max_score) * 10)
    return "█" * filled + "░" * (10 - filled) + f"  {score:.1f}"


def build_doc(brief: dict, scored_clips: dict, prompts: dict, output_path: Path):
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=15*mm, bottomMargin=15*mm
    )
    st = build_styles()
    story = []
    w = A4[0] - 36*mm

    # Header
    story.append(Paragraph("POST-01 EDITOR BRIEFING", st["title"]))
    story.append(Paragraph(
        f"{brief.get('project_title', '')} — {brief.get('client', {}).get('name', '')}",
        st["subtitle"]
    ))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%d %b %Y %H:%M')} · "
        f"Style preset: {brief.get('style_preset', '—')} · "
        f"Brief ID: {brief.get('brief_id', '—')}",
        st["note"]
    ))
    story.append(HRFlowable(width=w, color=TFC_ORANGE, thickness=1.5, spaceAfter=4*mm))

    # Concept block
    concept = brief.get("concept", {})
    story.append(Paragraph("CONCEPT", st["section"]))
    story.append(Paragraph(f"<b>Logline:</b> {concept.get('logline', '—')}", st["body"]))
    story.append(Paragraph(f"<b>Tone:</b> {concept.get('tone', '—')}  |  "
                           f"<b>POV:</b> {concept.get('pov', '—')}", st["body"]))
    story.append(Spacer(1, 2*mm))

    # Beats overview
    beats = brief.get("beats", [])
    if beats:
        story.append(Paragraph("BEATS", st["section"]))
        beat_rows = [["ID", "Beat", "Type", "Target", "Must Include"]]
        for b in beats:
            must = ", ".join(b.get("must_include", [])) or "—"
            beat_rows.append([
                b.get("id", ""),
                b.get("label", ""),
                b.get("type", "").replace("_", " "),
                f"{b.get('duration_target_sec', '?')}s",
                must[:60] + ("…" if len(must) > 60 else "")
            ])
        beat_table = Table(beat_rows, colWidths=[18*mm, 35*mm, 25*mm, 14*mm, None])
        beat_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TFC_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [TFC_LIGHT, colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#DDDDDD")),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(beat_table)
        story.append(Spacer(1, 4*mm))

    # Scored clip list — grouped by beat
    clips = scored_clips.get("clips", [])
    if clips:
        story.append(HRFlowable(width=w, color=colors.HexColor("#DDDDDD"), thickness=0.5))
        story.append(Paragraph("SCORED CLIP LIST", st["section"]))

        beat_map = {}
        for clip in clips:
            bid = clip.get("beat_id", "unassigned")
            beat_map.setdefault(bid, []).append(clip)

        for bid, beat_clips in beat_map.items():
            beat_label = next((b.get("label") for b in beats if b.get("id") == bid), bid)
            story.append(Paragraph(f"{bid.upper()} — {beat_label}", st["subtitle"]))

            header = ["Clip", "File", "In", "Out", "Transcript", "Score", "Flags"]
            rows = [header]
            for clip in beat_clips[:8]:  # top 8 per beat
                flags = clip.get("flags", [])
                flag_str = " ".join(f"[{f}]" for f in flags) if flags else ""
                text = clip.get("transcript_text", "")
                rows.append([
                    clip.get("clip_id", "")[-12:],
                    clip.get("source_file", ""),
                    clip.get("timecode_in", ""),
                    clip.get("timecode_out", ""),
                    text[:70] + ("…" if len(text) > 70 else ""),
                    f"{clip.get('composite_score', 0):.1f}",
                    flag_str[:40]
                ])
                if clip.get("editor_note"):
                    rows.append(["", "", "", "", f"→ {clip['editor_note']}", "", ""])

            col_w = [22*mm, 25*mm, 16*mm, 16*mm, None, 12*mm, 32*mm]
            t = Table(rows, colWidths=col_w)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TFC_DARK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [TFC_LIGHT, colors.white]),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#DDDDDD")),
                ("FONTNAME", (0, 1), (0, -1), "Courier"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(t)
            story.append(Spacer(1, 3*mm))

    # AI Prompt Pack
    prompt_packs = prompts.get("prompt_packs", [])
    if prompt_packs:
        story.append(HRFlowable(width=w, color=colors.HexColor("#DDDDDD"), thickness=0.5))
        story.append(Paragraph("AI PROMPT PACK", st["section"]))
        story.append(Paragraph(
            "Paste these prompts directly into the relevant tools. "
            "Do not edit — they are optimised for TFC style.",
            st["note"]
        ))
        story.append(Spacer(1, 2*mm))

        for pack in prompt_packs:
            story.append(Paragraph(
                f"{pack.get('beat_id', '').upper()} — {pack.get('beat_label', '')}",
                st["subtitle"]
            ))
            for shot in pack.get("shots", []):
                tool = shot.get("tool", "")
                tool_colour = {"Higgsfield": TFC_ORANGE, "Kling": TFC_GREEN,
                               "NanoBanana": colors.HexColor("#6A1B9A")}.get(tool, TFC_MID)

                header_data = [[
                    Paragraph(f"<b>{shot.get('shot_id', '')}</b>", st["body"]),
                    Paragraph(f"<b>{tool}</b>", ParagraphStyle("th", fontSize=8,
                              fontName="Helvetica-Bold", textColor=tool_colour)),
                    Paragraph(f"{shot.get('duration_sec', '?')}s", st["body"]),
                    Paragraph(shot.get("intent_note", ""), st["note"]),
                ]]
                ht = Table(header_data, colWidths=[30*mm, 25*mm, 12*mm, None])
                ht.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), TFC_LIGHT),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ]))
                story.append(ht)
                story.append(Paragraph(shot.get("prompt", ""), st["mono"]))
                if shot.get("negative_prompt"):
                    story.append(Paragraph(
                        f"Negative: {shot['negative_prompt']}", st["note"]
                    ))
                story.append(Spacer(1, 2*mm))

    # Footer
    story.append(HRFlowable(width=w, color=TFC_ORANGE, thickness=1))
    story.append(Paragraph(
        f"THE FILM CREW — POST-01 · thefilmcrew.co · "
        f"Brief {brief.get('brief_id', '')} · "
        f"This document is auto-generated — source of truth is the JSON output.",
        st["note"]
    ))

    doc.build(story)
    print(f"  Briefing doc written: {output_path}")


def run_briefing_doc(brief_path: str, scored_clips: dict, prompts: dict,
                     output_dir: str = None) -> Path:
    brief_p = Path(brief_path)
    with open(brief_p) as f:
        brief = json.load(f)

    out_dir = Path(output_dir) if output_dir else brief_p.parent.parent.parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    brief_id = brief.get("brief_id", "unknown").replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = out_dir / f"{brief_id}_editor_brief_{ts}.pdf"

    build_doc(brief, scored_clips, prompts, output_path)
    return output_path
