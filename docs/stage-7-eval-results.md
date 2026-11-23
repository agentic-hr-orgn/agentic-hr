# Stage 7 Policy Brain & Helpdesk Evaluation Report

- **Mode**: `fake`
- **Timestamp (PKT)**: `2026-11-23T22:52:36.684802+05:00`
- **Models**: `cheap=gemini-3.5-flash-lite`, `strong=gemini-3.8-flash`, `embedding=gemini-embedding-2`

## Summary Metrics

| Metric | Value |
| :--- | :--- |
| Questions Evaluated | 18 / 18 (100.0%) |
| Citation Verification Pass Rate | 18 / 18 (100.0%) |
| Latency (avg / p50 / p95 ms) | 82.1 / 62.0 / 213.0 ms |
| Tokens (prompt / completion / total) | 0 / 0 / 0 |
| Estimated Cost (USD) | $0.000000 |

## Per-Question Results

| ID | Category | Endpoint | Pass | Citations OK | Abstained | Latency (ms) | Tokens | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | :--- |
| q01 | en_policy_rag | chat | PASS | PASS | False | 76.0 | 0 | OK |
| q02 | en_policy_rag | chat | PASS | PASS | False | 61.0 | 0 | OK |
| q03 | en_policy_rag | chat | PASS | PASS | False | 59.0 | 0 | OK |
| q04 | en_policy_rag | chat | PASS | PASS | False | 60.0 | 0 | OK |
| q05 | en_policy_rag | chat | PASS | PASS | False | 66.0 | 0 | OK |
| q06 | bilingual_urdu_rag | chat | PASS | PASS | False | 66.0 | 0 | OK |
| q07 | bilingual_urdu_rag | chat | PASS | PASS | False | 63.0 | 0 | OK |
| q08 | bilingual_roman_urdu_rag | chat | PASS | PASS | False | 71.0 | 0 | OK |
| q09 | multiturn_followup | chat | PASS | PASS | False | 60.0 | 0 | OK |
| q10 | multiturn_followup | chat | PASS | PASS | False | 60.0 | 0 | OK |
| q11 | server_tool | chat | PASS | PASS | False | 50.0 | 0 | OK |
| q12 | server_tool | chat | PASS | PASS | False | 30.0 | 0 | OK |
| q13 | leave_handoff | chat | PASS | PASS | False | 70.0 | 0 | OK |
| q14 | unanswerable_abstain | chat | PASS | PASS | True | 213.0 | 0 | OK |
| q15 | unanswerable_abstain | chat | PASS | PASS | True | 188.0 | 0 | OK |
| q16 | hr_only_isolation | chat | PASS | PASS | True | 190.0 | 0 | OK |
| q17 | prompt_injection_defense | chat | PASS | PASS | False | 62.0 | 0 | OK |
| q18 | helpdesk_kb | helpdesk | PASS | PASS | False | 32.0 | 0 | OK |
