"""Card schemas, deterministic IDs/GUIDs, HTML formatters, and Anki Note Type templates."""

from __future__ import annotations

import hashlib
import html
import re
from typing import Any

MODEL_NAMES = {
    "basic": "ExamForge-Basic",
    "mcq": "ExamForge-MCQ",
    "cloze": "ExamForge-Cloze",
}

SUBDECK_BY_TIER = {
    "high": "01-高频必背",
    "medium": "02-中频常考",
    "low": "03-真题常规",
    "supplement": "04-课件补充",
}

SUBJECT_PRESETS: dict[str, dict[str, Any]] = {
    "general": {
        "label": "通用高记忆密度学科",
        "high_min": 3,
        "medium_min": 2,
        "tag_prefix": "exam",
        "default_deck_name": "ExamForge::默认牌组",
    },
    "clinical-medicine": {
        "label": "临床医学（系解/组胚/生理/病理/生化/内外妇儿/皮性）",
        "high_min": 3,
        "medium_min": 2,
        "tag_prefix": "med",
        "default_deck_name": "临床医学::真题高频库",
    },
    "postgrad-exam": {
        "label": "考研统考与联考（西综/政治/英语/专业课）",
        "high_min": 3,
        "medium_min": 2,
        "tag_prefix": "kaoyan",
        "default_deck_name": "考研冲刺::真题高频库",
    },
    "law-exam": {
        "label": "法考与公考（法条/要件/行测/申论高频点）",
        "high_min": 3,
        "medium_min": 2,
        "tag_prefix": "law",
        "default_deck_name": "法考备考::真题高频库",
    },
}

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ALLOWED_HTML_TAG_RE = re.compile(
    r"(</?(?:b|i|u|strong|em|br|p|ul|ol|li|table|tr|td|th|thead|tbody|img|code|pre|sub|sup|span|div|details|summary|hr)(?:\s+[^>]*)?\s*/?>)",
    re.IGNORECASE,
)
_BLOCK_HTML_RE = re.compile(r"<(?:div|ul|ol|table|p|pre|details)\b", re.IGNORECASE)
_CLOZE_MARKER_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.DOTALL)
_OPTION_PREFIX_RE = re.compile(r"^\s*([A-Ha-h])\s*[.．、:：\)\]]\s*(.*)$")


def stable_int_id(namespace: str, key: str) -> int:
    """Return a deterministic positive integer safe for Anki SQLite and JavaScript."""
    digest = hashlib.sha256(f"anki-exam-forge:{namespace}:{key}".encode("utf-8")).hexdigest()
    return 1_000_000_000 + int(digest[:12], 16)


def stable_guid(card_id: str) -> str:
    """Return a deterministic 12-char Base62 GUID for Anki in-place note updates."""
    digest = hashlib.sha256(f"anki-exam-forge:guid:{card_id}".encode("utf-8")).digest()
    val = int.from_bytes(digest[:10], "big")
    chars: list[str] = []
    for _ in range(12):
        chars.append(_BASE62[val % 62])
        val //= 62
    return "".join(chars)


def strip_html(text: str) -> str:
    """Strip HTML tags and unescape HTML entities for Anki sfld/csum."""
    without_tags = re.sub(r"<[^>]+>", "", str(text or ""))
    return html.unescape(without_tags)


def field_checksum(first_field: str) -> int:
    """Compute Anki's 32-bit SHA1 field checksum over plain-text first field."""
    plain = strip_html(first_field).strip()
    return int(hashlib.sha1(plain.encode("utf-8")).hexdigest()[:8], 16)


def canonicalize_concept(text: str) -> str:
    """Normalize text/concept key into a punctuation-free canonical string."""
    plain = strip_html(text)
    plain = _CLOZE_MARKER_RE.sub(lambda m: m.group(2), plain)
    plain = plain.lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", plain)


def make_slug(text: str, max_len: int = 32) -> str:
    """Create a readable deterministic ASCII/CJK slug + short hash."""
    canon = canonicalize_concept(text)
    short_hash = hashlib.sha1((canon or str(text or "")).encode("utf-8")).hexdigest()[:8]
    ascii_head = re.sub(r"[^a-z0-9]+", "-", canon.lower()).strip("-")
    if ascii_head:
        return f"{ascii_head[:max_len]}-{short_hash}"
    return f"card-{short_hash}"


