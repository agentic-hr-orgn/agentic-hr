"""
Clause-Aware Bilingual Policy Chunker for Agentic HR Core (Stage 5 Prompt 2, ADR 008).

Splits cleaned policy pages into 300-600 token chunks while respecting:
- Hierarchical headings (`#`, `##`, ALL-CAPS/numbered headings, DOCX heading styles)
- Atomic numbered clauses (`1.`, `1.1`, `2.3.1`, `Clause 4`, `Section 5`, Urdu `دفعہ` / `شق`)
- Atomic Markdown tables (never splitting mid-row)
- ~40-60 token overlap between sibling chunks within the same section
- Sentence-boundary fallback only when a single clause exceeds `max_tokens` (600)
"""

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.policy_parser import ParsedPolicyDocument
from app.services.text_normalizer import (
    detect_language,
    detect_prompt_injection_signals,
    normalize_bilingual_text,
    normalize_for_search,
)

DEFAULT_MIN_TOKENS = 300
DEFAULT_MAX_TOKENS = 600
DEFAULT_OVERLAP_TOKENS = 50

_TOKEN_WORD_RE = re.compile(r"[\u0600-\u06FF]+|[A-Za-z0-9]+|[^\s\w]")
_URDU_WORD_RE = re.compile(r"[\u0600-\u06FF]")

# Heading detectors
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_SECTION_HEADING_RE = re.compile(
    r"^(?:Section|Article|Chapter|Part|باب|حصہ)\s+[\d۰-۹IVXLC]+[:.\-–—]?\s*(.*)$",
    flags=re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z\u0600-\u06FF][^\n]{2,80})$")
_ALL_CAPS_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9\s,&/\-]{3,70}$")

# Clause start detectors (numbered clauses, Clause/Section markers, Urdu دفعہ / شق)
_CLAUSE_START_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)+\.?\s+"  # 1.1, 2.3.1
    r"|\d+\.\s+"  # 1., 2.
    r"|\([a-z0-9ivx]+\)\s+"  # (a), (i), (1)
    r"|(?:Clause|Section|Rule|Policy)\s+\d+(?:\.\d+)*[:.\-–—]?\s+"
    r"|(?:دفعہ|شق|اصول|قاعدہ)\s*[\d۰-۹]+[:.\-–—]?\s*"
    r")",
    flags=re.IGNORECASE,
)

# Sentence boundary splitter for oversized (> max_tokens) single clauses
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?۔؟])\s+")


@dataclass(frozen=True)
class PolicyChunkDraft:
    """Chunk artifact ready for `policy_chunks` persistence and embedding."""

    chunk_index: int
    section_path: str
    page_start: int
    page_end: int
    page_range: str
    text: str
    normalized_text: str
    content_hash: str
    token_count: int
    language: Literal["en", "ur", "mixed"]
    has_injection_flag: bool
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class _AtomicUnit:
    """An indivisible clause, paragraph, or table block tagged with its section and page."""

    section_path: str
    text: str
    page_start: int
    page_end: int
    token_count: int
    is_table: bool = False
    is_clause: bool = False


def estimate_token_count(text: str) -> int:
    """
    Deterministic bilingual token estimator:
    - Latin/numeric words: `max(1, ceil(len(word) / 4.0))`
    - Arabic/Urdu script words: `max(1, ceil(len(word) / 2.5))`
    - Punctuation tokens: 1 token each
    """
    if not text or not text.strip():
        return 0

    total = 0
    for token in _TOKEN_WORD_RE.findall(text):
        if _URDU_WORD_RE.search(token):
            total += max(1, math.ceil(len(token) / 2.5))
        elif token[0].isalnum():
            total += max(1, math.ceil(len(token) / 4.0))
        else:
            total += 1
    return total


def _detect_heading(line: str) -> tuple[int, str] | None:
    """Returns `(level, clean_heading_text)` if `line` is a structural heading, else None."""
    stripped = line.strip()
    if not stripped or stripped.startswith("|"):
        return None

    md_match = _MD_HEADING_RE.match(stripped)
    if md_match:
        return len(md_match.group(1)), md_match.group(2).strip()

    if _SECTION_HEADING_RE.match(stripped) and len(stripped) <= 100:
        return 1, stripped

    if _ALL_CAPS_HEADING_RE.match(stripped) and any(ch.isalpha() for ch in stripped):
        return 1, stripped.title()

    num_match = _NUMBERED_HEADING_RE.match(stripped)
    if num_match and not stripped.endswith((".", "۔", "?", "؟", ";", ":")):
        num_str = num_match.group(1)
        # Treat short title-like numbered lines (<= 8 words, no sentence period) as headings
        if len(stripped.split()) <= 8:
            level = min(3, num_str.count(".") + 1)
            return level, stripped

    return None


