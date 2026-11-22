"""Deterministic past-exam concept frequency analyzer and deck generator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import (
    SUBJECT_PRESETS,
    canonicalize_concept,
    coerce_media_list,
    coerce_source_years,
    format_frequency_badge,
    make_slug,
    normalize_card,
    sanitize_tag,
    stable_guid,
)


def _extract_primary_text(item: dict[str, Any]) -> str:
    return str(
        item.get("text")
        or item.get("front")
        or item.get("stem")
        or item.get("question")
        or item.get("prompt")
        or ""
    ).strip()


def _extract_answer_text(item: dict[str, Any]) -> str:
    return str(item.get("back") or item.get("answer") or "").strip()


def _pick_richest_str(candidates: list[str]) -> str:
    non_empty = [c.strip() for c in candidates if c and c.strip()]
    if not non_empty:
        return ""
    return max(non_empty, key=len)


def _md_cell(val: str) -> str:
    return str(val or "").replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").strip()


def analyze_frequency(
    input_data: dict[str, Any] | list[Any],
    deck_name: str | None = None,
    subject_preset: str = "general",
    high_min: int | None = None,
    medium_min: int | None = None,
    report_path: str | None = None,
    deck_out_path: str | None = None,
) -> dict[str, Any]:
    """Group past-exam items by canonical concept key, count appearances, and build a prioritized deck."""
    preset_key = subject_preset if subject_preset in SUBJECT_PRESETS else "general"
    preset = SUBJECT_PRESETS[preset_key]

    eff_high = int(high_min if high_min is not None else preset["high_min"])
    eff_med = int(medium_min if medium_min is not None else preset["medium_min"])
    if eff_high < eff_med:
        eff_high = eff_med

    if isinstance(input_data, dict):
        items = input_data.get("items") or input_data.get("cards") or []
        resolved_deck_name = str(
            deck_name or input_data.get("deck_name") or preset["default_deck_name"]
        ).strip()
        resolved_preset = str(input_data.get("subject_preset") or preset_key).strip()
        if resolved_preset in SUBJECT_PRESETS:
            preset_key = resolved_preset
            preset = SUBJECT_PRESETS[preset_key]
    elif isinstance(input_data, list):
        items = input_data
        resolved_deck_name = str(deck_name or preset["default_deck_name"]).strip()
    else:
        raise TypeError("input_data must be a JSON list of exam items or an object containing 'items'")

    groups: dict[str, dict[str, Any]] = {}
    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            raise TypeError(f"Exam item at index {idx} must be a dict")
        raw_key = str(raw_item.get("concept_key") or "").strip()
        primary = _extract_primary_text(raw_item)
        canon = canonicalize_concept(raw_key or primary)
        if not canon:
            canon = f"item{idx}"

        bucket = groups.setdefault(
            canon,
            {
                "canonical_key": canon,
                "display_key": raw_key or primary or canon,
                "items": [],
                "count": 0,
            },
        )
        try:
            weight = max(1, int(raw_item.get("count") or 1))
        except (TypeError, ValueError):
            weight = 1
        bucket["count"] += weight
        bucket["items"].append((idx, raw_item))
        if raw_key and len(raw_key) > len(bucket["display_key"]):
            bucket["display_key"] = raw_key

    cards: list[dict[str, Any]] = []
    topic_stats: dict[str, dict[str, int]] = {}
    tier_counts = {"high": 0, "medium": 0, "low": 0}

    for canon, bucket in groups.items():
        grp_items = [pair[1] for pair in bucket["items"]]
        freq = bucket["count"]
        if freq >= eff_high:
            tier = "high"
        elif freq >= eff_med:
            tier = "medium"
        else:
            tier = "low"
        tier_counts[tier] += 1

        # Pick representative item (preferring richer answer/explanation, or explicit mcq/cloze)
        rep = max(
            grp_items,
            key=lambda it: (
                len(_extract_answer_text(it))
                + len(str(it.get("explanation") or it.get("analysis") or ""))
                + (50 if it.get("options") or "{{c" in _extract_primary_text(it) else 0)
            ),
        )

        # Collect distinct question phrasings across years
        variants: list[str] = []
        seen_canon_variants: set[str] = set()
        for it in grp_items:
            p = _extract_primary_text(it)
            pc = canonicalize_concept(p)
            if p and pc not in seen_canon_variants:
                seen_canon_variants.add(pc)
                variants.append(p)

        rep_primary = _extract_primary_text(rep)
        rep_canon = canonicalize_concept(rep_primary)
        alt_variants = [v for v in variants if canonicalize_concept(v) != rep_canon]

        # Collect deduplicated sorted years, tags, media, topics
        all_years: list[str] = []
        for it in grp_items:
            all_years.extend(coerce_source_years(it.get("source_years") or it.get("years") or it.get("year")))
        unique_years = sorted(set(all_years))

        all_media: list[str] = []
        for it in grp_items:
            all_media.extend(coerce_media_list(it.get("media") or it.get("images")))
        dedup_media = list(dict.fromkeys(all_media))

        all_tags: list[str] = []
        for it in grp_items:
            raw_t = it.get("tags") or []
            if isinstance(raw_t, str):
                raw_t = [x for x in re.split(r"[,\s]+", raw_t) if x.strip()]
            for t in raw_t:
                st = sanitize_tag(str(t))
                if st and st not in all_tags:
                    all_tags.append(st)

        # Add automatic frequency & provenance tags
        auto_tags = [
            f"freq::{tier}",
            f"freq-count::{freq}x",
            "source::past-exam",
            f"subject::{preset['tag_prefix']}",
        ]
        for at in auto_tags:
            if at not in all_tags:
                all_tags.append(at)

        topic = _pick_richest_str([str(it.get("topic") or it.get("chapter") or "") for it in grp_items]) or "未分类考点"
        explanation = _pick_richest_str(
            [str(it.get("explanation") or it.get("analysis") or it.get("extra") or "") for it in grp_items]
        )
        if alt_variants:
            variants_note = "历年同类问法：" + " / ".join(alt_variants)
            explanation = f"{explanation}\n{variants_note}".strip() if explanation else variants_note

        mnemonic = _pick_richest_str([str(it.get("mnemonic") or it.get("memory_tip") or "") for it in grp_items])
        card_id = make_slug(canon)
        guid = stable_guid(card_id)

        merged_raw = dict(rep)
        merged_raw.update(
            {
                "card_id": card_id,
                "guid": guid,
                "concept_key": bucket["display_key"],
                "topic": topic,
                "frequency": freq,
                "frequency_tier": tier,
                "frequency_badge": format_frequency_badge(freq, tier, "past_exam"),
                "source_kind": "past_exam",
                "source_years": unique_years,
                "tags": all_tags,
                "media": dedup_media,
                "explanation": explanation,
                "mnemonic": mnemonic,
            }
        )
        card = normalize_card(merged_raw)
        cards.append(card)

        t_stat = topic_stats.setdefault(topic, {"concepts": 0, "appearances": 0, "high": 0})
        t_stat["concepts"] += 1
        t_stat["appearances"] += freq
        if tier == "high":
            t_stat["high"] += 1

    cards.sort(key=lambda c: (-int(c["frequency"]), str(c.get("topic") or ""), str(c.get("concept_key") or "")))

    total_appearances = sum(int(c["frequency"]) for c in cards)
    top_concepts = [
        {
            "card_id": c["card_id"],
            "concept_key": c["concept_key"],
            "topic": c["topic"],
            "frequency": c["frequency"],
            "frequency_tier": c["frequency_tier"],
            "source_years": c["source_years"],
            "card_type": c["card_type"],
        }
        for c in cards
    ]

    # Render Markdown frequency report
    md_lines = [
        f"# 真题考点频次统计报告 — {resolved_deck_name}",
        "",
        "## 1. 总览",
        f"- **原始题目总数**：{total_appearances} 道",
        f"- **归并后独立考点**：{len(cards)} 个",
        f"- **高频考点（≥{eff_high} 次）**：{tier_counts['high']} 个",
        f"- **中频考点（≥{eff_med} 次）**：{tier_counts['medium']} 个",
        f"- **常规真题（1 次）**：{tier_counts['low']} 个",
        "",
        "## 2. 考点频次排行榜",
        "| 排名 | 考点 / 题干核心 | 章节/主题 | 出现次数 | 考频分级 | 出现年份 | 卡片ID |",
        "|---:|---|---|---:|---|---|---|",
    ]
    tier_zh = {"high": "高频必背", "medium": "中频常考", "low": "真题常规"}
    for rank, c in enumerate(cards, start=1):
        years = ", ".join(c["source_years"]) or "-"
        md_lines.append(
            f"| {rank} | {_md_cell(c['concept_key'])} | {_md_cell(c['topic'])} | "
            f"{c['frequency']} | {tier_zh.get(c['frequency_tier'], c['frequency_tier'])} | "
            f"{_md_cell(years)} | `{c['card_id']}` |"
        )

    md_lines.extend(
        [
            "",
            "## 3. 章节分布汇总",
            "| 章节/主题 | 独立考点数 | 题目总出现次 | 高频考点数 |",
            "|---|---:|---:|---:|",
        ]
    )
    for tname, st in sorted(topic_stats.items(), key=lambda kv: (-kv[1]["appearances"], kv[0])):
        md_lines.append(f"| {_md_cell(tname)} | {st['concepts']} | {st['appearances']} | {st['high']} |")

    md_lines.extend(["", "## 4. 按章节整理的高频复习提纲", ""])
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        by_topic.setdefault(c["topic"], []).append(c)
    for tname, grp in by_topic.items():
        md_lines.append(f"### {tname}")
        for c in grp:
            ans = c.get("back") or c.get("answer") or c.get("text") or ""
            years = ", ".join(c["source_years"])
            yr_note = f" ({years})" if years else ""
            md_lines.append(f"- **[{c['frequency_badge']}]** {c['concept_key']}{yr_note} → {ans}")
        md_lines.append("")

    report_md = "\n".join(md_lines)
    deck_obj: dict[str, Any] = {
        "deck_name": resolved_deck_name,
        "subject_preset": preset_key,
        "subdeck_strategy": "tier",
        "stats": {
            "total_appearances": total_appearances,
            "unique_concepts": len(cards),
            "high_count": tier_counts["high"],
            "medium_count": tier_counts["medium"],
            "low_count": tier_counts["low"],
        },
        "cards": cards,
    }

    written_report: str | None = None
    if report_path:
        rp = Path(report_path).resolve()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(report_md, encoding="utf-8")
        written_report = str(rp)

    written_deck: str | None = None
    if deck_out_path:
        dp = Path(deck_out_path).resolve()
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(json.dumps(deck_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        written_deck = str(dp)

    return {
        "ok": True,
        "deck_name": resolved_deck_name,
        "subject_preset": preset_key,
        "stats": deck_obj["stats"],
        "topic_breakdown": topic_stats,
        "top_concepts": top_concepts,
        "report_path": written_report,
        "deck_out_path": written_deck,
        "report_markdown": report_md,
        "deck": deck_obj,
    }
