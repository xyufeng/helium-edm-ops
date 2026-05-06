# Helium EDM Ops

This is a small CLI automation for the real Helium.sg workflow:

1. Receive a client email list and EDM HTML.
2. Classify uploaded files and plan what to do with each file.
3. Add the correct client-specific header and footer to the EDM.
4. Normalize, dedupe, and validate contacts.
5. Verify emails with EmailListVerify.
6. Generate an AI preflight: subject suggestion, risks, fixes, and plain-text fallback.
7. Import clean contacts into Sendy.
8. Create, schedule, or send the Sendy campaign.

## Why this works for the Stripe take-home

The workflow is real and recurring: each client campaign requires list cleanup, manual upload to EmailListVerify, manual download, manual Sendy import, HTML/plain-text preparation, and campaign creation. The tool compresses that into one command with an auditable run report.

After expert review, the recommended positioning is: **Helium EDM Intake Agent turns a messy client handoff into a verified, client-branded, Sendy-ready campaign draft with an auditable processing plan.**

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Fill in `.env` with your Sendy, EmailListVerify, dashboard password, and optional OpenAI keys.

For Helium, Sendy's API docs are generated with `app_path=https://helium.sg`, so `SENDY_BASE_URL` should be:

```text
https://helium.sg
```

## Dashboard

Start the password-protected dashboard:

```bash
helium-edm-dashboard
```

Then open:

```text
http://127.0.0.1:5001
```

The dashboard lets the operator:

- Sign in with `DASHBOARD_PASSWORD`
- Choose a real Helium Sendy client/brand
- Use brand sender and reply-to defaults from client config
- Upload the EDM HTML and contact list CSV
- See EmailListVerify and Sendy configuration status
- Fetch Sendy brands and lists automatically when the Sendy API key is configured
- Choose one or more Sendy lists by name after discovery
- Record consent attestation for the uploaded list
- Run the verification/list hygiene workflow
- Separate accepted, rejected, and quarantined verification outcomes
- Apply an optional suppression list before Sendy upload
- Run deterministic EDM deliverability checks
- Upload accepted contacts into every selected Sendy list
- Create a Sendy draft campaign
- Generate invoice artifacts for the partner billing handoff
- Open the generated report, rendered EDM, verified CSV, invoice, and JSON audit trail

Dry run is the default execution mode so the operator can inspect the plan and artifacts before touching live APIs. Live API mode uses EmailListVerify for verification and Sendy for contact import/draft creation.

Live Sendy import or campaign creation requires the operator to confirm that the uploaded list has provided consent. The tool records that attestation in the run report; it does not try to infer consent from EmailListVerify.

Production dashboard settings:

- Set `HELIUM_ENV=production` to require an explicit `DASHBOARD_PASSWORD` and `FLASK_SECRET_KEY`.
- Set `MAX_UPLOAD_MB` to control upload limits. The default is `25`.
- Set `DASHBOARD_COOKIE_SECURE=true` when serving over HTTPS.

AWS deployment:

- The repo includes `Procfile`, `requirements.txt`, `helium_edm/wsgi.py`, `.ebignore`, and `scripts/deploy_aws_eb.sh` for Elastic Beanstalk.
- Configure AWS credentials with `aws login`, then see `docs/aws-elastic-beanstalk.md`.
- Current AWS environment: `helium-edm-ops-prod` at `http://helium-edm-ops-prod.eba-tfa3sapy.ap-southeast-1.elasticbeanstalk.com`.

## Agent-style demo without API keys

Drop mixed files into an intake folder, then let the agent classify and process them:

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

The agent will:

- Identify the CSV contact list
- Identify the EDM HTML
- Read the subject from campaign notes
- Apply `templates/clients/export-partner/header.html` and `templates/clients/export-partner/footer.html`
- Write `runs/latest/rendered_edm.html`
- Verify/import the accepted contacts
- Create a Sendy draft payload

## Explicit-file demo without API keys

```bash
helium-edm \
  --contacts samples/client_list.csv \
  --html samples/client_edm.html \
  --client export-partner \
  --subject "Private briefing for export growth teams" \
  --client-note "Client is targeting overseas-facing Chinese exporters." \
  --list-id demo_list_id \
  --brand-id demo_brand_id \
  --import-to-sendy \
  --create-campaign \
  --dry-run
```

Outputs:

- `runs/latest/verified_contacts.csv`
- `runs/latest/run_report.json`
- `runs/latest/rendered_edm.html`
- `runs/latest/invoice_rows.csv`
- `runs/latest/invoice.html`

Verification policy:

