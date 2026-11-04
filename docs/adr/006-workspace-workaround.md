# ADR 006: Workspace Workaround with Gmail Accounts

## Status
Accepted - Updated

## Context
- Free domain mooo.com blocked by Google billing risk OR_BACR2_31 even with valid Nayapay international card
- Tried plus-alias (owner+recruiter@gmail.com) for 6 test accounts
- Google OAuth Test users requires active Google Account, plus-alias not accepted: "Email addresses must be associated with an active Google Account"

## Decision
Use 6 separate real Gmail accounts instead of Workspace and plus-aliases for entire project V1-V14

Accounts:
1. agentic.hr.test.ubaid.owner@gmail.com (Owner) - Main
2. agentic.hr.test.ubaid.recruiter@gmail.com - Recruiter
3. agentic.hr.test.ubaid.hm@gmail.com - Hiring Manager
4. agentic.hr.test.ubaid.employee@gmail.com - Employee
5. agentic.hr.test.ubaid.candidate1@gmail.com - Candidate 1
6. agentic.hr.test.ubaid.candidate2@gmail.com - Candidate 2

## Consequence
- No functional compromise for V1: gmail.send, drive.file, spreadsheets, calendar.events work same
- All 6 can be added as Test users in OAuth consent screen
- Directory mocked via Google Sheet
- Production will use paid Workspace domain (.xyz) in Stage 1

## Workaround for OR_BACR2_31
Free subdomain mooo.com flagged as low-quality by Google billing. Cheap paid domain .xyz ($1) will solve in production.