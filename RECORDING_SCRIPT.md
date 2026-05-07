# Helium EDM Intake Agent Recording Script

Recording target: 5 to 10 minutes  
Recommended pace: about 7 minutes  
Recording date: May 7, 2026  

Links to keep open:

- Presentation: https://demo.helium.sg/presentation
- Dashboard: https://demo.helium.sg
- Repository: https://github.com/xyufeng/helium-edm-ops

## Before Recording

Open these tabs before you start:

1. `https://demo.helium.sg/presentation`
2. `https://demo.helium.sg`
3. The GitHub repo or local `STRIPE_TAKE_HOME.md`

Keep the dashboard run in dry-run mode unless you intentionally want to call live APIs during the recording.

## 0:00 - 0:45 Opening

Hi, I'm Yufeng. For this take-home, I built an AI-powered EDM intake agent for a real workflow from my side business, helium.sg.

The recurring workflow is simple to describe but painful to execute. A partner in China sends me email lists and EDM creative. I need to clean and verify the list, apply the right client header and footer, upload accepted recipients into Sendy, create a campaign draft, and generate billing records for my partner.

Before this project, the work was spread across files, spreadsheets, EmailListVerify, Sendy, manual HTML checks, and invoicing. I wanted to turn that into one operator workflow.

## 0:45 - 1:45 Workflow Today

The old process looked like this:

First, I receive the files from the client. Usually that means an EDM HTML file and a recipient list, often as CSV or XLSX. Sometimes the workbook has multiple sheets, and only one sheet is actually the contact list.

Second, I inspect the list manually. I check whether it has an email column, remove malformed rows and duplicates, and make sure the list is consented.

Third, I run verification through EmailListVerify. The result is not just pass or fail. Some statuses should be accepted, some rejected, and some quarantined for review.

Fourth, I wrap the EDM body with the correct client-specific header and footer. Each client or brand on helium.sg can have its own sender defaults and HTML wrapper.

Fifth, I upload the accepted contacts into Sendy, select the right brand and one or more Sendy recipient lists, and create a campaign draft.

Finally, I need to invoice the partner. I already use a lightweight Google Sheet to export invoices to PDF, so I wanted the run to produce invoice rows automatically.

The pain is not any one task. The pain is that every step is small, repetitive, and easy to get subtly wrong.

## 1:45 - 2:45 Product Shape

I designed the tool around four steps: assess, plan, act, and report.

Assess means the agent looks at the uploaded files and decides what each file is: contact list, EDM HTML, notes, suppression list, or unknown.

Plan means it creates a processing sequence before acting. For example, if the uploaded contact file is an XLSX workbook, it converts every non-empty sheet into a CSV first. Then it selects the generated CSV that actually contains email addresses.

Act means the deterministic pipeline does the operational work: local validation, deduplication, suppression handling, EmailListVerify verification, Sendy list upload, Sendy campaign draft creation, and invoice artifact generation.

Report means the run leaves behind a full audit trail: verified contacts CSV, rendered EDM HTML, run report JSON, dashboard summary, Sendy results, and invoice rows.

That distinction mattered to me. I did not want an agent that silently sends emails. I wanted an agent that makes a review-ready campaign draft and shows its work.

## 2:45 - 3:45 Building Process

I started with the CLI because the source workflow is file-based. A CLI made it easier to build repeatable behavior and test the pipeline with sample inputs.

Then I added the dashboard because the real operator workflow starts with file upload, not command flags. The dashboard lets me choose a client, load Sendy brands and lists, upload the EDM and contact list, paste campaign notes, confirm consent, and choose dry-run or live mode.

The prompt evolved as I built. My early prompt was basically a loose HTML reviewer. That was not enough. I changed it into a structured preflight reviewer that returns JSON with:

- whether the email is ready to send
- blocking issues
- warnings
- subject suggestions
- plain-text fallback summary

The important design choice was to use AI only where judgment helps, and use deterministic code where correctness matters. File conversion, list validation, verification status handling, suppression, Sendy API calls, and invoice calculations are all deterministic.

## 3:45 - 4:30 What Did Not Work

One thing that did not work cleanly was Sendy API discovery.

I expected Sendy brand and list endpoints to return clean JSON every time. In practice, the response sometimes included control characters that made normal JSON parsing fail.

