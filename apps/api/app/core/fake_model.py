"""
Deterministic Fake Model Provider & Bilingual Concept Embedder for Agentic HR Core.
Complies with ADR 003, ADR 006, ADR 007, ADR 008, and Stage 2/Stage 5 specifications.

Provides 100% offline, deterministic model execution for testing and local CI:
- Synthesizes valid Pydantic model payloads dynamically.
- Supports fault injection (simulated rate limits, timeouts, permanent errors, repair flows).
- Generates deterministic L2-normalized 768-dim bilingual (Urdu <-> English) embeddings combining
  an HR domain synonym/concept lexicon with signed character/word n-gram random projections.
- Accurately tracks token usage without external network calls or API keys.
- Accurately represents unpriced/offline model cost as None (null).
"""

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from app.services.text_normalizer import normalize_for_search


class FakeRateLimitError(Exception):
    """Simulated upstream rate limit error (HTTP 429)."""


class FakePermanentError(Exception):
    """Simulated upstream non-retryable permanent error (HTTP 400/401/403/404)."""


def l2_normalize(vector: Sequence[float]) -> list[float]:
    """
    L2-normalizes a numeric vector to unit norm (||v||_2 == 1.0).
    Mandatory for `gemini-embedding-001` MRL dimensions < 3072 (ADR 008).
    - Rejects empty vectors or vectors containing NaN/Inf with ValueError.
    - Safely handles all-zero vectors by returning `[0.0] * len(vector)` without ZeroDivisionError.
    """
    if not vector:
        raise ValueError("Cannot L2-normalize an empty vector.")

    vals = [float(x) for x in vector]
    for idx, val in enumerate(vals):
        if not math.isfinite(val):
            raise ValueError(f"Vector element at index {idx} is not finite: {val!r}")

    sq_sum = sum(v * v for v in vals)
    norm = math.sqrt(sq_sum)
    if norm <= 1e-12:
        return [0.0] * len(vals)

    return [v / norm for v in vals]


