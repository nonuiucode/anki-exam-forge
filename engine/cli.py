"""Unified Python CLI for Anki Exam Forge (works standalone or via Node.js MCP/CLI)."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

if not __package__:
    _PARENT = str(Path(__file__).resolve().parent.parent)
    if _PARENT not in sys.path:
        sys.path.insert(0, _PARENT)

from engine import __version__
from engine.apkg import build_apkg, inspect_apkg
from engine.deck_store import apply_mnemonics, diff_and_merge, validate_deck
from engine.extract import _import_pymupdf, extract_source
from engine.freq import analyze_frequency
from engine.models import SUBJECT_PRESETS


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8")
            except Exception:
                pass


def _load_json_or_jsonl(path_str: str) -> Any:
    p = Path(path_str).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Input file not found: {path_str}")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if p.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def _run_doctor() -> dict[str, Any]:
    fitz_mod = _import_pymupdf()
    pdftoppm_path = shutil.which("pdftoppm")
    pdf_backend = "pymupdf" if fitz_mod is not None else ("pdftoppm" if pdftoppm_path else "none")
    return {
        "ok": True,
        "version": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
        "sqlite_version": sqlite3.sqlite_version,
        "stdlib_ready": True,
        "pptx_docx_ready": True,
        "pdf_backend": pdf_backend,
        "pymupdf_available": fitz_mod is not None,
        "pdftoppm_path": pdftoppm_path,
        "presets": list(SUBJECT_PRESETS.keys()),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anki-exam-forge",
        description="Closed-loop Anki exam flashcard forge CLI",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON (default)")
    parser.add_argument("--human", action="store_true", help="Output human-readable summary")
    sub = parser.add_subparsers(dest="command", required=True)

    # doctor
    p_doc = sub.add_parser("doctor", help="Inspect Python, SQLite, PDF, and OOXML readiness")
    p_doc.add_argument("--json", action="store_true")
    p_doc.add_argument("--human", action="store_true")

    # extract
    p_ext = sub.add_parser("extract", help="Extract text/pages/media from PDF, PPTX, DOCX, text, or images")
    p_ext.add_argument("source", help="Input file or image directory")
    p_ext.add_argument("--out-dir", dest="out_dir", default=None)
    p_ext.add_argument("--from-page", dest="from_page", type=int, default=1)
    p_ext.add_argument("--to-page", dest="to_page", type=int, default=None)
    p_ext.add_argument("--max-pages", dest="max_pages", type=int, default=20)
    p_ext.add_argument("--render-mode", dest="render_mode", choices=["auto", "always", "never"], default="auto")
    p_ext.add_argument("--dpi", type=int, default=150)
    p_ext.add_argument("--no-images", dest="no_images", action="store_true")
    p_ext.add_argument("--json", action="store_true")
    p_ext.add_argument("--human", action="store_true")

    # freq
    p_freq = sub.add_parser("freq", help="Analyze past-exam concept frequencies and emit Markdown report + deck JSON")
    p_freq.add_argument("input", help="JSON or JSONL file with exam items")
    p_freq.add_argument("--deck-name", dest="deck_name", default=None)
    p_freq.add_argument("--preset", default="general", choices=list(SUBJECT_PRESETS.keys()))
    p_freq.add_argument("--high-min", dest="high_min", type=int, default=None)
    p_freq.add_argument("--medium-min", dest="medium_min", type=int, default=None)
    p_freq.add_argument("--report-out", dest="report_out", default=None)
    p_freq.add_argument("--deck-out", dest="deck_out", default=None)
    p_freq.add_argument("--json", action="store_true")
    p_freq.add_argument("--human", action="store_true")

    # validate
    p_val = sub.add_parser("validate", help="Validate deck JSON structure, cloze syntax, and media references")
    p_val.add_argument("deck", help="Path to deck JSON file")
    p_val.add_argument("--media-dir", dest="media_dir", default=None)
    p_val.add_argument("--json", action="store_true")
    p_val.add_argument("--human", action="store_true")

    # build
    p_bld = sub.add_parser("build", help="Compile deck JSON into a deterministic-GUID Anki .apkg archive")
    p_bld.add_argument("deck", help="Path to deck JSON file")
    p_bld.add_argument("--out", required=True, help="Output .apkg path")
    p_bld.add_argument("--media-dir", dest="media_dir", default=None)
    p_bld.add_argument("--subdeck-strategy", dest="subdeck_strategy", choices=["tier", "topic", "flat"], default=None)
    p_bld.add_argument("--json", action="store_true")
    p_bld.add_argument("--human", action="store_true")

    # inspect
    p_ins = sub.add_parser("inspect", help="Inspect an existing .apkg archive and filter cards (e.g. leeches)")
    p_ins.add_argument("apkg", help="Path to .apkg file")
    p_ins.add_argument("--tag", default=None)
    p_ins.add_argument("--deck", default=None)
    p_ins.add_argument("--query", default=None)
    p_ins.add_argument("--min-lapses", dest="min_lapses", type=int, default=None)
    p_ins.add_argument("--extract-media", dest="extract_media", default=None)
    p_ins.add_argument("--out-json", dest="out_json", default=None)
    p_ins.add_argument("--json", action="store_true")
    p_ins.add_argument("--human", action="store_true")

    # diff
    p_dif = sub.add_parser("diff", help="Diff lecture supplement candidates against an existing deck or .apkg")
    p_dif.add_argument("--base", required=True, help="Base deck JSON or .apkg file")
    p_dif.add_argument("--candidates", required=True, help="Candidate supplement cards JSON/JSONL")
    p_dif.add_argument("--threshold", type=float, default=0.62)
    p_dif.add_argument("--merge-near", dest="merge_near", action="store_true")
    p_dif.add_argument("--out-deck", dest="out_deck", default=None)
    p_dif.add_argument("--json", action="store_true")
    p_dif.add_argument("--human", action="store_true")

    # mnemonic
    p_mne = sub.add_parser("mnemonic", help="Write mnemonics back to matching cards while preserving GUIDs")
    p_mne.add_argument("--base", required=True, help="Base deck JSON or .apkg file")
    p_mne.add_argument("--patches", required=True, help="Mnemonic patches JSON/JSONL file")
    p_mne.add_argument("--mode", choices=["replace", "append"], default="replace")
    p_mne.add_argument("--out-deck", dest="out_deck", default=None)
    p_mne.add_argument("--out-apkg", dest="out_apkg", default=None)
    p_mne.add_argument("--full-deck", dest="full_deck", action="store_true")
    p_mne.add_argument("--media-dir", dest="media_dir", default=None)
    p_mne.add_argument("--json", action="store_true")
    p_mne.add_argument("--human", action="store_true")

    return parser


def _print_human(cmd: str, res: dict[str, Any]) -> None:
    if cmd == "doctor":
        print(
            f"Anki Exam Forge v{res['version']} | Python {res['python']} | "
            f"SQLite {res['sqlite_version']} | PPTX/DOCX: ready | PDF: {res['pdf_backend']}"
        )
    elif cmd == "extract":
        print(
            f"Extracted {len(res.get('units', []))} unit(s) ({res.get('source_type')}) -> {res.get('out_dir')}\n"
            f"Markdown: {res.get('extracted_md_path')}\nManifest: {res.get('manifest_path')}"
        )
    elif cmd == "freq":
        st = res["stats"]
        print(
            f"Deck '{res['deck_name']}': {st['total_appearances']} appearances -> "
            f"{st['unique_concepts']} unique concepts "
            f"(high={st['high_count']}, medium={st['medium_count']}, low={st['low_count']})"
        )
    elif cmd == "validate":
        print(
            f"Valid={res['valid']} | cards={res['card_count']} | "
            f"errors={res['error_count']} | warnings={res['warning_count']}"
        )
    elif cmd == "build":
        print(
            f"Built {res['output_path']} | notes={res['note_count']} | "
            f"cards={res['card_count']} | media={res['media_count']}"
        )
    elif cmd == "inspect":
        print(
            f"Inspected {res['apkg_path']} ({res['db_source']}): "
            f"{res['total_notes']} notes, {res['total_cards']} cards, matched={res['matched_count']}"
        )
    elif cmd == "diff":
        print(
            f"Diff '{res['deck_name']}': exact_dups={res['exact_duplicate_count']}, "
            f"near_dups={res['near_duplicate_count']}, new={res['new_count']}, total={res['merged_total_count']}"
        )
    elif cmd == "mnemonic":
        print(
            f"Mnemonic patch '{res['deck_name']}': matched={res['matched_count']}, "
            f"unmatched={res['unmatched_count']}"
        )


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)

    cmd = args.command
    if cmd == "doctor":
        result = _run_doctor()
    elif cmd == "extract":
        result = extract_source(
            args.source,
            out_dir=args.out_dir,
            from_page=args.from_page,
            to_page=args.to_page,
            max_pages=args.max_pages,
            render_mode=args.render_mode,
            dpi=args.dpi,
            extract_images=not args.no_images,
        )
    elif cmd == "freq":
        raw = _load_json_or_jsonl(args.input)
        result = analyze_frequency(
            raw,
            deck_name=args.deck_name,
            subject_preset=args.preset,
            high_min=args.high_min,
            medium_min=args.medium_min,
            report_path=args.report_out,
            deck_out_path=args.deck_out,
        )
    elif cmd == "validate":
        deck = _load_json_or_jsonl(args.deck)
        result = validate_deck(deck, media_dir=args.media_dir)
    elif cmd == "build":
        deck = _load_json_or_jsonl(args.deck)
        result = build_apkg(
            deck,
            output_path=args.out,
            media_dir=args.media_dir,
            subdeck_strategy=args.subdeck_strategy,
        )
    elif cmd == "inspect":
        result = inspect_apkg(
            args.apkg,
            tag_filter=args.tag,
            deck_filter=args.deck,
            query=args.query,
            min_lapses=args.min_lapses,
            extract_media_dir=args.extract_media,
        )
        if args.out_json:
            op = Path(args.out_json).resolve()
            op.parent.mkdir(parents=True, exist_ok=True)
            op.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            result["out_json_path"] = str(op)
    elif cmd == "diff":
        cands = _load_json_or_jsonl(args.candidates)
        result = diff_and_merge(
            args.base,
            cands,
            near_threshold=args.threshold,
            merge_near=args.merge_near,
            out_deck_path=args.out_deck,
        )
    elif cmd == "mnemonic":
        patches = _load_json_or_jsonl(args.patches)
        result = apply_mnemonics(
            args.base,
            patches,
            mode=args.mode,
            out_deck_path=args.out_deck,
            out_apkg_path=args.out_apkg,
            update_only=not args.full_deck,
            media_dir=args.media_dir,
        )
    else:
        raise ValueError(f"Unknown command: {cmd}")

    if getattr(args, "human", False) and not getattr(args, "json", False):
        _print_human(cmd, result)
    else:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _configure_stdio()
        sys.stderr.write(f"Error: {exc}\n")
        raise SystemExit(1)
