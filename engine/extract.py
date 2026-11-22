"""Zero/minimal-dependency document & slide extractor for PDF, PPTX, DOCX, text, and images."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _import_pymupdf():
    try:
        import pymupdf as fitz  # type: ignore

        return fitz
    except ImportError:
        try:
            import fitz  # type: ignore

            return fitz
        except ImportError:
            return None


def _default_out_dir(src: Path) -> Path:
    digest = hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / f"anki-forge-extract-{digest}"


def _read_text_with_fallback(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _xml_paragraphs(xml_bytes: bytes, ns: str, p_tag: str = "p", t_tag: str = "t") -> list[str]:
    root = ET.fromstring(xml_bytes)
    lines: list[str] = []
    for p_el in root.iter(f"{ns}{p_tag}"):
        chunks = [t.text for t in p_el.iter(f"{ns}{t_tag}") if t.text]
        line = "".join(chunks).strip()
        if line:
            lines.append(line)
    return lines


def _extract_pptx(
    src: Path,
    out_path: Path,
    from_page: int,
    to_page: int | None,
    max_pages: int,
    extract_images: bool,
) -> dict[str, Any]:
    media_dir = out_path / "media"
    with zipfile.ZipFile(str(src), "r") as zf:
        all_names = set(zf.namelist())
        slide_entries: list[tuple[int, str]] = []
        for name in all_names:
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if m:
                slide_entries.append((int(m.group(1)), name))
        slide_entries.sort(key=lambda pair: pair[0])

        total = len(slide_entries)
        end_idx = min(total, to_page if to_page is not None else from_page + max_pages - 1)
        selected = [pair for pair in slide_entries if from_page <= pair[0] <= end_idx]

        units: list[dict[str, Any]] = []
        extracted_media_paths: list[str] = []

        for snum, sname in selected:
            lines = _xml_paragraphs(zf.read(sname), _A_NS)
            notes_lines: list[str] = []
            slide_images: list[str] = []

            rels_name = f"ppt/slides/_rels/slide{snum}.xml.rels"
            if rels_name in all_names:
                rels_root = ET.fromstring(zf.read(rels_name))
                img_counter = 0
                for rel in rels_root.iter(f"{_REL_NS}Relationship"):
                    rtype = rel.attrib.get("Type", "")
                    target = rel.attrib.get("Target", "")
                    if not target:
                        continue
                    resolved = posixpath.normpath(posixpath.join("ppt/slides", target))
                    if resolved not in all_names:
                        continue
                    if rtype.endswith("/notesSlide"):
                        notes_lines.extend(_xml_paragraphs(zf.read(resolved), _A_NS))
                    elif extract_images and rtype.endswith("/image"):
                        ext = Path(resolved).suffix.lower() or ".png"
                        if ext in _IMAGE_EXTS:
                            img_counter += 1
                            media_dir.mkdir(parents=True, exist_ok=True)
                            img_out = media_dir / f"slide_{snum:03d}_img_{img_counter:02d}{ext}"
                            img_out.write_bytes(zf.read(resolved))
                            slide_images.append(str(img_out))
                            extracted_media_paths.append(str(img_out))

            units.append(
                {
                    "index": snum,
                    "label": f"Slide {snum}",
                    "text": "\n".join(lines),
                    "notes": "\n".join(notes_lines),
                    "images": slide_images,
                }
            )

    next_from = end_idx + 1 if end_idx < total else None
    return {
        "source_type": "pptx",
        "total_units": total,
        "processed_range": [from_page, end_idx] if selected else [],
        "next_from_page": next_from,
        "rendered_pages": [],
        "media_files": extracted_media_paths,
        "units": units,
    }


def _extract_docx(
    src: Path,
    out_path: Path,
    from_page: int,
    to_page: int | None,
    max_pages: int,
    extract_images: bool,
) -> dict[str, Any]:
    media_dir = out_path / "media"
    blocks: list[str] = []
    extracted_media_paths: list[str] = []

    with zipfile.ZipFile(str(src), "r") as zf:
        all_names = zf.namelist()
        if "word/document.xml" in all_names:
            root = ET.fromstring(zf.read("word/document.xml"))
            body = root.find(f"{_W_NS}body")
            nodes = list(body) if body is not None else list(root)
            for child in nodes:
                if child.tag == f"{_W_NS}p":
                    texts = [t.text for t in child.iter(f"{_W_NS}t") if t.text]
                    line = "".join(texts).strip()
                    if line:
                        blocks.append(line)
                elif child.tag == f"{_W_NS}tbl":
                    for row in child.iter(f"{_W_NS}tr"):
                        cells: list[str] = []
                        for cell in row.iter(f"{_W_NS}tc"):
                            cell_parts = [
                                "".join(t.text for t in p.iter(f"{_W_NS}t") if t.text).strip()
                                for p in cell.iter(f"{_W_NS}p")
                            ]
                            cells.append(" ".join(c for c in cell_parts if c))
                        if any(cells):
                            blocks.append(" | ".join(cells))

        if extract_images:
            img_idx = 0
            for member in sorted(all_names):
                if member.startswith("word/media/"):
                    ext = Path(member).suffix.lower() or ".png"
                    if ext in _IMAGE_EXTS:
                        img_idx += 1
                        media_dir.mkdir(parents=True, exist_ok=True)
                        img_out = media_dir / f"docx_img_{img_idx:02d}{ext}"
                        img_out.write_bytes(zf.read(member))
                        extracted_media_paths.append(str(img_out))

    # Group paragraphs into ~30-block logical sections for pagination
    chunk_size = 30
    chunks = [blocks[i : i + chunk_size] for i in range(0, len(blocks), chunk_size)] or [[]]
    total = len(chunks)
    end_idx = min(total, to_page if to_page is not None else from_page + max_pages - 1)
    units: list[dict[str, Any]] = []
    for sec_idx in range(max(1, from_page), end_idx + 1):
        units.append(
            {
                "index": sec_idx,
                "label": f"Section {sec_idx}",
                "text": "\n".join(chunks[sec_idx - 1]),
                "notes": "",
                "images": extracted_media_paths if sec_idx == from_page else [],
            }
        )

    next_from = end_idx + 1 if end_idx < total else None
    return {
        "source_type": "docx",
        "total_units": total,
        "processed_range": [from_page, end_idx] if units else [],
        "next_from_page": next_from,
        "rendered_pages": [],
        "media_files": extracted_media_paths,
        "units": units,
    }


def _extract_pdf(
    src: Path,
    out_path: Path,
    from_page: int,
    to_page: int | None,
    max_pages: int,
    render_mode: str,
    dpi: int,
    extract_images: bool,
) -> dict[str, Any]:
    pages_dir = out_path / "pages"
    media_dir = out_path / "media"
    fitz = _import_pymupdf()

    if fitz is not None:
        doc = fitz.open(str(src))
        try:
            total = len(doc)
            end_idx = min(total, to_page if to_page is not None else from_page + max_pages - 1)
            units: list[dict[str, Any]] = []
            rendered_pages: list[str] = []
            media_files: list[str] = []
            seen_xrefs: set[int] = set()

            for pno in range(max(1, from_page), end_idx + 1):
                page = doc[pno - 1]
                text = (page.get_text("text") or "").strip()
                page_imgs = page.get_images(full=True)
                should_render = (
                    render_mode == "always"
                    or (render_mode == "auto" and (len(text) < 40 or len(page_imgs) > 0))
                )
                rendered_path: str | None = None
                if should_render and render_mode != "never":
                    pages_dir.mkdir(parents=True, exist_ok=True)
                    pix = page.get_pixmap(dpi=dpi)
                    pfile = pages_dir / f"page_{pno:03d}.png"
                    pix.save(str(pfile))
                    rendered_path = str(pfile)
                    rendered_pages.append(rendered_path)

                embedded_paths: list[str] = []
                if extract_images and page_imgs:
                    img_seq = 0
                    for img_info in page_imgs:
                        xref = int(img_info[0])
                        if xref in seen_xrefs:
                            continue
                        seen_xrefs.add(xref)
                        try:
                            extracted = doc.extract_image(xref)
                        except Exception:
                            continue
                        width = int(extracted.get("width") or 0)
                        height = int(extracted.get("height") or 0)
                        if width < 48 or height < 48:
                            continue
                        ext = str(extracted.get("ext") or "png").lower()
                        img_seq += 1
                        media_dir.mkdir(parents=True, exist_ok=True)
                        mfile = media_dir / f"pdf_p{pno:03d}_img_{img_seq:02d}.{ext}"
                        mfile.write_bytes(extracted["image"])
                        embedded_paths.append(str(mfile))
                        media_files.append(str(mfile))

                units.append(
                    {
                        "index": pno,
                        "label": f"Page {pno}",
                        "text": text,
                        "needs_vision": len(text) < 40 or bool(page_imgs),
                        "rendered_page": rendered_path,
                        "images": embedded_paths,
                    }
                )
        finally:
            doc.close()

        next_from = end_idx + 1 if end_idx < total else None
        return {
            "source_type": "pdf",
            "total_units": total,
            "processed_range": [from_page, end_idx] if units else [],
            "next_from_page": next_from,
            "rendered_pages": rendered_pages,
            "media_files": media_files,
            "units": units,
        }

    # Optional CLI fallback via pdftoppm if PyMuPDF is not installed
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        pages_dir.mkdir(parents=True, exist_ok=True)
        last_page = to_page if to_page is not None else from_page + max_pages - 1
        prefix = pages_dir / "page"
        subprocess.run(
            [pdftoppm, "-png", "-r", str(dpi), "-f", str(from_page), "-l", str(last_page), str(src), str(prefix)],
            check=True,
            capture_output=True,
        )
        rendered = sorted(str(p) for p in pages_dir.glob("page-*.png"))
        units = [
            {
                "index": from_page + i,
                "label": f"Page {from_page + i}",
                "text": "",
                "needs_vision": True,
                "rendered_page": p,
                "images": [],
            }
            for i, p in enumerate(rendered)
        ]
        return {
            "source_type": "pdf",
            "total_units": None,
            "processed_range": [from_page, from_page + len(rendered) - 1] if rendered else [],
            "next_from_page": from_page + len(rendered) if rendered else None,
            "rendered_pages": rendered,
            "media_files": [],
            "units": units,
        }

    raise RuntimeError(
        "PDF extraction requires 'pymupdf' (pip install pymupdf) or 'pdftoppm' on PATH."
    )


def _extract_text_file(src: Path, from_page: int, to_page: int | None, max_pages: int) -> dict[str, Any]:
    raw = _read_text_with_fallback(src)
    lines = raw.replace("\r\n", "\n").split("\n")
    chunk_size = 80
    chunks = [lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)] or [[]]
    total = len(chunks)
    end_idx = min(total, to_page if to_page is not None else from_page + max_pages - 1)
    units = [
        {
            "index": idx,
            "label": f"Chunk {idx}",
            "text": "\n".join(chunks[idx - 1]).strip(),
            "images": [],
        }
        for idx in range(max(1, from_page), end_idx + 1)
    ]
    next_from = end_idx + 1 if end_idx < total else None
    return {
        "source_type": src.suffix.lstrip(".").lower() or "text",
        "total_units": total,
        "processed_range": [from_page, end_idx] if units else [],
        "next_from_page": next_from,
        "rendered_pages": [],
        "media_files": [],
        "units": units,
    }


def _extract_images_input(src: Path, from_page: int, to_page: int | None, max_pages: int) -> dict[str, Any]:
    if src.is_file():
        imgs = [src]
    else:
        imgs = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in _IMAGE_EXTS)
    total = len(imgs)
    end_idx = min(total, to_page if to_page is not None else from_page + max_pages - 1)
    units = [
        {
            "index": idx,
            "label": imgs[idx - 1].name,
            "text": "",
            "needs_vision": True,
            "rendered_page": str(imgs[idx - 1]),
            "images": [str(imgs[idx - 1])],
        }
        for idx in range(max(1, from_page), end_idx + 1)
    ]
    next_from = end_idx + 1 if end_idx < total else None
    return {
        "source_type": "images",
        "total_units": total,
        "processed_range": [from_page, end_idx] if units else [],
        "next_from_page": next_from,
        "rendered_pages": [u["rendered_page"] for u in units],
        "media_files": [u["rendered_page"] for u in units],
        "units": units,
    }


def extract_source(
    source_path: str,
    out_dir: str | None = None,
    from_page: int = 1,
    to_page: int | None = None,
    max_pages: int = 20,
    render_mode: str = "auto",
    dpi: int = 150,
    extract_images: bool = True,
) -> dict[str, Any]:
    """Extract text, notes, rendered pages, and embedded media from PDF/PPTX/DOCX/text/images."""
    src = Path(source_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Source path not found: {source_path}")

    from_page = max(1, int(from_page))
    max_pages = max(1, int(max_pages))
    out_path = Path(out_dir).resolve() if out_dir else _default_out_dir(src)
    out_path.mkdir(parents=True, exist_ok=True)

    ext = src.suffix.lower()
    if src.is_dir() or ext in _IMAGE_EXTS:
        payload = _extract_images_input(src, from_page, to_page, max_pages)
    elif ext == ".pptx":
        payload = _extract_pptx(src, out_path, from_page, to_page, max_pages, extract_images)
    elif ext == ".docx":
        payload = _extract_docx(src, out_path, from_page, to_page, max_pages, extract_images)
    elif ext == ".pdf":
        payload = _extract_pdf(src, out_path, from_page, to_page, max_pages, render_mode, int(dpi), extract_images)
    elif ext in {".md", ".txt", ".json", ".jsonl", ".csv"}:
        payload = _extract_text_file(src, from_page, to_page, max_pages)
    else:
        raise ValueError(f"Unsupported source extension '{ext}': {source_path}")

    md_lines = [f"# Extracted Source: {src.name}", ""]
    for unit in payload["units"]:
        md_lines.append(f"## {unit['label']}")
        if unit.get("text"):
            md_lines.append(unit["text"])
        if unit.get("notes"):
            md_lines.append(f"\n> Notes:\n{unit['notes']}")
        if unit.get("rendered_page"):
            md_lines.append(f"- Rendered page: `{unit['rendered_page']}`")
        if unit.get("images"):
            md_lines.append(f"- Images: {', '.join(unit['images'])}")
        md_lines.append("")

    extracted_md_path = out_path / "extracted.md"
    extracted_md_path.write_text("\n".join(md_lines), encoding="utf-8")

    result = {
        "ok": True,
        "source_path": str(src),
        "out_dir": str(out_path),
        "extracted_md_path": str(extracted_md_path),
        **payload,
    }
    manifest_path = out_path / "manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["manifest_path"] = str(manifest_path)
    return result