# Curated HR bilingual (English, Urdu script normalized, Roman Urdu) concept lexicon.
# Note: All Urdu phrases are passed through `normalize_for_search` at import time so
# Alef/Yeh variants match `normalize_for_search(input_text)` deterministically.
_RAW_BILINGUAL_HR_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "leave_general",
        (
            "leave",
            "leaves",
            "pto",
            "time off",
            "vacation",
            "holiday",
            "holidays",
            "absence",
            "فصل",
            "چھٹی",
            "چھٹیاں",
            "رخصت",
            "تعطیل",
            "تعطیلات",
            "chutti",
            "chhutiyan",
            "rukhsat",
        ),
    ),
    (
        "annual_leave",
        (
            "annual",
            "annual leave",
            "yearly",
            "earned leave",
            "paid leave",
            "privilege leave",
            "carry forward",
            "20 days",
            "twenty days",
            "سالانہ",
            "سالانہ چھٹی",
            "بیس دن",
            "20 دن",
            "salana",
            "salana chutti",
        ),
    ),
    (
        "sick_medical_leave",
        (
            "sick",
            "sick leave",
            "illness",
            "medical certificate",
            "medical leave",
            "doctor",
            "بیماری",
            "طبی",
            "طبی چھٹی",
            "میڈیکل سرٹیفکیٹ",
            "علاج",
            "bimari",
            "tibbi",
        ),
    ),
    (
        "casual_emergency_leave",
        (
            "casual",
            "casual leave",
            "emergency",
            "urgent",
            "short notice",
            "ہنگامی",
            "اتفاقیہ",
            "ہنگامی چھٹی",
            "ضروری کام",
            "hangami",
            "ittefaqia",
        ),
    ),
    (
        "parental_maternity_leave",
        (
            "maternity",
            "paternity",
            "parental",
            "childbirth",
            "newborn",
            "bereavement",
            "سوگ",
            "زچگی",
            "والدین",
            "ولادت",
            "zachgi",
        ),
    ),
    (
        "attendance_hours",
        (
            "attendance",
            "working hours",
            "core hours",
            "punctuality",
            "shift",
            "late arrival",
            "grace period",
            "15-minute",
            "15 minutes",
            "check in",
            "roster",
            "حاضری",
            "اوقات کار",
            "دفتر کے اوقات",
            "دفتر",
            "منٹ",
            "رعایت",
            "پابندی وقت",
            "تاخیر",
            "hazri",
            "auqat",
            "waqt",
        ),
    ),
    (
        "remote_work_wfh",
        (
            "remote",
            "remote work",
            "work from home",
            "wfh",
            "hybrid",
            "telecommute",
            "home office",
            "days per week",
            "two days",
            "internet",
            "internet allowance",
            "ریموٹ",
            "ریموٹ ورک",
            "گھر سے کام",
            "ہفتے میں دو دن",
            "دو دن",
            "انٹرنیٹ",
            "انٹرنیٹ الاؤنس",
            "ہائبرڈ",
            "ghar se kaam",
        ),
    ),
    (
        "health_insurance_medical",
        (
            "health insurance",
            "insurance",
            "hospital",
            "hospitalization",
            "opd",
            "ipd",
            "clinic",
            "reimbursement",
            "coverage",
            "ہیلتھ انشورنس",
            "میڈیکل",
            "صحت",
            "بیمہ",
            "ہسپتال",
            "علاج معالجہ",
            "sehat",
            "bima",
        ),
    ),
    (
        "salary_payroll_compensation",
        (
            "salary",
            "payroll",
            "compensation",
            "wages",
            "payday",
            "bonus",
            "allowance",
            "stipend",
            "deduction",
            "tax",
            "تنخواہ",
            "مشاہرہ",
            "بونس",
            "الاؤنس",
            "معاوضہ",
            "کٹوتی",
            "tankhwah",
            "muawza",
        ),
    ),
    (
        "overtime_policy",
        (
            "overtime",
            "extra hours",
            "weekend work",
            "double rate",
            "compensatory off",
            "اوور ٹائم",
            "اضافی وقت",
            "اضافی گھنٹے",
            "izafi waqt",
        ),
    ),
    (
        "it_hardware_laptop",
        (
            "laptop",
            "hardware",
            "computer",
            "device",
            "equipment",
            "asset",
            "charger",
            "monitor",
            "لیپ ٹاپ",
            "کمپیوٹر",
            "آلات",
            "ڈیوائس",
        ),
    ),
    (
        "it_security_password",
        (
            "password",
            "passwords",
            "mfa",
            "2fa",
            "two factor",
            "authentication",
            "cybersecurity",
            "phishing",
            "vpn",
            "encryption",
            "bitlocker",
            "filevault",
            "full-disk",
            "screen lock",
            "rotate",
            "rotation",
            "credentials",
            "پاس ورڈ",
            "سیکیورٹی",
            "حفاظت",
            "تصدیق",
        ),
    ),
    (
        "code_of_conduct_ethics",
        (
            "code of conduct",
            "conduct",
            "ethics",
            "integrity",
            "discipline",
            "dress code",
            "conflict of interest",
            "professionalism",
            "ضابطہ اخلاق",
            "اخلاقیات",
            "نظم و ضبط",
            "رویے",
            "zabta akhlaq",
        ),
    ),
    (
        "anti_harassment_safety",
        (
            "harassment",
            "anti harassment",
            "discrimination",
            "bullying",
            "grievance",
            "complaint",
            "inquiry committee",
            "ہراسانی",
            "شکایت",
            "تدارک",
            "تحفظ",
            "امتیازی سلوک",
            "harasani",
            "shikayat",
        ),
    ),
    (
        "resignation_termination_exit",
        (
            "resignation",
            "notice period",
            "termination",
            "offboarding",
            "exit interview",
            "final settlement",
            "clearance",
            "استعفی",
            "نوٹس پیریڈ",
            "برطرفی",
            "خاتمہ ملازمت",
            "حتمی حساب",
            "istefa",
        ),
    ),
    (
        "probation_confirmation",
        (
            "probation",
            "probationary",
            "confirmation",
            "trial period",
            "3 months",
            "ninety days",
            "آزمائشی مدت",
            "مستقل ملازمت",
            "تین ماہ",
            "azmaishi muddat",
        ),
    ),
    (
        "approval_governance",
        (
            "approval",
            "approve",
            "manager",
            "hr head",
            "supervisor",
            "department head",
            "authorization",
            "منظوری",
            "مینیجر",
            "سربراہ",
            "اجازت",
            "manzoori",
            "ijazat",
        ),
    ),
    (
        "travel_expense_reimbursement",
        (
            "travel",
            "business trip",
            "per diem",
            "hotel",
            "flight",
            "mileage",
            "receipt",
            "سفر",
            "سفری اخراجات",
            "رسید",
            "safar",
            "akhrajat",
        ),
    ),
)

