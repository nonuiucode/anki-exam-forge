"""Deck validation, incremental supplement diff/merge, and mnemonic patch writeback."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .apkg import _resolve_media_path, build_apkg, inspect_apkg
from .models import (
    canonicalize_concept,
    coerce_media_list,
    coerce_source_years,
    format_frequency_badge,
    make_slug,
    normalize_card,
    sanitize_tag,
    stable_guid,
)

_CLOZE_VALID_RE = re.compile(r"\{\{c([1-9]\d*)::.+?\}\}", re.DOTALL)
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)

_MNEMONIC_LABELS = {
    "acronym": "口诀/缩略",
    "rhyme": "顺口溜",
    "homophone": "谐音联想",
    "mechanism": "机制因果链",
    "contrast": "对比辨析",
    "visual": "画面故事",
    "loci": "记忆宫殿",
}


def _primary_text(card: dict[str, Any]) -> str:
    ctype = card.get("card_type")
    if ctype == "cloze":
        return str(card.get("text") or "").strip()
    if ctype == "mcq":
        return str(card.get("stem") or "").strip()
    return str(card.get("front") or "").strip()


def _answer_text(card: dict[str, Any]) -> str:
    ctype = card.get("card_type")
    if ctype == "cloze":
        return str(card.get("text") or "").strip()
    if ctype == "mcq":
        return str(card.get("answer") or "").strip()
    return str(card.get("back") or "").strip()


def _load_deck_source(base_source: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(base_source, dict):
        cards = [normalize_card(c, i) for i, c in enumerate(base_source.get("cards") or [])]
        return {
            "deck_name": str(base_source.get("deck_name") or "ExamForge::Default"),
            "subdeck_strategy": str(base_source.get("subdeck_strategy") or "tier"),
            "cards": cards,
        }
    p = Path(str(base_source)).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Base deck source not found: {base_source}")
    if p.suffix.lower() == ".apkg":
        inspected = inspect_apkg(str(p))
        return {
            "deck_name": inspected["deck_name"],
            "subdeck_strategy": "tier",
            "cards": [normalize_card(c, i) for i, c in enumerate(inspected["cards"])],
        }
    parsed = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(parsed, list):
        cards = [normalize_card(c, i) for i, c in enumerate(parsed)]
        return {"deck_name": "ExamForge::Default", "subdeck_strategy": "tier", "cards": cards}
    if isinstance(parsed, dict):
        cards = [normalize_card(c, i) for i, c in enumerate(parsed.get("cards") or parsed.get("items") or [])]
        return {
            "deck_name": str(parsed.get("deck_name") or "ExamForge::Default"),
            "subdeck_strategy": str(parsed.get("subdeck_strategy") or "tier"),
            "cards": cards,
        }
    raise TypeError(f"Unsupported base deck JSON structure in {base_source}")


def validate_deck(deck_data: dict[str, Any], media_dir: str | None = None) -> dict[str, Any]:
    """Validate card structure, cloze syntax, MCQ options, duplicate IDs/GUIDs, and media paths."""
    raw_cards = deck_data.get("cards") if isinstance(deck_data, dict) else deck_data
    if not isinstance(raw_cards, list):
        return {
            "valid": False,
            "error_count": 1,
            "warning_count": 0,
            "card_count": 0,
            "missing_mnemonic_count": 0,
            "mnemonic_coverage": 0.0,
            "errors": [{"index": -1, "code": "invalid_deck", "message": "Deck must contain a 'cards' list"}],
            "warnings": [],
        }

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_guids: set[str] = set()
    missing_mnemonic_count = 0
    media_root = Path(media_dir).resolve() if media_dir else None

    if len(raw_cards) == 0:
        errors.append({"index": -1, "code": "empty_deck", "message": "Deck contains zero cards"})

    for idx, raw in enumerate(raw_cards):
        try:
            card = normalize_card(raw, idx)
        except Exception as exc:
            errors.append({"index": idx, "code": "invalid_card_object", "message": str(exc)})
            continue

        cid = card["card_id"]
        guid = card["guid"]
        ctype = card["card_type"]
        primary = _primary_text(card)
        answer = _answer_text(card)

        if cid in seen_ids:
            errors.append({"index": idx, "card_id": cid, "code": "duplicate_card_id", "message": f"Duplicate card_id '{cid}'"})
        seen_ids.add(cid)

        if guid in seen_guids:
            errors.append({"index": idx, "card_id": cid, "code": "duplicate_guid", "message": f"Duplicate guid '{guid}'"})
        seen_guids.add(guid)

        if not primary:
            errors.append({"index": idx, "card_id": cid, "code": "empty_prompt", "message": "Primary prompt (front/stem/text) cannot be empty"})

        if ctype == "cloze":
            if primary and not _CLOZE_VALID_RE.search(primary):
                errors.append({
                    "index": idx,
                    "card_id": cid,
                    "code": "invalid_cloze",
                    "message": "Cloze card must contain at least one valid {{c1::...}} deletion",
                })
        elif ctype == "mcq":
            opts = card.get("options") or []
            if len(opts) < 2:
                errors.append({
                    "index": idx,
                    "card_id": cid,
                    "code": "insufficient_mcq_options",
                    "message": "MCQ card must have at least 2 options",
                })
            if not answer:
                errors.append({"index": idx, "card_id": cid, "code": "empty_answer", "message": "MCQ card answer cannot be empty"})
            if not (card.get("explanation") or "").strip():
                warnings.append({"index": idx, "card_id": cid, "code": "missing_mcq_explanation", "message": "MCQ card has no explanation"})
        else:
            if not answer:
                errors.append({"index": idx, "card_id": cid, "code": "empty_back", "message": "Basic card back cannot be empty"})

        if len(primary) > 300:
            warnings.append({
                "index": idx,
                "card_id": cid,
                "code": "verbose_prompt",
                "message": f"Prompt length ({len(primary)} chars) exceeds 300 chars; consider splitting",
            })
        if ctype == "basic" and len(answer) > 650:
            warnings.append({
                "index": idx,
                "card_id": cid,
                "code": "verbose_answer",
                "message": f"Answer length ({len(answer)} chars) exceeds 650 chars; consider splitting into atomic cards",
            })
        if not (card.get("topic") or "").strip():
            warnings.append({"index": idx, "card_id": cid, "code": "missing_topic", "message": "Card has no topic/chapter label"})
        if not (card.get("mnemonic") or "").strip():
            missing_mnemonic_count += 1
            warnings.append({
                "index": idx,
                "card_id": cid,
                "code": "missing_mnemonic",
                "message": "Card has no mnemonic hook; author a concise memory cue during card generation",
            })

        # Check media existence if media_dir is provided OR explicit path references appear
        refs = list(card.get("media") or [])
        for txt in (primary, answer, str(card.get("explanation") or ""), str(card.get("mnemonic") or "")):
            refs.extend(_IMG_SRC_RE.findall(txt))
        for mref in refs:
            if media_root is not None or "/" in mref or "\\" in mref or Path(mref).suffix:
                if _resolve_media_path(mref, media_root) is None:
                    errors.append({
                        "index": idx,
                        "card_id": cid,
                        "code": "missing_media",
                        "message": f"Referenced media file not found: {mref}",
                    })

    card_count = len(raw_cards)
    mnemonic_coverage = round((card_count - missing_mnemonic_count) / card_count, 4) if card_count else 0.0
    return {
        "valid": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "card_count": card_count,
        "missing_mnemonic_count": missing_mnemonic_count,
        "mnemonic_coverage": mnemonic_coverage,
        "errors": errors,
        "warnings": warnings,
    }


def _char_bigrams(s: str) -> set[str]:
    if not s:
        return set()
    if len(s) == 1:
        return {s}
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _word_tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{2,}", s.lower()) if w}


def text_similarity(a: str, b: str) -> float:
    """Compute pure-stdlib hybrid similarity (CJK/alnum bigram Jaccard + ASCII token Jaccard)."""
    ca = canonicalize_concept(a)
    cb = canonicalize_concept(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0

    bg_a = _char_bigrams(ca)
    bg_b = _char_bigrams(cb)
    bg_union = bg_a | bg_b
    bg_score = len(bg_a & bg_b) / len(bg_union) if bg_union else 0.0

    wt_a = _word_tokens(a)
    wt_b = _word_tokens(b)
    if wt_a and wt_b:
        wt_union = wt_a | wt_b
        wt_score = len(wt_a & wt_b) / len(wt_union)
        return round(0.75 * bg_score + 0.25 * wt_score, 4)
    return round(bg_score, 4)


def diff_and_merge(
    base_source: str | dict[str, Any],
    candidates: list[Any] | dict[str, Any],
    near_threshold: float = 0.62,
    merge_near: bool = False,
    out_deck_path: str | None = None,
) -> dict[str, Any]:
    """Compare PPT/notes candidate cards against existing deck/apkg and merge only new concepts cleanly."""
    base_deck = _load_deck_source(base_source)
    existing_cards = [dict(c) for c in base_deck["cards"]]

    if isinstance(candidates, dict):
        raw_cands = candidates.get("cards") or candidates.get("items") or []
    elif isinstance(candidates, list):
        raw_cands = candidates
    else:
        raise TypeError("candidates must be a list or an object with 'cards'/'items'")

    # Index existing cards for exact/near lookups
    by_id = {c["card_id"]: c for c in existing_cards}
    by_canon: dict[str, dict[str, Any]] = {}
    for c in existing_cards:
        ck = canonicalize_concept(c.get("concept_key") or "")
        pk = canonicalize_concept(_primary_text(c))
        if ck:
            by_canon[ck] = c
        if pk:
            by_canon.setdefault(pk, c)

    exact_duplicates: list[dict[str, Any]] = []
    near_duplicates: list[dict[str, Any]] = []
    new_cards: list[dict[str, Any]] = []
    used_ids = set(by_id.keys())

    for idx, raw_cand in enumerate(raw_cands):
        cand = normalize_card(raw_cand, idx)
        cand_primary = _primary_text(cand)
        cand_ck = canonicalize_concept(cand.get("concept_key") or cand_primary)
        cand_pk = canonicalize_concept(cand_primary)

        # 1. Exact check by card_id, derived concept slug, or canonical concept/prompt
        derived_cid = make_slug(cand_ck) if cand_ck else ""
        exact_match = (
            by_id.get(cand["card_id"])
            or (by_id.get(derived_cid) if derived_cid else None)
            or by_canon.get(cand_ck)
            or by_canon.get(cand_pk)
        )
        if exact_match is not None:
            exact_duplicates.append(
                {
                    "candidate_front": cand_primary,
                    "candidate_concept": cand["concept_key"],
                    "matched_card_id": exact_match["card_id"],
                    "matched_guid": exact_match["guid"],
                    "matched_front": _primary_text(exact_match),
                }
            )
            continue

        # 2. Near-duplicate check via hybrid similarity
        best_sim = 0.0
        best_match: dict[str, Any] | None = None
        cand_cmp = f"{cand.get('concept_key', '')} {cand_primary}".strip()
        for ex in existing_cards:
            ex_cmp = f"{ex.get('concept_key', '')} {_primary_text(ex)}".strip()
            sim = max(
                text_similarity(cand_primary, _primary_text(ex)),
                text_similarity(cand_cmp, ex_cmp),
            )
            if sim > best_sim:
                best_sim = sim
                best_match = ex

        if best_match is not None and best_sim >= float(near_threshold):
            near_entry = {
                "candidate_front": cand_primary,
                "candidate_concept": cand["concept_key"],
                "matched_card_id": best_match["card_id"],
                "matched_guid": best_match["guid"],
                "matched_front": _primary_text(best_match),
                "similarity": best_sim,
                "merged": bool(merge_near),
            }
            near_duplicates.append(near_entry)
            if merge_near:
                # Enrich existing card in-place while strictly keeping original guid/card_id
                extra_note = (cand.get("explanation") or _answer_text(cand) or "").strip()
                if extra_note and extra_note not in (best_match.get("explanation") or ""):
                    prev_exp = (best_match.get("explanation") or "").strip()
                    best_match["explanation"] = (
                        f"{prev_exp}\n[课件补充] {extra_note}".strip() if prev_exp else f"[课件补充] {extra_note}"
                    )
                for y in coerce_source_years(cand.get("source_years")):
                    if y not in best_match["source_years"]:
                        best_match["source_years"].append(y)
                for t in cand.get("tags") or []:
                    if t not in best_match["tags"]:
                        best_match["tags"].append(t)
                if "source::ppt-supplement" not in best_match["tags"]:
                    best_match["tags"].append("source::ppt-supplement")
            continue

        # 3. Genuinely new card from PPT / lecture supplement
        new_cid = cand["card_id"]
        if new_cid in used_ids:
            new_cid = make_slug(f"supp-{cand_primary}-{idx}")
        used_ids.add(new_cid)

        cand["card_id"] = new_cid
        cand["guid"] = stable_guid(new_cid)
        cand["source_kind"] = "ppt_supplement"
        cand["frequency"] = max(1, int(cand.get("frequency") or 1))
        if not raw_cand.get("frequency_tier"):
            cand["frequency_tier"] = "supplement"
        if not raw_cand.get("frequency_badge"):
            cand["frequency_badge"] = format_frequency_badge(
                cand["frequency"], cand["frequency_tier"], "ppt_supplement"
            )
        if not cand.get("subdeck"):
            cand["subdeck"] = "04-课件补充"
        tags = list(cand.get("tags") or [])
        if "source::ppt-supplement" not in tags:
            tags.append("source::ppt-supplement")
        cand["tags"] = tags

        new_cards.append(cand)
        existing_cards.append(cand)
        by_id[new_cid] = cand
        if cand_ck:
            by_canon[cand_ck] = cand
        if cand_pk:
            by_canon[cand_pk] = cand

    merged_deck = {
        "deck_name": base_deck["deck_name"],
        "subdeck_strategy": base_deck.get("subdeck_strategy") or "tier",
        "cards": existing_cards,
    }

    written_path: str | None = None
    if out_deck_path:
        op = Path(out_deck_path).resolve()
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(merged_deck, ensure_ascii=False, indent=2), encoding="utf-8")
        written_path = str(op)

    return {
        "ok": True,
        "deck_name": merged_deck["deck_name"],
        "existing_count": len(base_deck["cards"]),
        "candidate_count": len(raw_cands),
        "exact_duplicate_count": len(exact_duplicates),
        "near_duplicate_count": len(near_duplicates),
        "new_count": len(new_cards),
        "merged_total_count": len(existing_cards),
        "exact_duplicates": exact_duplicates,
        "near_duplicates": near_duplicates,
        "new_cards": new_cards,
        "out_deck_path": written_path,
        "merged_deck": merged_deck,
    }


def _format_mnemonic_text(raw_mnemonic: str, technique: str | None) -> str:
    body = str(raw_mnemonic or "").strip()
    if not body:
        return ""
    tech = str(technique or "").strip().lower()
    label = _MNEMONIC_LABELS.get(tech)
    if label and not body.startswith("【"):
        return f"【{label}】{body}"
    return body


def apply_mnemonics(
    base_source: str | dict[str, Any],
    patches: list[Any] | dict[str, Any],
    mode: str = "replace",
    out_deck_path: str | None = None,
    out_apkg_path: str | None = None,
    update_only: bool = True,
    media_dir: str | None = None,
) -> dict[str, Any]:
    """Write structured mnemonics back into matching cards while strictly preserving original GUIDs."""
    base_deck = _load_deck_source(base_source)
    cards = [dict(c) for c in base_deck["cards"]]

    if isinstance(patches, dict):
        patch_list = patches.get("patches") or patches.get("mnemonics") or patches.get("items") or []
    elif isinstance(patches, list):
        patch_list = patches
    else:
        raise TypeError("patches must be a JSON list or an object containing 'patches'")

    by_guid = {c["guid"]: c for c in cards if c.get("guid")}
    by_id = {c["card_id"]: c for c in cards if c.get("card_id")}
    by_canon: dict[str, dict[str, Any]] = {}
    for c in cards:
        ck = canonicalize_concept(c.get("concept_key") or "")
        pk = canonicalize_concept(_primary_text(c))
        if ck:
            by_canon[ck] = c
        if pk:
            by_canon.setdefault(pk, c)

    updated_cards: list[dict[str, Any]] = []
    updated_ids: list[str] = []
    unmatched: list[dict[str, Any]] = []
    eff_mode = "append" if str(mode).strip().lower() == "append" else "replace"

    for idx, patch in enumerate(patch_list):
        if not isinstance(patch, dict):
            unmatched.append({"index": idx, "reason": "patch is not an object"})
            continue
        p_guid = str(patch.get("guid") or "").strip()
        p_cid = str(patch.get("card_id") or patch.get("id") or "").strip()
        p_ck_raw = str(patch.get("concept_key") or patch.get("front") or "").strip()
        p_ck = canonicalize_concept(p_ck_raw)
        derived_cid = make_slug(p_ck) if p_ck else ""
        derived_guid = stable_guid(derived_cid) if derived_cid else ""

        target = None
        if p_guid and p_guid in by_guid:
            target = by_guid[p_guid]
        elif p_cid and p_cid in by_id:
            target = by_id[p_cid]
        elif p_ck and p_ck in by_canon:
            target = by_canon[p_ck]
        elif derived_cid and derived_cid in by_id:
            target = by_id[derived_cid]
        elif derived_guid and derived_guid in by_guid:
            target = by_guid[derived_guid]

        if target is None:
            unmatched.append(
                {
                    "index": idx,
                    "guid": p_guid,
                    "card_id": p_cid,
                    "concept_key": patch.get("concept_key"),
                    "reason": "no matching card found by guid, card_id, or concept_key",
                }
            )
            continue

        formatted = _format_mnemonic_text(
            str(patch.get("mnemonic") or patch.get("memory_tip") or ""),
            patch.get("technique"),
        )
        if not formatted:
            unmatched.append({"index": idx, "card_id": target["card_id"], "reason": "empty mnemonic text"})
            continue

        existing_mn = str(target.get("mnemonic") or "").strip()
        if eff_mode == "append" and existing_mn:
            if formatted not in existing_mn:
                target["mnemonic"] = f"{existing_mn}\n{formatted}"
        else:
            target["mnemonic"] = formatted

        tags = list(target.get("tags") or [])
        if "mnemonic::added" not in tags:
            tags.append("mnemonic::added")
        if patch.get("technique"):
            tech_tag = sanitize_tag(f"mnemonic::{patch['technique']}")
            if tech_tag and tech_tag not in tags:
                tags.append(tech_tag)
        target["tags"] = tags

        if target["card_id"] not in updated_ids:
            updated_ids.append(target["card_id"])
            updated_cards.append(target)

    updated_deck = {
        "deck_name": base_deck["deck_name"],
        "subdeck_strategy": base_deck.get("subdeck_strategy") or "tier",
        "cards": cards,
    }

    written_deck: str | None = None
    if out_deck_path:
        dp = Path(out_deck_path).resolve()
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(json.dumps(updated_deck, ensure_ascii=False, indent=2), encoding="utf-8")
        written_deck = str(dp)

    built_apkg: dict[str, Any] | None = None
    if out_apkg_path:
        cards_to_pack = updated_cards if (update_only and updated_cards) else cards
        pack_deck = {
            "deck_name": base_deck["deck_name"],
            "subdeck_strategy": base_deck.get("subdeck_strategy") or "tier",
            "cards": cards_to_pack,
        }
        built_apkg = build_apkg(pack_deck, out_apkg_path, media_dir=media_dir)

    return {
        "ok": True,
        "deck_name": base_deck["deck_name"],
        "patch_count": len(patch_list),
        "matched_count": len(updated_ids),
        "unmatched_count": len(unmatched),
        "updated_card_ids": updated_ids,
        "unmatched_patches": unmatched,
        "out_deck_path": written_deck,
        "out_apkg_path": built_apkg["output_path"] if built_apkg else None,
        "apkg_summary": built_apkg,
        "updated_deck": updated_deck,
    }
