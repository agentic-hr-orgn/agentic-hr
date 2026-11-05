# ADR 005: Sheets as Synced View
Status: Decided
Context: Sheets treated as DB causes quotas, drift
Decision: Postgres is source of truth, Sheets is a synced view via Temporal
Consequences: No broken assumptions, read-only mode, revoke handling