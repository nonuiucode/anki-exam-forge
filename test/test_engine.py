"""Comprehensive unit and end-to-end tests for the Anki Exam Forge Python engine."""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.apkg import build_apkg, inspect_apkg
from engine.deck_store import apply_mnemonics, diff_and_merge, validate_deck
from engine.extract import _import_pymupdf, extract_source
from engine.freq import analyze_frequency
from engine.models import MODEL_SPECS, field_checksum


def _make_png_bytes(width: int = 64, height: int = 64) -> bytes:
    """Create a valid minimal RGBA PNG using Python stdlib only."""
    raw_row = b"\x00" + (b"\x25\x63\xeb\xff" * width)
    raw = raw_row * height

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class TestAnkiExamForgeEngine(unittest.TestCase):
    def test_build_and_inspect_apkg_all_types_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            media_dir = tmp_dir / "assets"
            media_dir.mkdir(parents=True, exist_ok=True)
            img_path = media_dir / "brachial.png"
            img_bytes = _make_png_bytes()
            img_path.write_bytes(img_bytes)

            deck = {
                "deck_name": "TestMed::Anatomy",
                "subdeck_strategy": "tier",
                "cards": [
                    {
                        "card_id": "low-basic-1",
                        "card_type": "basic",
                        "topic": "Upper Limb",
                        "frequency": 1,
                        "frequency_tier": "low",
                        "front": "Which nerve runs in the spiral groove?",
                        "back": "Radial nerve (pH < 7.35 test inequality).",
                        "explanation": "Closely hugs the humeral shaft.",
                        "source_years": ["2021"],
                        "media": ["brachial.png"],
                    },
                    {
                        "card_id": "high-mcq-1",
                        "card_type": "mcq",
                        "topic": "Cardiology",
                        "frequency": 5,
                        "frequency_tier": "high",
                        "stem": "Which ECG leads elevate in anterior MI?",
                        "options": ["A. II, III, aVF", "B. V1-V5", "C. I, aVL", "D. V7-V9"],
                        "answer": "B",
                        "explanation": "V1-V5 reflect anterior left ventricle.",
                        "source_years": ["2019", "2020", "2022", "2023", "2025"],
                        "tags": ["freq::high", "course::internal"],
                    },
                    {
                        "card_id": "med-cloze-1",
                        "card_type": "cloze",
                        "topic": "Valves",
                        "frequency": 2,
                        "frequency_tier": "medium",
                        "text": "Mitral stenosis causes a {{c1::diastolic}} rumble best heard at the {{c2::cardiac apex}}.",
                        "extra": "Associated with opening snap.",
                        "source_years": ["2022", "2024"],
                    },
                ],
            }

            apkg_path = tmp_dir / "all_types.apkg"
            res = build_apkg(deck, str(apkg_path), media_dir=str(media_dir))
            self.assertEqual(res["note_count"], 3)
            # Basic(1) + MCQ(1) + Cloze with c1 & c2(2) = 4 cards
            self.assertEqual(res["card_count"], 4)
            self.assertEqual(res["media_count"], 1)
            self.assertTrue(apkg_path.is_file())

            # Inspect raw ZIP and SQLite tables directly
            with zipfile.ZipFile(str(apkg_path), "r") as zf:
                names = set(zf.namelist())
                self.assertIn("collection.anki2", names)
                self.assertIn("media", names)
                media_manifest = json.loads(zf.read("media").decode("utf-8"))
                self.assertEqual(media_manifest, {"0": "brachial.png"})
                self.assertEqual(zf.read("0"), img_bytes)

                db_copy = tmp_dir / "extracted.anki2"
                db_copy.write_bytes(zf.read("collection.anki2"))
                conn = sqlite3.connect(str(db_copy))
                try:
                    tables = {
                        r[0]
                        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    }
                    self.assertTrue({"col", "notes", "cards", "revlog", "graves"}.issubset(tables))

                    notes = conn.execute("SELECT id, guid, mid, tags, flds, sfld, csum FROM notes").fetchall()
                    self.assertEqual(len(notes), 3)
                    for nid, guid, mid, tags, flds, sfld, csum in notes:
                        first_fld = flds.split("\x1f")[0]
                        self.assertEqual(csum, field_checksum(first_fld))
                        if tags:
                            self.assertTrue(tags.startswith(" ") and tags.endswith(" "))

                    cards = conn.execute("SELECT id, nid, did, ord, due FROM cards ORDER BY due ASC").fetchall()
                    self.assertEqual(len(cards), 4)
                    # Highest frequency card (MCQ, freq=5) must have due=1
                    decks_col = json.loads(conn.execute("SELECT decks FROM col").fetchone()[0])
                    self.assertIn("1", decks_col)
                    first_card_note = conn.execute(
                        "SELECT mid FROM notes WHERE id=?", (cards[0][1],)
                    ).fetchone()
                    self.assertEqual(first_card_note[0], MODEL_SPECS["mcq"]["id"])

                    # Cloze note must produce 2 cards with ord 0 and 1
                    cloze_nids = [
                        n[0] for n in notes if n[2] == MODEL_SPECS["cloze"]["id"]
                    ]
                    self.assertEqual(len(cloze_nids), 1)
                    cloze_ords = sorted(
                        c[3] for c in cards if c[1] == cloze_nids[0]
                    )
                    self.assertEqual(cloze_ords, [0, 1])
                finally:
                    conn.close()

            # Inspect round-trip fidelity + media extraction
            extracted_media = tmp_dir / "out_media"
            insp = inspect_apkg(str(apkg_path), extract_media_dir=str(extracted_media))
            self.assertEqual(insp["total_notes"], 3)
            self.assertEqual(insp["total_cards"], 4)
            self.assertEqual(insp["media_files"], ["brachial.png"])
            self.assertEqual((extracted_media / "brachial.png").read_bytes(), img_bytes)
            mcq_cards = [c for c in insp["cards"] if c["card_type"] == "mcq"]
            self.assertEqual(len(mcq_cards), 1)
            self.assertEqual(len(mcq_cards[0]["options"]), 4)
            self.assertEqual(mcq_cards[0]["answer"], "B")

    def test_guid_stability_across_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            deck = {
                "deck_name": "StableDeck",
                "cards": [
                    {
                        "card_id": "stable-1",
                        "front": "Question A",
                        "back": "Answer A",
                        "topic": "Topic 1",
                    },
                    {
                        "card_id": "stable-2",
                        "card_type": "cloze",
                        "text": "Cloze {{c1::target}} value",
                        "topic": "Topic 2",
                    },
                ],
            }
            p1 = tmp_dir / "build1.apkg"
            p2 = tmp_dir / "build2.apkg"
            build_apkg(deck, str(p1))
            build_apkg(deck, str(p2))

            i1 = inspect_apkg(str(p1))
            i2 = inspect_apkg(str(p2))
            self.assertEqual(
                [(c["card_id"], c["guid"]) for c in i1["cards"]],
                [(c["card_id"], c["guid"]) for c in i2["cards"]],
            )

            # Check deterministic model & deck IDs across both SQLite col tables
            def read_col_ids(path: Path) -> tuple[set[str], set[str]]:
                with zipfile.ZipFile(str(path), "r") as zf:
                    tmp_db = tmp_dir / f"{path.stem}.sqlite"
                    tmp_db.write_bytes(zf.read("collection.anki2"))
                conn = sqlite3.connect(str(tmp_db))
                try:
                    m, d = conn.execute("SELECT models, decks FROM col").fetchone()
                    return set(json.loads(m).keys()), set(json.loads(d).keys())
                finally:
                    conn.close()

            self.assertEqual(read_col_ids(p1), read_col_ids(p2))

    def test_inspect_prefers_anki21_and_filters_leech_and_lapses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # Build a real collection.anki21 and a 1-note dummy collection.anki2
            stub_apkg = tmp_dir / "stub.apkg"
            real_apkg = tmp_dir / "real.apkg"
            build_apkg(
                {"deck_name": "Stub", "cards": [{"card_id": "stub-only", "front": "Upgrade Anki", "back": "Stub"}]},
                str(stub_apkg),
            )
            build_apkg(
                {
                    "deck_name": "Med",
                    "cards": [
                        {"card_id": "easy-1", "front": "Easy Fact", "back": "Ans 1", "tags": ["freq::low"]},
                        {"card_id": "hard-1", "front": "Stubborn Cranial Nerves", "back": "12 pairs", "tags": ["leech", "freq::high"]},
                    ],
                },
                str(real_apkg),
            )

            with zipfile.ZipFile(str(stub_apkg), "r") as zs:
                stub_db = zs.read("collection.anki2")
            with zipfile.ZipFile(str(real_apkg), "r") as zr:
                real_db = zr.read("collection.anki2")

            # Mutate real_db so hard-1 card has lapses=5
            real_db_file = tmp_dir / "real.anki21"
            real_db_file.write_bytes(real_db)
            conn = sqlite3.connect(str(real_db_file))
            try:
                hard_nid = conn.execute("SELECT id FROM notes WHERE tags LIKE '%leech%'").fetchone()[0]
                conn.execute("UPDATE cards SET lapses=5, reps=12 WHERE nid=?", (hard_nid,))
                conn.commit()
            finally:
                conn.close()

            combo_apkg = tmp_dir / "desktop_export.apkg"
            with zipfile.ZipFile(str(combo_apkg), "w") as zout:
                zout.writestr("collection.anki2", stub_db)
                zout.write(str(real_db_file), "collection.anki21")
                zout.writestr("media", "{}")

            insp = inspect_apkg(str(combo_apkg), tag_filter="leech", min_lapses=3)
            self.assertEqual(insp["db_source"], "collection.anki21")
            self.assertEqual(insp["total_notes"], 2)
            self.assertEqual(insp["matched_count"], 1)
            self.assertEqual(insp["cards"][0]["card_id"], "hard-1")
            self.assertEqual(insp["cards"][0]["max_lapses"], 5)

            # Verify collection.anki21b-only gives an actionable error
            v3_apkg = tmp_dir / "v3_only.apkg"
            with zipfile.ZipFile(str(v3_apkg), "w") as zv3:
                zv3.writestr("collection.anki21b", b"compressed-zstd")
                zv3.writestr("media", "{}")
            with self.assertRaises(ValueError) as ctx:
                inspect_apkg(str(v3_apkg))
            self.assertIn("collection.anki21b", str(ctx.exception))

    def test_frequency_analysis_end_to_end(self) -> None:
        sample_file = REPO_ROOT / "examples" / "sample-medical-exams.json"
        raw = json.loads(sample_file.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            rep_path = tmp_dir / "freq.md"
            deck_path = tmp_dir / "deck_v1.json"
            res = analyze_frequency(
                raw,
                subject_preset="clinical-medicine",
                report_path=str(rep_path),
                deck_out_path=str(deck_path),
            )
            self.assertEqual(res["stats"]["total_appearances"], 11)
            self.assertEqual(res["stats"]["unique_concepts"], 5)
            self.assertEqual(res["stats"]["high_count"], 2)
            self.assertEqual(res["stats"]["medium_count"], 1)
            self.assertEqual(res["stats"]["low_count"], 2)
            counts = [c["frequency"] for c in res["top_concepts"]]
            self.assertEqual(counts, [4, 3, 2, 1, 1])
            self.assertTrue(rep_path.is_file())
            self.assertTrue(deck_path.is_file())
            report_text = rep_path.read_text(encoding="utf-8")
            self.assertIn("桡神经损伤临床表现", report_text)
            self.assertIn("2019, 2021, 2023, 2025", report_text)

    def test_validate_deck_catches_errors_and_warnings(self) -> None:
        bad_deck = {
            "deck_name": "BadDeck",
            "cards": [
                {"card_id": "dup-1", "card_type": "cloze", "text": "No cloze syntax here", "topic": "T1"},
                {"card_id": "dup-1", "card_type": "mcq", "stem": "Pick one", "options": ["A. Only one"], "answer": "A"},
                {"card_id": "missing-img", "front": "Q " + ("x" * 310), "back": "A", "media": ["does_not_exist.png"]},
            ],
        }
        res = validate_deck(bad_deck)
        self.assertFalse(res["valid"])
        err_codes = {e["code"] for e in res["errors"]}
        warn_codes = {w["code"] for w in res["warnings"]}
        self.assertIn("invalid_cloze", err_codes)
        self.assertIn("insufficient_mcq_options", err_codes)
        self.assertIn("duplicate_card_id", err_codes)
        self.assertIn("missing_media", err_codes)
        self.assertIn("verbose_prompt", warn_codes)
        self.assertIn("missing_topic", warn_codes)

    def test_extract_synthetic_pptx_docx_and_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            png_bytes = _make_png_bytes()

            # 1. Synthetic PPTX with 2 slides, speaker notes, and embedded image
            pptx_path = tmp_dir / "lecture.pptx"
            with zipfile.ZipFile(str(pptx_path), "w") as zp:
                zp.writestr(
                    "ppt/slides/slide1.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<p:cSld><p:spTree><p:sp><p:txBody>"
                    "<a:p><a:r><a:t>第一页：门静脉高压三大表现</a:t></a:r></a:p>"
                    "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
                )
                zp.writestr(
                    "ppt/slides/_rels/slide1.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>'
                    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>'
                    "</Relationships>",
                )
                zp.writestr(
                    "ppt/notesSlides/notesSlide1.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<p:cSld><p:spTree><p:sp><p:txBody>"
                    "<a:p><a:r><a:t>讲者备注：重点考食管胃底静脉曲张</a:t></a:r></a:p>"
                    "</p:txBody></p:sp></p:spTree></p:cSld></p:notes>",
                )
                zp.writestr("ppt/media/image1.png", png_bytes)
                zp.writestr(
                    "ppt/slides/slide2.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<p:cSld><p:spTree><p:sp><p:txBody>"
                    "<a:p><a:r><a:t>第二页：肾病综合征</a:t></a:r></a:p>"
                    "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
                )

            ext_pptx = extract_source(str(pptx_path), out_dir=str(tmp_dir / "pptx_out"))
            self.assertEqual(ext_pptx["source_type"], "pptx")
            self.assertEqual(len(ext_pptx["units"]), 2)
            self.assertIn("门静脉高压三大表现", ext_pptx["units"][0]["text"])
            self.assertIn("食管胃底静脉曲张", ext_pptx["units"][0]["notes"])
            self.assertEqual(len(ext_pptx["media_files"]), 1)
            self.assertTrue(Path(ext_pptx["media_files"][0]).is_file())

            # 2. Synthetic DOCX with paragraph, table, and embedded image
            docx_path = tmp_dir / "notes.docx"
            with zipfile.ZipFile(str(docx_path), "w") as zd:
                zd.writestr(
                    "word/document.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:body>"
                    "<w:p><w:r><w:t>讲义标题：酸碱平衡</w:t></w:r></w:p>"
                    "<w:tbl><w:tr>"
                    "<w:tc><w:p><w:r><w:t>类型</w:t></w:r></w:p></w:tc>"
                    "<w:tc><w:p><w:r><w:t>指标</w:t></w:r></w:p></w:tc>"
                    "</w:tr><w:tr>"
                    "<w:tc><w:p><w:r><w:t>呼吸性酸中毒</w:t></w:r></w:p></w:tc>"
                    "<w:tc><w:p><w:r><w:t>PaCO2 &gt; 45 mmHg</w:t></w:r></w:p></w:tc>"
                    "</w:tr></w:tbl>"
                    "</w:body></w:document>",
                )
                zd.writestr("word/media/img1.png", png_bytes)

            ext_docx = extract_source(str(docx_path), out_dir=str(tmp_dir / "docx_out"))
            self.assertEqual(ext_docx["source_type"], "docx")
            self.assertIn("讲义标题：酸碱平衡", ext_docx["units"][0]["text"])
            self.assertIn("呼吸性酸中毒 | PaCO2 > 45 mmHg", ext_docx["units"][0]["text"])
            self.assertEqual(len(ext_docx["media_files"]), 1)

            # 3. Synthetic PDF when PyMuPDF is available
            fitz = _import_pymupdf()
            if fitz is not None:
                pdf_path = tmp_dir / "exam.pdf"
                doc = fitz.open()
                page = doc.new_page()
                page.insert_text((72, 72), "2024 Anatomy Exam Question 1")
                doc.save(str(pdf_path))
                doc.close()

                ext_pdf = extract_source(
                    str(pdf_path),
                    out_dir=str(tmp_dir / "pdf_out"),
                    render_mode="always",
                    dpi=100,
                )
                self.assertEqual(ext_pdf["source_type"], "pdf")
                self.assertEqual(len(ext_pdf["rendered_pages"]), 1)
                self.assertTrue(Path(ext_pdf["rendered_pages"][0]).is_file())
                self.assertIn("Anatomy Exam Question 1", ext_pdf["units"][0]["text"])

    def test_ppt_incremental_diff_and_mnemonic_roundtrip(self) -> None:
        exams = json.loads((REPO_ROOT / "examples" / "sample-medical-exams.json").read_text(encoding="utf-8"))
        supp = json.loads((REPO_ROOT / "examples" / "sample-ppt-supplement.json").read_text(encoding="utf-8"))
        patches = json.loads((REPO_ROOT / "examples" / "sample-mnemonics.json").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # Stage 1 & 2: frequency analysis -> validate -> build v1 .apkg
            freq_res = analyze_frequency(exams, subject_preset="clinical-medicine")
            v1_deck = freq_res["deck"]
            self.assertTrue(validate_deck(v1_deck)["valid"])
            v1_apkg = tmp_dir / "v1.apkg"
            build_apkg(v1_deck, str(v1_apkg))
            v1_guids = {c["card_id"]: c["guid"] for c in inspect_apkg(str(v1_apkg))["cards"]}

            # Stage 3: diff lecture PPT supplement against v1 .apkg directly
            diff_res = diff_and_merge(str(v1_apkg), supp, near_threshold=0.58, merge_near=True)
            self.assertEqual(diff_res["exact_duplicate_count"], 1)
            self.assertEqual(diff_res["near_duplicate_count"], 1)
            self.assertEqual(diff_res["new_count"], 2)
            self.assertEqual(diff_res["merged_total_count"], 7)

            v2_deck = diff_res["merged_deck"]
            self.assertTrue(validate_deck(v2_deck)["valid"])
            v2_apkg = tmp_dir / "v2.apkg"
            build_apkg(v2_deck, str(v2_apkg))

            v2_insp = inspect_apkg(str(v2_apkg))
            v2_guids = {c["card_id"]: c["guid"] for c in v2_insp["cards"]}
            for cid, orig_guid in v1_guids.items():
                self.assertEqual(v2_guids[cid], orig_guid)

            # Stage 4: apply mnemonics back from v2 .apkg and export surgical update .apkg
            patch_apkg = tmp_dir / "mnemonic_update.apkg"
            mne_res = apply_mnemonics(
                str(v2_apkg),
                patches,
                mode="replace",
                out_apkg_path=str(patch_apkg),
                update_only=True,
            )
            self.assertEqual(mne_res["matched_count"], 2)
            self.assertEqual(mne_res["unmatched_count"], 0)

            patch_insp = inspect_apkg(str(patch_apkg))
            self.assertEqual(patch_insp["total_notes"], 2)
            for card in patch_insp["cards"]:
                self.assertIn("【", card["mnemonic"])
                self.assertEqual(card["guid"], v2_guids[card["card_id"]])


if __name__ == "__main__":
    unittest.main()