def _build_section_path(root_title: str, heading_stack: list[tuple[int, str]]) -> str:
    parts = [root_title.strip()] if root_title.strip() else []
    for _, heading_text in heading_stack:
        if not parts or parts[-1].lower() != heading_text.lower():
            parts.append(heading_text)
    return " > ".join(parts) if parts else "Policy Document"


def _split_oversized_table(unit: _AtomicUnit, max_tokens: int) -> list[_AtomicUnit]:
    """Splits a Markdown table exceeding `max_tokens` strictly between rows, keeping header."""
    lines = [ln for ln in unit.text.split("\n") if ln.strip()]
    if len(lines) <= 3:
        return [unit]

    header_lines = lines[:2]
    header_text = "\n".join(header_lines)
    header_tokens = estimate_token_count(header_text)

    chunks: list[_AtomicUnit] = []
    current_rows: list[str] = []
    current_tokens = header_tokens

    for row in lines[2:]:
        row_tokens = estimate_token_count(row)
        if current_rows and current_tokens + row_tokens > max_tokens:
            block_text = "\n".join([*header_lines, *current_rows])
            chunks.append(
                _AtomicUnit(
                    section_path=unit.section_path,
                    text=block_text,
                    page_start=unit.page_start,
                    page_end=unit.page_end,
                    token_count=estimate_token_count(block_text),
                    is_table=True,
                )
            )
            current_rows = [row]
            current_tokens = header_tokens + row_tokens
        else:
            current_rows.append(row)
            current_tokens += row_tokens

    if current_rows:
        block_text = "\n".join([*header_lines, *current_rows])
        chunks.append(
            _AtomicUnit(
                section_path=unit.section_path,
                text=block_text,
                page_start=unit.page_start,
                page_end=unit.page_end,
                token_count=estimate_token_count(block_text),
                is_table=True,
            )
        )

    return chunks