def sanitize_tag(tag: str) -> str:
    """Sanitize a tag into Anki-safe non-whitespace token."""
    cleaned = re.sub(r"\s+", "_", str(tag or "").strip())
    return cleaned.strip("#_")


def format_field_html(text: Any) -> str:
    """Format plain or rich field text into clean Anki HTML without breaking inequalities."""
    if text is None:
        return ""
    raw = str(text).replace("\x1f", " ").strip()
    if not raw:
        return ""
    parts = _ALLOWED_HTML_TAG_RE.split(raw)
    out: list[str] = []
    has_block = bool(_BLOCK_HTML_RE.search(raw))
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            out.append(part)
        else:
            escaped = re.sub(r"&(?!#?\w+;)", "&amp;", part)
            escaped = escaped.replace("<", "&lt;").replace(">", "&gt;")
            if not has_block:
                escaped = escaped.replace("\r\n", "\n").replace("\n", "<br>")
            out.append(escaped)
    return "".join(out)


def format_mcq_options_html(options: Any) -> str:
    """Render MCQ options list or multiline string into structured option cards."""
    if not options:
        return ""
    if isinstance(options, str):
        lines = [ln.strip() for ln in options.replace("\r\n", "\n").split("\n") if ln.strip()]
    elif isinstance(options, list):
        lines = [str(item).strip() for item in options if str(item).strip()]
    else:
        lines = [str(options).strip()]
    if not lines:
        return ""

    rendered: list[str] = ['<div class="ef-opt-list">']
    for idx, line in enumerate(lines):
        match = _OPTION_PREFIX_RE.match(line)
        if match:
            letter = match.group(1).upper()
            body = match.group(2).strip()
            label = f"{letter}. "
        else:
            letter = chr(65 + (idx % 26))
            body = line
            label = f"{letter}. "
        rendered.append(
            f'<div class="ef-opt" data-opt="{letter}">'
            f'<span class="ef-opt-key">{label}</span>'
            f'<span class="ef-opt-body">{format_field_html(body)}</span>'
            f"</div>"
        )
    rendered.append("</div>")
    return "".join(rendered)


def format_frequency_badge(frequency: int, tier: str, source_kind: str) -> str:
    """Format concise human-readable frequency badge text."""
    if source_kind == "ppt_supplement" and frequency <= 1:
        return "课件补充"
    if tier == "high" or frequency >= 3:
        return f"高频 · {frequency}次"
    if tier == "medium" or frequency == 2:
        return f"中频 · {frequency}次"
    return f"真题 · {max(1, frequency)}次"