_NORMALIZED_BILINGUAL_CONCEPTS: list[tuple[str, tuple[str, ...]]] = [
    (
        concept_name,
        tuple(dict.fromkeys(norm_kw for kw in keywords if (norm_kw := normalize_for_search(kw)))),
    )
    for concept_name, keywords in _RAW_BILINGUAL_HR_CONCEPTS
]


def compute_bilingual_fake_embedding(text: str, *, dim: int = 768) -> list[float]:
    """
    Deterministic, zero-network bilingual (Urdu <-> English) embedding generator:
    1. Normalizes input text via `normalize_for_search()` (Prompt 2).
    2. Projects matched HR bilingual concepts into dedicated orthogonal bands (`dims 0..min(143, dim)`)
       so cross-language queries and policy chunks share dominant semantic coordinates.
    3. Projects word tokens and character 3/4-grams via signed SHA-256 random projections into
       the remaining dimensions (`144..dim-1` or `0..dim-1` for small dims) for lexical precision.
    4. Applies mandatory L2 normalization (falling back to `[1.0, 0.0, ...]` on empty text).
    """
    if dim <= 0:
        raise ValueError(f"Embedding dimension must be positive, got {dim}.")

    norm_text = normalize_for_search(text or "")
    vec = [0.0] * dim

    if not norm_text:
        vec[0] = 1.0
        return vec

    tokens = norm_text.split()
    token_set = set(tokens)
    padded_text = f" {norm_text} "

    # 1. Bilingual Concept Band (4 orthogonal slots per concept)
    concept_band_limit = min(dim, len(_NORMALIZED_BILINGUAL_CONCEPTS) * 4)
    for c_idx, (_concept_name, phrases) in enumerate(_NORMALIZED_BILINGUAL_CONCEPTS):
        base_slot = (c_idx * 4) % dim
        hit_weight = 0.0
        for phrase in phrases:
            if " " in phrase:
                if f" {phrase} " in padded_text:
                    hit_weight += 1.5
            elif phrase in token_set:
                hit_weight += 1.0
            elif len(phrase) >= 4 and phrase in norm_text:
                hit_weight += 0.6

        if hit_weight > 0.0:
            # Sub-linear saturation so repeated mentions don't drown secondary concepts
            boost = 4.5 * (1.0 + math.log1p(hit_weight))
            for offset in range(min(4, dim)):
                slot = (base_slot + offset) % dim
                sign = 1.0 if (offset % 2 == 0) else -1.0
                vec[slot] += sign * boost

    # 2. Word & Character N-Gram Signed Projections
    ngram_start = concept_band_limit if dim > concept_band_limit + 16 else 0
    ngram_span = max(1, dim - ngram_start)

    features: list[tuple[str, float]] = []
    for tok in tokens:
        features.append((f"w:{tok}", 0.55))
        if len(tok) >= 3:
            for i in range(len(tok) - 2):
                features.append((f"c3:{tok[i : i + 3]}", 0.18))

    for idx in range(len(tokens) - 1):
        features.append((f"bi:{tokens[idx]}_{tokens[idx + 1]}", 0.40))

    for feat, weight in features:
        digest = hashlib.sha256(feat.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[0:4], "big")
        h2 = int.from_bytes(digest[4:8], "big")
        slot1 = ngram_start + (h1 % ngram_span)
        slot2 = ngram_start + (h2 % ngram_span)
        sign1 = 1.0 if (digest[8] & 1) == 0 else -1.0
        sign2 = 1.0 if (digest[9] & 1) == 0 else -1.0
        vec[slot1] += sign1 * weight
        vec[slot2] += sign2 * (weight * 0.5)

    normalized = l2_normalize(vec)
    if all(v == 0.0 for v in normalized):
        normalized[0] = 1.0
    return normalized


@dataclass
class FakeFaultConfig:
    """Configurable fault injection state for deterministic testing."""

    rate_limit_countdown: int = 0
    timeout_countdown: int = 0
    permanent_error_countdown: int = 0
    simulate_repair_success: bool = False
    simulate_repair_failure: bool = False
    fail_private_route: bool = False
    custom_responses: dict[str, str] = field(default_factory=dict)


