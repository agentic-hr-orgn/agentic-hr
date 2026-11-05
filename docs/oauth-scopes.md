# OAuth Scopes - Stage 0

| Service | Permission / Scope | Purpose | Classification | Verification Required |
|---|---|---|---|---|
| Gmail | gmail.send | Send HR emails, CV forwarding | Sensitive | Yes, needs verification |
| Drive | drive.file | Work with selected CV files | Non-sensitive | No |
| Sheets | drive.file (prefer) | Work with selected spreadsheets | Non-sensitive if using drive.file | No |
| Calendar | calendar.events | Create interview events | Sensitive | Yes |

Note: gmail.readonly and gmail.modify are RESTRICTED - we will NOT use them. Only send-only.

## CV Submission Design Decision
Method A: Upload - Candidate uploads CV directly in app
Method B: Forwarding address - candidate sends CV to agentic.hr.test.ubaid.owner+cv@gmail.com which forwards to app ingestion
Restriction: No inbox-read scope, no gmail.readonly