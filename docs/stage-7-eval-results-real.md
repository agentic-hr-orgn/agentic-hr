# Stage 7 Policy Brain & Helpdesk Evaluation Report

- **Mode**: `real`
- **Timestamp (PKT)**: `2026-11-24T00:14:16.844048+05:00`
- **Models**: `cheap=gemini/gemini-3.5-flash-lite`, `strong=gemini/gemini-3.5-flash-lite`, `embedding=gemini-embedding-001`

## Summary Metrics

| Metric | Value |
| :--- | :--- |
| Questions Evaluated | 18 / 18 (100.0%) |
| Citation Verification Pass Rate | 18 / 18 (100.0%) |
| Latency (avg / p50 / p95 ms) | 16100.9 / 11500.0 / 44589.0 ms |
| Tokens (prompt / completion / total) | 13973 / 1295 / 72812 |
| Estimated Cost (USD) | $0.007670* |

> \*Estimated at `gemini-3.5-flash-lite` fallback pricing (`$0.10 / 1M` input tokens, `$0.40 / 1M` output tokens across `72,812` total tokens = `71,517` input-attributed + `1,295` output tokens; live run predated fallback cost attribution in `model_gateway.py`). Clean non-retry passes completed in `2.2s–5.3s`, while overall avg/p95 latencies include transient Gemini `503 UNAVAILABLE` backoff retries.

## Per-Question Results

| ID | Category | Endpoint | Pass | Citations OK | Abstained | Latency (ms) | Tokens | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | :--- |
| q01 | en_policy_rag | chat | PASS | PASS | False | 35888.0 | 4264 | OK |
| q02 | en_policy_rag | chat | PASS | PASS | False | 3873.0 | 4254 | OK |
| q03 | en_policy_rag | chat | PASS | PASS | False | 10253.0 | 3890 | OK |
| q04 | en_policy_rag | chat | PASS | PASS | False | 5283.0 | 3908 | OK |
| q05 | en_policy_rag | chat | PASS | PASS | False | 15415.0 | 3854 | OK |
| q06 | bilingual_urdu_rag | chat | PASS | PASS | False | 21207.0 | 4311 | OK |
| q07 | bilingual_urdu_rag | chat | PASS | PASS | False | 12971.0 | 4297 | OK |
| q08 | bilingual_roman_urdu_rag | chat | PASS | PASS | False | 4228.0 | 4305 | OK |
| q09 | multiturn_followup | chat | PASS | PASS | False | 11500.0 | 4314 | OK |
| q10 | multiturn_followup | chat | PASS | PASS | False | 10219.0 | 4277 | OK |
| q11 | server_tool | chat | PASS | PASS | False | 49.0 | 0 | OK |
| q12 | server_tool | chat | PASS | PASS | False | 28.0 | 0 | OK |
| q13 | leave_handoff | chat | PASS | PASS | False | 20818.0 | 0 | OK |
| q14 | unanswerable_abstain | chat | PASS | PASS | True | 44589.0 | 9101 | OK |
| q15 | unanswerable_abstain | chat | PASS | PASS | True | 41456.0 | 9064 | OK |
| q16 | hr_only_isolation | chat | PASS | PASS | True | 12346.0 | 9087 | OK |
| q17 | prompt_injection_defense | chat | PASS | PASS | False | 37515.0 | 3886 | OK |
| q18 | helpdesk_kb | helpdesk | PASS | PASS | False | 2177.4 | 0 | OK |