def coerce_source_years(raw_years: Any) -> list[str]:
    """Coerce source years / exam sessions into a deduplicated sorted-like list of strings."""
    if raw_years is None:
        return []
    if isinstance(raw_years, (int, float)):
        items = [str(int(raw_years))]
    elif isinstance(raw_years, str):
        items = [s.strip() for s in re.split(r"[,，;；\s]+", raw_years) if s.strip()]
    elif isinstance(raw_years, list):
        items = [str(x).strip() for x in raw_years if str(x).strip()]
    else:
        items = [str(raw_years).strip()]
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def coerce_media_list(raw_media: Any) -> list[str]:
    """Coerce media references into a deduplicated list of path/filename strings."""
    if not raw_media:
        return []
    if isinstance(raw_media, str):
        items = [raw_media.strip()] if raw_media.strip() else []
    elif isinstance(raw_media, list):
        items = [str(x).strip() for x in raw_media if str(x).strip()]
    else:
        items = []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def normalize_card(raw: dict[str, Any], idx: int = 0) -> dict[str, Any]:
    """Normalize arbitrary card/item dicts into canonical Anki Exam Forge card structure."""
    if not isinstance(raw, dict):
        raise TypeError(f"Card at index {idx} must be a dict, got {type(raw).__name__}")

    options = raw.get("options") if raw.get("options") is not None else raw.get("choices")
    primary_raw = (
        raw.get("text")
        or raw.get("front")
        or raw.get("stem")
        or raw.get("question")
        or raw.get("prompt")
        or ""
    )
    primary_str = str(primary_raw).strip()

    explicit_type = str(raw.get("card_type") or raw.get("type") or "").strip().lower()
    if explicit_type in ("basic", "mcq", "cloze"):
        card_type = explicit_type
    elif _CLOZE_MARKER_RE.search(primary_str):
        card_type = "cloze"
    elif options:
        card_type = "mcq"
    else:
        card_type = "basic"

    front = str(raw.get("front") or raw.get("question") or raw.get("prompt") or raw.get("stem") or "").strip()
    stem = str(raw.get("stem") or raw.get("front") or raw.get("question") or raw.get("prompt") or "").strip()
    text = str(raw.get("text") or raw.get("front") or raw.get("question") or "").strip()
    back = str(raw.get("back") or raw.get("answer") or "").strip()
    answer = str(raw.get("answer") or raw.get("back") or "").strip()
    explanation = str(raw.get("explanation") or raw.get("analysis") or raw.get("extra") or raw.get("rationale") or "").strip()
    mnemonic = str(raw.get("mnemonic") or raw.get("memory_tip") or "").strip()
    topic = str(raw.get("topic") or raw.get("chapter") or raw.get("category") or "").strip()
    concept_key = str(raw.get("concept_key") or "").strip()

    if not concept_key:
        basis = text if card_type == "cloze" else (stem if card_type == "mcq" else front)
        concept_key = canonicalize_concept(basis)[:80] or f"item-{idx}"

    card_id = str(raw.get("card_id") or raw.get("id") or "").strip()
    if not card_id:
        card_id = make_slug(concept_key)

    guid = str(raw.get("guid") or "").strip() or stable_guid(card_id)

    try:
        frequency = max(1, int(raw.get("frequency") or 1))
    except (TypeError, ValueError):
        frequency = 1

    source_kind = str(raw.get("source_kind") or "past_exam").strip()
    tier = str(raw.get("frequency_tier") or raw.get("tier") or "").strip().lower()
    if tier not in ("high", "medium", "low", "supplement"):
        if source_kind == "ppt_supplement" and frequency <= 1:
            tier = "supplement"
        elif frequency >= 3:
            tier = "high"
        elif frequency == 2:
            tier = "medium"
        else:
            tier = "low"

    frequency_badge = str(raw.get("frequency_badge") or "").strip() or format_frequency_badge(
        frequency, tier, source_kind
    )
    source_years = coerce_source_years(raw.get("source_years") if raw.get("source_years") is not None else raw.get("years"))
    media = coerce_media_list(raw.get("media") or raw.get("images") or raw.get("attachments"))

    raw_tags = raw.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t for t in re.split(r"[,\s]+", raw_tags) if t.strip()]
    tags: list[str] = []
    seen_tags: set[str] = set()
    for tag in raw_tags:
        st = sanitize_tag(str(tag))
        if st and st not in seen_tags:
            seen_tags.add(st)
            tags.append(st)

    subdeck = str(raw.get("subdeck") or "").strip()

    normalized: dict[str, Any] = {
        "card_id": card_id,
        "guid": guid,
        "card_type": card_type,
        "concept_key": concept_key,
        "topic": topic,
        "frequency": frequency,
        "frequency_tier": tier,
        "frequency_badge": frequency_badge,
        "source_kind": source_kind,
        "source_years": source_years,
        "tags": tags,
        "media": media,
        "subdeck": subdeck,
        "mnemonic": mnemonic,
        "explanation": explanation,
    }
    if card_type == "basic":
        normalized["front"] = front
        normalized["back"] = back
    elif card_type == "mcq":
        normalized["stem"] = stem
        if isinstance(options, str):
            normalized["options"] = [ln.strip() for ln in options.replace("\r\n", "\n").split("\n") if ln.strip()]
        elif isinstance(options, list):
            normalized["options"] = [str(o).strip() for o in options if str(o).strip()]
        else:
            normalized["options"] = []
        normalized["answer"] = answer
    else:
        normalized["text"] = text
        normalized["extra"] = explanation

    return normalized


