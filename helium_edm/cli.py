from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import textwrap
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Contact:
    email: str
    name: str = ""
    source_row: int = 0


@dataclass(frozen=True)
class VerifiedContact:
    email: str
    name: str
    status: str
    accepted: bool
    reason: str


@dataclass(frozen=True)
class FileAssessment:
    path: str
    role: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class IntakePlan:
    client: str
    contacts_path: str
    edm_html_path: str
    header_path: str
    footer_path: str
    subject: str
    actions: list[str]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize_email(value: str) -> str:
    return value.strip().lower()


def read_contacts(csv_path: Path, email_column: str = "email", name_column: str = "name") -> tuple[list[Contact], list[str]]:
    warnings: list[str] = []
    contacts: list[Contact] = []

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")

        fields = {field.strip().lower(): field for field in reader.fieldnames}
        email_field = fields.get(email_column.lower())
        name_field = fields.get(name_column.lower())

        if not email_field:
            possible = ", ".join(reader.fieldnames)
            raise ValueError(f"Email column '{email_column}' not found. Available columns: {possible}")

        seen: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            email_address = normalize_email(row.get(email_field, ""))
            name = (row.get(name_field, "") if name_field else "").strip()

            if not email_address:
                warnings.append(f"row {row_number}: blank email skipped")
                continue
            if not EMAIL_RE.match(email_address):
                warnings.append(f"row {row_number}: malformed email skipped: {email_address}")
                continue
            if email_address in seen:
                warnings.append(f"row {row_number}: duplicate skipped: {email_address}")
                continue

            seen.add(email_address)
            contacts.append(Contact(email=email_address, name=name, source_row=row_number))

    return contacts, warnings


