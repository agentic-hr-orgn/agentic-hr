# ADR 007: Model Routing Tiers & Zero-Retention Privacy Governance (2026 Amendment)

Status: Decided (Amends ADR 003)
Date: 2026-11-14

## Context
As Agentic HR Core advances into Stage 4 (Onboarding Wizard, Employees, Encrypted Record Keeper) and beyond, our central `ModelGateway` (`apps/api/app/core/model_gateway.py`) requires four distinct model routing tiers to balance throughput, latency, complex reasoning, and strict data sovereignty:
1. **`cheap`**: High-throughput, low-latency document extraction, OCR structuring, and initial rubric screening.
2. **`strong`**: Bilingual (English & Urdu) policy RAG synthesis, structured contract/offer analysis, and multi-constraint evaluation.
3. **`reasoning`**: Deep multi-step reasoning, complex statutory/compliance edge cases, and autonomous test/rubric critique.
4. **`private`**: Fail-closed VPC or on-premise model endpoint for real (non-synthetic) sensitive employee and candidate PII.

## Decision

### 1. Four-Tier Model Routing Matrix (Environment-Configured)
Model identifiers and providers are never hardcoded in business services; they are resolved dynamically by `ModelGateway.resolve_route()` via environment variables:

| Route Tier | Enum (`ModelRoute`) | Env Variables | Recommended 2026 Models (Examples) | Fake Mode Default (`USE_FAKE_INTEGRATIONS=true`) |
| :--- | :--- | :--- | :--- | :--- |
| **Cheap** | `ModelRoute.CHEAP` (`"cheap"`) | `CHEAP_MODEL_PROVIDER`, `CHEAP_MODEL_ID`, `CHEAP_MODEL_API_KEY` | `gemini-3.8-flash` / `gemini-4-hydrogen` / `gemini-3.5-flash-lite` | `fake/fake-cheap-model` |
| **Strong** | `ModelRoute.STRONG` (`"strong"`) | `STRONG_MODEL_PROVIDER`, `STRONG_MODEL_ID`, `STRONG_MODEL_API_KEY` | `claude-sonnet-5-5` / `gemini-4-argon` / `gemini-3.8-flash` | `fake/fake-strong-model` |
| **Reasoning** | `ModelRoute.REASONING` (`"reasoning"`) | `REASONING_MODEL_PROVIDER`, `REASONING_MODEL_ID`, `REASONING_MODEL_API_KEY` | `claude-opus-5-5` | `fake/fake-reasoning-model` |
| **Private** | `ModelRoute.PRIVATE` (`"private"`) | `PRIVATE_MODEL_PROVIDER`, `PRIVATE_MODEL_ID`, `PRIVATE_MODEL_API_KEY`, `PRIVATE_MODEL_ENDPOINT` | Self-hosted vLLM / Vertex AI Private Endpoint / Bedrock VPC | Fail-closed unless explicitly configured |

### 2. Data Privacy & Zero-Training Boundary (Amending ADR 003)
- **Free-Tier APIs**: Only **synthetic test data** (Acme Tech Solutions) is permitted on free-tier developer endpoints (e.g., Google AI Studio Free Tier), where prompts may be reviewed or used for model training.
- **Zero-Retention Enterprise Endpoints**: `cheap`, `strong`, and `reasoning` routes may process non-sensitive or de-identified operational prompts in pilot/production ONLY when backed by paid enterprise API agreements with contractual zero-data-retention and zero-training guarantees.
- **Strict Real PII Boundary (`private` route)**: Real (non-synthetic) sensitive HR PII—including CNIC numbers, bank account / IBAN details, home addresses, and confidential compensation records—is **strictly restricted to `ModelRoute.PRIVATE`**.
- **Fail-Closed Enforcement**: `ModelRoute.PRIVATE` requires `PRIVATE_MODEL_PROVIDER`, `PRIVATE_MODEL_ID`, and `PRIVATE_MODEL_ENDPOINT` to be explicitly configured. If any are missing, `ModelGateway` raises `ModelRouteUnavailableError` immediately and **never** falls back to `cheap`, `strong`, or `reasoning`.

### 3. Prompt Injection Defense & Repair Encapsulation
- All untrusted external text (CVs, emails, uploaded documents, and prior unvalidated model outputs during schema repair) must be wrapped using `wrap_untrusted_text()` (`apps/api/app/core/untrusted.py`).
- When executing via LiteLLM against live providers, `ModelGateway` separates system instructions (`role: "system"`) from user/untrusted data (`role: "user"`).

## Consequences
- Clean separation between routing policy (`ModelRoute`) and vendor model versions configured in `.env`.
- Zero vendor lock-in and 100% offline deterministic CI execution under `USE_FAKE_INTEGRATIONS=true`.
- Strict fail-closed protection preventing accidental leakage of real employee PII to public or free-tier endpoints.