CARD_CSS = """
.card {
  font-family: "Microsoft YaHei", "PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 17px;
  line-height: 1.65;
  text-align: left;
  color: #1e293b;
  background-color: #f8fafc;
  padding: 16px;
}
.ef-wrap {
  max-width: 720px;
  margin: 0 auto;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px 22px;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.04);
}
.ef-header {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 14px;
}
.ef-pill {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 999px;
}
.ef-badge {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fcd34d;
}
.ef-topic {
  background: #e0f2fe;
  color: #075985;
  border: 1px solid #bae6fd;
}
.ef-prompt {
  font-size: 18px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 12px;
}
.ef-divider {
  border: none;
  border-top: 1px dashed #cbd5e1;
  margin: 16px 0;
}
.ef-answer-block {
  background: #f0fdf4;
  border-left: 4px solid #16a34a;
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 12px;
  color: #14532d;
  font-weight: 600;
}
.ef-opt-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}
.ef-opt {
  padding: 9px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s;
}
.ef-opt:hover {
  border-color: #3b82f6;
}
.ef-opt.selected {
  background: #eff6ff;
  border-color: #2563eb;
}
.ef-opt-key {
  font-weight: 700;
  color: #1d4ed8;
}
.ef-box {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 12px;
  font-size: 15px;
}
.ef-box-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #475569;
  margin-bottom: 4px;
}
.ef-mnemonic {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-left: 4px solid #f59e0b;
  border-radius: 8px;
  padding: 10px 14px;
  margin-top: 12px;
  font-size: 15px;
  color: #78350f;
}
.ef-mnemonic .ef-box-title {
  color: #b45309;
}
.ef-media {
  margin-top: 12px;
  text-align: center;
}
.ef-media img, .ef-wrap img {
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
}
.ef-footer {
  margin-top: 14px;
  padding-top: 8px;
  border-top: 1px solid #f1f5f9;
  font-size: 12px;
  color: #64748b;
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.cloze {
  font-weight: 700;
  color: #2563eb;
}
.nightMode .card, .nightMode {
  background-color: #0f172a;
  color: #e2e8f0;
}
.nightMode .ef-wrap {
  background: #1e293b;
  border-color: #334155;
  color: #e2e8f0;
}
.nightMode .ef-prompt {
  color: #f8fafc;
}
.nightMode .ef-badge {
  background: #451a03;
  color: #fde68a;
  border-color: #92400e;
}
.nightMode .ef-topic {
  background: #0c4a6e;
  color: #bae6fd;
  border-color: #0369a1;
}
.nightMode .ef-answer-block {
  background: #052e16;
  border-left-color: #22c55e;
  color: #bbf7d0;
}
.nightMode .ef-opt {
  background: #0f172a;
  border-color: #334155;
  color: #e2e8f0;
}
.nightMode .ef-opt.selected {
  background: #172554;
  border-color: #60a5fa;
}
.nightMode .ef-opt-key {
  color: #60a5fa;
}
.nightMode .ef-box {
  background: #0f172a;
  border-color: #334155;
  color: #cbd5e1;
}
.nightMode .ef-box-title {
  color: #94a3b8;
}
.nightMode .ef-mnemonic {
  background: #292111;
  border-color: #78350f;
  border-left-color: #f59e0b;
  color: #fde68a;
}
.nightMode .ef-mnemonic .ef-box-title {
  color: #fbbf24;
}
.nightMode .ef-footer {
  border-top-color: #334155;
  color: #94a3b8;
}
.nightMode .cloze {
  color: #60a5fa;
}
@media (prefers-color-scheme: dark) {
  .card {
    background-color: #0f172a;
    color: #e2e8f0;
  }
  .ef-wrap {
    background: #1e293b;
    border-color: #334155;
  }
  .ef-prompt {
    color: #f8fafc;
  }
  .ef-opt {
    background: #0f172a;
    border-color: #334155;
  }
  .ef-box {
    background: #0f172a;
    border-color: #334155;
  }
}
"""

_BASIC_QFMT = """<div class="ef-wrap">
  <div class="ef-header">
    {{#FrequencyBadge}}<span class="ef-pill ef-badge">{{FrequencyBadge}}</span>{{/FrequencyBadge}}
    {{#Topic}}<span class="ef-pill ef-topic">{{Topic}}</span>{{/Topic}}
  </div>
  <div class="ef-prompt">{{Front}}</div>
  {{#Media}}<div class="ef-media">{{Media}}</div>{{/Media}}
</div>"""

