# agentic-hr
Agentic-HR Core: Agentic AI Workforce for SMEs. 4 Agents - DocuVerify (OCR for scanned CVs), Screener (0-100 scoring), Code Tester (E2B sandbox), Policy Brain (RAG). Built with MCP, RBAC, Approval Gates, Aegis-AI. Google Workspace Native.

## Current Platform Status
- **Stage 1 (Platform Skeleton)**: Complete
- **Stage 2 (Governance Core + Pass 1 Hardening)**: Complete (`0013` Forced RLS + `security_invoker` views, AES-256-GCM, Temporal approvals, 4-tier `ModelGateway`)
- **Stage 3 (Milestone A: Connectors + Pass 2 Hardening)**: Complete (`gmail.send`, `drive.file`, `calendar.events`, Sheets sync, 13 FastMCP guarded tools)
- **Stage 4 (Milestone B — Phase 0: Onboarding Wizard, Employees & Encrypted Record Keeper)**: Complete (`v0.4-stage4-hardened`) — see [docs/stage-4-verification.md](docs/stage-4-verification.md)
- **Next Stage**: Stage 5 (Recruitment Agents — DocuVerify & Screener)
