# Helium EDM Ops: AI-Assisted Email Campaign Operations

## Overview

I run a small side business, [helium.sg](https://helium.sg), that helps clients outside Singapore with lightweight go-to-market and digital operations work.

One recurring workflow comes from a friend/client in China who needs a cheap and dependable way to send email campaigns to lists outside China. The current stack uses:

- [Sendy](https://sendy.co/) for low-cost email campaign sending
- [EmailListVerify](https://emaillistverify.com/) for email list hygiene
- Client-provided CSV contact lists and EDM HTML

The problem is not that any single step is technically hard. The problem is that the workflow has many small, manual, failure-prone handoffs: cleaning lists, verifying emails, downloading results, importing into Sendy, preparing plain text, checking basic deliverability issues, and creating the campaign.

I built a CLI agent that turns that process into one repeatable command with an auditable run report.

## Workflow Today

### Who Does It

I receive email campaign materials from my client in China. A typical handoff includes:

- A CSV file with recipient emails and sometimes names/company fields
- An EDM HTML file
- A proposed subject line or rough campaign context
- A target Sendy list or campaign destination

### How Often

This happens whenever the client has a new outbound EDM campaign. The workflow is recurring and operational: each campaign requires the same basic preparation steps before it can be safely sent.

### Current Manual Process

The current process looks roughly like this:

1. Receive the CSV contact list and EDM HTML from the client.
2. Open the CSV manually and inspect the columns.
3. Remove obvious bad rows, blank emails, malformed emails, and duplicates.
4. Upload the list to EmailListVerify.
5. Wait for the verification results.
6. Download the cleaned list.
7. Manually prepare the accepted contacts for Sendy.
8. Import the cleaned contacts into the right Sendy list.
9. Open the EDM HTML and check for basic campaign issues.
10. Create or edit a plain-text fallback.
11. Create a campaign in Sendy.
12. Paste the subject, HTML, plain text, sender details, and list ID.
13. Create a draft, schedule it, or send it.
14. Keep some record of what was imported, rejected, and sent.

### What Makes It Painful

The pain is mostly operational drag:

- The workflow has too many browser tabs and copy/paste steps.
- It is easy to import the wrong version of a list.
- Deduplication and malformed-email cleanup are boring but important.
- Manual verification/download/upload creates avoidable delay.
- Campaign QA is inconsistent when done quickly.
- There is no default audit trail showing which contacts were accepted, rejected, imported, and used for the campaign.

The workflow is a good fit for automation because the structure is stable, the APIs exist, and the human judgment is concentrated in a few review points rather than every mechanical step.

## What I Built

I built `helium-edm`, a Python CLI agent for end-to-end EDM campaign preparation. The sharper framing after expert review is: **Helium EDM Intake Agent turns a messy client handoff into a verified, client-branded, Sendy-ready campaign draft with an auditable processing plan.**

The tool:

1. Provides a password-protected dashboard for the operator.
2. Lets the operator choose the Helium client.
3. Accepts uploaded EDM HTML and contact list CSV files.
4. Evaluates the uploaded files and classifies each one as contact list, EDM HTML, header, footer, notes, or unknown.
5. Builds a processing plan.
6. Shows EmailListVerify and Sendy configuration status.
7. Adds the correct client-specific header and footer to the EDM.
8. Normalizes and deduplicates emails.
9. Rejects malformed addresses before paid verification.
10. Verifies emails through EmailListVerify and separates accepted, rejected, quarantined, and suppressed outcomes.
11. Converts the final EDM HTML into a plain-text fallback.
12. Runs deterministic deliverability checks and an AI preflight review on the EDM content.
13. Records an operator consent attestation for the uploaded list.
14. Imports accepted contacts into Sendy when enabled.
15. Creates a Sendy draft, scheduled campaign, or send-now campaign.
16. Generates partner invoice artifacts for the billing handoff.
17. Writes an auditable run report.

The tool is designed to run in `--dry-run` mode for demonstration without real API keys, and in live mode when Sendy, EmailListVerify, and optional OpenAI credentials are provided. In the normal workflow, it creates a Sendy campaign draft on the Helium Sendy instance so the campaign is ready for final review before sending.

I also added a client template layer. Each Helium client can have their own saved header and footer in `templates/clients/<client-slug>/`, so the agent can package the campaign in the correct brand wrapper without rebuilding it each time.

I then added a non-secret client config layer in `config/clients/<client-slug>.json` so the dashboard can default the Sendy brand, optional Sendy list selection, sender name, from email, reply-to, and template paths for each client.

I later tightened this from sample configuration into live Helium Sendy wiring: the dashboard client dropdown now shows the real Sendy brands configured for Helium, and choosing a client preselects the matching Sendy brand, uses that brand's sender defaults, and loads that brand's live Sendy lists. The operator can select multiple lists for the same campaign.

Important safety boundary: this is an opted-in EDM operations and list hygiene tool. EmailListVerify is used to check address quality, not consent. Helium's operating assumption is that uploaded client lists have already provided consent, and the tool records an operator attestation for audit. The default outcome is a Sendy draft for human review, not an automatic blast.

## Tools Chosen

### Python CLI

I chose a CLI because the real workflow is file-based and operational. The client sends files; I need to process them repeatably. A CLI also makes the workflow easy to demo, script, and run from a laptop without building a full web app.

The CLI acts like a small agent loop:

1. Assess: inspect each uploaded file.
2. Plan: decide which file is the list, EDM, header, footer, or notes.
3. Act: render the final email with the selected client wrapper, verify contacts, upload the list, and create the Sendy campaign.
4. Report: write an audit trail for review.

### Sendy API

Sendy exposes HTTP POST endpoints for subscribing contacts and creating campaigns. That means the campaign setup step can be automated instead of performed through the dashboard.

Relevant API surfaces:

- Subscribe contact: `/subscribe`
- Create campaign: `/api/campaigns/create.php`

Sources:

- https://sendy.co/api
- https://sendy.co/api?app_path=https://helium.sg
- https://sendy.co/api#third-party-resources-integrations

### EmailListVerify API

EmailListVerify supports API-based email validation. For this prototype, I implemented one-by-one verification because it is simple to reason about in a short build window. Their official PHP repo also describes a bulk flow: upload a file, poll by file ID, then download reports when complete.

Sources:

- https://api.emaillistverify.com/api-doc
- https://emaillistverify.com/api
- https://github.com/EmailListVerify-com/Emaillistverify-Php

### OpenAI API

I used AI only where judgment is useful: campaign preflight. The deterministic code handles list processing and API calls. The AI reviews the EDM content and returns structured JSON with:

- Suggested subject line
- Deliverability or compliance risk flags
- Concrete fixes
- Plain-text campaign summary

This separation keeps the dangerous parts deterministic while still using AI to improve review quality.

## Build Process

### Progress Log

The build evolved in a few small steps:

1. I first built the deterministic campaign pipeline: parse the contact CSV, dedupe, verify, generate plain text, import contacts, and create a Sendy draft.
2. I added an intake-agent mode so I can drop mixed files into a folder and let the tool classify which file is the contact list, which file is the EDM HTML, and which file contains campaign notes.
3. I added EDM rendering so the campaign body is wrapped with a header and footer before Sendy campaign creation.
4. I then changed the wrapper logic from one global Helium header/footer to per-client templates, because each Helium client needs their own branded header and footer.
5. I added a password-protected dashboard so the operator can choose a client, upload files, see verification/Sendy service status, run the workflow, and review outputs without using CLI flags directly.
6. I added per-client Sendy config so choosing a client can auto-fill brand/list/sender defaults while keeping API keys in `.env`.
7. I connected the dashboard to live Sendy brand/list discovery so the operator can select the right destination instead of copying IDs by hand.

The current template convention is:

```text
templates/clients/<client-slug>/header.html
templates/clients/<client-slug>/footer.html
```

The agent uses `--client <client-slug>` to select the correct wrapper. If the uploaded intake folder contains a one-off `header.html` or `footer.html`, that uploaded file overrides the saved client template for that campaign.

### Expert Review

I reviewed the project through four lenses: critical thinking, design thinking, email operations, and UI/UX demo storytelling. The consolidated recommendations were:

- Position the project as an operator workflow agent, not a bulk email tool.
- Make the agent's reasoning visible through file classifications, confidence, processing plan, and audit artifacts.
- Keep draft creation as the default; require explicit human intent for scheduling or sending.
- Treat email verification as list hygiene, not proof of consent.
- Add or document safety checks for unsubscribe links, sender identity, plain-text fallback, HTTPS links, and client-specific footer/legal text.
- Show before/after artifacts in the demo: raw intake folder, rendered EDM, verified contact CSV, and Sendy draft payload.
- Record every run so the operator can answer what was accepted, rejected, rendered, imported, and prepared.

The review changed how I describe the tool. The strongest claim is not "I automated sending emails." It is: "I automated the repetitive operational handling required to safely prepare a client EDM campaign for review in Sendy."

### First Approach

My first prompt idea was too broad:

```text
Clean this email list and summarize this EDM.
```

That produced generic output. It could summarize the campaign, but it did not map well to the real workflow. It did not tell me:

- Which rows were skipped
- Which emails were accepted or rejected
- What would be imported into Sendy
- Whether the campaign was ready to send
- What artifacts I could audit later

### Adjustment

I changed the design from a general-purpose "AI campaign assistant" into a deterministic operations pipeline with a narrow AI review step.

The better structure was:

1. Code handles CSV parsing, dedupe, validation, API calls, and reporting.
2. A file-intake agent classifies the uploaded files and creates a plan.
3. The email renderer selects the right client-specific header and footer before campaign creation.
4. AI reviews the final EDM content and returns strict JSON.
5. The final run report combines deterministic results, agent planning, Sendy actions, and AI feedback.

This was the important design shift: AI is not responsible for deciding who gets emailed or silently mutating the list. It is responsible for preflight judgment and copy suggestions.

### Debugging Moment

One real integration issue appeared during live Sendy testing. Brand discovery worked, but one brand's list endpoint returned JSON-like data containing raw tab/control characters inside a list name. Python's strict JSON parser rejected that response, even though the data was otherwise usable.

I adjusted the shared Sendy response parser to try strict JSON first, then retry with Python's tolerant JSON mode for Sendy's malformed-but-readable responses. I added a regression test for that exact case and re-ran live discovery against all Helium Sendy brands. The dashboard can now load all four brands and their lists.

## Prompt Used

The AI preflight prompt used by the tool is:

```text
You are helping Helium.sg prepare an EDM campaign received from a client.
Return strict JSON with:
- suggested_subject: one concise subject line
- risk_flags: array of deliverability/compliance/content risks
- fixes: array of concrete edits to make before sending
- plain_text_summary: 4-6 sentence plain-text fallback summary

Client note:
{client_note}

Current subject:
{subject}

HTML:
{html}
```

This prompt evolved from a loose summarization request into a structured preflight reviewer. The JSON output makes it easy to store results in the run report and inspect them before sending.

## Working Demo

The repo includes realistic sample inputs in `samples/intake`:

- `samples/intake/client-list-may.csv`
- `samples/intake/export-growth-edm.html`
- `samples/intake/campaign-notes.txt`

The demo command is:

```bash
helium-edm \
  --input-dir samples/intake \
  --client export-partner \
  --list-id demo_list_id \
  --brand-id demo_brand_id \
  --import-to-sendy \
  --create-campaign \
  --dry-run
```

In dry-run mode, the tool does not call external APIs. It shows the exact Sendy requests it would make and writes the same output files.

The dashboard demo is:

```bash
helium-edm-dashboard
```

Then open `http://127.0.0.1:5001`, sign in with `DASHBOARD_PASSWORD`, choose `export-partner`, upload the sample CSV and HTML, record the consent attestation, leave dry run enabled, and process the campaign. When Sendy is configured, the dashboard can load Sendy brands and lists, then auto-fill the selected brand ID and list ID. The dashboard also shows whether EmailListVerify and Sendy are configured, then links to the generated run report, rendered EDM, verified CSV, and JSON audit trail.

If a client config exists, selecting the client also auto-fills known brand/list/sender defaults before processing.

For deployment, I prepared and launched the project on AWS Elastic Beanstalk with a production WSGI entrypoint, `gunicorn`, a `Procfile`, deployment packaging script, `.ebignore`, and a `/healthz` endpoint for health checks. The running AWS environment is `helium-edm-ops-prod` in `ap-southeast-1`, fronted by CloudFront with ACM SSL at `https://demo.helium.sg`.

Example output summary:

```json
{
  "contacts_read": 3,
  "accepted": 2,
  "rejected": 1,
  "warnings": 2,
  "output_dir": "runs/latest"
}
```

The sample list intentionally includes:

- Two valid contacts
- One duplicate
- One malformed email
- One invalid email

The tool removes the duplicate and malformed row before verification, accepts the two valid contacts, rejects the invalid contact, adds the selected client's header and footer to the EDM, and generates a Sendy campaign payload.

## Outputs

Each run writes the main campaign, audit, and billing artifacts:

### `runs/latest/verified_contacts.csv`

This file records every usable contact after local validation, including:

- Email
- Name
- Verification status
- Disposition: accepted, rejected, or quarantine
- Suppression reason when applicable
- Whether the contact was accepted
- Reason

### `runs/latest/run_report.json`

This file records:

- Summary counts
- File classifications
- Processing plan
- Selected client wrapper
- Input warnings
- Accepted/rejected/quarantined verification counts
- Suppressed contact counts
- AI preflight result
- Deterministic deliverability checks
- Sendy import results
- Campaign creation result
- Consent attestation and basis
- Invoice metadata

### `runs/latest/invoice_rows.csv`

This file is designed for my existing lightweight invoicing process: import or paste the rows into a Google Sheet, then export that sheet to PDF. It includes the partner, client, campaign, counts, selected billing rates, line items, and total.

I aligned it to my current Helium tracker/PDF format: invoice ID, date, period, client, setup fee, sending fee per email, cleaning fee per email, `Setup Cost`, `Email Cleaning`, `Email Sending`, `DISCOUNT`, `Total Cost`, `Commission`, and `PAYABLE`.

### `runs/latest/invoice.html`

This is a printable invoice artifact for quick review. The Google Sheet remains the source for final PDF formatting, but the run now produces the structured billing data automatically.

### `runs/latest/rendered_edm.html`

This is the final campaign HTML after the uploaded EDM body has been wrapped with the selected client's header and footer.

This is useful because email campaign operations need traceability. If the client asks what happened to a list, I can show exactly which contacts were skipped, rejected, accepted, and prepared for Sendy.

## Expert Recommendations Recorded

The full review is recorded in `EXPERT_REVIEW.md`. The most important recommendations to carry into the video are:

1. Start with the messy client handoff, not the code.
2. Show the agent's processing plan before showing the final campaign payload.
3. Say explicitly that the agent creates a review-ready draft and does not silently send.
4. Show the client-specific header/footer to make the multi-client workflow obvious.
5. Show `run_report.json`, `rendered_edm.html`, `verified_contacts.csv`, and `invoice_rows.csv` as the audit trail from campaign prep to billing.

I implemented the highest-impact UI recommendation as part of the dashboard work: every dashboard run now writes a human-readable `runs/latest/index.html` report that summarizes the run for screen recording. The report shows campaign readiness, artifact links, verification counts, consent attestation, campaign metadata, processing plan, file assessment, warnings, deliverability checks, Sendy campaign result, and invoice artifacts.

## Roadmap

I am using GitHub Issues as the active roadmap and debugging tracker:

```text
https://github.com/xyufeng/helium-edm-ops/issues
```

The first issue set covers dashboard polish, consent basis, per-client Sendy config, suppression lists, EmailListVerify status policy, deterministic deliverability checks, live Sendy discovery debugging, and automated dashboard smoke tests. That first roadmap pass is complete.

Later roadmap work added production client wiring, multi-list selection, explicit live/dry-run execution mode, tracker-aligned invoice artifacts, and AWS Elastic Beanstalk deployment packaging.

## Real Usage

After setting environment variables in `.env`, the real command for creating a Sendy draft is:

```bash
helium-edm \
  --input-dir client_uploads \
  --client CLIENT_SLUG \
  --confirm-consent \
  --consent-basis provided_client_consent \
  --list-id YOUR_SENDY_LIST_ID \
  --brand-id YOUR_SENDY_BRAND_ID \
  --import-to-sendy \
  --create-campaign
```

For a scheduled send:

```bash
helium-edm \
  --input-dir client_uploads \
  --client CLIENT_SLUG \
  --confirm-consent \
  --consent-basis provided_client_consent \
  --list-id YOUR_SENDY_LIST_ID \
  --import-to-sendy \
  --create-campaign \
  --send-campaign \
  --schedule-date-time "May 15, 2026 10:00am" \
  --schedule-timezone "Asia/Singapore"
```

## Working Tool Link

Repository/tool location:

```text
https://github.com/xyufeng/helium-edm-ops
```

AWS deployment artifact:

```text
Elastic Beanstalk application: helium-edm-ops
Region: ap-southeast-1
Environment: helium-edm-ops-prod
Uploaded/deployed version: 97f8733-20260506144713
HTTPS URL: https://demo.helium.sg
Health check: https://demo.helium.sg/healthz
CloudFront distribution: E3I851YFE9Y1Y7
```

Main files:

- `helium_edm/cli.py`
- `README.md`
- `EXPERT_REVIEW.md`
- `.env.example`
- `helium_edm/dashboard.py`
- `config/clients/default.json`
- `config/clients/export-partner.json`
- `templates/clients/default/header.html`
- `templates/clients/default/footer.html`
- `templates/clients/export-partner/header.html`
- `templates/clients/export-partner/footer.html`
- `samples/intake/client-list-may.csv`
- `samples/intake/export-growth-edm.html`

## What Changed

Before this tool, the workflow required manual cleanup, manual verification, manual Sendy import, manual campaign creation, and inconsistent QA.

After this tool, the workflow becomes:

1. Put the uploaded client files into an intake folder.
2. Run one command with the correct `--client` slug.
3. Review the generated report.
4. Confirm the Sendy draft or scheduled send.

The transformation is not just speed. The bigger improvement is reliability: every campaign follows the same operational path, and every run leaves behind a record.