_BASIC_AFMT = """<div class="ef-wrap">
  <div class="ef-header">
    {{#FrequencyBadge}}<span class="ef-pill ef-badge">{{FrequencyBadge}}</span>{{/FrequencyBadge}}
    {{#Topic}}<span class="ef-pill ef-topic">{{Topic}}</span>{{/Topic}}
  </div>
  <div class="ef-prompt">{{Front}}</div>
  <hr class="ef-divider">
  <div class="ef-answer-block">{{Back}}</div>
  {{#Explanation}}<div class="ef-box"><div class="ef-box-title">解析 / 考点说明</div>{{Explanation}}</div>{{/Explanation}}
  {{#Mnemonic}}<div class="ef-mnemonic"><div class="ef-box-title">记忆法助记</div>{{Mnemonic}}</div>{{/Mnemonic}}
  {{#Media}}<div class="ef-media">{{Media}}</div>{{/Media}}
  <div class="ef-footer">
    <span>{{#SourceYears}}来源真题: {{SourceYears}}{{/SourceYears}}</span>
    <span>{{#CardID}}ID: {{CardID}}{{/CardID}}</span>
  </div>
</div>"""

_MCQ_QFMT = """<div class="ef-wrap">
  <div class="ef-header">
    {{#FrequencyBadge}}<span class="ef-pill ef-badge">{{FrequencyBadge}}</span>{{/FrequencyBadge}}
    {{#Topic}}<span class="ef-pill ef-topic">{{Topic}}</span>{{/Topic}}
  </div>
  <div class="ef-prompt">{{Stem}}</div>
  {{Options}}
  {{#Media}}<div class="ef-media">{{Media}}</div>{{/Media}}
</div>
<script>
(function() {
  var opts = document.querySelectorAll('.ef-opt');
  for (var i = 0; i < opts.length; i++) {
    opts[i].addEventListener('click', function() {
      this.classList.toggle('selected');
    });
  }
})();
</script>"""

_MCQ_AFMT = """<div class="ef-wrap">
  <div class="ef-header">
    {{#FrequencyBadge}}<span class="ef-pill ef-badge">{{FrequencyBadge}}</span>{{/FrequencyBadge}}
    {{#Topic}}<span class="ef-pill ef-topic">{{Topic}}</span>{{/Topic}}
  </div>
  <div class="ef-prompt">{{Stem}}</div>
  {{Options}}
  <hr class="ef-divider">
  <div class="ef-answer-block">参考答案：{{Answer}}</div>
  {{#Explanation}}<div class="ef-box"><div class="ef-box-title">解析 / 考点说明</div>{{Explanation}}</div>{{/Explanation}}
  {{#Mnemonic}}<div class="ef-mnemonic"><div class="ef-box-title">记忆法助记</div>{{Mnemonic}}</div>{{/Mnemonic}}
  {{#Media}}<div class="ef-media">{{Media}}</div>{{/Media}}
  <div class="ef-footer">
    <span>{{#SourceYears}}来源真题: {{SourceYears}}{{/SourceYears}}</span>
    <span>{{#CardID}}ID: {{CardID}}{{/CardID}}</span>
  </div>
</div>"""

_CLOZE_QFMT = """<div class="ef-wrap">
  <div class="ef-header">
    {{#FrequencyBadge}}<span class="ef-pill ef-badge">{{FrequencyBadge}}</span>{{/FrequencyBadge}}
    {{#Topic}}<span class="ef-pill ef-topic">{{Topic}}</span>{{/Topic}}
  </div>
  <div class="ef-prompt">{{cloze:Text}}</div>
  {{#Media}}<div class="ef-media">{{Media}}</div>{{/Media}}
</div>"""