- Accepted statuses are imported into Sendy. Default: `ok`.
- Quarantine statuses are held for human review and not imported. Defaults include `unknown`, `risky`, `catch_all`, and `accept_all` variants.
- Everything else is rejected.

Raw EmailListVerify status and the final disposition are both written to `verified_contacts.csv`.

Suppression lists:

- Pass `--suppression-list path/to/suppression.csv` or upload a suppression CSV/JSON in the dashboard.
- Suppression CSVs should include `email` and optional `reason` columns.
- Suppressed contacts are not uploaded to Sendy and are reported with disposition `suppressed`.

Deliverability checks:

- Unsubscribe link/text presence
- HTTPS links
- Plain-text fallback
- Image alt text
- HTML size
- Required footer text from client config

Blocking deliverability errors stop live Sendy campaign creation. Dry-runs still produce the report for review.

## Real Sendy draft

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

## Real scheduled send

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

## Prompt used by the AI preflight

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

## Demo story

The first naive version was "summarize this EDM and clean this list." It was too vague: it produced generic marketing copy but did not tell me whether the campaign was ready to send. The better version treats the AI as a preflight reviewer inside a deterministic pipeline: first the code dedupes and verifies emails, then AI reviews only the campaign content and returns structured JSON that the CLI can store in the run report.

## Client templates

Each Helium client can have their own wrapper:

```text
templates/clients/<client-slug>/header.html
templates/clients/<client-slug>/footer.html
```

Use `--client <client-slug>` to select the wrapper. Uploaded `header.html` or `footer.html` files in the intake folder still override the saved client templates for one-off campaigns.

## Client config

Each Helium client can also have non-secret Sendy defaults:

```text
config/clients/<client-slug>.json
```

Supported fields:

- `display_name`
- `sendy_brand_id`
- `sendy_brand_name`
- `sendy_list_id` for an optional default list, or comma-separated default lists
- `from_name`
- `from_email`
- `reply_to`
- `header_path`
- `footer_path`
- `required_footer_text`
- `dashboard_visible`

Do not store API keys in client config. Keep secrets in `.env`.

The production dashboard hides sample configs and shows the live Helium Sendy brands configured in `config/clients`: ISLE, CIIE, Test, and China Security Association. Choosing a client preselects its Sendy brand, loads the live list options for that brand, and uses the configured brand sender defaults without requiring per-campaign edits.

Invoice artifacts mirror the existing Helium tracker format: invoice ID, date, period, client, setup cost per campaign, sending cost per email, cleaning cost per email, `Setup Cost`, `Email Cleaning`, `Email Sending`, `DISCOUNT`, `Total Cost`, `Commission`, and `PAYABLE`.

## Expert review

See `EXPERT_REVIEW.md` for the critical thinking, design thinking, email operations, and UI/UX review. The main recommendations are:

- Frame this as opted-in EDM operations and list hygiene, not bulk outbound automation.
- Keep Sendy draft creation as the default safety boundary.
- Make the processing plan and audit artifacts visible in the demo.
- Add consent basis, suppression-list handling, client config, and a human-readable run report as next improvements.

## Roadmap and debugging

GitHub Issues are the roadmap and debugging tracker for this project:

```text
https://github.com/xyufeng/helium-edm-ops/issues
```

Use Issues to track new features, safety improvements, Sendy/EmailListVerify integration work, dashboard polish, and bugs found during live testing.

The first roadmap pass is complete. Live Sendy discovery now loads the Helium account's brands and lists from the dashboard; the parser handles Sendy list responses that contain raw control characters inside list names.

## Tests

Run the smoke test suite:

```bash
pytest
```

The dashboard smoke test covers password login, sample file upload, dry-run processing, suppression handling, and generated artifacts.

The Sendy parser regression test covers malformed-but-readable JSON returned by Sendy's list endpoint.

The generated human-readable report at `runs/latest/index.html` includes:

- Execution mode and external action modes
- Campaign readiness status
- Links to rendered EDM, verified CSV, and JSON report
- Links to Google-Sheet-ready invoice rows and printable invoice
- Accepted/rejected/quarantined/suppressed/imported counts
- Consent attestation
- Campaign metadata
- Processing plan
- File assessment
- Input warnings
- Deliverability checks
- Sendy campaign result

## Sources

- Sendy API: https://sendy.co/api
- Helium Sendy API path: https://sendy.co/api?app_path=https://helium.sg
- Sendy third-party resources and integrations: https://sendy.co/api#third-party-resources-integrations
- EmailListVerify API docs: https://api.emaillistverify.com/api-doc
- EmailListVerify API: https://emaillistverify.com/api
- EmailListVerify bulk API flow via official PHP repo: https://github.com/EmailListVerify-com/Emaillistverify-Php
