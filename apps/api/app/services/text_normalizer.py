"""
Single Shared Bilingual (English & Urdu) Text Normalizer & Prompt-Injection Detector
for Agentic HR Core (Stage 5 Prompt 2, ADR 008).

Used identically at BOTH ingestion time (`policy_chunks.normalized_text`, `tsv`) and
search query time (Stage 5 Prompt 5 hybrid retrieval) so Arabic/Urdu keyboard variants,
diacritics, tatweel, zero-width characters, and Urdu/Arabic digits match deterministically.
"""

import re
import unicodedata
from typing import Literal

from app.core.untrusted import safe_encode_untrusted_text

# Zero-width, soft-hyphen, BOM, and BiDi formatting characters that break matching or hide payloads
_ZERO_WIDTH_AND_BIDI_RE = re.compile(r"[\u00AD\u200B-\u200F\u202A-\u202E\u2060\u2066-\u2069\uFEFF]")

# Arabic/Urdu diacritics (harakat), Quranic marks, and tatweel (kashida)
_ARABIC_DIACRITICS_AND_TATWEEL_RE = re.compile(
    r"[\u0610-\u061A\u0640\u064B-\u065F\u0670\u06D6-\u06ED]"
)

# Display-safe Arabic-keyboard -> Urdu canonical letter map:
# Preserves Urdu آ (\u0622), ئ (\u0626), Bari Yeh ے (\u06D2), and Do-Chashmi Heh ھ (\u06BE)
_DISPLAY_URDU_TRANSLATION = str.maketrans(
    {
        "\u064a": "\u06cc",  # Arabic Yeh ي -> Urdu Yeh ی
        "\u0649": "\u06cc",  # Alef Maksura ى -> Urdu Yeh ی
        "\u0643": "\u06a9",  # Arabic Kaf ك -> Urdu Keheh ک
        "\u06c0": "\u06c1",  # Heh with Yeh above ۀ -> Urdu Heh Goal ہ
        "\u0629": "\u06c1",  # Arabic Teh Marbuta ة -> Urdu Heh Goal ہ
        "\u06c3": "\u06c1",  # Urdu Teh Marbuta Goal ۃ -> Urdu Heh Goal ہ
        "\u0647": "\u06c1",  # Arabic Heh ه -> Urdu Heh Goal ہ
    }
)

# Aggressive search-time canonicalization (applied on top of display normalization):
# Unifies Alef variants (آ/أ/إ/ٱ -> ا), Yeh-Hamza (ئ -> ی), and Arabic/Urdu digits -> ASCII 0-9
_SEARCH_URDU_TRANSLATION = str.maketrans(
    {
        "\u0622": "\u0627",  # Alef with Madda آ -> ا
        "\u0623": "\u0627",  # Alef with Hamza Above أ -> ا
        "\u0625": "\u0627",  # Alef with Hamza Below إ -> ا
        "\u0671": "\u0627",  # Alef Wasla ٱ -> ا
        "\u0626": "\u06cc",  # Yeh with Hamza Above ئ -> ی
        # Eastern Arabic-Indic digits (٠-٩) -> ASCII 0-9
        "\u0660": "0",
        "\u0661": "1",
        "\u0662": "2",
        "\u0663": "3",
        "\u0664": "4",
        "\u0665": "5",
        "\u0666": "6",
        "\u0667": "7",
        "\u0668": "8",
        "\u0669": "9",
        # Extended Arabic-Indic / Urdu digits (۰-۹) -> ASCII 0-9
        "\u06f0": "0",
        "\u06f1": "1",
        "\u06f2": "2",
        "\u06f3": "3",
        "\u06f4": "4",
        "\u06f5": "5",
        "\u06f6": "6",
        "\u06f7": "7",
        "\u06f8": "8",
        "\u06f9": "9",
    }
)

# Punctuation & symbols replaced with spaces during search normalization
_SEARCH_PUNCTUATION_RE = re.compile(
    r"[\u060C\u061B\u061F\u066A\u06D4\u00AB\u00BB\u2018-\u201F\u2013\u2014"
    r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]"
)

_HORIZONTAL_WS_RE = re.compile(r"[ \t\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_ALL_WS_RE = re.compile(r"\s+")

# Script counters for deterministic language detection
_ARABIC_URDU_CHAR_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)
_LATIN_CHAR_RE = re.compile(r"[A-Za-z\u00C0-\u024F]")

