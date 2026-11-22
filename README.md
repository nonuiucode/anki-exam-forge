# anki-exam-forge

[中文文档](README.zh.md)

A closed-loop **Antigravity plugin**, **MCP server**, and **universal cross-platform Agent Skill** for turning past exam papers and lecture slides into high-yield, zero-duplicate **Anki `.apkg`** decks. Designed with **zero third-party runtime dependencies** on Node.js `>=20` and Python `>=3.9` stdlib (`pymupdf` or `pdftoppm` optional for PDF rendering).

## The 4-Stage Closed Loop

1. **Past-Exam Ingestion & Deterministic Frequency Analysis**
   - `extract` reads `.pdf` (extracting text + rasterizing scanned/illustrated pages to PNGs for multimodal inspection), `.pptx` (slide text, speaker notes, and embedded images via stdlib XML/ZIP), `.docx` (paragraphs, tables, and images), `.md`/`.txt`, and image folders.
   - `freq` aggregates exam items by normalized `concept_key`, counts exact appearances deterministically, summarizes source years and alternative phrasings, writes a **Markdown frequency report**, and outputs a prioritized `deck.json` tagged with `freq::high` / `freq::medium` / `freq::low`.
2. **Zero-Dependency Stable-GUID `.apkg` Builder & Inspector**
   - Pure-stdlib Python builds Anki v11 `.apkg` archives (`collection.anki2` + `media`) with light/dark-aware templates across three Note Types:
     - `ExamForge-Basic` (Q&A cards)
     - `ExamForge-MCQ` (Interactive click-to-highlight options + rationale on back)
     - `ExamForge-Cloze` (Multi-cloze `{{c1::...}}` / `{{c2::...}}` card expansion)
   - Orders `cards.due` so high-frequency past-exam concepts appear first, bundles referenced images, and assigns deterministic `guid`s so future updates apply **in place** without losing Anki review history.
   - `inspect` reads `.apkg` archives (preferring `collection.anki21` over `collection.anki2`), extracts media, and filters by tag (`leech`), deck, keyword, or `minLapses`.
3. **Lecture PPT / Notes Incremental Supplement (Zero Duplicates)**
   - `diff` compares candidate cards from lecture slides/notes against an existing `deck.json` or `.apkg` using canonical key matching + hybrid bigram Jaccard similarity — skipping exact duplicates, surfacing near-duplicates (with optional `--merge-near` enrichment), and appending only genuinely new cards into `04-课件补充` while preserving every existing `guid`.
4. **Leech / Stuck Card Mnemonic Writeback**
   - `inspect` isolates stubborn high-lapse or `leech`-tagged cards; `mnemonic` writes structured hooks (acronyms, rhymes, homophones, mechanism chains, contrast tables, visual/loci cues) strictly into the `Mnemonic` field and exports a surgical `.apkg` update preserving original `guid`s.

> **Core Principle:** Mnemonics solve *initial encoding* for stubborn items; Anki spaced repetition maintains *long-term retrievability*.

## 8 Tools (MCP & CLI)

| MCP Tool | CLI Subcommand | Purpose |
|---|---|---|
| `anki_doctor` | `doctor` | Inspect Python, SQLite, OOXML (`.pptx`/`.docx`), and PDF backend readiness |
| `anki_extract_source` | `extract` | Extract text, speaker notes, rendered PNG pages, and images from PDF/PPTX/DOCX/text |
| `anki_analyze_frequency` | `freq` | Deterministically count past-exam frequencies and generate Markdown report + deck JSON |
| `anki_validate_deck` | `validate` | Validate card fields, `{{c1::}}` cloze syntax, MCQ options, IDs/GUIDs, and media paths |
| `anki_build_apkg` | `build` | Compile deck JSON into a deterministic-GUID `.apkg` with frequency subdecks & media |
| `anki_inspect_apkg` | `inspect` | Inspect `.apkg` (preferring `collection.anki21`) and filter by tag, deck, query, or `minLapses` |
| `anki_diff_supplement` | `diff` | Diff lecture supplement candidates against an existing deck or `.apkg` safely |
| `anki_apply_mnemonics` | `mnemonic` | Write memory hooks into matching cards' `Mnemonic` field and export in-place `.apkg` updates |

## Prerequisites

- **Python `>= 3.9`** (core `.apkg`, frequency engine, `.pptx`/`.docx` extraction use stdlib only)
- **Node.js `>= 20`** (for MCP server, Node CLI, and installer)
- **Optional PDF rendering**: `pymupdf` (`pip install pymupdf`) or `pdftoppm` on `PATH` (only needed when processing `.pdf` files)

## Quick Start

```bash
# 1. Install as an Antigravity global plugin (~/.gemini/config/plugins/anki-exam-forge/)
node scripts/install.mjs --antigravity-global

# 2. Or install into a specific workspace (.agents/plugins/anki-exam-forge/)
node scripts/install.mjs --antigravity-workspace /path/to/workspace

# 3. Or install as a self-contained universal Skill (includes engine/ for Claude Code / Codex / OpenCode)
node scripts/install.mjs --skill-only ~/.agents/skills

# 4. Or print portable MCP JSON config for Claude Code / Cursor / Gemini CLI / Codex
node scripts/install.mjs --print-mcp
```

Run diagnostics:

```bash
node server/cli.mjs doctor
# or directly via Python:
python engine/cli.py doctor
```

## End-to-End Walkthrough (`examples/`)

```bash
# 1. Count past-exam frequencies -> Markdown report + v1 deck
node server/cli.mjs freq examples/sample-medical-exams.json --preset clinical-medicine --report-out out/freq.md --deck-out out/deck_v1.json

# 2. Validate & build v1 .apkg
node server/cli.mjs validate out/deck_v1.json
node server/cli.mjs build out/deck_v1.json --out out/med_v1.apkg

# 3. Diff lecture PPT candidates against existing .apkg -> merge & rebuild v2
node server/cli.mjs diff --base out/med_v1.apkg --candidates examples/sample-ppt-supplement.json --merge-near --out-deck out/deck_v2.json
node server/cli.mjs build out/deck_v2.json --out out/med_v2.apkg

# 4. Inspect & write mnemonics back while preserving GUIDs
node server/cli.mjs inspect out/med_v2.apkg --query 垂腕
node server/cli.mjs mnemonic --base out/med_v2.apkg --patches examples/sample-mnemonics.json --out-deck out/deck_v3.json --out-apkg out/med_mnemonic_patch.apkg
```

## Running Tests

```bash
npm test
# Or individually:
npm run test:py
npm run test:js
```

## License

Released under the [MIT License](LICENSE).