class FakeModelProvider:
    """
    Deterministic offline model provider simulator.
    Can dynamically generate responses, bilingual embeddings, or follow fault-injection rules.
    """

    def __init__(self) -> None:
        self.faults = FakeFaultConfig()
        self.call_history: list[dict[str, Any]] = []
        self.embedding_call_history: list[dict[str, Any]] = []

    def reset(self) -> None:
        """Resets all fault counters, custom responses, and call history."""
        self.faults = FakeFaultConfig()
        self.call_history.clear()
        self.embedding_call_history.clear()

    def set_rate_limit_countdown(self, attempts: int) -> None:
        """Injects simulated 429 RateLimitError for the next N attempts."""
        self.faults.rate_limit_countdown = attempts

    def set_timeout_countdown(self, attempts: int) -> None:
        """Injects simulated TimeoutError for the next N attempts."""
        self.faults.timeout_countdown = attempts

    def set_permanent_error_countdown(self, attempts: int) -> None:
        """Injects simulated non-retryable permanent error for the next N attempts."""
        self.faults.permanent_error_countdown = attempts

    def set_simulate_repair_success(self, enabled: bool = True) -> None:
        """First response is invalid JSON/schema, repair response succeeds."""
        self.faults.simulate_repair_success = enabled

    def set_simulate_repair_failure(self, enabled: bool = True) -> None:
        """Both initial and repair responses return invalid JSON/schema."""
        self.faults.simulate_repair_failure = enabled

    def register_response(self, keyword: str, response: str) -> None:
        """Registers a deterministic response triggered when keyword is found in the prompt."""
        self.faults.custom_responses[keyword] = response

    async def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"],
        model: str,
        dim: int = 768,
    ) -> list[list[float]]:
        """
        Generates deterministic L2-normalized bilingual embeddings for a batch of texts
        and records call telemetry in `self.embedding_call_history`.
        """
        self.embedding_call_history.append(
            {
                "texts": list(texts),
                "batch_size": len(texts),
                "task_type": task_type,
                "model": model,
                "dim": dim,
            }
        )

        if self.faults.permanent_error_countdown > 0:
            self.faults.permanent_error_countdown -= 1
            raise FakePermanentError("HTTP 400 Bad Request: Invalid embedding parameters.")

        if self.faults.timeout_countdown > 0:
            self.faults.timeout_countdown -= 1
            await asyncio.sleep(0.01)
            raise TimeoutError("Simulated embedding request timeout")

        if self.faults.rate_limit_countdown > 0:
            self.faults.rate_limit_countdown -= 1
            raise FakeRateLimitError("Rate limit exceeded: 429 Too Many Requests.")

        return [compute_bilingual_fake_embedding(t, dim=dim) for t in texts]

    async def generate(
        self,
        prompt: str,
        route: str,
        model_id: str,
        timeout_seconds: float = 30.0,
        schema: type[BaseModel] | None = None,
    ) -> tuple[str, int, int]:
        """
        Executes generation, applying fault injection and token tracking.
        Returns: (completion_text, prompt_tokens, completion_tokens).
        """
        self.call_history.append(
            {
                "prompt": prompt,
                "route": route,
                "model_id": model_id,
                "schema": schema.__name__ if schema else None,
            }
        )

        # 1. Fault: Permanent (non-retryable) error (HTTP 400 / 401 / 403 / 404)
        if self.faults.permanent_error_countdown > 0:
            self.faults.permanent_error_countdown -= 1
            raise FakePermanentError(
                "HTTP 400 Bad Request: Invalid model parameters or unsupported feature."
            )

        # 2. Fault: Timeout
        if self.faults.timeout_countdown > 0:
            self.faults.timeout_countdown -= 1
            await asyncio.sleep(0.01)
            raise TimeoutError(f"Simulated request timeout after {timeout_seconds}s")

        # 3. Fault: Rate Limit (HTTP 429)
        if self.faults.rate_limit_countdown > 0:
            self.faults.rate_limit_countdown -= 1
            raise FakeRateLimitError("Rate limit exceeded: 429 Too Many Requests. Retry after 1s.")

        # 4. Fault: Structured Output Repair Scenarios
        is_repair_prompt = "REPAIR ATTEMPT" in prompt or "validation error" in prompt.lower()

        if self.faults.simulate_repair_failure:
            output = json.dumps({"corrupted_key": "unrepairable_value", "extra": 42})
        elif self.faults.simulate_repair_success:
            if not is_repair_prompt:
                output = json.dumps({"missing_required_fields": True})
            else:
                output = (
                    self._synthesize_schema(schema)
                    if schema
                    else "Repaired text response successfully generated."
                )
        else:
            # 5. Check custom registered keyword responses
            output = None
            for keyword, custom_resp in self.faults.custom_responses.items():
                if keyword in prompt:
                    output = custom_resp
                    break

            if output is None:
                if schema:
                    if schema.__name__ == "LeaveIntakeExtraction":
                        output = self._parse_leave_intake_prompt(prompt)
                    else:
                        output = self._synthesize_schema(schema)
                else:
                    output = f"Deterministic model response for route '{route}' using '{model_id}'."

        prompt_tokens = max(10, len(prompt.split()) * 2)
        completion_tokens = max(5, len(output.split()) * 2)

        return output, prompt_tokens, completion_tokens

    @staticmethod
    def _parse_leave_intake_prompt(prompt: str) -> str:
        """
        Deterministic bilingual (English, Urdu script, Roman Urdu) parser for
        `LeaveIntakeExtraction` structured output in offline/fake mode.
        """
        as_of_match = re.search(r"AS_OF_DATE:\s*(\d{4}-\d{2}-\d{2})", prompt)
        as_of = date.fromisoformat(as_of_match.group(1)) if as_of_match else date(2026, 6, 1)

        msg_match = re.search(
            r"<untrusted_[a-zA-Z0-9_]+>\s*(.*?)\s*</untrusted_[a-zA-Z0-9_]+>",
            prompt,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if msg_match:
            raw_message = msg_match.group(1).strip()
        else:
            fallback_match = re.search(
                r"<<<BEGIN_UNTRUSTED_DATA[^>]*>>>\s*(.*?)\s*<<<END_UNTRUSTED_DATA[^>]*>>>",
                prompt,
                flags=re.DOTALL,
            )
            raw_message = fallback_match.group(1).strip() if fallback_match else ""
        lower_msg = raw_message.lower()

        # 1. Detect leave type (English + Urdu + Roman Urdu); generic "chutti"/"leave" alone stays None
        leave_type_code: str | None = None
        type_patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
            (
                "annual",
                ("annual", "vacation", "earned leave", "salana", "سالانہ", "سالانه"),
            ),
            (
                "sick",
                (
                    "sick",
                    "medical",
                    "illness",
                    "fever",
                    "doctor",
                    "bimari",
                    "bimar",
                    "bukhar",
                    "tabiyat",
                    "بیماری",
                    "طبی",
                    "بخار",
                ),
            ),
            (
                "casual",
                (
                    "casual",
                    "urgent",
                    "emergency",
                    "personal",
                    "ittefaqia",
                    "hangami",
                    "zaroori",
                    "اتفاقیہ",
                    "ہنگامی",
                    "ضروری",
                ),
            ),
            ("maternity", ("maternity", "زچگی", "zachgi")),
            ("paternity", ("paternity",)),
            ("unpaid", ("unpaid", "lwp", "without pay", "بغیر تنخواہ")),
        )
        for code, keywords in type_patterns:
            if any(kw in lower_msg or kw in raw_message for kw in keywords):
                leave_type_code = code
                break

        # 2. Detect half-day & period
        is_half_day = any(
            kw in lower_msg or kw in raw_message
            for kw in ("half day", "half-day", "halfday", "aadha din", "adha din", "آدھا دن")
        )
        half_day_period: str | None = None
        if is_half_day:
            if any(
                kw in lower_msg or kw in raw_message for kw in ("morning", "subah", "صبح", "am")
            ):
                half_day_period = "morning"
            elif any(
                kw in lower_msg or kw in raw_message
                for kw in ("afternoon", "dopehar", "evening", "shaam", "دوپہر", "pm")
            ):
                half_day_period = "afternoon"

        # 3. Extract dates (ISO dates, month names, relative phrases)
        start_date: date | None = None
        end_date: date | None = None

        iso_dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", raw_message)
        if iso_dates:
            parsed_iso = [date.fromisoformat(d) for d in iso_dates]
            start_date = parsed_iso[0]
            end_date = parsed_iso[1] if len(parsed_iso) >= 2 else parsed_iso[0]
        else:
            months_map = {
                "jan": 1,
                "january": 1,
                "feb": 2,
                "february": 2,
                "mar": 3,
                "march": 3,
                "apr": 4,
                "april": 4,
                "may": 5,
                "jun": 6,
                "june": 6,
                "jul": 7,
                "july": 7,
                "aug": 8,
                "august": 8,
                "sep": 9,
                "sept": 9,
                "september": 9,
                "oct": 10,
                "october": 10,
                "nov": 11,
                "november": 11,
                "dec": 12,
                "december": 12,
            }
            month_pat = "|".join(months_map.keys())
            md_matches = re.findall(
                rf"\b(?:({month_pat})\s+(\d{{1,2}})|(\d{{1,2}})\s+({month_pat}))(?:,?\s*(\d{{4}}))?\b",
                lower_msg,
            )
            parsed_named: list[date] = []
            for m1, d1, d2, m2, yr in md_matches:
                m_str = m1 or m2
                d_int = int(d1 or d2)
                y_int = int(yr) if yr else as_of.year
                try:
                    parsed_named.append(date(y_int, months_map[m_str], d_int))
                except ValueError:
                    continue
            if parsed_named:
                start_date = parsed_named[0]
                end_date = parsed_named[1] if len(parsed_named) >= 2 else parsed_named[0]

        if start_date is None:
            if any(kw in lower_msg for kw in ("day after tomorrow", "parson")):
                start_date = as_of + timedelta(days=2)
            elif any(kw in lower_msg for kw in ("tomorrow", "kal", "کل")):
                start_date = as_of + timedelta(days=1)
            elif any(kw in lower_msg for kw in ("today", "aaj", "آج")):
                start_date = as_of
            else:
                weekday_names = {
                    "monday": 0,
                    "tuesday": 1,
                    "wednesday": 2,
                    "thursday": 3,
                    "friday": 4,
                    "saturday": 5,
                    "sunday": 6,
                }
                next_wd = re.search(
                    r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                    lower_msg,
                )
                if next_wd:
                    target_wd = weekday_names[next_wd.group(1)]
                    delta = (target_wd - as_of.weekday()) % 7
                    if delta == 0:
                        delta = 7
                    start_date = as_of + timedelta(days=delta)

        if start_date is not None and end_date is None:
            dur_match = re.search(r"\bfor\s+(\d{1,2})\s+days?\b", lower_msg)
            if dur_match and not is_half_day:
                num_days = max(1, int(dur_match.group(1)))
                end_date = start_date + timedelta(days=num_days - 1)
            else:
                end_date = start_date

        # 4. Extract concise reason
        reason: str | None = None
        reason_match = re.search(
            r"(?:because|due to|reason[:\s]+|kyunke|wajah[:\s]+)\s+(.+)",
            raw_message,
            flags=re.IGNORECASE,
        )
        if reason_match:
            reason = reason_match.group(1).strip(" .!?")[:300]
        elif leave_type_code and start_date:
            reason = raw_message[:300]

        payload = {
            "leave_type_code": leave_type_code,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "is_half_day": is_half_day,
            "half_day_period": half_day_period,
            "reason": reason,
            "clarifying_question": None,
        }
        return json.dumps(payload)

    def _synthesize_schema(self, schema: type[BaseModel]) -> str:
        """Dynamically synthesizes a 100% valid JSON payload conforming to any Pydantic model."""
        import types
        from typing import Union

        payload: dict[str, Any] = {}

        for name, field_info in schema.model_fields.items():
            annotation = field_info.annotation
            origin = get_origin(annotation)
            args = get_args(annotation)

            # Handle Optional / Union[T, None]
            target_type = annotation
            if origin in (Union, types.UnionType):
                non_none = [a for a in args if a is not type(None)]
                if non_none:
                    target_type = non_none[0]
                    origin = get_origin(target_type)
                    args = get_args(target_type)

            if origin is list:
                payload[name] = [f"item_{name}"]
            elif origin is dict:
                payload[name] = {"key": f"value_{name}"}
            elif target_type is int:
                payload[name] = 42
            elif target_type is float:
                payload[name] = 85.5
            elif target_type is bool:
                payload[name] = True
            elif target_type is str:
                payload[name] = f"synthetic_{name}"
            elif isinstance(target_type, type) and issubclass(target_type, BaseModel):
                payload[name] = json.loads(self._synthesize_schema(target_type))
            else:
                payload[name] = f"sample_{name}"

        return json.dumps(payload)


# Singleton fake provider instance for application runtime & tests
fake_model_provider = FakeModelProvider()
