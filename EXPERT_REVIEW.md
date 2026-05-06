# Expert Review: Helium EDM Intake Agent

This document records a review by four expert lenses: critical thinking, design thinking, email operations, and UI/UX demo storytelling.

## Shared Recommendation

Position the project as:

> Helium EDM Intake Agent turns a messy client handoff into a verified, client-branded, Sendy-ready campaign draft with an auditable processing plan.

Avoid framing it as bulk email automation. The safer and stronger frame is an opted-in EDM operations and list hygiene agent that prepares campaigns for human review.

## Critical Thinking Review

### Assessment

The project is strong because it automates a real recurring workflow: receiving EDM assets and email lists from a client, identifying what each file is, preparing the campaign, checking list quality, and creating a Sendy draft.

The strongest value is not sending email faster. It is reducing judgment-heavy manual work: file triage, list hygiene, client-specific branding, campaign assembly, and auditability.

### Assumptions To Make Explicit

- Lists are permission-based or otherwise lawful to process.
- EmailListVerify checks address quality, not consent.
- The agent creates a draft or scheduled campaign for review by default.
- Each Helium client has known templates, sender identity, Sendy brand ID, and Sendy list ID.
- The human operator remains responsible for final approval before sending.

### Risks

- Compliance risk if the project sounds like cold outbound automation.
- Deliverability risk if verification is treated as sufficient protection.
- Classification risk if the wrong file is identified as the EDM or list.
- Template risk if a client header/footer breaks the final HTML.
- API risk if Sendy or EmailListVerify fails mid-run.

### Recommendations

- Show a visible processing plan before action.
- Include file roles, confidence scores, intended actions, and blocking issues.
- Keep draft creation as the default and require an explicit flag for schedule/send.
- Record compliance checks in the report.
- Show a clear before/after: raw files to send-ready draft.

## Design Thinking Review

### Core User Framing

The user is an operator managing repeated client submissions. The workflow is not casual marketing work; it is cross-border campaign operations.

### Workflow Transformation

Before:

- Client sends mixed files through chat or email.
- Operator identifies which file is the list, which file is the EDM, and which metadata is missing.
- Operator cleans the list, verifies emails, copies contacts into Sendy, edits EDM HTML, adds client branding, creates a campaign, and checks everything manually.

After:

- Operator drops all files into one intake folder.
- Agent classifies files, builds a processing plan, applies the right client wrapper, verifies the list, uploads accepted contacts, creates a Sendy draft, and writes a run report.
- Human remains in control at final review/send.

### Recommended Journey To Show

1. Receive client assets.
2. Put all files into an intake folder.
3. Run one command.
4. Agent evaluates files.
5. Agent explains its plan.
6. Agent cleans and verifies contacts.
7. Agent renders the client-branded EDM.
8. Agent creates the Sendy campaign draft.
9. Operator reviews the campaign on helium.sg.

### Service Design Strength

The useful design pattern is the separation between intake, evaluation, planning, execution, and audit. Stripe reviewers are likely to appreciate that this is not just an AI prompt; it is a workflow system with deterministic rules, APIs, and AI used only where ambiguity exists.

## Email Operations Review

### Core Position

Frame the automation as an opted-in EDM operations and list hygiene agent, not a bulk outbound or cold email tool.

### Deliverability Recommendations

- Add a pre-send deliverability checklist:
  - SPF, DKIM, and DMARC are configured.
  - From address matches the sending domain.
  - Reply-to address is monitored.
  - Subject avoids spam-heavy wording.
  - HTML has a plain-text fallback.
  - Images have alt text.
  - Links use HTTPS.
  - Unsubscribe link is present.
  - Sender identity or required footer text is present.

- Treat Sendy campaign creation as draft-first by default.
- Add checks for broken links, image URLs, and oversized HTML.

### Consent And Compliance Recommendations

- Capture `consent_basis` for every run, such as customer list, newsletter opt-in, event registration, partner-provided opt-in list, or internal test list.
- If consent basis is missing, block live import/send and explain why.
- Keep an audit trail with source filename, client slug, timestamp, contact counts, consent basis, Sendy list ID, and campaign result.
- Add a suppression list so unsubscribed, bounced, or manually suppressed contacts are not re-imported.

### List Hygiene Recommendations

- Normalize emails to lowercase and trim whitespace.
- Dedupe before verification to reduce cost.
- Import valid only.
- Reject invalid, malformed, and duplicate contacts.
- Quarantine risky or unknown contacts for human review.
- Flag role addresses such as `info@`, `sales@`, and `admin@`.

### API Reference

Use the official EmailListVerify API docs as the canonical reference for verification behavior:

```text
https://api.emaillistverify.com/api-doc
```

Use the Helium Sendy API path as the canonical Sendy endpoint reference:

```text
https://sendy.co/api?app_path=https://helium.sg
```

The Sendy third-party resources and integrations section is also useful as ecosystem context:

```text
https://sendy.co/api#third-party-resources-integrations
```

### Operational Reliability Recommendations

- Save each run under a unique timestamped folder, with `runs/latest` pointing to the newest run.
- Make reruns idempotent where possible.
- Use clear statuses: `planned`, `blocked`, `verified`, `rendered`, `imported`, `draft_created`, `ready_for_review`.
- Add structured error handling for API timeouts, invalid keys, malformed CSVs, missing templates, and invalid HTML.

## UI/UX And Demo Storytelling Review

### Core Recommendation

Present the tool as an operator workflow agent, not as a developer CLI. The CLI is the implementation surface; the demo should make the agent behavior visible through artifacts.

### Best Video Flow

1. Start with the messy intake folder.
2. Show client templates.
3. Run the command with `--input-dir` and `--client`.
4. Show terminal output.
5. Show `runs/latest/run_report.json`.
6. Show `runs/latest/rendered_edm.html`.
7. Show `runs/latest/verified_contacts.csv`.
8. Show the Sendy draft or dry-run campaign payload.

### Best Demo Moment

Show the raw intake folder and say:

> In the old workflow, this is where I would inspect each file manually. The agent now does that first.

Then show the processing plan and final outputs.

### UI Recommendation

A full UI was not necessary, but a small operator dashboard is useful because it makes the workflow legible without asking the viewer to parse CLI flags. The implemented dashboard is intentionally narrow:

- Password-protected login
- Client selector
- EDM and contact-list upload
- EmailListVerify and Sendy configuration status
- Dry-run/live controls
- Sendy contact upload and draft creation controls
- Links to the rendered EDM, verified CSV, JSON report, and human-readable report

The highest-impact demo improvement was also implemented: a generated human-readable report, such as:

```text
runs/latest/index.html
```

It should show file classifications, processing plan, contact verification summary, rendered EDM preview, campaign readiness status, warnings, and rejections.

## Prioritized Recommendations

### Must Emphasize In Submission

1. This prepares opted-in EDM campaigns for review; it does not blindly send.
2. The agent classifies files, creates a plan, and records its decisions.
3. Per-client wrappers make the workflow useful for multiple Helium clients.
4. Email verification is list hygiene, not consent.
5. The final Sendy campaign is draft-first by default.

### Best Next Build Improvements

1. Generate a human-readable `runs/latest/index.html` report.
2. Add `consent_basis` as a required live-run field.
3. Add a client config file mapping client slug to Sendy brand/list/sender settings.
4. Add suppression-list handling.
5. Add richer deliverability checks for links, images, sender identity, and HTML size.