So I adjusted the parser. Instead of assuming ideal JSON, the Sendy client now sanitizes control characters, detects error responses, and gives the dashboard a clear status. That turned the Sendy section from a demo form into a real production wiring check.

A second adjustment came from the actual client workflow. I originally treated campaign notes as an uploaded notes file. But for dashboard use, that was awkward. Campaign notes are now a textarea with placeholder instructions, and the dashboard saves them into the run folder as `campaign-notes.txt` so the same planner can still inspect them.

## 4:30 - 6:30 Working Demo

Now I'll show the working tool.

In the dashboard, I choose a client. The client config controls the Sendy brand defaults, sender defaults, and client-specific header and footer.

Next, I load Sendy brands and lists. The tool can upload accepted contacts into multiple Sendy lists for the selected brand.

For the contact list, I can upload either CSV or XLSX. This matters because in the real workflow, a workbook is often provided, and it may have multiple sheets. The agent converts each non-empty worksheet into its own CSV, then classifies the generated CSVs and chooses the one with email addresses.

For the EDM, I upload the HTML file. Then I paste campaign notes into the textarea. For example:

```text
Subject: Invitation to the May buyer briefing
Audience: Consented Singapore importers and distributors
Use lists: Main recipients + event follow-up
Special handling: Exclude previous unsubscribes. Create draft only.
Invoice period: May 2026
```

I confirm the consent attestation. In this business workflow, all uploaded lists are client-provided consented lists, and the tool records that basis in the audit trail.

For recording, I keep dry-run mode enabled. Dry-run mode does not call external APIs, but it produces the same artifacts and shows the Sendy requests it would make.

After processing, the dashboard links to the run report. The report shows accepted, rejected, quarantined, and suppressed contacts. It also shows the file assessment, processing plan, consent attestation, deliverability checks, campaign result, and invoice artifacts.

I can open the rendered EDM to verify the client header and footer were added. I can open the verified contacts CSV to see exactly why each contact was accepted, rejected, quarantined, or suppressed. And I can open invoice rows, which are aligned to my current Google Sheet invoice workflow.

The end state is that I have a campaign on helium.sg ready to review and send, plus the billing records for my partner.

## 6:30 - 7:30 Outputs And Audit Trail

The key output is not just the Sendy draft. The key output is traceability.

Every run writes:

- `verified_contacts.csv`
- `rendered_edm.html`
- `run_report.json`
- a human-readable dashboard report
- `invoice_rows.csv`
- `invoice.html`
- `invoice.json`

That means if a client asks what happened to a list, I can show exactly what the tool did. If an email was skipped, the reason is recorded. If a contact was suppressed, the suppression reason is recorded. If Sendy upload was simulated or live, the mode is recorded.

This is important because EDM operations are easy to automate badly. The goal here is not just speed. The goal is reliable, inspectable operations.

## 7:30 - 8:15 Deployment And Links

The working tool is deployed at:

```text
https://demo.helium.sg
```

The health check is:

```text
https://demo.helium.sg/healthz
```

The presentation I am using for this recording is:

```text
https://demo.helium.sg/presentation
```

The prompts, code, and documentation are in the GitHub repository:

```text
https://github.com/xyufeng/helium-edm-ops
```

The app is deployed on AWS Elastic Beanstalk behind CloudFront and ACM SSL. Secrets are stored as environment variables and are not committed to the repo.

## 8:15 - 8:45 Close

This take-home was useful because it forced me to build around a real operational workflow, not an abstract AI demo.

The result is a small forward-deployed AI tool: it starts from messy client handoff, applies client-specific business rules, uses APIs where the operator used to copy-paste, records its decisions, and leaves a human with a Sendy campaign draft ready to review.

That is the transformation I wanted: not just faster email ops, but a more dependable operating system for helium.sg campaign work.

## If You Need To Shorten

Cut these sections first:

1. Shorten the old workflow section.
2. Skip the second failure about campaign notes.
3. Show only three output artifacts: run report, rendered EDM, verified contacts CSV.

## If You Need To Stretch

Add detail here:

1. Show the generated worksheet CSVs from an XLSX upload.
2. Open `run_report.json` and point out the processing plan.
3. Open `invoice_rows.csv` and connect it to the Google Sheet PDF invoice workflow.