def html_to_plain_text(html_text: str) -> str:
    body = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html_text)
    body = re.sub(r"(?i)<br\s*/?>", "\n", body)
    body = re.sub(r"(?i)</p\s*>", "\n\n", body)
    body = TAG_RE.sub(" ", body)
    body = html.unescape(body)
    lines = [WS_RE.sub(" ", line).strip() for line in body.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def body_inner_html(html_text: str) -> str:
    match = re.search(r"(?is)<body[^>]*>(.*?)</body>", html_text)
    if match:
        return match.group(1).strip()
    return html_text.strip()


def title_from_html(html_text: str) -> str:
    for pattern in (r"(?is)<title[^>]*>(.*?)</title>", r"(?is)<h1[^>]*>(.*?)</h1>"):
        match = re.search(pattern, html_text)
        if match:
            value = html_to_plain_text(match.group(1))
            if value:
                return value
    return ""


def render_helium_email(edm_html: str, header_html: str, footer_html: str) -> str:
    body = "\n".join(part for part in [body_inner_html(header_html), body_inner_html(edm_html), body_inner_html(footer_html)] if part)
    return (
        '<!doctype html>\n'
        '<html>\n'
        '  <head>\n'
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '  </head>\n'
        '  <body style="margin:0; padding:0; background:#f6f7f9;">\n'
        '    <div style="max-width:680px; margin:0 auto; background:#ffffff; font-family:Arial, sans-serif; color:#111827;">\n'
        f'{body}\n'
        '    </div>\n'
        '  </body>\n'
        '</html>\n'
    )


def slugify_client(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "default"


def resolve_client_templates(client: str, header: Path | None, footer: Path | None) -> tuple[Path, Path]:
    client_slug = slugify_client(client)
    client_dir = Path("templates") / "clients" / client_slug
    client_header = client_dir / "header.html"
    client_footer = client_dir / "footer.html"

    header_path = header or client_header
    footer_path = footer or client_footer

    if not header and not client_header.exists():
        header_path = Path("templates/clients/default/header.html")
    if not footer and not client_footer.exists():
        footer_path = Path("templates/clients/default/footer.html")

    return header_path, footer_path


def csv_has_email_column(path: Path) -> tuple[bool, str]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            normalized = [item.strip().lower() for item in header]
            if "email" in normalized or "email address" in normalized or "e-mail" in normalized:
                return True, "CSV header contains an email column."
            sample = "\n".join(",".join(row) for _, row in zip(range(8), reader))
            if re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", sample):
                return True, "CSV sample contains email addresses."
    except UnicodeDecodeError:
        return False, "Could not read as UTF-8 CSV."
    except OSError as exc:
        return False, f"Could not inspect CSV: {exc}"
    return False, "CSV does not look like a contact list."


def classify_file(path: Path) -> FileAssessment:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix == ".csv":
        has_email, reason = csv_has_email_column(path)
        if has_email:
            return FileAssessment(str(path), "contacts", 0.95, reason)
        return FileAssessment(str(path), "unknown", 0.2, reason)

    if suffix in {".html", ".htm"}:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return FileAssessment(str(path), "unknown", 0.0, f"Could not read file: {exc}")

        if "header" in name:
            return FileAssessment(str(path), "header", 0.95, "Filename indicates this is the header.")
        if "footer" in name:
            return FileAssessment(str(path), "footer", 0.95, "Filename indicates this is the footer.")
        if any(token in content.lower() for token in ("<html", "<body", "<table", "<p", "<h1")):
            return FileAssessment(str(path), "edm_html", 0.8, "HTML content looks like the campaign body.")
        return FileAssessment(str(path), "unknown", 0.3, "HTML file did not look like an EDM body.")

    if suffix in {".json", ".txt", ".md"}:
        return FileAssessment(str(path), "notes", 0.65, "Text-like file that may contain campaign context.")

    return FileAssessment(str(path), "unknown", 0.1, f"Unsupported file extension: {suffix or '(none)'}")


def extract_subject_from_notes(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        for key in ("subject", "subject_line", "campaign_subject", "title"):
            value = data.get(key) if isinstance(data, dict) else ""
            if isinstance(value, str) and value.strip():
                return value.strip()

    for line in text.splitlines():
        match = re.match(r"(?i)\s*(subject|subject line|title)\s*:\s*(.+?)\s*$", line)
        if match:
            return match.group(2).strip()
    return ""


def choose_assessment(assessments: list[FileAssessment], role: str) -> FileAssessment | None:
    matches = [item for item in assessments if item.role == role]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.confidence, reverse=True)[0]


def build_intake_plan(input_dir: Path, subject: str, client: str, default_header: Path, default_footer: Path) -> tuple[IntakePlan, list[FileAssessment]]:
    if not input_dir.exists():
        raise ValueError(f"Input directory does not exist: {input_dir}")

    files = [path for path in sorted(input_dir.iterdir()) if path.is_file()]
    assessments = [classify_file(path) for path in files]

    contacts = choose_assessment(assessments, "contacts")
    edm = choose_assessment(assessments, "edm_html")
    header = choose_assessment(assessments, "header")
    footer = choose_assessment(assessments, "footer")
    notes = [Path(item.path) for item in assessments if item.role == "notes"]

    if not contacts:
        raise ValueError("No contact CSV found. Add a CSV with an email column to the intake folder.")
    if not edm:
        raise ValueError("No EDM HTML found. Add the client EDM HTML to the intake folder.")

    inferred_subject = subject
    if not inferred_subject:
        for note in notes:
            inferred_subject = extract_subject_from_notes(note)
            if inferred_subject:
                break
    if not inferred_subject:
        inferred_subject = title_from_html(Path(edm.path).read_text(encoding="utf-8", errors="replace"))
    if not inferred_subject:
        inferred_subject = f"Helium EDM campaign {time.strftime('%Y-%m-%d')}"

    header_path = Path(header.path) if header else default_header
    footer_path = Path(footer.path) if footer else default_footer
    actions = [
        "Classify uploaded files by role.",
        "Use contact CSV as the recipient list.",
        "Use EDM HTML as the campaign body.",
        f"Apply the {client} client header and footer unless uploaded files override them.",
        "Generate plain-text fallback and run AI preflight.",
        "Verify contacts through EmailListVerify.",
        "Upload accepted contacts to Sendy.",
        "Create a Sendy draft campaign ready to be reviewed and sent.",
    ]

    return (
        IntakePlan(
            client=client,
            contacts_path=str(Path(contacts.path)),
            edm_html_path=str(Path(edm.path)),
            header_path=str(header_path),
            footer_path=str(footer_path),
            subject=inferred_subject,
            actions=actions,
        ),
        assessments,
    )


def verify_email(api_key: str, email_address: str, timeout: int = 30) -> str:
    response = requests.get(
        "https://apps.emaillistverify.com/api/verifyEmail",
        params={"secret": api_key, "email": email_address},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text.strip().lower()


def verify_contacts(
    contacts: list[Contact],
    accepted_statuses: set[str],
    dry_run: bool,
    pause_seconds: float,
) -> list[VerifiedContact]:
    api_key = env("EMAILLISTVERIFY_API_KEY")
    if not dry_run and not api_key:
        raise ValueError("EMAILLISTVERIFY_API_KEY is required unless --dry-run is set.")

    verified: list[VerifiedContact] = []
    for contact in contacts:
        if dry_run:
            status = "ok" if not contact.email.endswith(".invalid") else "invalid"
        else:
            status = verify_email(api_key, contact.email)
            if pause_seconds:
                time.sleep(pause_seconds)

        accepted = status in accepted_statuses
        reason = "accepted" if accepted else f"rejected by EmailListVerify status '{status}'"
        verified.append(
            VerifiedContact(
                email=contact.email,
                name=contact.name,
                status=status,
                accepted=accepted,
                reason=reason,
            )
        )
    return verified


class SendyClient:
    def __init__(self, base_url: str, api_key: str, dry_run: bool) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.dry_run = dry_run

    def post(self, path: str, payload: dict[str, Any]) -> str:
        if self.dry_run:
            return f"DRY_RUN {path} {json.dumps(redact(payload), sort_keys=True)}"
        response = requests.post(f"{self.base_url}{path}", data=payload, timeout=60)
        response.raise_for_status()
        return response.text.strip()

    def subscribe(self, list_id: str, contact: VerifiedContact) -> str:
        return self.post(
            "/subscribe",
            {
                "api_key": self.api_key,
                "list": list_id,
                "email": contact.email,
                "name": contact.name,
                "boolean": "true",
            },
        )

    def get_brands(self) -> Any:
        result = self.post("/api/brands/get-brands.php", {"api_key": self.api_key})
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result

    def get_lists(self, brand_id: str, include_hidden: bool = False) -> Any:
        result = self.post(
            "/api/lists/get-lists.php",
            {
                "api_key": self.api_key,
                "brand_id": brand_id,
                "include_hidden": "yes" if include_hidden else "no",
            },
        )
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result

    def create_campaign(
        self,
        *,
        from_name: str,
        from_email: str,
        reply_to: str,
        title: str,
        subject: str,
        html_text: str,
        plain_text: str,
        list_ids: str,
        brand_id: str,
        send_campaign: bool,
        schedule_date_time: str,
        schedule_timezone: str,
    ) -> str:
        payload = {
            "api_key": self.api_key,
            "from_name": from_name,
            "from_email": from_email,
            "reply_to": reply_to,
            "title": title,
            "subject": subject,
            "html_text": html_text,
            "plain_text": plain_text,
            "track_opens": "1",
            "track_clicks": "1",
            "send_campaign": "1" if send_campaign else "0",
        }
        if send_campaign:
            payload["list_ids"] = list_ids
        else:
            payload["brand_id"] = brand_id
        if schedule_date_time:
            payload["schedule_date_time"] = schedule_date_time
            payload["schedule_timezone"] = schedule_timezone
        return self.post("/api/campaigns/create.php", payload)


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    if "api_key" in redacted:
        redacted["api_key"] = "***"
    return redacted


def ai_preflight(html_text: str, plain_text: str, subject: str, client_note: str) -> dict[str, Any]:
    api_key = env("OPENAI_API_KEY")
    if not api_key:
        return {
            "enabled": False,
            "note": "OPENAI_API_KEY not set; used deterministic fallback.",
            "suggested_subject": subject,
            "plain_text_preview": plain_text[:600],
            "checks": deterministic_checks(html_text, plain_text, subject),
        }

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    prompt = {
        "role": "user",
        "content": textwrap.dedent(
            f"""
            You are helping Helium.sg prepare an EDM campaign received from a client.
            Return strict JSON with:
            - suggested_subject: one concise subject line
            - risk_flags: array of deliverability/compliance/content risks
            - fixes: array of concrete edits to make before sending
            - plain_text_summary: 4-6 sentence plain-text fallback summary

            Client note:
            {client_note or "(none)"}

            Current subject:
            {subject}

            HTML:
            {html_text[:12000]}
            """
        ).strip(),
    }
    response = client.responses.create(
        model=env("OPENAI_MODEL", "gpt-4.1-mini"),
        input=[prompt],
        text={"format": {"type": "json_object"}},
    )
    return json.loads(response.output_text)


def deterministic_checks(html_text: str, plain_text: str, subject: str) -> list[str]:
    checks: list[str] = []
    lower_html = html_text.lower()
    if "[unsubscribe]" not in lower_html and "unsubscribe" not in lower_html:
        checks.append("No obvious unsubscribe text/tag found.")
    if len(subject) > 80:
        checks.append("Subject is longer than 80 characters.")
    if not plain_text:
        checks.append("Plain text version is empty.")
    if "http://" in lower_html:
        checks.append("Non-HTTPS link detected.")
    return checks or ["No deterministic issues found."]


def write_report(
    output_dir: Path,
    verified: list[VerifiedContact],
    warnings: list[str],
    ai_result: dict[str, Any],
    sendy_results: list[dict[str, str]],
    campaign_result: str,
    plan: IntakePlan | None = None,
    assessments: list[FileAssessment] | None = None,
    rendered_html_path: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    accepted = [item for item in verified if item.accepted]
    rejected = [item for item in verified if not item.accepted]

    with (output_dir / "verified_contacts.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["email", "name", "status", "accepted", "reason"])
        writer.writeheader()
        for item in verified:
            writer.writerow(asdict(item))

    report = {
        "summary": {
            "accepted": len(accepted),
            "rejected": len(rejected),
            "warnings": len(warnings),
        },
        "file_assessments": [asdict(item) for item in assessments or []],
        "processing_plan": asdict(plan) if plan else None,
        "rendered_html_path": str(rendered_html_path) if rendered_html_path else None,
        "warnings": warnings,
        "ai_preflight": ai_result,
        "sendy_import": sendy_results,
        "campaign_result": campaign_result,
    }
    (output_dir / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def process_campaign(
    *,
    contacts_path: Path,
    html_path: Path,
    header_path: Path,
    footer_path: Path,
    subject: str,
    client: str,
    list_id: str,
    brand_id: str,
    output_dir: Path,
    title: str = "",
    client_note: str = "",
    email_column: str = "email",
    name_column: str = "name",
    accepted_statuses: set[str] | None = None,
    dry_run: bool = False,
    import_to_sendy: bool = False,
    create_campaign: bool = False,
    send_campaign: bool = False,
    schedule_date_time: str = "",
    schedule_timezone: str = "Asia/Singapore",
    verification_pause: float = 0.0,
    plan: IntakePlan | None = None,
    assessments: list[FileAssessment] | None = None,
) -> dict[str, Any]:
    accepted_statuses = accepted_statuses or {"ok"}

    edm_html = html_path.read_text(encoding="utf-8")
    header_html = header_path.read_text(encoding="utf-8") if header_path.exists() else ""
    footer_html = footer_path.read_text(encoding="utf-8") if footer_path.exists() else ""
    html_text = render_helium_email(edm_html, header_html, footer_html)
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered_html_path = output_dir / "rendered_edm.html"
    rendered_html_path.write_text(html_text, encoding="utf-8")

    plain_text = html_to_plain_text(html_text)
    contacts, warnings = read_contacts(contacts_path, email_column, name_column)
    verified = verify_contacts(contacts, accepted_statuses, dry_run, verification_pause)
    accepted = [item for item in verified if item.accepted]

    ai_result = ai_preflight(html_text, plain_text, subject, client_note)

    sendy = SendyClient(env("SENDY_BASE_URL"), env("SENDY_API_KEY"), dry_run)
    if not dry_run and (import_to_sendy or create_campaign):
        if not env("SENDY_BASE_URL") or not env("SENDY_API_KEY"):
            raise ValueError("SENDY_BASE_URL and SENDY_API_KEY are required for Sendy calls.")

    sendy_results: list[dict[str, str]] = []
    if import_to_sendy:
        for contact in accepted:
            result = sendy.subscribe(list_id, contact)
            sendy_results.append({"email": contact.email, "result": result})

    campaign_result = "skipped"
    if create_campaign:
        if not send_campaign and not brand_id:
            raise ValueError("--brand-id is required when creating a Sendy draft.")
        campaign_result = sendy.create_campaign(
            from_name=env("SENDY_DEFAULT_FROM_NAME", "Helium"),
            from_email=env("SENDY_DEFAULT_FROM_EMAIL", "hello@helium.sg"),
            reply_to=env("SENDY_DEFAULT_REPLY_TO", "hello@helium.sg"),
            title=title or subject,
            subject=ai_result.get("suggested_subject", subject),
            html_text=html_text,
            plain_text=ai_result.get("plain_text_summary", plain_text),
            list_ids=list_id,
            brand_id=brand_id,
            send_campaign=send_campaign,
            schedule_date_time=schedule_date_time,
            schedule_timezone=schedule_timezone,
        )

    write_report(
        output_dir,
        verified,
        warnings,
        ai_result,
        sendy_results,
        campaign_result,
        plan=plan,
        assessments=assessments,
        rendered_html_path=rendered_html_path,
    )

    return {
        "subject": subject,
        "client": client,
        "contacts_file": str(contacts_path),
        "edm_file": str(html_path),
        "contacts_read": len(contacts),
        "accepted": len(accepted),
        "rejected": len(verified) - len(accepted),
        "warnings": len(warnings),
        "rendered_html": str(rendered_html_path),
        "campaign_result": campaign_result,
        "output_dir": str(output_dir),
        "sendy_imported": len(sendy_results),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automate Helium.sg list verification and Sendy campaign setup.",
    )
    parser.add_argument("--input-dir", type=Path, help="Folder of uploaded client files for the agent to classify and process.")
    parser.add_argument("--client", default="default", help="Helium client slug used for client-specific header/footer templates.")
    parser.add_argument("--contacts", type=Path, help="CSV list from client.")
    parser.add_argument("--html", type=Path, help="EDM HTML file from client.")
    parser.add_argument("--header", type=Path, help="Header HTML to prepend. Overrides the client template.")
    parser.add_argument("--footer", type=Path, help="Footer HTML to append. Overrides the client template.")
    parser.add_argument("--subject", default="", help="Campaign subject line.")
    parser.add_argument("--title", default="", help="Internal Sendy campaign title.")
    parser.add_argument("--client-note", default="", help="Context for AI preflight.")
    parser.add_argument("--list-id", required=True, help="Sendy list ID to import/send to.")
    parser.add_argument("--brand-id", default="", help="Sendy brand ID for draft creation.")
    parser.add_argument("--email-column", default="email")
    parser.add_argument("--name-column", default="name")
    parser.add_argument("--accepted-status", action="append", default=["ok"], help="Accepted EmailListVerify status. Repeatable.")
    parser.add_argument("--output-dir", type=Path, default=Path("runs/latest"))
    parser.add_argument("--dry-run", action="store_true", help="Do not call external APIs.")
    parser.add_argument("--import-to-sendy", action="store_true", help="Subscribe accepted contacts to Sendy.")
    parser.add_argument("--create-campaign", action="store_true", help="Create campaign in Sendy.")
    parser.add_argument("--send-campaign", action="store_true", help="Send immediately instead of creating a draft.")
    parser.add_argument("--schedule-date-time", default="", help='Example: "June 15, 2026 6:05pm".')
    parser.add_argument("--schedule-timezone", default="Asia/Singapore")
    parser.add_argument("--verification-pause", type=float, default=0.0, help="Seconds between EmailListVerify calls.")
    return parser


def ensure_input_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, str, IntakePlan | None, list[FileAssessment]]:
    client = slugify_client(args.client)
    default_header, default_footer = resolve_client_templates(client, args.header, args.footer)

    if args.input_dir:
        plan, assessments = build_intake_plan(args.input_dir, args.subject, client, default_header, default_footer)
        return (
            Path(plan.contacts_path),
            Path(plan.edm_html_path),
            Path(plan.header_path),
            Path(plan.footer_path),
            plan.subject,
            plan,
            assessments,
        )

    missing = []
    if not args.contacts:
        missing.append("--contacts")
    if not args.html:
        missing.append("--html")
    if not args.subject:
        missing.append("--subject")
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required arguments without --input-dir: {joined}")

    plan = IntakePlan(
        client=client,
        contacts_path=str(args.contacts),
        edm_html_path=str(args.html),
        header_path=str(default_header),
        footer_path=str(default_footer),
        subject=args.subject,
        actions=[
            "Use explicitly provided contacts and HTML files.",
            f"Apply the {client} client header and footer.",
            "Generate plain-text fallback and run AI preflight.",
            "Verify contacts through EmailListVerify.",
            "Upload accepted contacts to Sendy if requested.",
            "Create a Sendy campaign if requested.",
        ],
    )
    assessments = [
        FileAssessment(str(args.contacts), "contacts", 1.0, "Provided explicitly with --contacts."),
        FileAssessment(str(args.html), "edm_html", 1.0, "Provided explicitly with --html."),
        FileAssessment(str(default_header), "header", 1.0, "Resolved from explicit override or client template."),
        FileAssessment(str(default_footer), "footer", 1.0, "Resolved from explicit override or client template."),
    ]
    return args.contacts, args.html, default_header, default_footer, args.subject, plan, assessments


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    contacts_path, html_path, header_path, footer_path, subject, plan, assessments = ensure_input_paths(args)

    summary = process_campaign(
        contacts_path=contacts_path,
        html_path=html_path,
        header_path=header_path,
        footer_path=footer_path,
        subject=subject,
        client=plan.client if plan else slugify_client(args.client),
        list_id=args.list_id,
        brand_id=args.brand_id,
        output_dir=args.output_dir,
        title=args.title,
        client_note=args.client_note,
        email_column=args.email_column,
        name_column=args.name_column,
        accepted_statuses=set(args.accepted_status),
        dry_run=args.dry_run,
        import_to_sendy=args.import_to_sendy,
        create_campaign=args.create_campaign,
        send_campaign=args.send_campaign,
        schedule_date_time=args.schedule_date_time,
        schedule_timezone=args.schedule_timezone,
        verification_pause=args.verification_pause,
        plan=plan,
        assessments=assessments,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
