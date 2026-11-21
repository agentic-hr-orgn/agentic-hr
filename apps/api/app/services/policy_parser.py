"""
Permissive Layered Policy Document Parser & Urdu/OCR Quality Scorer (Stage 5 Prompt 2, ADR 008).

Supports:
- `.pdf` via `pdfplumber` (MIT) with table-bbox exclusion so table cells are formatted as
  Markdown tables and ordered by vertical `top` coordinate without duplicating text in prose.
- `.docx` via `python-docx` (MIT) preserving heading hierarchy, tables in reading order,
  and explicit `<w:br w:type="page"/>` / `page_break_before` multi-page boundaries.
- `.txt` via strict UTF-8 decoding with optional form-feed (`\\f`) page separation.

Explicitly avoids AGPL `PyMuPDF` and defers `Docling` to the Stage 14 worker container via
the `PolicyDocumentParser` protocol.
"""

import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from app.core.config import settings
from app.services.text_normalizer import (
    detect_language,
    detect_prompt_injection_signals,
    normalize_bilingual_text,
)

ExtractionStatus = Literal["ok", "needs_ocr", "failed"]

MIN_PAGE_NON_WS_CHARS = 25
QUALITY_OCR_THRESHOLD = 0.60
HEADER_FOOTER_MIN_PAGES = 3
HEADER_FOOTER_REPEAT_RATIO = 0.60

_REPLACEMENT_OR_PUA_RE = re.compile(r"[\uFFFD\uF000-\uF8FF]")
_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_PRES_FORMS_RE = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
_LONG_UNBROKEN_URDU_RE = re.compile(r"[\u0600-\u06FF]{36,}")
_PAGE_NUMBER_MASK_RE = re.compile(r"\b\d+\b")
_HEADING_GUARD_RE = re.compile(
    r"^(?:#{1,6}\s+|(?:\d+(?:\.\d+)*\.?\s+)|(?:clause|section|article|chapter|part)\s+\d+|"
    r"(?:دفعہ|شق|باب|حصہ)\s*[\d۰-۹]+)",
    flags=re.IGNORECASE,
)


class PolicyParseError(ValueError):
    """Raised when an uploaded policy file cannot be decoded or parsed."""


@dataclass(frozen=True)
class PageQualityReport:
    """Deterministic quality assessment for a single extracted policy page."""

    page_number: int
    quality_score: float
    needs_ocr: bool
    extraction_status: ExtractionStatus
    language: Literal["en", "ur", "mixed"]
    reasons: list[str]
    char_count: int
    replacement_char_ratio: float
    arabic_script_ratio: float


@dataclass
class ParsedPolicyPage:
    """Normalized page record ready for `policy_document_pages` persistence and chunking."""

    page_number: int
    raw_text: str
    cleaned_text: str
    language: Literal["en", "ur", "mixed"]
    quality_score: float
    needs_ocr: bool
    ocr_applied: bool
    extraction_status: ExtractionStatus
    error_message: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedPolicyDocument:
    """Complete parsed representation of an uploaded policy document."""

    file_name: str
    mime_type: str
    page_count: int
    pages: list[ParsedPolicyPage]
    removed_headers: list[str] = field(default_factory=list)
    removed_footers: list[str] = field(default_factory=list)
    low_confidence_pages: list[dict[str, Any]] = field(default_factory=list)
    has_prompt_injection_warning: bool = False
    injection_signals: list[str] = field(default_factory=list)


