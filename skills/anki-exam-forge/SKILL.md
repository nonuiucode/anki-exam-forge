---
name: anki-exam-forge
description: "Build high-yield Anki .apkg decks for exam preparation in a 4-stage closed loop: past-exam frequency analysis (PDF/images/text -> deterministic frequency report + deck), multi-type stable-GUID .apkg generation (Basic/MCQ/Cloze + media), lecture PPT/DOCX/PDF incremental supplement diffing without duplicates, and leech/stuck card mnemonic writeback."
---

# Anki Exam Forge

Turn scattered past exam papers, scanned PDFs, and lecture slides into an evolving, zero-duplicate **Anki `.apkg`** study system built around **memory retrievability** and **exam ROI**.

## Dual Execution Paths (MCP or Standalone CLI)

Use whichever interface is available in your agent environment:

- **MCP Tools** (when installed as an Antigravity plugin or MCP server):
  1. `anki_doctor` — check Python, SQLite, OOXML (`.pptx`/`.docx`), and PDF extraction readiness.
  2. `anki_extract_source` — extract text, speaker notes, rendered PNG pages (for scanned PDFs), and embedded images from `.pdf`, `.pptx`, `.docx`, `.md`/`.txt`, or image folders.
  3. `anki_analyze_frequency` — group exam items by canonical `concept_key`, count exact appearances deterministically, emit a Markdown frequency report, and produce a prioritized `deck.json`.
  4. `anki_validate_deck` — check prompt/answer presence, `{{c1::...}}` cloze syntax, `>= 2` MCQ options, unique `card_id`/`guid`, and media file paths.
  5. `anki_build_apkg` — compile `deck.json` into a deterministic-GUID `.apkg` (`ExamForge-Basic`, `ExamForge-MCQ`, `ExamForge-Cloze`) ordered by exam frequency.
  6. `anki_inspect_apkg` — inspect an existing `.apkg` (preferring `collection.anki21`), extract bundled media, and filter notes by tag (`leech`), deck, query, or `minLapses`.
  7. `anki_diff_supplement` — diff lecture/PPT candidate cards against an existing `deck.json` or `.apkg`, keeping all original GUIDs intact and appending only genuinely new cards.
  8. `anki_apply_mnemonics` — write structured memory-technique mnemonics back into matching cards while keeping original GUIDs for zero-progress-loss Anki updates.

- **Standalone CLI** (when installed via `--skill-only` or running without MCP):
  - Pure Python (works directly from the skill folder, zero pip requirements for core `.apkg`/`.pptx`/`.docx` flow):
    `python <skill-or-plugin-dir>/engine/cli.py <doctor|extract|freq|validate|build|inspect|diff|mnemonic> ...`
  - Node.js wrapper (when running from the plugin repo):
    `node <plugin-dir>/server/cli.mjs <doctor|extract|freq|validate|build|inspect|diff|mnemonic> ...`

---

## 4-Phase Closed-Loop Workflow

### Phase 1: Past-Exam Ingestion & Frequency Analysis
1. **Run `anki_doctor`** (or `cli.py doctor`) to confirm PDF/Python readiness.
2. **Extract sources in manageable windows**: call `anki_extract_source` on past-exam PDFs (`fromPage: 1, maxPages: 15..20, renderMode: "auto"`). For scanned/image-heavy pages, read the generated `pages/page_XXX.png` images with your multimodal vision capability instead of relying solely on sparse OCR text. Continue with `next_from_page` until all papers are covered.
3. **Normalize each exam question** into structured JSON items containing:
   - `concept_key`: concise canonical concept name so differently worded questions testing the *same* underlying fact merge cleanly (e.g., `"桡神经损伤临床表现"`, `"胃食管反流病一线用药"`).
   - `card_type`: `"basic"` | `"mcq"` | `"cloze"`.
   - `front` / `stem` / `text`, `options` (for `mcq`), `back` / `answer`, `explanation`, `topic`, `source_years` (e.g., `["2021", "2023"]`), and `media` (any diagram filenames).
4. **Never count frequencies in your head**: pass the extracted items array/file to `anki_analyze_frequency` (choose `subjectPreset`: `clinical-medicine`, `postgrad-exam`, `law-exam`, or `general`) to generate the deterministic **Markdown frequency report** and prioritized `deck.json`.

### Phase 2: Card Authoring, Validation & `.apkg` Packaging
1. Before finalizing cards, consult:
   - [Card Design Guide](./references/card-design-guide.md) — atomicity, cloze vs MCQ vs basic selection, and diagram hygiene.
   - [Subject Presets](./references/subject-presets.md) — field conventions and tag hierarchies for clinical medicine, postgraduate/licensing exams, and STEM/humanities.
2. **Validate before packaging**: run `anki_validate_deck` (pass `mediaDir` if cards reference extracted figures). Fix any blocking errors (`invalid_cloze`, `missing_media`, `duplicate_card_id`, etc.) and consider splitting verbose cards flagged by warnings.
3. **Build the `.apkg`**: run `anki_build_apkg` (`subdeckStrategy: "tier"` places cards into `01-高频必背`, `02-中频常考`, `03-真题常规`, `04-课件补充`, sorted so the highest-frequency past-exam cards surface first in Anki's `due` queue).

### Phase 3: Lecture PPT / Notes Incremental Supplement (Exam Score Booster)
Once past-exam high-yield cards form the passing baseline, use lecture slides (`.pptx`, `.docx`, `.pdf`) to fill remaining syllabus gaps **without overwriting or duplicating existing cards**:
1. Extract the lecture deck with `anki_extract_source` (`extractImages: true` pulls embedded diagrams/tables into `media/`).
2. Draft candidate cards from the new slides/notes.
3. Run `anki_diff_supplement` against the existing `deck.json` **or** exported `.apkg`:
   - **Exact duplicates** are skipped automatically.
   - **Near duplicates** (`similarity >= nearThreshold`) are surfaced for review; pass `mergeNear: true` when slide details enrich an existing past-exam card.
   - **New concepts** are assigned `source::ppt-supplement` and placed into `04-课件补充`, while **every existing card keeps its exact original `guid`**.
4. Re-validate and rebuild `.apkg` (`anki_build_apkg`). Re-importing into Anki updates modified notes in place without losing review history.

### Phase 4: Stuck / Leech Card Mnemonic Repair
When a user exports their active Anki deck (with scheduling info, or check **"Support older Anki versions / 兼容旧版 Anki"** when exporting `.apkg`) or points to stubborn items:
1. Run `anki_inspect_apkg` with `tagFilter: "leech"` and/or `minLapses: 3` (or `query` for specific keywords) to isolate cards that keep failing recall.
2. Apply [Mnemonic Playbook](./references/mnemonic-playbook.md) to craft short, vivid memory hooks (`acronym`, `rhyme`, `homophone`, `mechanism`, `contrast`, `visual`, `loci`) specifically targeting *why* the user confuses that card.
3. Write the hooks into the `Mnemonic` field via `anki_apply_mnemonics` (`updateOnly: true` emits a lightweight surgical update `.apkg` containing only patched cards with their **exact original GUIDs**).
4. Remind the user: mnemonics solve **initial encoding** for stubborn items; Anki spaced repetition maintains **long-term retrievability** — keep reviewing the updated card normally.