# Prompt-injection / rule-override detection signatures (checked on raw + zero-width-stripped text)
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:previous|prior|above|earlier|company|hr|policy)?\s*"
            r"(?:rules|instructions|directives|guidelines|prompts)\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "disregard_policy_or_rules",
        re.compile(
            r"\b(?:disregard|override|bypass|forget)\s+(?:all\s+)?"
            r"(?:previous\s+|prior\s+|company\s+|hr\s+)?"
            r"(?:policy|policies|rules|instructions|guidelines|restrictions)\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_override",
        re.compile(
            r"\b(?:system\s+prompt|new\s+system\s+instructions|"
            r"reveal\s+(?:your\s+)?system\s+prompt|act\s+as\s+(?:the\s+)?system)\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "untrusted_delimiter_breakout",
        re.compile(r"<\s*/\s*untrusted_[a-zA-Z0-9_]+\s*>", flags=re.IGNORECASE),
    ),
    (
        "jailbreak_directive",
        re.compile(
            r"\b(?:do\s+anything\s+now|developer\s+mode\s+enabled|"
            r"approve\s+(?:this\s+)?(?:request|candidate|leave)\s+immediately\s+without)\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "urdu_override_directive",
        re.compile(
            r"(?:تمام\s+سابقہ\s+ہدایات\s+نظر\s*انداز|"
            r"\bpurani\s+hidayat\s+ignore\b|"
            r"\bsab\s+qawaneen\s+nazar\s*andaz\b)",
            flags=re.IGNORECASE,
        ),
    ),
)


def strip_zero_width_and_control(text: str) -> str:
    """Strips zero-width marks, BOM, BiDi overrides, and disallowed ASCII control characters."""
    if not text:
        return ""
    cleaned = safe_encode_untrusted_text(text)
    return _ZERO_WIDTH_AND_BIDI_RE.sub("", cleaned)


def normalize_bilingual_text(text: str) -> str:
    """
    Display-safe bilingual normalization for `cleaned_text` and `policy_chunks.text`:
    - Unicode NFC normalization.
    - Strips zero-width characters, soft hyphens, BiDi overrides, harakat/diacritics, and tatweel.
    - Unifies Arabic keyboard variants (`ي`/`ى` -> `ی`, `ك` -> `ک`, `ۀ`/`ة`/`ه` -> `ہ`) while
      preserving `آ`, `ئ`, Urdu Bari Yeh (`ے`), and Do-Chashmi Heh (`ھ`) for readability.
    - Collapses horizontal spaces/tabs while preserving paragraph and heading line breaks.
    """
    if not text:
        return ""

    s = unicodedata.normalize("NFC", str(text))
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _ZERO_WIDTH_AND_BIDI_RE.sub("", s)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = _ARABIC_DIACRITICS_AND_TATWEEL_RE.sub("", s)
    s = s.translate(_DISPLAY_URDU_TRANSLATION)

    lines = [_HORIZONTAL_WS_RE.sub(" ", line).strip() for line in s.split("\n")]
    joined = "\n".join(lines)
    joined = _MULTI_NEWLINE_RE.sub("\n\n", joined)
    return joined.strip()


def normalize_for_search(text: str) -> str:
    """
    Aggressive canonical normalization used identically for BOTH `policy_chunks.normalized_text`
    (PostgreSQL `simple` FTS & `pg_trgm`) and incoming search queries:
    - Applies `normalize_bilingual_text`.
    - Canonicalizes Alef variants (`آ`/`أ`/`إ`/`ٱ` -> `ا`), Yeh-Hamza (`ئ` -> `ی`),
      and Arabic/Urdu numerals (`٠-٩`, `۰-۹` -> `0-9`).
    - Lowercases Latin text, normalizes Urdu/English punctuation, and collapses whitespace.
    """
    if not text:
        return ""

    s = normalize_bilingual_text(text)
    s = s.translate(_SEARCH_URDU_TRANSLATION)
    s = s.lower()
    s = _SEARCH_PUNCTUATION_RE.sub(" ", s)
    s = _ALL_WS_RE.sub(" ", s)
    return s.strip()


def detect_language(text: str) -> Literal["en", "ur", "mixed"]:
    """
    Deterministic Unicode script-ratio detector:
    - Counts Arabic/Urdu script characters vs Latin letters.
    - Returns `"mixed"` when both scripts account for >= 15% of alphabetic characters.
    - Returns `"ur"` when Arabic/Urdu dominates, else `"en"`.
    """
    if not text:
        return "en"

    ur_count = len(_ARABIC_URDU_CHAR_RE.findall(text))
    en_count = len(_LATIN_CHAR_RE.findall(text))
    total = ur_count + en_count
    if total == 0:
        return "en"

    ur_ratio = ur_count / total
    en_ratio = en_count / total
    if ur_ratio >= 0.15 and en_ratio >= 0.15:
        return "mixed"
    return "ur" if ur_count > en_count else "en"


def detect_prompt_injection_signals(text: str) -> list[str]:
    """
    Detects explicit or zero-width-obfuscated prompt-injection / rule-override directives
    in external policy documents (Rule 6 — Untrusted Data).
    Checks both raw text and zero-width-stripped normalized text.
    """
    if not text:
        return []

    raw_str = str(text)
    stripped = _ZERO_WIDTH_AND_BIDI_RE.sub("", unicodedata.normalize("NFC", raw_str))
    stripped_collapsed = _ALL_WS_RE.sub(" ", stripped)
    bilingual_norm = _ALL_WS_RE.sub(" ", normalize_bilingual_text(raw_str))

    candidates = (raw_str, stripped_collapsed, bilingual_norm)
    matched: list[str] = []
    for signal_name, pattern in _INJECTION_PATTERNS:
        if any(pattern.search(candidate) for candidate in candidates):
            matched.append(signal_name)

    return matched
