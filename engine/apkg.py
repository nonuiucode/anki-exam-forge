"""Zero-dependency Anki .apkg builder and inspector (SQLite v11 + ZIP)."""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from .models import (
    MODEL_NAMES,
    MODEL_SPECS,
    SUBDECK_BY_TIER,
    build_anki_models_json,
    field_checksum,
    format_field_html,
    format_frequency_badge,
    format_mcq_options_html,
    normalize_card,
    stable_guid,
    stable_int_id,
    strip_html,
)

_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
_CLOZE_ORD_RE = re.compile(r"\{\{c(\d+)::", re.DOTALL)
_EF_OPT_RE = re.compile(
    r'<div\s+class="ef-opt"[^>]*>\s*<span\s+class="ef-opt-key">(.*?)</span>\s*<span\s+class="ef-opt-body">(.*?)</span>\s*</div>',
    re.IGNORECASE | re.DOTALL,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS col (
    id              integer primary key,
    crt             integer not null,
    mod             integer not null,
    scm             integer not null,
    ver             integer not null,
    dty             integer not null,
    usn             integer not null,
    ls              integer not null,
    conf            text not null,
    models          text not null,
    decks           text not null,
    dconf           text not null,
    tags            text not null
);
CREATE TABLE IF NOT EXISTS notes (
    id              integer primary key,
    guid            text not null,
    mid             integer not null,
    mod             integer not null,
    usn             integer not null,
    tags            text not null,
    flds            text not null,
    sfld            text not null,
    csum            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE IF NOT EXISTS cards (
    id              integer primary key,
    nid             integer not null,
    did             integer not null,
    ord             integer not null,
    mod             integer not null,
    usn             integer not null,
    type            integer not null,
    queue           integer not null,
    due             integer not null,
    ivl             integer not null,
    factor          integer not null,
    reps            integer not null,
    lapses          integer not null,
    left            integer not null,
    odue            integer not null,
    odid            integer not null,
    flags           integer not null,
    data            text not null
);
CREATE TABLE IF NOT EXISTS revlog (
    id              integer primary key,
    cid             integer not null,
    usn             integer not null,
    ease            integer not null,
    ivl             integer not null,
    lastIvl         integer not null,
    factor          integer not null,
    time            integer not null,
    type            integer not null
);
CREATE TABLE IF NOT EXISTS graves (
    usn             integer not null,
    oid             integer not null,
    type            integer not null
);
CREATE INDEX IF NOT EXISTS ix_notes_usn on notes (usn);
CREATE INDEX IF NOT EXISTS ix_cards_usn on cards (usn);
CREATE INDEX IF NOT EXISTS ix_revlog_usn on revlog (usn);
CREATE INDEX IF NOT EXISTS ix_cards_nid on cards (nid);
CREATE INDEX IF NOT EXISTS ix_cards_sched on cards (did, queue, due);
CREATE INDEX IF NOT EXISTS ix_revlog_cid on revlog (cid);
CREATE INDEX IF NOT EXISTS ix_notes_csum on notes (csum);
"""


def _default_conf(deck_id: int) -> dict[str, Any]:
    return {
        "nextPos": 1,
        "estTimes": True,
        "activeDecks": [deck_id],
        "sortType": "noteFld",
        "timeLim": 0,
        "sortBackwards": False,
        "addToCur": True,
        "curDeck": deck_id,
        "newBury": True,
        "newSpread": 0,
        "dueCounts": True,
        "curModel": str(MODEL_SPECS["basic"]["id"]),
        "collapseTime": 1200,
    }


def _default_dconf(mod_ts: int) -> dict[str, dict[str, Any]]:
    return {
        "1": {
            "id": 1,
            "mod": mod_ts,
            "name": "Default",
            "usn": -1,
            "maxTaken": 60,
            "autoplay": True,
            "timer": 0,
            "replayq": True,
            "new": {
                "bury": False,
                "delays": [1, 10],
                "initialFactor": 2500,
                "ints": [1, 4, 7],
                "order": 1,
                "perDay": 30,
            },
            "rev": {
                "bury": False,
                "ease4": 1.3,
                "fuzz": 0.05,
                "ivlFct": 1.0,
                "maxIvl": 36500,
                "perDay": 200,
                "hardFactor": 1.2,
            },
            "lapse": {
                "delays": [10],
                "leechAction": 0,
                "leechFails": 8,
                "minInt": 1,
                "mult": 0.0,
            },
        }
    }


def _make_deck_entry(deck_id: int, name: str, mod_ts: int, desc: str = "") -> dict[str, Any]:
    return {
        "id": deck_id,
        "mod": mod_ts,
        "name": name,
        "usn": -1,
        "lrnToday": [0, 0],
        "revToday": [0, 0],
        "newToday": [0, 0],
        "timeToday": [0, 0],
        "collapsed": False,
        "browserCollapsed": False,
        "desc": desc,
        "dyn": 0,
        "conf": 1,
        "extendNew": 0,
        "extendRev": 0,
    }


def _resolve_target_subdeck(card: dict[str, Any], root_deck: str, strategy: str) -> str:
    explicit = str(card.get("subdeck") or "").strip()
    if explicit:
        if explicit == root_deck or explicit.startswith(f"{root_deck}::"):
            return explicit
        return f"{root_deck}::{explicit}"

    if strategy == "flat":
        return root_deck
    if strategy == "topic":
        topic = str(card.get("topic") or "").strip()
        return f"{root_deck}::{topic}" if topic else root_deck

    # Default strategy: "tier"
    if card.get("source_kind") == "ppt_supplement" and card.get("frequency", 1) <= 1:
        sub = SUBDECK_BY_TIER["supplement"]
    else:
        tier = str(card.get("frequency_tier") or "low").lower()
        sub = SUBDECK_BY_TIER.get(tier, SUBDECK_BY_TIER["low"])
    return f"{root_deck}::{sub}"


def _resolve_media_path(ref: str, media_dir: Path | None) -> Path | None:
    cleaned = ref.strip()
    if not cleaned:
        return None
    direct = Path(cleaned)
    if direct.is_file():
        return direct.resolve()
    if media_dir is not None:
        rel = media_dir / cleaned
        if rel.is_file():
            return rel.resolve()
        by_name = media_dir / direct.name
        if by_name.is_file():
            return by_name.resolve()
    return None


def _sanitize_media_filename(ref: str) -> str:
    base = os.path.basename(ref.replace("\\", "/").strip())
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base) or "media.bin"


def _build_note_fields(
    card: dict[str, Any],
    media_html: str,
) -> tuple[dict[str, Any], list[str]]:
    ctype = card["card_type"]
    spec = MODEL_SPECS[ctype]
    years_str = ", ".join(card.get("source_years") or [])
    badge = card.get("frequency_badge") or format_frequency_badge(
        card.get("frequency", 1),
        card.get("frequency_tier", "low"),
        card.get("source_kind", "past_exam"),
    )
    topic = format_field_html(card.get("topic") or "")
    mnemonic = format_field_html(card.get("mnemonic") or "")
    card_id = str(card["card_id"]).replace("\x1f", " ").strip()

    if ctype == "basic":
        fields = [
            format_field_html(card.get("front") or ""),
            format_field_html(card.get("back") or ""),
            format_field_html(card.get("explanation") or ""),
            mnemonic,
            format_field_html(badge),
            format_field_html(years_str),
            topic,
            media_html,
            card_id,
        ]
    elif ctype == "mcq":
        opts_raw = card.get("options")
        if isinstance(opts_raw, str) and '<div class="ef-opt-list"' in opts_raw:
            opts_html = opts_raw
        else:
            opts_html = format_mcq_options_html(opts_raw)
        fields = [
            format_field_html(card.get("stem") or ""),
            opts_html,
            format_field_html(card.get("answer") or ""),
            format_field_html(card.get("explanation") or ""),
            mnemonic,
            format_field_html(badge),
            format_field_html(years_str),
            topic,
            media_html,
            card_id,
        ]
    else:
        fields = [
            format_field_html(card.get("text") or ""),
            format_field_html(card.get("extra") or card.get("explanation") or ""),
            mnemonic,
            format_field_html(badge),
            format_field_html(years_str),
            topic,
            media_html,
            card_id,
        ]
    return spec, fields


def build_apkg(
    deck_data: dict[str, Any],
    output_path: str,
    media_dir: str | None = None,
    subdeck_strategy: str | None = None,
) -> dict[str, Any]:
    """Compile normalized deck_data into a valid Anki .apkg archive."""
    if not isinstance(deck_data, dict):
        raise TypeError("deck_data must be a JSON object")

    root_deck = str(deck_data.get("deck_name") or "ExamForge::Default").strip() or "ExamForge::Default"
    strategy = (subdeck_strategy or deck_data.get("subdeck_strategy") or "tier").strip().lower()
    if strategy not in ("tier", "topic", "flat"):
        strategy = "tier"

    raw_cards = deck_data.get("cards") or []
    if not raw_cards:
        raise ValueError("Cannot build .apkg: deck contains zero cards")

    cards = [normalize_card(c, idx) for idx, c in enumerate(raw_cards)]
    # Sort priority for Anki due ordering: past_exam before ppt_supplement, then higher frequency first.
    indexed_cards = list(enumerate(cards))
    indexed_cards.sort(
        key=lambda pair: (
            0 if pair[1].get("source_kind", "past_exam") == "past_exam" else 1,
            -int(pair[1].get("frequency") or 1),
            pair[0],
        )
    )
    ordered_cards = [pair[1] for pair in indexed_cards]

    media_root = Path(media_dir).resolve() if media_dir else None
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    now_sec = int(time.time())
    root_did = stable_int_id("deck", root_deck)
    decks_map: dict[str, dict[str, Any]] = {
        "1": _make_deck_entry(1, "Default", now_sec),
        str(root_did): _make_deck_entry(
            root_did, root_deck, now_sec, str(deck_data.get("description") or "")
        ),
    }
    models_map = build_anki_models_json(root_did, now_sec)

    # Media deduplication map: target_filename -> source Path
    media_registry: dict[str, Path] = {}
    source_to_name: dict[Path, str] = {}

    def register_media(ref: str) -> str:
        resolved = _resolve_media_path(ref, media_root)
        if resolved is None:
            raise FileNotFoundError(f"Referenced media file not found: {ref}")
        if resolved in source_to_name:
            return source_to_name[resolved]
        base_name = _sanitize_media_filename(ref)
        candidate = base_name
        stem = Path(base_name).stem
        suffix = Path(base_name).suffix
        counter = 2
        while candidate in media_registry and media_registry[candidate] != resolved:
            candidate = f"{stem}_{counter}{suffix}"
            counter += 1
        media_registry[candidate] = resolved
        source_to_name[resolved] = candidate
        return candidate

    def rewrite_inline_imgs(text: str) -> tuple[str, set[str]]:
        embedded_names: set[str] = set()

        def _repl(match: re.Match[str]) -> str:
            orig_src = match.group(1)
            safe_name = register_media(orig_src)
            embedded_names.add(safe_name)
            return match.group(0).replace(orig_src, safe_name, 1)

        updated = _IMG_SRC_RE.sub(_repl, text)
        return updated, embedded_names

    used_note_ids: set[int] = set()
    used_card_ids: set[int] = set()
    base_ms = max(1_700_000_000_000, int(time.time() * 1000))
    all_tags: set[str] = set()
    subdeck_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {"basic": 0, "mcq": 0, "cloze": 0}
    tier_counts: dict[str, int] = {}

    note_rows: list[tuple[Any, ...]] = []
    card_rows: list[tuple[Any, ...]] = []
    due_seq = 1

    for idx, card in enumerate(ordered_cards):
        target_deck_name = _resolve_target_subdeck(card, root_deck, strategy)
        target_did = stable_int_id("deck", target_deck_name)
        if str(target_did) not in decks_map:
            decks_map[str(target_did)] = _make_deck_entry(target_did, target_deck_name, now_sec)
        subdeck_counts[target_deck_name] = subdeck_counts.get(target_deck_name, 0) + 1

        ctype = card["card_type"]
        type_counts[ctype] = type_counts.get(ctype, 0) + 1
        tier_key = card.get("frequency_tier") or "low"
        tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1

        # Resolve inline <img> tags in text fields first
        inline_media_names: set[str] = set()
        card_copy = dict(card)
        for text_key in ("front", "stem", "text", "back", "answer", "explanation", "extra", "mnemonic"):
            val = card_copy.get(text_key)
            if isinstance(val, str) and "<img" in val.lower():
                rewritten, found = rewrite_inline_imgs(val)
                card_copy[text_key] = rewritten
                inline_media_names.update(found)

        # Build Media field from card["media"] entries not already inline
        media_tags_html: list[str] = []
        for mref in card.get("media") or []:
            safe_name = register_media(mref)
            if safe_name not in inline_media_names:
                media_tags_html.append(f'<img src="{html.escape(safe_name, quote=True)}">')
        media_html = "".join(media_tags_html)

        spec, fields_list = _build_note_fields(card_copy, media_html)
        flds_joined = "\x1f".join(fields_list)
        sfld_val = strip_html(fields_list[0]).strip()
        csum_val = field_checksum(fields_list[0])

        nid = base_ms + idx * 100
        while nid in used_note_ids or nid in used_card_ids:
            nid += 1
        used_note_ids.add(nid)

        guid = str(card.get("guid") or stable_guid(card["card_id"]))
        tags_list = list(card.get("tags") or [])
        all_tags.update(tags_list)
        tags_str = f" {' '.join(tags_list)} " if tags_list else ""

        note_rows.append(
            (nid, guid, spec["id"], now_sec, -1, tags_str, flds_joined, sfld_val, csum_val, 0, "{}")
        )

        if ctype == "cloze":
            ords = sorted({int(m) - 1 for m in _CLOZE_ORD_RE.findall(fields_list[0]) if int(m) >= 1})
            if not ords:
                raise ValueError(f"Cloze card '{card['card_id']}' lacks a valid {{{{c1::...}}}} deletion")
        else:
            ords = [0]

        for card_ord in ords:
            cid = base_ms + idx * 100 + 1 + card_ord
            while cid in used_card_ids or cid in used_note_ids:
                cid += 1
            used_card_ids.add(cid)
            card_rows.append(
                (cid, nid, target_did, card_ord, now_sec, -1, 0, 0, due_seq, 0, 2500, 0, 0, 0, 0, 0, 0, "{}")
            )
            due_seq += 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_SCHEMA_SQL)
            tags_json = {t: 0 for t in sorted(all_tags)}
            conn.execute(
                "INSERT INTO col (id, crt, mod, scm, ver, dty, usn, ls, conf, models, decks, dconf, tags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    1,
                    now_sec,
                    now_sec * 1000,
                    now_sec * 1000,
                    11,
                    0,
                    0,
                    0,
                    json.dumps(_default_conf(root_did), ensure_ascii=False),
                    json.dumps(models_map, ensure_ascii=False),
                    json.dumps(decks_map, ensure_ascii=False),
                    json.dumps(_default_dconf(now_sec), ensure_ascii=False),
                    json.dumps(tags_json, ensure_ascii=False),
                ),
            )
            conn.executemany(
                "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                note_rows,
            )
            conn.executemany(
                "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                card_rows,
            )
            conn.commit()
        finally:
            conn.close()

        tmp_apkg = out_file.parent / f".tmp-{os.getpid()}-{out_file.name}"
        media_json_map: dict[str, str] = {}
        try:
            with zipfile.ZipFile(str(tmp_apkg), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(str(db_path), "collection.anki2")
                for m_idx, (fname, src_path) in enumerate(sorted(media_registry.items())):
                    idx_key = str(m_idx)
                    media_json_map[idx_key] = fname
                    zf.write(str(src_path), idx_key)
                zf.writestr("media", json.dumps(media_json_map, ensure_ascii=False))
            os.replace(str(tmp_apkg), str(out_file))
        except Exception:
            if tmp_apkg.exists():
                tmp_apkg.unlink(missing_ok=True)
            raise

    return {
        "ok": True,
        "output_path": str(out_file),
        "deck_name": root_deck,
        "subdeck_strategy": strategy,
        "note_count": len(note_rows),
        "card_count": len(card_rows),
        "media_count": len(media_registry),
        "subdecks": subdeck_counts,
        "by_type": type_counts,
        "by_tier": tier_counts,
    }


def _html_field_to_plain_preserving_rich(val: str) -> str:
    """Convert Anki field HTML back to normalized editable text without breaking rich tags."""
    if not val:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", val, flags=re.IGNORECASE)
    return html.unescape(text).strip()


def _parse_mcq_options_field(val: str) -> list[str]:
    matches = _EF_OPT_RE.findall(val or "")
    if matches:
        out: list[str] = []
        for key_html, body_html in matches:
            key = strip_html(key_html).strip()
            body = _html_field_to_plain_preserving_rich(body_html)
            out.append(f"{key} {body}".strip())
        return out
    plain = _html_field_to_plain_preserving_rich(val)
    return [ln.strip() for ln in plain.split("\n") if ln.strip()]


def inspect_apkg(
    apkg_path: str,
    tag_filter: str | None = None,
    deck_filter: str | None = None,
    query: str | None = None,
    min_lapses: int | None = None,
    extract_media_dir: str | None = None,
) -> dict[str, Any]:
    """Read and inspect an existing .apkg archive (preferring collection.anki21 over collection.anki2)."""
    apkg_file = Path(apkg_path).resolve()
    if not apkg_file.is_file():
        raise FileNotFoundError(f".apkg file not found: {apkg_path}")

    with zipfile.ZipFile(str(apkg_file), "r") as zf:
        names = set(zf.namelist())
        if "collection.anki21" in names:
            db_member = "collection.anki21"
        elif "collection.anki2" in names:
            db_member = "collection.anki2"
        elif "collection.anki21b" in names:
            raise ValueError(
                "This .apkg uses Anki 2.1.50+ compressed v3 database (collection.anki21b). "
                "Please re-export from Anki Desktop with 'Support older Anki versions' (兼容旧版 Anki) enabled."
            )
        else:
            raise ValueError(f"Invalid .apkg archive (missing collection database): {apkg_path}")

        media_map: dict[str, str] = {}
        if "media" in names:
            try:
                parsed_media = json.loads(zf.read("media").decode("utf-8"))
                if isinstance(parsed_media, dict):
                    media_map = {str(k): str(v) for k, v in parsed_media.items()}
            except Exception:
                media_map = {}

        if extract_media_dir and media_map:
            dest_media = Path(extract_media_dir).resolve()
            dest_media.mkdir(parents=True, exist_ok=True)
            for zip_key, filename in media_map.items():
                if zip_key in names:
                    safe_name = _sanitize_media_filename(filename)
                    (dest_media / safe_name).write_bytes(zf.read(zip_key))

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / db_member
            db_path.write_bytes(zf.read(db_member))
            conn = sqlite3.connect(str(db_path))
            try:
                col_row = conn.execute("SELECT models, decks FROM col LIMIT 1").fetchone()
                if not col_row:
                    raise ValueError("Corrupt .apkg: col table is empty")
                models_dict = json.loads(col_row[0])
                decks_dict = json.loads(col_row[1])

                card_stats: dict[int, dict[str, Any]] = {}
                for nid, did, lapses, reps in conn.execute(
                    "SELECT nid, did, lapses, reps FROM cards"
                ).fetchall():
                    entry = card_stats.setdefault(
                        nid, {"dids": [], "card_count": 0, "max_lapses": 0, "total_reps": 0}
                    )
                    entry["dids"].append(did)
                    entry["card_count"] += 1
                    entry["max_lapses"] = max(entry["max_lapses"], int(lapses or 0))
                    entry["total_reps"] += int(reps or 0)

                note_rows = conn.execute(
                    "SELECT id, guid, mid, tags, flds FROM notes ORDER BY id ASC"
                ).fetchall()
            finally:
                conn.close()

    deck_names = {int(k): v.get("name", str(k)) for k, v in decks_dict.items() if isinstance(v, dict)}
    tag_filter_low = tag_filter.strip().lower() if tag_filter else None
    deck_filter_low = deck_filter.strip().lower() if deck_filter else None
    query_low = query.strip().lower() if query else None

    total_notes = len(note_rows)
    total_cards = sum(s["card_count"] for s in card_stats.values())
    matched_cards: list[dict[str, Any]] = []
    deck_counter: dict[str, int] = {}
    model_counter: dict[str, int] = {}

    for nid, guid, mid, tags_raw, flds_raw in note_rows:
        model = models_dict.get(str(mid), {})
        model_name = str(model.get("name") or f"Model-{mid}")
        model_type = int(model.get("type") or 0)
        field_names = [str(f.get("name") or f"Field{i}") for i, f in enumerate(model.get("flds") or [])]
        raw_vals = str(flds_raw or "").split("\x1f")
        fields_dict = {
            (field_names[i] if i < len(field_names) else f"Field{i}"): val
            for i, val in enumerate(raw_vals)
        }

        tags_list = [t for t in str(tags_raw or "").strip().split() if t]
        stats = card_stats.get(nid, {"dids": [], "card_count": 0, "max_lapses": 0, "total_reps": 0})
        primary_did = stats["dids"][0] if stats["dids"] else 1
        dname = deck_names.get(primary_did, "Default")

        deck_counter[dname] = deck_counter.get(dname, 0) + 1
        model_counter[model_name] = model_counter.get(model_name, 0) + 1

        # Apply filters
        if tag_filter_low:
            if not any(t.lower() == tag_filter_low or t.lower().startswith(f"{tag_filter_low}::") for t in tags_list):
                continue
        if deck_filter_low and deck_filter_low not in dname.lower():
            continue
        if min_lapses is not None and stats["max_lapses"] < min_lapses:
            continue
        if query_low:
            joined_plain = " ".join(strip_html(v) for v in raw_vals).lower()
            if query_low not in joined_plain and not any(query_low in t.lower() for t in tags_list):
                continue

        # Infer ExamForge or generic card mapping
        if model_name == MODEL_NAMES["mcq"] or ("Stem" in fields_dict and "Options" in fields_dict):
            card_type = "mcq"
        elif model_type == 1 or model_name == MODEL_NAMES["cloze"] or "Text" in fields_dict:
            card_type = "cloze"
        else:
            card_type = "basic"

        media_srcs: list[str] = []
        for v in raw_vals:
            media_srcs.extend(_IMG_SRC_RE.findall(v))
        media_unique: list[str] = []
        for ms in media_srcs:
            if ms not in media_unique:
                media_unique.append(ms)

        card_id = (fields_dict.get("CardID") or "").strip() or f"note-{guid}"
        topic = _html_field_to_plain_preserving_rich(fields_dict.get("Topic") or "")
        mnemonic = _html_field_to_plain_preserving_rich(fields_dict.get("Mnemonic") or "")
        badge = _html_field_to_plain_preserving_rich(fields_dict.get("FrequencyBadge") or "")
        years_raw = _html_field_to_plain_preserving_rich(fields_dict.get("SourceYears") or "")
        source_years = [y.strip() for y in years_raw.split(",") if y.strip()] if years_raw else []

        # Parse frequency count and tier from badge/tags when available
        freq_val = 1
        freq_match = re.search(r"(\d+)\s*次", badge)
        if freq_match:
            freq_val = int(freq_match.group(1))
        if "freq::high" in tags_list or "高频" in badge:
            tier = "high"
        elif "freq::medium" in tags_list or "中频" in badge:
            tier = "medium"
        elif "source::ppt-supplement" in tags_list or "课件补充" in badge:
            tier = "supplement"
        else:
            tier = "low"

        subdeck_rel = dname.split("::", 1)[1] if "::" in dname else ""
        rec: dict[str, Any] = {
            "note_id": nid,
            "guid": guid,
            "card_id": card_id,
            "model_name": model_name,
            "card_type": card_type,
            "deck_name": dname,
            "subdeck": subdeck_rel,
            "tags": tags_list,
            "card_count": stats["card_count"],
            "max_lapses": stats["max_lapses"],
            "total_reps": stats["total_reps"],
            "topic": topic,
            "mnemonic": mnemonic,
            "frequency": freq_val,
            "frequency_tier": tier,
            "frequency_badge": badge,
            "source_kind": "ppt_supplement" if "source::ppt-supplement" in tags_list else "past_exam",
            "source_years": source_years,
            "media": media_unique,
            "fields": fields_dict,
        }

        if card_type == "mcq":
            rec["stem"] = _html_field_to_plain_preserving_rich(fields_dict.get("Stem") or "")
            rec["options"] = _parse_mcq_options_field(fields_dict.get("Options") or "")
            rec["answer"] = _html_field_to_plain_preserving_rich(fields_dict.get("Answer") or "")
            rec["explanation"] = _html_field_to_plain_preserving_rich(fields_dict.get("Explanation") or "")
        elif card_type == "cloze":
            rec["text"] = _html_field_to_plain_preserving_rich(
                fields_dict.get("Text") or (raw_vals[0] if raw_vals else "")
            )
            rec["extra"] = _html_field_to_plain_preserving_rich(
                fields_dict.get("Extra") or (raw_vals[1] if len(raw_vals) > 1 else "")
            )
            rec["explanation"] = rec["extra"]
        else:
            front_val = fields_dict.get("Front") if "Front" in fields_dict else (raw_vals[0] if raw_vals else "")
            back_val = fields_dict.get("Back") if "Back" in fields_dict else (raw_vals[1] if len(raw_vals) > 1 else "")
            rec["front"] = _html_field_to_plain_preserving_rich(front_val)
            rec["back"] = _html_field_to_plain_preserving_rich(back_val)
            rec["explanation"] = _html_field_to_plain_preserving_rich(fields_dict.get("Explanation") or "")

        matched_cards.append(rec)

    top_deck = max(deck_counter.items(), key=lambda kv: kv[1])[0].split("::")[0] if deck_counter else "Default"
    return {
        "ok": True,
        "apkg_path": str(apkg_file),
        "db_source": db_member,
        "deck_name": top_deck,
        "total_notes": total_notes,
        "total_cards": total_cards,
        "matched_count": len(matched_cards),
        "media_count": len(media_map),
        "media_files": sorted(media_map.values()),
        "decks": deck_counter,
        "models": model_counter,
        "cards": matched_cards,
    }
