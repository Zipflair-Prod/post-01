# POST-01 Roadmap

## Phase 1 — What Works Now (Current build)

| # | Component | Status |
|---|---|---|
| 1 | Brief → JSON schema | ✓ Built |
| 2 | Transcript scorer (Claude API) | ✓ Built |
| 3 | AI prompt pack generator | ✓ Built |
| 4 | Editor briefing doc (PDF) | ✓ Built |
| 5 | QNAP folder creation script | ✓ Built |
| 6 | FCPXML rough assembly generator | ✓ Built — validate timecodes on TFCPOST01 |
| 7 | topgear.json style preset | ✓ Built |
| 8 | Settings config (QNAP base path) | ✓ Built |

**First session checklist:**
- [ ] Enable DaVinci scripting on TFCPOST01
- [ ] Map C&C brief to JSON schema
- [ ] Live run of transcript scorer on C&C transcripts
- [ ] Validate FCPXML import in DaVinci
- [ ] Test Hedge export format (one afternoon)

---

## Phase 2 — When Phase 1 is Stable

### Visual Search
**"Find more of this car / driver"**

Drop a screenshot or specify a car number (e.g. Porsche #36) and POST-01 finds every timecode
across all footage where that subject appears.

**How it works:**
1. `ffmpeg` extracts frames from QNAP footage at ~1fps (or keyframes only)
2. Frames indexed with timecodes per clip
3. Query modes:
   - **Text query:** "Porsche #36", "driver helmet red", "pit lane reaction shot"
   - **Reference image:** drop a screenshot → POST-01 finds visual matches across the frame index
4. Claude vision API analyses frames against the query
5. Matching timecodes fed back into the scored clip list as `VISUAL_MATCH` flags

**Why it matters for TFC:**
- Automotive / motorsport clients: specific car numbers, liveries, drivers
- Multi-camera shoots: find every clean wide of the presenter across all cards
- Saves hours of manual log-and-transfer on complex shoots

**Dependencies:**
- `ffmpeg` on TFCPOST01 (likely already present)
- Frame storage on QNAP (temp folder, can be purged after scoring)
- Claude vision API (same key, additional token cost — manageable)

**Effort:** Medium — 1–2 days once frame extraction is confirmed working

---

### Hedge Visual Metadata Ingestion
Wire Hedge's AI tag output into POST-01's scoring so clips are scored on visual quality
(sharpness, lighting, faces in-frame) as well as transcript content.

**Blocker:** confirm Hedge export format on TFCPOST01 first (one afternoon).
**Note:** Hedge tags are style-agnostic — the style preset still does the editorial filter.

---

### Higgsfield / Kling API Automation
When public APIs exist, POST-01 fires AI shot generation automatically.
Prompts are already being generated in Phase 1 — the API call is the only new piece.
**Status:** No public API yet. Monitor — probably within 12 months.

---

### Fine-tuned Style Model
Once the brief archive is fully structured and tagged, fine-tuning on TFC's own data
produces a POST-01 that scores clips the way TFC edits, not a generic AI.
**Prerequisite:** brief archive converted to JSON schema + outcome tags applied.

---

### Creative Scoring
POST-01 flags not just relevant clips but funny ones, surprising ones, moments that
would land in a Top Gear edit. Requires the fine-tuned model or a large few-shot archive.
This is the creative juice layer.
