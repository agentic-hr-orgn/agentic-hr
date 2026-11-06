# IMPORTANT GOOGLE WORKSPACE TESTING DECISION
## Agentic HR — Stage 0/Stage 1 Project Instructions

Our original plan to create a Google Workspace testing domain using FreeDNS was unsuccessful.

We therefore adopted a temporary V1 testing arrangement.

### 1. Current Google testing accounts

We have six separate real Gmail test accounts:

- agentic.hr.test.ubaid.owner@gmail.com
- agentic.hr.test.ubaid.recruiter@gmail.com
- agentic.hr.test.ubaid.hm@gmail.com
- agentic.hr.test.ubaid.employee@gmail.com
- agentic.hr.test.ubaid.candidate1@gmail.com
- agentic.hr.test.ubaid.candidate2@gmail.com

These are independent Google Accounts, not Google Workspace mailboxes.

They are already added to Google Cloud Console under:

Google Auth Platform → Audience → Test users.

Do not assume that we have a managed Google Workspace domain.

### 2. Current Google OAuth permissions

Our intended minimum permission scopes are:

- Gmail: gmail.send
- Drive: drive.file
- Calendar: calendar.events, subject to further least-privilege review.

Do not request:

- gmail.readonly
- gmail.modify
- mail.google.com

Never introduce broader Gmail inbox-reading access without an explicitly approved architecture change.

### 3. Current employee directory

We created a Google Sheet called:

Agentic HR - Employee Directory (Mock)

This provides our temporary employee-directory data for selected V1 demonstrations.

Do not assume that Google Admin Directory API or Workspace administrator privileges are available.

The future primary application database remains PostgreSQL.

Google Sheets must remain a synchronized view or explicitly designated import/export source, not the application's authoritative database.

### 4. Current integration strategy

During Stage 1:

- Keep USE_FAKE_INTEGRATIONS=true.
- Do not connect real employee accounts.
- Do not send real Gmail messages.
- Do not create real Calendar events.
- Do not scan Gmail inboxes.
- Build integration interfaces that can later support real Google APIs.
- Use synthetic employee, candidate and company data.

If optional OAuth testing is performed, use only the six authorized testing accounts and the documented minimum permissions.

Handle OAuth refresh-token expiration and reauthorization safely.

### 5. Future production plan

Our planned production environment will use:

- An inexpensive paid .xyz domain.
- A properly configured Google Workspace subscription.
- Managed company mailboxes.
- Properly configured OAuth permissions.
- Approved Google Workspace integrations.

This is a future production configuration, not a prerequisite for completing the Stage 1 platform skeleton.

Do not redesign or complicate the existing Stage 1 architecture because of the temporary Gmail workaround.

### 6. Required documentation

Preserve and read:

- docs/oauth-scopes.md
- docs/synthetic-data-plan.md
- docs/adr/006-workspace-workaround.md

Do not overwrite these decisions or recreate the previous failed FreeDNS testing setup.