def _split_oversized_clause(
    unit: _AtomicUnit,
    *,
    max_tokens: int,
    overlap_tokens: int,
) -> list[_AtomicUnit]:
    """
    Splits a single clause exceeding `max_tokens` strictly at sentence boundaries.
    Produces disjoint sentence-grouped pieces sized to leave room for outer chunk overlap
    without ever duplicating sentences or exceeding `max_tokens`.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(unit.text) if s.strip()]
    if len(sentences) <= 1:
        return [unit]

    piece_limit = max(1, max_tokens - overlap_tokens) if overlap_tokens > 0 else max_tokens
    result: list[_AtomicUnit] = []
    current_sents: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = estimate_token_count(sent)
        if current_sents and current_tokens + sent_tokens > piece_limit:
            text_block = " ".join(current_sents)
            result.append(
                _AtomicUnit(
                    section_path=unit.section_path,
                    text=text_block,
                    page_start=unit.page_start,
                    page_end=unit.page_end,
                    token_count=estimate_token_count(text_block),
                    is_clause=unit.is_clause,
                )
            )
            current_sents = [sent]
            current_tokens = sent_tokens
        else:
            current_sents.append(sent)
            current_tokens += sent_tokens

    if current_sents:
        text_block = " ".join(current_sents)
        result.append(
            _AtomicUnit(
                section_path=unit.section_path,
                text=text_block,
                page_start=unit.page_start,
                page_end=unit.page_end,
                token_count=estimate_token_count(text_block),
                is_clause=unit.is_clause,
            )
        )

    return result


def _extract_section_units(
    parsed_doc: ParsedPolicyDocument,
    document_title: str,
) -> list[_AtomicUnit]:
    """Converts parsed pages into an ordered stream of section-tagged atomic clauses & tables."""
    units: list[_AtomicUnit] = []
    heading_stack: list[tuple[int, str]] = []
    current_section = _build_section_path(document_title, heading_stack)

    buf_lines: list[str] = []
    buf_page_start: int | None = None
    buf_page_end: int | None = None
    buf_is_clause = False

    def flush_buffer() -> None:
        nonlocal buf_lines, buf_page_start, buf_page_end, buf_is_clause
        if not buf_lines:
            return
        joined = "\n".join(buf_lines).strip()
        if joined:
            units.append(
                _AtomicUnit(
                    section_path=current_section,
                    text=joined,
                    page_start=buf_page_start or 1,
                    page_end=buf_page_end or (buf_page_start or 1),
                    token_count=estimate_token_count(joined),
                    is_table=False,
                    is_clause=buf_is_clause,
                )
            )
        buf_lines = []
        buf_page_start = None
        buf_page_end = None
        buf_is_clause = False

    for page in parsed_doc.pages:
        if not page.cleaned_text or not page.cleaned_text.strip():
            continue

        lines = page.cleaned_text.split("\n")
        idx = 0
        while idx < len(lines):
            raw_line = lines[idx]
            stripped = raw_line.strip()

            # Blank line ends a non-clause paragraph
            if not stripped:
                flush_buffer()
                idx += 1
                continue

            # Markdown table block (contiguous lines starting with '|')
            if stripped.startswith("|"):
                flush_buffer()
                table_lines: list[str] = []
                while idx < len(lines) and lines[idx].strip().startswith("|"):
                    table_lines.append(lines[idx].strip())
                    idx += 1
                table_text = "\n".join(table_lines)
                units.append(
                    _AtomicUnit(
                        section_path=current_section,
                        text=table_text,
                        page_start=page.page_number,
                        page_end=page.page_number,
                        token_count=estimate_token_count(table_text),
                        is_table=True,
                        is_clause=False,
                    )
                )
                continue

            # Structural heading updates section_path breadcrumb
            heading = _detect_heading(stripped)
            if heading is not None:
                flush_buffer()
                level, heading_text = heading
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, heading_text))
                current_section = _build_section_path(document_title, heading_stack)
                idx += 1
                continue

            # Clause boundary starts a new atomic clause unit
            if _CLAUSE_START_RE.match(stripped):
                flush_buffer()
                buf_lines = [stripped]
                buf_page_start = page.page_number
                buf_page_end = page.page_number
                buf_is_clause = True
                idx += 1
                continue

            # Continuation line for active clause or paragraph
            if buf_page_start is None:
                buf_page_start = page.page_number
            buf_page_end = page.page_number
            buf_lines.append(stripped)
            idx += 1

    flush_buffer()
    return units


def _trailing_overlap_text(units: list[_AtomicUnit], overlap_tokens: int) -> str:
    """Extracts ~40-60 tokens of trailing complete sentences from the previous sibling chunk."""
    if overlap_tokens <= 0 or not units:
        return ""

    non_table_texts = [u.text for u in units if not u.is_table]
    if not non_table_texts:
        return ""

    combined = " ".join(non_table_texts)
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(combined) if s.strip()]
    if not sentences:
        return ""

    selected: list[str] = []
    tok_sum = 0
    for sent in reversed(sentences):
        st = estimate_token_count(sent)
        if selected and tok_sum + st > overlap_tokens + 15:
            break
        selected.insert(0, sent)
        tok_sum += st
        if tok_sum >= overlap_tokens:
            break
    return " ".join(selected)


def _finalize_chunk(
    *,
    chunk_index: int,
    section_path: str,
    text: str,
    page_start: int,
    page_end: int,
    clause_count: int,
    contains_table: bool,
) -> PolicyChunkDraft:
    cleaned = normalize_bilingual_text(text)
    normalized = normalize_for_search(cleaned)
    hash_input = f"{section_path}\n{normalized}".encode()
    content_hash = hashlib.sha256(hash_input).hexdigest()
    page_range = f"{page_start}" if page_start == page_end else f"{page_start}-{page_end}"
    injection_signals = detect_prompt_injection_signals(cleaned)

    return PolicyChunkDraft(
        chunk_index=chunk_index,
        section_path=section_path,
        page_start=page_start,
        page_end=page_end,
        page_range=page_range,
        text=cleaned,
        normalized_text=normalized,
        content_hash=content_hash,
        token_count=estimate_token_count(cleaned),
        language=detect_language(cleaned),
        has_injection_flag=bool(injection_signals),
        metadata_json={
            "clause_count": clause_count,
            "contains_table": contains_table,
            "injection_signals": injection_signals,
        },
    )


def chunk_policy_document(
    parsed_doc: ParsedPolicyDocument,
    *,
    document_title: str,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[PolicyChunkDraft]:
    """
    Chunks a parsed policy document into clause-aware, section-attributed `PolicyChunkDraft` items:
    - Targets `min_tokens`..`max_tokens` (default 300-600 tokens).
    - Never cuts a clause mid-clause or a Markdown table row mid-row.
    - Applies `~overlap_tokens` (default 50) overlap between sibling chunks in the same section.
    - Splits an oversized (> `max_tokens`) single clause strictly at sentence boundaries.
    """
    raw_units = _extract_section_units(parsed_doc, document_title=document_title)
    if not raw_units:
        return []

    # Normalize any single unit exceeding max_tokens cleanly
    prepared_units: list[_AtomicUnit] = []
    for unit in raw_units:
        if unit.token_count > max_tokens:
            if unit.is_table:
                prepared_units.extend(_split_oversized_table(unit, max_tokens=max_tokens))
            else:
                prepared_units.extend(
                    _split_oversized_clause(
                        unit,
                        max_tokens=max_tokens,
                        overlap_tokens=overlap_tokens,
                    )
                )
        else:
            prepared_units.append(unit)

    chunks: list[PolicyChunkDraft] = []
    active_section: str | None = None
    active_units: list[_AtomicUnit] = []
    prev_flushed_units: list[_AtomicUnit] = []
    active_prefix: str = ""
    active_tokens: int = 0

    def flush_active(*, keep_overlap_for_same_section: bool) -> None:
        nonlocal active_units, prev_flushed_units, active_prefix, active_tokens
        if not active_units or active_section is None:
            active_units = []
            prev_flushed_units = []
            active_prefix = ""
            active_tokens = 0
            return

        body_parts = (
            [active_prefix, *[u.text for u in active_units]]
            if active_prefix
            else [u.text for u in active_units]
        )
        chunk_text = "\n\n".join(part for part in body_parts if part)
        page_start = min(u.page_start for u in active_units)
        page_end = max(u.page_end for u in active_units)
        clause_count = sum(1 for u in active_units if u.is_clause)
        contains_table = any(u.is_table for u in active_units)

        chunks.append(
            _finalize_chunk(
                chunk_index=len(chunks),
                section_path=active_section,
                text=chunk_text,
                page_start=page_start,
                page_end=page_end,
                clause_count=clause_count,
                contains_table=contains_table,
            )
        )

        if keep_overlap_for_same_section:
            prev_flushed_units = list(active_units)
            active_prefix = _trailing_overlap_text(
                prev_flushed_units,
                overlap_tokens=overlap_tokens,
            )
            active_tokens = estimate_token_count(active_prefix) if active_prefix else 0
        else:
            prev_flushed_units = []
            active_prefix = ""
            active_tokens = 0
        active_units = []

    for unit in prepared_units:
        # Flush when switching sections so section_path remains pure
        if active_section is not None and unit.section_path != active_section:
            flush_active(keep_overlap_for_same_section=False)

        active_section = unit.section_path

        # If adding this unit exceeds max_tokens, flush current sibling chunk with overlap
        if active_units and (active_tokens + unit.token_count > max_tokens):
            flush_active(keep_overlap_for_same_section=True)

        # Ensure (overlap + unit) respects max_tokens by shrinking/skipping overlap if needed
        if not active_units and active_prefix and (active_tokens + unit.token_count > max_tokens):
            remaining_budget = max_tokens - unit.token_count
            if remaining_budget > 0:
                active_prefix = _trailing_overlap_text(
                    prev_flushed_units,
                    overlap_tokens=remaining_budget,
                )
                active_tokens = estimate_token_count(active_prefix) if active_prefix else 0
            else:
                active_prefix = ""
                active_tokens = 0

            if active_prefix and (active_tokens + unit.token_count > max_tokens):
                active_prefix = ""
                active_tokens = 0

        active_units.append(unit)
        active_tokens += unit.token_count

        # Note: if we already reached min_tokens, keep packing only if next unit still fits <= max_tokens
        _ = min_tokens

    flush_active(keep_overlap_for_same_section=False)
    return chunks