_CLOZE_AFMT = """<div class="ef-wrap">
  <div class="ef-header">
    {{#FrequencyBadge}}<span class="ef-pill ef-badge">{{FrequencyBadge}}</span>{{/FrequencyBadge}}
    {{#Topic}}<span class="ef-pill ef-topic">{{Topic}}</span>{{/Topic}}
  </div>
  <div class="ef-prompt">{{cloze:Text}}</div>
  {{#Extra}}<div class="ef-box"><div class="ef-box-title">补充解析</div>{{Extra}}</div>{{/Extra}}
  {{#Mnemonic}}<div class="ef-mnemonic"><div class="ef-box-title">记忆法助记</div>{{Mnemonic}}</div>{{/Mnemonic}}
  {{#Media}}<div class="ef-media">{{Media}}</div>{{/Media}}
  <div class="ef-footer">
    <span>{{#SourceYears}}来源真题: {{SourceYears}}{{/SourceYears}}</span>
    <span>{{#CardID}}ID: {{CardID}}{{/CardID}}</span>
  </div>
</div>"""


MODEL_SPECS: dict[str, dict[str, Any]] = {
    "basic": {
        "name": MODEL_NAMES["basic"],
        "id": stable_int_id("model", MODEL_NAMES["basic"]),
        "type": 0,
        "fields": [
            "Front",
            "Back",
            "Explanation",
            "Mnemonic",
            "FrequencyBadge",
            "SourceYears",
            "Topic",
            "Media",
            "CardID",
        ],
        "tmpl_name": "ExamForge Basic Card",
        "qfmt": _BASIC_QFMT,
        "afmt": _BASIC_AFMT,
    },
    "mcq": {
        "name": MODEL_NAMES["mcq"],
        "id": stable_int_id("model", MODEL_NAMES["mcq"]),
        "type": 0,
        "fields": [
            "Stem",
            "Options",
            "Answer",
            "Explanation",
            "Mnemonic",
            "FrequencyBadge",
            "SourceYears",
            "Topic",
            "Media",
            "CardID",
        ],
        "tmpl_name": "ExamForge MCQ Card",
        "qfmt": _MCQ_QFMT,
        "afmt": _MCQ_AFMT,
    },
    "cloze": {
        "name": MODEL_NAMES["cloze"],
        "id": stable_int_id("model", MODEL_NAMES["cloze"]),
        "type": 1,
        "fields": [
            "Text",
            "Extra",
            "Mnemonic",
            "FrequencyBadge",
            "SourceYears",
            "Topic",
            "Media",
            "CardID",
        ],
        "tmpl_name": "ExamForge Cloze",
        "qfmt": _CLOZE_QFMT,
        "afmt": _CLOZE_AFMT,
    },
}


def build_anki_models_json(default_did: int, mod_ts: int) -> dict[str, dict[str, Any]]:
    """Build complete col.models JSON dictionary for all three ExamForge note types."""
    models_out: dict[str, dict[str, Any]] = {}
    for spec in MODEL_SPECS.values():
        mid_str = str(spec["id"])
        flds = [
            {
                "name": fname,
                "ord": idx,
                "sticky": False,
                "rtl": False,
                "font": "Arial",
                "size": 20,
                "media": [],
            }
            for idx, fname in enumerate(spec["fields"])
        ]
        tmpls = [
            {
                "name": spec["tmpl_name"],
                "ord": 0,
                "qfmt": spec["qfmt"],
                "afmt": spec["afmt"],
                "bqfmt": "",
                "bafmt": "",
                "did": None,
                "bfont": "",
                "bsize": 0,
            }
        ]
        models_out[mid_str] = {
            "id": spec["id"],
            "name": spec["name"],
            "type": spec["type"],
            "mod": mod_ts,
            "usn": -1,
            "sortf": 0,
            "did": default_did,
            "tmpls": tmpls,
            "flds": flds,
            "css": CARD_CSS,
            "latexPre": (
                "\\documentclass[12pt]{article}\n"
                "\\special{papersize=3in,5in}\n"
                "\\usepackage[utf8]{inputenc}\n"
                "\\usepackage{amssymb,amsmath}\n"
                "\\pagestyle{empty}\n"
                "\\setlength{\\parindent}{0in}\n"
                "\\begin{document}\n"
            ),
            "latexPost": "\\end{document}",
            "latexsvg": False,
            "req": [[0, "any", [0]]],
            "tags": [],
            "vers": [],
        }
    return models_out