class PolicyDocumentParser(Protocol):
    """Parser protocol allowing Stage 14 containerized Docling swap without downstream changes."""

    def parse(
        self,
        content: bytes,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> ParsedPolicyDocument:
        """Parses raw file bytes into page-level structured text and quality diagnostics."""
        ...


def score_page_extraction(text: str, page_number: int) -> PageQualityReport:
    """
    Scores extracted text for a single page across:
    1. Minimum non-whitespace character density (detects scanned / image-only pages).
    2. Replacement (`\\uFFFD`) and private-use glyph boxes (`\\uF000-\\uF8FF`).
    3. Urdu/Arabic script ratio + garbled presentation forms / missing-space runs.
    4. Excessive non-alphanumeric symbol noise.
    """
    cleaned = normalize_bilingual_text(text or "")
    non_ws = re.sub(r"\s+", "", cleaned)
    char_count = len(non_ws)
    language = detect_language(cleaned)

    reasons: list[str] = []
    score = 1.0

    if char_count < MIN_PAGE_NON_WS_CHARS:
        reasons.append("low_character_density")
        density_ratio = char_count / float(MIN_PAGE_NON_WS_CHARS)
        score = min(score, round(0.35 * density_ratio, 4))

    replacement_count = len(_REPLACEMENT_OR_PUA_RE.findall(text or ""))
    replacement_ratio = replacement_count / max(char_count, 1)
    if replacement_count >= 3 or replacement_ratio > 0.02:
        reasons.append("replacement_characters_detected")
        score -= min(0.55, 0.25 + replacement_ratio * 2.0)

    arabic_count = len(_ARABIC_SCRIPT_RE.findall(cleaned))
    pres_form_count = len(_ARABIC_PRES_FORMS_RE.findall(text or ""))
    arabic_ratio = (arabic_count + pres_form_count) / max(char_count, 1)

    if arabic_ratio >= 0.15:
        if _LONG_UNBROKEN_URDU_RE.search(cleaned):
            reasons.append("garbled_urdu_missing_spaces")
            score -= 0.45
        if (
            pres_form_count >= 5
            and (pres_form_count / max(arabic_count + pres_form_count, 1)) > 0.3
        ):
            reasons.append("garbled_urdu_presentation_forms")
            score -= 0.45

    if char_count >= MIN_PAGE_NON_WS_CHARS:
        non_table_chars = re.sub(r"[|\-\s]", "", cleaned)
        alpha_num = sum(1 for ch in non_table_chars if ch.isalnum())
        symbol_ratio = 1.0 - (alpha_num / max(len(non_table_chars), 1))
        if symbol_ratio > 0.55:
            reasons.append("excessive_symbol_noise")
            score -= 0.35

    clamped_score = round(max(0.0, min(1.0, score)), 4)
    needs_ocr = clamped_score < QUALITY_OCR_THRESHOLD or bool(reasons)
    status: ExtractionStatus = "needs_ocr" if needs_ocr else "ok"

    return PageQualityReport(
        page_number=page_number,
        quality_score=clamped_score,
        needs_ocr=needs_ocr,
        extraction_status=status,
        language=language,
        reasons=reasons,
        char_count=char_count,
        replacement_char_ratio=round(replacement_ratio, 4),
        arabic_script_ratio=round(arabic_ratio, 4),
    )


def _canonical_banner_key(line: str) -> str:
    """Normalizes a candidate header/footer line so 'Page 1 of 4' and 'Page 2 of 4' match."""
    collapsed = re.sub(r"\s+", " ", line.strip()).lower()
    return _PAGE_NUMBER_MASK_RE.sub("#", collapsed)


def _is_protected_heading(line: str) -> bool:
    """Returns True if a line looks like a real section/clause heading or Markdown table row."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("|"):
        return True
    return bool(_HEADING_GUARD_RE.match(stripped))


def strip_repeated_headers_and_footers(
    page_texts: list[str],
    *,
    min_pages: int = HEADER_FOOTER_MIN_PAGES,
    repeat_ratio: float = HEADER_FOOTER_REPEAT_RATIO,
) -> tuple[list[str], list[str], list[str]]:
    """
    Detects lines repeating at the top or bottom of >= `repeat_ratio` of pages
    (when `len(page_texts) >= min_pages`) and strips them while preserving real headings.
    Returns `(cleaned_page_texts, removed_headers, removed_footers)`.
    """
    page_count = len(page_texts)
    if page_count < min_pages:
        return list(page_texts), [], []

    threshold = math.ceil(page_count * repeat_ratio)
    top_counts: Counter[str] = Counter()
    bottom_counts: Counter[str] = Counter()
    key_samples: dict[str, str] = {}

    split_pages: list[list[str]] = [p.split("\n") for p in page_texts]

    for lines in split_pages:
        non_empty = [ln.strip() for ln in lines if ln.strip()]
        if not non_empty:
            continue

        top_candidates = non_empty[:2]
        bottom_candidates = non_empty[-2:] if len(non_empty) > 2 else non_empty[-1:]

        seen_top: set[str] = set()
        for ln in top_candidates:
            if _is_protected_heading(ln) or len(ln) > 160:
                continue
            key = _canonical_banner_key(ln)
            if key and key not in seen_top:
                seen_top.add(key)
                top_counts[key] += 1
                key_samples.setdefault(key, ln)

        seen_bottom: set[str] = set()
        for ln in bottom_candidates:
            if _is_protected_heading(ln) or len(ln) > 160:
                continue
            key = _canonical_banner_key(ln)
            if key and key not in seen_bottom:
                seen_bottom.add(key)
                bottom_counts[key] += 1
                key_samples.setdefault(key, ln)

    header_keys = {k for k, cnt in top_counts.items() if cnt >= threshold}
    footer_keys = {k for k, cnt in bottom_counts.items() if cnt >= threshold}

    if not header_keys and not footer_keys:
        return list(page_texts), [], []

    cleaned_pages: list[str] = []
    for lines in split_pages:
        working = list(lines)

        # Strip matching lines from top (up to 2 non-empty lines)
        removed_top = 0
        idx = 0
        while idx < len(working) and removed_top < 2:
            if not working[idx].strip():
                idx += 1
                continue
            if _canonical_banner_key(working[idx]) in header_keys:
                working.pop(idx)
                removed_top += 1
            else:
                break

        # Strip matching lines from bottom (up to 2 non-empty lines)
        removed_bottom = 0
        idx = len(working) - 1
        while idx >= 0 and removed_bottom < 2:
            if not working[idx].strip():
                idx -= 1
                continue
            if _canonical_banner_key(working[idx]) in footer_keys:
                working.pop(idx)
                idx -= 1
                removed_bottom += 1
            else:
                break

        cleaned_pages.append("\n".join(working).strip())

    removed_headers = [key_samples[k] for k in sorted(header_keys)]
    removed_footers = [key_samples[k] for k in sorted(footer_keys)]
    return cleaned_pages, removed_headers, removed_footers


def _format_markdown_table(rows: list[list[str | None]]) -> str:
    """Formats a 2D cell matrix into a compact GitHub-Flavored Markdown table."""
    cleaned_rows: list[list[str]] = []
    for row in rows:
        cells = [
            re.sub(r"\s+", " ", normalize_bilingual_text(cell or "")).replace("|", "\\|").strip()
            for cell in row
        ]
        if any(cells):
            cleaned_rows.append(cells)

    if not cleaned_rows:
        return ""

    col_count = max(len(r) for r in cleaned_rows)
    padded = [r + [""] * (col_count - len(r)) for r in cleaned_rows]

    header = "| " + " | ".join(padded[0]) + " |"
    separator = "| " + " | ".join(["---"] * col_count) + " |"
    body = ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join([header, separator, *body])


def _attempt_optional_ocr(pdf_bytes: bytes, page_number: int) -> str | None:
    """
    Runs optional local Tesseract OCR (`urd+eng`) ONLY when `settings.ENABLE_LOCAL_OCR=True`
    and both `pytesseract` and `pypdfium2` / Tesseract binary are installed.
    Never raises on missing optional binaries; returns None to trigger `needs_ocr` fallback.
    """
    if not settings.ENABLE_LOCAL_OCR:
        return None

    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
        import pytesseract  # type: ignore[import-untyped]

        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            page = pdf[page_number - 1]
            bitmap = page.render(scale=2.0)
            pil_image = bitmap.to_pil()
            text = pytesseract.image_to_string(pil_image, lang=settings.OCR_LANGUAGES)
            return str(text or "")
        finally:
            pdf.close()
    except Exception:
        return None


def _extract_pdf_pages(content: bytes) -> list[tuple[str, int]]:
    """
    Extracts pages from PDF bytes using `pdfplumber` (MIT).
    Excludes table bounding boxes from prose extraction via `page.filter(...)` and interleaves
    prose lines and Markdown tables strictly by vertical `top` coordinate to prevent duplication.
    Returns list of `(raw_page_text, table_count)` tuples.
    """
    import pdfplumber

    results: list[tuple[str, int]] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                found_tables = page.find_tables() or []
                table_bboxes = [t.bbox for t in found_tables]
                positioned_blocks: list[tuple[float, str]] = []
                table_count = 0

                for tbl in found_tables:
                    raw_rows = tbl.extract() or []
                    md_table = _format_markdown_table(raw_rows)
                    if md_table:
                        positioned_blocks.append((float(tbl.bbox[1]), md_table))
                        table_count += 1

                if table_bboxes:

                    def _outside_tables(
                        obj: dict[str, Any],
                        bboxes: list[tuple[float, float, float, float]] = table_bboxes,
                    ) -> bool:
                        x0 = obj.get("x0")
                        x1 = obj.get("x1")
                        top = obj.get("top")
                        bottom = obj.get("bottom")
                        if x0 is None or x1 is None or top is None or bottom is None:
                            return True
                        cx = (float(x0) + float(x1)) / 2.0
                        cy = (float(top) + float(bottom)) / 2.0
                        for bx0, btop, bx1, bbottom in bboxes:
                            if (bx0 - 1.0) <= cx <= (bx1 + 1.0) and (btop - 1.0) <= cy <= (
                                bbottom + 1.0
                            ):
                                return False
                        return True

                    prose_page = page.filter(_outside_tables)
                else:
                    prose_page = page

                if table_bboxes:
                    text_lines = prose_page.extract_text_lines(layout=False) or []
                    for line_obj in text_lines:
                        line_txt = str(line_obj.get("text") or "").strip()
                        if line_txt:
                            positioned_blocks.append((float(line_obj.get("top", 0.0)), line_txt))
                    positioned_blocks.sort(key=lambda item: item[0])
                    page_text = "\n".join(block for _, block in positioned_blocks)
                else:
                    page_text = prose_page.extract_text() or ""

                results.append((page_text, table_count))
    except Exception as exc:
        raise PolicyParseError(f"PDF_PARSE_FAILED: Could not parse PDF document: {exc}") from exc

    return results


def _docx_paragraph_has_page_break(paragraph_elem: Any) -> bool:
    """Returns True if a DOCX paragraph XML element contains an explicit page break."""
    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for br in paragraph_elem.iter(f"{w_ns}br"):
        if br.get(f"{w_ns}type") == "page":
            return True
    return False


def _extract_docx_pages(content: bytes) -> list[tuple[str, int]]:
    """
    Extracts `.docx` paragraphs and tables in exact document reading order using `python-docx`.
    Preserves Heading 1..6 / Title styles as Markdown headings and handles explicit page breaks
    (`<w:br w:type="page"/>` and `page_break_before`).
    """
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:
        raise PolicyParseError(f"DOCX_PARSE_FAILED: Could not parse DOCX document: {exc}") from exc

    w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    pages_blocks: list[list[str]] = [[]]
    pages_table_counts: list[int] = [0]

    for child in document.element.body.iterchildren():
        if child.tag == f"{w_ns}p":
            para = Paragraph(child, document)
            if para.paragraph_format.page_break_before and pages_blocks[-1]:
                pages_blocks.append([])
                pages_table_counts.append(0)

            has_br = _docx_paragraph_has_page_break(child)
            raw_txt = (para.text or "").strip()
            if raw_txt:
                style_name = (para.style.name if para.style else "") or ""
                if style_name == "Title":
                    pages_blocks[-1].append(f"# {raw_txt}")
                elif style_name.startswith("Heading "):
                    level_part = style_name.replace("Heading ", "").strip()
                    level = int(level_part) if level_part.isdigit() else 2
                    level = max(1, min(6, level))
                    pages_blocks[-1].append(f"{'#' * level} {raw_txt}")
                else:
                    pages_blocks[-1].append(raw_txt)

            if has_br:
                pages_blocks.append([])
                pages_table_counts.append(0)

        elif child.tag == f"{w_ns}tbl":
            tbl = Table(child, document)
            rows = [[cell.text for cell in row.cells] for row in tbl.rows]
            md_tbl = _format_markdown_table(rows)
            if md_tbl:
                pages_blocks[-1].append(md_tbl)
                pages_table_counts[-1] += 1

    # Remove trailing empty page created if document ended with a page break
    while len(pages_blocks) > 1 and not pages_blocks[-1]:
        pages_blocks.pop()
        pages_table_counts.pop()

    return [
        ("\n\n".join(blocks), tbl_cnt)
        for blocks, tbl_cnt in zip(pages_blocks, pages_table_counts, strict=True)
    ]


def _extract_txt_pages(content: bytes) -> list[tuple[str, int]]:
    """Extracts strict UTF-8 plain text (with BOM support) and optional form-feed page splits."""
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PolicyParseError(
            f"TXT_DECODE_FAILED: Plain-text policy must be valid UTF-8: {exc}"
        ) from exc

    raw_pages = decoded.split("\f") if "\f" in decoded else [decoded]
    return [(page_text, 0) for page_text in raw_pages]


class LayeredPolicyParser:
    """
    Default Stage 5 implementation of `PolicyDocumentParser`:
    PDF (`pdfplumber`), DOCX (`python-docx`), and TXT (strict UTF-8) with repeated
    header/footer removal, Urdu/OCR quality scoring, and prompt-injection flagging.
    """

    def parse(
        self,
        content: bytes,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> ParsedPolicyDocument:
        ext = Path(file_name or "").suffix.lower()
        clean_mime = (mime_type or "").split(";")[0].strip().lower()

        is_pdf = ext == ".pdf" or clean_mime == "application/pdf"
        is_docx = (
            ext == ".docx"
            or clean_mime
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        is_txt = ext == ".txt" or clean_mime == "text/plain"

        if is_pdf:
            resolved_mime = "application/pdf"
            raw_entries = _extract_pdf_pages(content)
        elif is_docx:
            resolved_mime = (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            raw_entries = _extract_docx_pages(content)
        elif is_txt:
            resolved_mime = "text/plain"
            raw_entries = _extract_txt_pages(content)
        else:
            raise PolicyParseError(
                f"UNSUPPORTED_POLICY_FORMAT: '{file_name}' ({mime_type}) is not supported. "
                "Allowed formats: .pdf, .docx, .txt."
            )

        raw_texts = [entry[0] for entry in raw_entries]
        table_counts = [entry[1] for entry in raw_entries]

        normalized_pre_strip = [normalize_bilingual_text(t) for t in raw_texts]
        stripped_texts, removed_headers, removed_footers = strip_repeated_headers_and_footers(
            normalized_pre_strip
        )

        pages: list[ParsedPolicyPage] = []
        low_confidence_pages: list[dict[str, Any]] = []
        all_injection_signals: list[str] = []

        for idx, (raw_text, cleaned_text, table_count) in enumerate(
            zip(raw_texts, stripped_texts, table_counts, strict=True),
            start=1,
        ):
            report = score_page_extraction(cleaned_text, page_number=idx)
            ocr_applied = False
            final_cleaned = cleaned_text

            if report.needs_ocr and is_pdf and settings.ENABLE_LOCAL_OCR:
                ocr_text = _attempt_optional_ocr(content, page_number=idx)
                if ocr_text:
                    ocr_cleaned = normalize_bilingual_text(ocr_text)
                    ocr_report = score_page_extraction(ocr_cleaned, page_number=idx)
                    if ocr_report.quality_score > report.quality_score:
                        final_cleaned = ocr_cleaned
                        report = ocr_report
                        ocr_applied = True

            page_signals = detect_prompt_injection_signals(raw_text)
            for sig in detect_prompt_injection_signals(final_cleaned):
                if sig not in page_signals:
                    page_signals.append(sig)
            for sig in page_signals:
                if sig not in all_injection_signals:
                    all_injection_signals.append(sig)

            page_record = ParsedPolicyPage(
                page_number=idx,
                raw_text=raw_text,
                cleaned_text=final_cleaned,
                language=report.language,
                quality_score=report.quality_score,
                needs_ocr=report.needs_ocr,
                ocr_applied=ocr_applied,
                extraction_status=report.extraction_status,
                error_message=(
                    f"Low extraction quality ({', '.join(report.reasons)})"
                    if report.needs_ocr
                    else None
                ),
                metadata_json={
                    "table_count": table_count,
                    "char_count": report.char_count,
                    "quality_reasons": report.reasons,
                    "replacement_char_ratio": report.replacement_char_ratio,
                    "arabic_script_ratio": report.arabic_script_ratio,
                    "injection_signals": page_signals,
                },
            )
            pages.append(page_record)

            if report.needs_ocr:
                low_confidence_pages.append(
                    {
                        "page_number": idx,
                        "quality_score": report.quality_score,
                        "reasons": report.reasons,
                        "extraction_status": report.extraction_status,
                    }
                )

        return ParsedPolicyDocument(
            file_name=file_name,
            mime_type=resolved_mime,
            page_count=len(pages),
            pages=pages,
            removed_headers=removed_headers,
            removed_footers=removed_footers,
            low_confidence_pages=low_confidence_pages,
            has_prompt_injection_warning=bool(all_injection_signals),
            injection_signals=all_injection_signals,
        )
