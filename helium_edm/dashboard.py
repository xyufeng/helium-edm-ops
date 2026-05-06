from __future__ import annotations

import hmac
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, flash, jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from helium_edm.cli import (
    FileAssessment,
    IntakePlan,
    build_intake_plan,
    env,
    load_dotenv,
    load_client_config,
    process_campaign,
    resolve_client_templates,
    SendyClient,
    slugify_client,
)


APP_TITLE = "Helium EDM Intake Agent"
ALLOWED_EXTENSIONS = {".csv", ".html", ".htm", ".txt", ".md", ".json"}


def create_app() -> Flask:
    load_dotenv()
    app = Flask(__name__)
    app.secret_key = env("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["MAX_CONTENT_LENGTH"] = int(env("MAX_UPLOAD_MB", "25")) * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = env("DASHBOARD_COOKIE_SECURE", "").lower() == "true"
    validate_dashboard_config()

    @app.before_request
    def require_login() -> Response | None:
        if request.endpoint in {"login", "login_post", "healthz", "presentation"}:
            return None
        if session.get("authenticated"):
            return None
        return redirect(url_for("login"))

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"ok": True})

    @app.get("/presentation")
    def presentation() -> str:
        return render_template_string(PRESENTATION_TEMPLATE, title=f"{APP_TITLE} Presentation")

    @app.get("/login")
    def login() -> str:
        return render_template_string(LOGIN_TEMPLATE, title=APP_TITLE, BASE_CSS=BASE_CSS)

    @app.post("/login")
    def login_post() -> Response:
        password = request.form.get("password", "")
        expected = env("DASHBOARD_PASSWORD", "admin")
        if hmac.compare_digest(password, expected):
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        flash("Incorrect password.")
        return redirect(url_for("login"))

    @app.post("/logout")
    def logout() -> Response:
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def dashboard() -> str:
        latest = latest_result()
        return render_template_string(
            DASHBOARD_TEMPLATE,
            title=APP_TITLE,
            BASE_CSS=BASE_CSS,
            clients=list_clients(),
            status=service_status(),
            latest=latest,
        )

    @app.get("/sendy/brands")
    def sendy_brands() -> Response:
        try:
            return jsonify({"ok": True, "brands": get_sendy_brands()})
        except Exception as exc:
            return jsonify({"ok": False, "error": safe_error(exc)}), 400

    @app.get("/sendy/lists")
    def sendy_lists() -> Response:
        brand_id = request.args.get("brand_id", "").strip()
        if not brand_id:
            return jsonify({"ok": False, "error": "Choose a Sendy brand first."}), 400
        try:
            return jsonify({"ok": True, "lists": get_sendy_lists(brand_id)})
        except Exception as exc:
            return jsonify({"ok": False, "error": safe_error(exc)}), 400

    @app.post("/run")
    def run_campaign() -> Response:
        try:
            client = slugify_client(request.form.get("client", "default"))
            subject = request.form.get("subject", "").strip()
            selected_list_ids = [item.strip() for item in request.form.getlist("list_id") if item.strip()]
            list_id = ",".join(selected_list_ids) or request.form.get("list_id", "").strip()
            brand_id = request.form.get("brand_id", "").strip()
            from_name = request.form.get("from_name", "").strip()
            from_email = request.form.get("from_email", "").strip()
            reply_to = request.form.get("reply_to", "").strip()
            campaign_notes = request.form.get("notes", "").strip()
            client_note = request.form.get("client_note", "").strip()
            consent_basis = request.form.get("consent_basis", "").strip()
            consent_confirmed = request.form.get("consent_confirmed") == "on"
            run_mode = request.form.get("run_mode", "dry_run")
            dry_run = run_mode != "live" and request.form.get("dry_run", "on") == "on"
            import_to_sendy = request.form.get("import_to_sendy") == "on"
            create_campaign = request.form.get("create_campaign") == "on"
            invoice_partner = request.form.get("invoice_partner", "").strip()
            invoice_currency = request.form.get("invoice_currency", "SGD").strip()
            invoice_campaign_fee = request.form.get("invoice_campaign_fee", "0").strip()
            invoice_verification_unit_fee = request.form.get("invoice_verification_unit_fee", "0").strip()
            invoice_list_fee = request.form.get("invoice_list_fee", "0").strip()
            invoice_sending_unit_fee = request.form.get("invoice_sending_unit_fee", "0").strip()
            invoice_commission_rate = request.form.get("invoice_commission_rate", "0.5").strip()
            invoice_discount = request.form.get("invoice_discount", "0").strip()
            invoice_period = request.form.get("invoice_period", "").strip()

            run_id = time.strftime("%Y%m%d-%H%M%S")
            output_dir = Path("runs") / run_id
            input_dir = output_dir / "input"
            input_dir.mkdir(parents=True, exist_ok=True)

            save_upload(request.files.get("contacts"), input_dir, "contacts")
            save_upload(request.files.get("edm"), input_dir, "edm")
            save_campaign_notes(campaign_notes, input_dir)
            suppression_path = save_optional_upload(request.files.get("suppression"), input_dir)
            if suppression_path == input_dir:
                suppression_path = None

            header_path, footer_path = resolve_client_templates(client, None, None)
            plan, assessments = build_intake_plan(input_dir, subject, client, header_path, footer_path)

            summary = process_campaign(
                contacts_path=Path(plan.contacts_path),
                html_path=Path(plan.edm_html_path),
                header_path=Path(plan.header_path),
                footer_path=Path(plan.footer_path),
                subject=plan.subject,
                client=client,
                list_id=list_id,
                brand_id=brand_id,
                output_dir=output_dir,
                from_name=from_name,
                from_email=from_email,
                reply_to=reply_to,
                client_note=client_note,
                consent_basis=consent_basis,
                consent_confirmed=consent_confirmed,
                suppression_path=suppression_path,
                dry_run=dry_run,
                import_to_sendy=import_to_sendy,
                create_campaign=create_campaign,
                invoice_partner=invoice_partner,
                invoice_currency=invoice_currency,
                invoice_campaign_fee=invoice_campaign_fee,
                invoice_verification_unit_fee=invoice_verification_unit_fee,
                invoice_list_fee=invoice_list_fee,
                invoice_sending_unit_fee=invoice_sending_unit_fee,
                invoice_commission_rate=invoice_commission_rate,
                invoice_discount=invoice_discount,
                invoice_period=invoice_period,
                plan=plan,
                assessments=assessments,
            )
            write_dashboard_report(output_dir, summary)
            update_latest(output_dir)
            flash("Campaign processed. Review the run report before sending.")
        except Exception as exc:
            flash(str(exc))
        return redirect(url_for("dashboard"))

    @app.get("/runs/<path:filename>")
    def static_run_file(filename: str) -> Response:
        return send_from_directory(Path("runs").resolve(), filename)

    @app.get("/client-config/<client>")
    def client_config(client: str) -> Response:
        config = load_client_config(client)
        return jsonify(
            {
                "ok": True,
                "config": {
                    "display_name": config.display_name,
                    "sendy_brand_id": config.sendy_brand_id,
                    "sendy_brand_name": config.sendy_brand_name,
                    "sendy_list_id": config.sendy_list_id,
                    "from_name": config.from_name,
                    "from_email": config.from_email,
                    "reply_to": config.reply_to,
                },
            }
        )

    return app


def validate_dashboard_config() -> None:
    if env("HELIUM_ENV") != "production":
        return
    if not env("DASHBOARD_PASSWORD"):
        raise ValueError("DASHBOARD_PASSWORD is required when HELIUM_ENV=production.")
    if not env("FLASK_SECRET_KEY"):
        raise ValueError("FLASK_SECRET_KEY is required when HELIUM_ENV=production.")


def list_clients() -> list[dict[str, str]]:
    config_dir = Path("config/clients")
    clients: list[dict[str, str]] = []
    if config_dir.exists():
        for path in sorted(config_dir.glob("*.json")):
            config = load_client_config(path.stem)
            if not config.dashboard_visible:
                continue
            clients.append(
                {
                    "slug": config.slug,
                    "label": config.display_name or config.sendy_brand_name or config.slug,
                    "brand_id": config.sendy_brand_id,
                }
            )
    if clients:
        return sorted(clients, key=lambda item: item["label"].lower())

    client_dir = Path("templates/clients")
    if not client_dir.exists():
        return [{"slug": "default", "label": "Default", "brand_id": ""}]
    template_clients = sorted(path.name for path in client_dir.iterdir() if path.is_dir())
    return [{"slug": client, "label": client, "brand_id": ""} for client in template_clients] or [
        {"slug": "default", "label": "Default", "brand_id": ""}
    ]


def service_status() -> dict[str, dict[str, str]]:
    return {
        "EmailListVerify": {
            "status": "configured" if env("EMAILLISTVERIFY_API_KEY") else "missing key",
            "detail": "Live verification available." if env("EMAILLISTVERIFY_API_KEY") else "Dry-run mode can still simulate verification.",
        },
        "Sendy": {
            "status": "configured" if env("SENDY_BASE_URL") and env("SENDY_API_KEY") else "missing config",
            "detail": env("SENDY_BASE_URL", "Set SENDY_BASE_URL and SENDY_API_KEY."),
        },
    }


def get_sendy_brands() -> Any:
    if not env("SENDY_BASE_URL") or not env("SENDY_API_KEY"):
        raise ValueError("Sendy is not configured. Set SENDY_BASE_URL and SENDY_API_KEY.")
    return SendyClient(env("SENDY_BASE_URL"), env("SENDY_API_KEY"), dry_run=False).get_brands()


def get_sendy_lists(brand_id: str) -> Any:
    if not env("SENDY_BASE_URL") or not env("SENDY_API_KEY"):
        raise ValueError("Sendy is not configured. Set SENDY_BASE_URL and SENDY_API_KEY.")
    return SendyClient(env("SENDY_BASE_URL"), env("SENDY_API_KEY"), dry_run=False).get_lists(brand_id)


def safe_error(exc: Exception) -> str:
    message = str(exc)
    for secret in (env("SENDY_API_KEY"), env("EMAILLISTVERIFY_API_KEY"), env("OPENAI_API_KEY")):
        if secret:
            message = message.replace(secret, "***")
    return message


def save_upload(file: FileStorage | None, input_dir: Path, label: str) -> Path:
    if not file or not file.filename:
        raise ValueError(f"Upload a {label} file.")
    return save_optional_upload(file, input_dir, required=True)


def save_optional_upload(file: FileStorage | None, input_dir: Path, required: bool = False) -> Path:
    if not file or not file.filename:
        if required:
            raise ValueError("Missing required upload.")
        return input_dir
    filename = secure_filename(file.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file extension: {suffix}")
    path = input_dir / filename
    file.save(path)
    return path


def save_campaign_notes(notes: str, input_dir: Path) -> Path | None:
    if not notes.strip():
        return None
    path = input_dir / "campaign-notes.txt"
    path.write_text(notes.strip() + "\n", encoding="utf-8")
    return path


def update_latest(output_dir: Path) -> None:
    latest = Path("runs/latest")
    if latest.exists() or latest.is_symlink():
        if latest.is_symlink() or latest.is_file():
            latest.unlink()
        else:
            shutil.rmtree(latest)
    shutil.copytree(output_dir, latest)


def latest_result() -> dict[str, Any] | None:
    report_path = Path("runs/latest/run_report.json")
    if not report_path.exists():
        return None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    dashboard_summary_path = Path("runs/latest/dashboard_summary.json")
    summary = {}
    if dashboard_summary_path.exists():
        summary = json.loads(dashboard_summary_path.read_text(encoding="utf-8"))
    return {"report": report, "summary": summary}


def write_dashboard_report(output_dir: Path, summary: dict[str, Any]) -> None:
    (output_dir / "dashboard_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report_path = output_dir / "run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    html = render_dashboard_report(summary, report)
    (output_dir / "index.html").write_text(html, encoding="utf-8")


def render_dashboard_report(summary: dict[str, Any], report: dict[str, Any]) -> str:
    plan = report.get("processing_plan") or {}
    assessments = report.get("file_assessments") or []
    warnings = report.get("warnings") or []
    delivery_checks = (report.get("ai_preflight") or {}).get("deterministic_checks") or []
    blocking_checks = [item for item in delivery_checks if item.get("severity") == "error"]
    ready_for_review = bool(summary.get("campaign_result") and summary.get("campaign_result") != "skipped" and not blocking_checks)
    return render_template_string(
        REPORT_TEMPLATE,
        title=APP_TITLE,
        BASE_CSS=BASE_CSS,
        summary=summary,
        plan=plan,
        assessments=assessments,
        warnings=warnings,
        delivery_checks=delivery_checks,
        ready_for_review=ready_for_review,
        blocking_checks=blocking_checks,
        report=report,
    )


LOGIN_TEMPLATE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <style>{{ BASE_CSS }}</style>
  </head>
  <body>
    <main class="login-shell">
      <form class="panel login-panel" method="post">
        <h1>{{ title }}</h1>
        <p>Sign in to prepare a client EDM campaign.</p>
        {% with messages = get_flashed_messages() %}
          {% if messages %}<div class="alert">{{ messages[0] }}</div>{% endif %}
        {% endwith %}
        <label>Password<input name="password" type="password" autofocus required></label>
        <button type="submit">Sign in</button>
      </form>
    </main>
  </body>
</html>
"""

PRESENTATION_TEMPLATE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <style>
      :root {
        color-scheme: light;
        font-family: Inter, Arial, sans-serif;
        color: #102033;
        background: #eef2f5;
      }
      * { box-sizing: border-box; }
      html { scroll-behavior: smooth; }
      body { margin: 0; }
      .deck-nav {
        position: sticky;
        top: 0;
        z-index: 20;
        display: flex;
        justify-content: space-between;
        gap: 18px;
        align-items: center;
        padding: 12px 24px;
        background: rgba(255,255,255,.92);
        border-bottom: 1px solid #d7e0ea;
        backdrop-filter: blur(10px);
      }
      .deck-nav strong { font-size: 14px; }
      .deck-nav a {
        color: #1455d9;
        text-decoration: none;
        font-weight: 750;
        font-size: 13px;
      }
      .nav-links { display: flex; flex-wrap: wrap; gap: 12px; justify-content: flex-end; }
      .slide {
        min-height: calc(100vh - 48px);
        display: grid;
        grid-template-columns: minmax(0, 1.04fr) minmax(320px, .96fr);
        gap: 34px;
        align-items: center;
        padding: 48px clamp(22px, 6vw, 82px);
        border-bottom: 1px solid #d9e2ec;
        background: #fbfcfd;
      }
      .slide:nth-of-type(even) { background: #f4f8fb; }
      .intro { color: #617085; font-size: 15px; font-weight: 750; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 12px; }
      h1, h2, p { margin-top: 0; }
      h1 { font-size: clamp(44px, 6vw, 82px); line-height: .94; margin-bottom: 24px; letter-spacing: 0; }
      h2 { font-size: clamp(32px, 4vw, 56px); line-height: 1; margin-bottom: 20px; letter-spacing: 0; }
      p { color: #43566d; font-size: 20px; line-height: 1.48; }
      .bullets { display: grid; gap: 12px; margin: 24px 0; padding: 0; list-style: none; }
      .bullets li {
        display: flex;
        gap: 12px;
        align-items: flex-start;
        color: #24364d;
        font-size: 18px;
        line-height: 1.42;
      }
      .bullets li::before {
        content: "";
        flex: 0 0 8px;
        width: 8px;
        height: 8px;
        margin-top: 9px;
        border-radius: 50%;
        background: #14a38b;
      }
      .visual {
        min-height: 440px;
        border: 1px solid #d5dee8;
        border-radius: 8px;
        background: #ffffff;
        box-shadow: 0 20px 45px rgba(34,49,72,.10);
        padding: 24px;
        display: grid;
        align-items: center;
      }
      .notes {
        margin-top: 28px;
        border-left: 4px solid #1455d9;
        padding: 14px 18px;
        background: #edf4ff;
        color: #20364f;
        font-size: 17px;
        line-height: 1.5;
      }
      .prompt {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        white-space: pre-wrap;
        color: #d9e8ff;
        background: #102033;
        border-radius: 8px;
        padding: 18px;
        font-size: 14px;
        line-height: 1.45;
      }
      .metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
      .metric {
        border: 1px solid #dce5ee;
        border-radius: 8px;
        padding: 16px;
        background: #f8fbfd;
      }
      .metric strong { display: block; color: #0f2948; font-size: 28px; margin-bottom: 5px; }
      .metric span { color: #617085; font-size: 13px; text-transform: uppercase; letter-spacing: .05em; }
      .browser {
        border: 1px solid #cad6e2;
        border-radius: 8px;
        overflow: hidden;
        background: #fff;
      }
      .browser-bar {
        height: 36px;
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 0 12px;
        background: #e7edf4;
      }
      .dot { width: 10px; height: 10px; border-radius: 50%; background: #ef6a5b; }
      .dot:nth-child(2) { background: #f4bd4f; }
      .dot:nth-child(3) { background: #61c454; }
      .screen { padding: 18px; display: grid; gap: 14px; }
      .field { border: 1px solid #d8e1eb; border-radius: 6px; padding: 12px; color: #263b52; background: #fbfcfe; }
      .button-row { display: flex; gap: 10px; flex-wrap: wrap; }
      .button-chip { border-radius: 6px; padding: 10px 12px; background: #1455d9; color: #fff; font-weight: 750; }
      .button-chip.secondary { background: #e8edf3; color: #263b52; }
      svg { width: 100%; height: auto; display: block; }
      .link-list { display: grid; gap: 10px; margin-top: 20px; }
      .link-list a { color: #1455d9; font-size: 19px; font-weight: 750; text-decoration: none; }
      @media (max-width: 920px) {
        .slide { grid-template-columns: 1fr; padding-top: 34px; }
        .visual { min-height: 320px; }
        h1 { font-size: 46px; }
        h2 { font-size: 36px; }
      }
      @media print {
        .deck-nav { display: none; }
        .slide { min-height: auto; break-after: page; }
      }
    </style>
  </head>
  <body>
    <nav class="deck-nav">
      <strong>Helium EDM Intake Agent</strong>
      <div class="nav-links">
        <a href="#workflow">Workflow</a>
        <a href="#build">Build</a>
        <a href="#demo">Demo</a>
        <a href="#links">Links</a>
      </div>
    </nav>

    <section class="slide" id="title">
      <div>
        <div class="intro">Stripe Forward Deployed AI Accelerator take-home</div>
        <h1>Helium EDM Intake Agent</h1>
        <p>From messy client handoff to a verified, branded Sendy campaign draft on helium.sg.</p>
        <div class="notes">Narration: I run a side business called helium.sg. A partner in China regularly sends me email lists and EDM creative, and needs a dependable way to send campaigns outside China. I built an AI-assisted operator tool around the real workflow I already do.</div>
      </div>
      <div class="visual">
        <svg viewBox="0 0 760 520" role="img" aria-label="Campaign intake transformed into a ready Sendy campaign">
          <rect x="34" y="74" width="230" height="118" rx="8" fill="#eef5ff" stroke="#9bb7e8"/>
          <text x="62" y="116" font-size="26" font-weight="700" fill="#102033">Client handoff</text>
          <text x="62" y="151" font-size="18" fill="#43566d">CSV + EDM + notes</text>
          <rect x="34" y="326" width="230" height="118" rx="8" fill="#fff7ed" stroke="#f2c288"/>
          <text x="62" y="368" font-size="26" font-weight="700" fill="#102033">Manual ops</text>
          <text x="62" y="403" font-size="18" fill="#43566d">Verify, wrap, upload</text>
          <path d="M278 132 C374 132 370 260 458 260" fill="none" stroke="#1455d9" stroke-width="8" stroke-linecap="round"/>
          <path d="M278 384 C374 384 370 260 458 260" fill="none" stroke="#14a38b" stroke-width="8" stroke-linecap="round"/>
          <circle cx="514" cy="260" r="84" fill="#102033"/>
          <text x="466" y="252" font-size="24" font-weight="800" fill="#fff">Agent</text>
          <text x="447" y="285" font-size="17" fill="#cfe7ff">Assess Plan Act</text>
          <rect x="596" y="74" width="132" height="370" rx="8" fill="#f8fbfd" stroke="#cbd8e4"/>
          <rect x="619" y="104" width="86" height="54" rx="6" fill="#dff7ef"/>
          <rect x="619" y="180" width="86" height="54" rx="6" fill="#e8edff"/>
          <rect x="619" y="256" width="86" height="54" rx="6" fill="#fff1df"/>
          <rect x="619" y="332" width="86" height="54" rx="6" fill="#edf4ff"/>
          <text x="620" y="425" font-size="18" font-weight="700" fill="#102033">Sendy draft</text>
        </svg>
      </div>
    </section>

    <section class="slide" id="workflow">
      <div>
        <div class="intro">Workflow today</div>
        <h2>Recurring work with too many tiny decisions</h2>
        <ul class="bullets">
          <li>Client sends a recipient list and EDM creative for each campaign.</li>
          <li>I clean the list, verify emails, add the right client header and footer, and prepare Sendy.</li>
          <li>The painful part is not one hard task. It is repeated judgment, copy-paste, and audit risk.</li>
        </ul>
        <div class="notes">Narration: The current workflow happens every time a campaign comes in. I receive files, open the CSV, check columns, run verification, patch the HTML, upload to Sendy, create a campaign, and separately calculate billing. It is slow because each step depends on the previous one being done correctly.</div>
      </div>
      <div class="visual">
        <svg viewBox="0 0 760 520" role="img" aria-label="Current workflow steps">
          <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#5b6b7c"/></marker></defs>
          <g font-family="Inter, Arial, sans-serif">
            <rect x="48" y="54" width="250" height="76" rx="8" fill="#fff" stroke="#cad6e2"/>
            <text x="72" y="101" font-size="22" font-weight="700" fill="#102033">Receive files</text>
            <rect x="462" y="54" width="250" height="76" rx="8" fill="#fff" stroke="#cad6e2"/>
            <text x="486" y="101" font-size="22" font-weight="700" fill="#102033">Guess file roles</text>
            <rect x="48" y="222" width="250" height="76" rx="8" fill="#fff" stroke="#cad6e2"/>
            <text x="72" y="269" font-size="22" font-weight="700" fill="#102033">Clean + verify</text>
            <rect x="462" y="222" width="250" height="76" rx="8" fill="#fff" stroke="#cad6e2"/>
            <text x="486" y="269" font-size="22" font-weight="700" fill="#102033">Wrap EDM HTML</text>
            <rect x="48" y="390" width="250" height="76" rx="8" fill="#fff" stroke="#cad6e2"/>
            <text x="72" y="437" font-size="22" font-weight="700" fill="#102033">Upload lists</text>
            <rect x="462" y="390" width="250" height="76" rx="8" fill="#fff" stroke="#cad6e2"/>
            <text x="486" y="437" font-size="22" font-weight="700" fill="#102033">Create draft</text>
            <path d="M300 92 H458" stroke="#5b6b7c" stroke-width="4" marker-end="url(#arrow)"/>
            <path d="M587 132 V216" stroke="#5b6b7c" stroke-width="4" marker-end="url(#arrow)"/>
            <path d="M462 260 H304" stroke="#5b6b7c" stroke-width="4" marker-end="url(#arrow)"/>
            <path d="M173 300 V384" stroke="#5b6b7c" stroke-width="4" marker-end="url(#arrow)"/>
            <path d="M300 428 H458" stroke="#5b6b7c" stroke-width="4" marker-end="url(#arrow)"/>
          </g>
        </svg>
      </div>
    </section>

    <section class="slide">
      <div>
        <div class="intro">First principles</div>
        <h2>The agent has one job: turn ambiguous uploads into a review-ready campaign</h2>
        <ul class="bullets">
          <li>Assess what each uploaded file is.</li>
          <li>Plan the exact processing sequence before acting.</li>
          <li>Act through APIs and deterministic checks.</li>
          <li>Leave behind artifacts that make the run auditable.</li>
        </ul>
        <div class="notes">Narration: I designed it as an operator workflow, not a chat toy. The tool should inspect the files, decide which is the contact list and which is the EDM, apply the right client defaults, then create a Sendy-ready draft without hiding the decisions it made.</div>
      </div>
      <div class="visual">
        <svg viewBox="0 0 760 520" role="img" aria-label="Assess plan act report loop">
          <circle cx="380" cy="260" r="154" fill="#f7fbff" stroke="#bfd0e1" stroke-width="3"/>
          <circle cx="380" cy="92" r="62" fill="#e8f2ff" stroke="#8cafeb"/>
          <text x="340" y="101" font-size="24" font-weight="800" fill="#102033">Assess</text>
          <circle cx="548" cy="260" r="62" fill="#e6f8f1" stroke="#87cdb8"/>
          <text x="516" y="269" font-size="24" font-weight="800" fill="#102033">Plan</text>
          <circle cx="380" cy="428" r="62" fill="#fff3df" stroke="#e2b36e"/>
          <text x="354" y="437" font-size="24" font-weight="800" fill="#102033">Act</text>
          <circle cx="212" cy="260" r="62" fill="#f0edff" stroke="#aaa0e8"/>
          <text x="176" y="269" font-size="24" font-weight="800" fill="#102033">Report</text>
          <text x="316" y="252" font-size="24" font-weight="800" fill="#102033">Human</text>
          <text x="304" y="286" font-size="24" font-weight="800" fill="#102033">reviews</text>
        </svg>
      </div>
    </section>

    <section class="slide" id="build">
      <div>
        <div class="intro">Building process</div>
        <h2>I combined narrow AI judgment with boring reliable code</h2>
        <ul class="bullets">
          <li>Python CLI for repeatable processing and testing.</li>
          <li>Flask dashboard for the actual operator workflow.</li>
          <li>EmailListVerify for list cleaning.</li>
          <li>Sendy API for brand/list discovery, subscriber import, and campaign draft creation.</li>
        </ul>
        <div class="notes">Narration: The build started as a command-line pipeline because that is easier to test. Then I wrapped it in a dashboard because the real workflow starts with files from a client. AI is useful for classification and preflight reasoning, while the API calls and counts need deterministic code.</div>
      </div>
      <div class="visual">
        <div class="prompt">System: You are an EDM operations assistant.

Given uploaded files, classify each file as contacts, EDM HTML, notes, or suppression list.
Then propose a processing plan.

Rules:
- Never send without explicit live mode.
- Use the selected client's header/footer.
- Treat EmailListVerify unknown/risky statuses as quarantine.
- Create a Sendy draft for human review.</div>
      </div>
    </section>

    <section class="slide">
      <div>
        <div class="intro">Debugging moment</div>
        <h2>The Sendy API returned data that was almost JSON</h2>
        <ul class="bullets">
          <li>Brand/list discovery worked in principle, but Sendy's response could include malformed control characters.</li>
          <li>I added a defensive parser and made the UI show a clear live status.</li>
          <li>That changed the dashboard from a mock form into a production wiring check.</li>
        </ul>
        <div class="notes">Narration: One useful failure was Sendy list discovery. I expected clean JSON. The real response had characters that broke normal parsing, so I added a robust parser and surfaced the error in the UI. That was the point where the demo became connected to the real system.</div>
      </div>
      <div class="visual">
        <div class="browser">
          <div class="browser-bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
          <div class="screen">
            <div class="field">Load Sendy brands</div>
            <div class="field">Brand found: China Security Association</div>
            <div class="field">Lists found: Main recipients, Event follow-up, Partner list</div>
            <div class="field">Parser recovery: cleaned response before JSON decode</div>
            <div class="button-row">
              <span class="button-chip">Live API mode</span>
              <span class="button-chip secondary">Draft only</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="slide" id="demo">
      <div>
        <div class="intro">Working demo</div>
        <h2>The dashboard follows the real campaign run</h2>
        <ul class="bullets">
          <li>Choose client and load Sendy brand defaults.</li>
          <li>Upload EDM HTML, contact list, notes, and optional suppression list.</li>
          <li>Choose dry-run or live API mode with consent attestation.</li>
          <li>Generate a report before the campaign is sent.</li>
        </ul>
        <div class="notes">Narration: In the demo I show the dashboard at demo.helium.sg. The client selection drives header, footer, sender defaults, Sendy brand, and recipient lists. I can run in dry mode for a safe recording or live mode for a real operational run.</div>
      </div>
      <div class="visual">
        <div class="browser">
          <div class="browser-bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
          <div class="screen">
            <div class="field">Client: China Security Association</div>
            <div class="field">Sendy brand: loaded from API</div>
            <div class="field">Sendy lists: multiple selected recipient lists</div>
            <div class="field">Contact list CSV: client-list.csv</div>
            <div class="field">EDM HTML: campaign.html</div>
            <div class="button-row">
              <span class="button-chip secondary">Dry run</span>
              <span class="button-chip">Process campaign</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="slide">
      <div>
        <div class="intro">Outputs</div>
        <h2>Every run creates a traceable review package</h2>
        <ul class="bullets">
          <li>Verified contacts CSV with accepted, rejected, quarantined, and suppressed rows.</li>
          <li>Rendered EDM with the selected client header and footer.</li>
          <li>Run report JSON plus a human-readable dashboard report.</li>
          <li>Sendy import and campaign creation results.</li>
        </ul>
        <div class="notes">Narration: The output is not just a success message. It produces the campaign HTML, the verified list, the JSON audit trail, and a dashboard report. This matters because campaign ops need evidence, especially when a client asks why a contact was skipped.</div>
      </div>
      <div class="visual">
        <div class="metric-grid">
          <div class="metric"><strong>accepted</strong><span>Ready contacts</span></div>
          <div class="metric"><strong>rejected</strong><span>Invalid or failed</span></div>
          <div class="metric"><strong>quarantine</strong><span>Risky or unknown</span></div>
          <div class="metric"><strong>suppressed</strong><span>Do-not-email rows</span></div>
          <div class="metric"><strong>rendered_edm.html</strong><span>Client wrapper applied</span></div>
          <div class="metric"><strong>run_report.json</strong><span>Audit trail</span></div>
        </div>
      </div>
    </section>

    <section class="slide">
      <div>
        <div class="intro">Billing handoff</div>
        <h2>I folded invoicing into the same run</h2>
        <ul class="bullets">
          <li>The run writes tracker-aligned invoice rows.</li>
          <li>It calculates setup, cleaning, sending, discount, commission, and payable amounts.</li>
          <li>The output can be pasted into my existing Google Sheet and exported as PDF.</li>
        </ul>
        <div class="notes">Narration: As I built the workflow, I realized the operation does not end at Sendy. I also need to invoice my partner. So I used my existing tracker and PDF invoice as examples, then made each campaign run produce invoice rows automatically.</div>
      </div>
      <div class="visual">
        <svg viewBox="0 0 760 520" role="img" aria-label="Invoice tracker table">
          <rect x="50" y="52" width="660" height="416" rx="8" fill="#fff" stroke="#cbd8e4"/>
          <rect x="50" y="52" width="660" height="58" rx="8" fill="#eaf2ff"/>
          <text x="78" y="88" font-size="24" font-weight="800" fill="#102033">Helium Emails</text>
          <g font-size="17" fill="#263b52">
            <text x="78" y="154">Invoice ID</text><text x="280" y="154">HE-2026-0001</text>
            <text x="78" y="204">Setup Cost</text><text x="280" y="204">$120.00</text>
            <text x="78" y="254">Email Cleaning</text><text x="280" y="254">$48.50</text>
            <text x="78" y="304">Email Sending</text><text x="280" y="304">$72.75</text>
            <text x="78" y="354">Commission</text><text x="280" y="354">$120.63</text>
            <text x="78" y="418" font-weight="800">PAYABLE</text><text x="280" y="418" font-weight="800">$120.62</text>
          </g>
          <rect x="496" y="160" width="146" height="186" rx="8" fill="#f3fbf8" stroke="#a6dccb"/>
          <text x="522" y="232" font-size="20" font-weight="800" fill="#102033">CSV rows</text>
          <text x="518" y="270" font-size="17" fill="#43566d">to Google</text>
          <text x="532" y="300" font-size="17" fill="#43566d">Sheets</text>
        </svg>
      </div>
    </section>

    <section class="slide">
      <div>
        <div class="intro">Production deployment</div>
        <h2>The tool is live behind HTTPS</h2>
        <ul class="bullets">
          <li>Flask app served through gunicorn on AWS Elastic Beanstalk.</li>
          <li>CloudFront and ACM provide SSL for demo.helium.sg.</li>
          <li>Operational dashboard is password protected; the presentation is public for review.</li>
        </ul>
        <div class="notes">Narration: I deployed it because the task asks for a working tool, not only a local script. The dashboard is live on AWS behind SSL, with secrets stored as environment variables and not in the repository.</div>
      </div>
      <div class="visual">
        <svg viewBox="0 0 760 520" role="img" aria-label="AWS deployment architecture">
          <rect x="42" y="210" width="160" height="92" rx="8" fill="#edf4ff" stroke="#9bb7e8"/>
          <text x="76" y="264" font-size="24" font-weight="800" fill="#102033">Browser</text>
          <rect x="300" y="88" width="170" height="92" rx="8" fill="#fff5e6" stroke="#e3b66e"/>
          <text x="326" y="143" font-size="24" font-weight="800" fill="#102033">CloudFront</text>
          <rect x="300" y="336" width="170" height="92" rx="8" fill="#e6f8f1" stroke="#87cdb8"/>
          <text x="350" y="391" font-size="24" font-weight="800" fill="#102033">ACM</text>
          <rect x="556" y="210" width="160" height="92" rx="8" fill="#f0edff" stroke="#aaa0e8"/>
          <text x="588" y="253" font-size="24" font-weight="800" fill="#102033">Elastic</text>
          <text x="571" y="283" font-size="24" font-weight="800" fill="#102033">Beanstalk</text>
          <path d="M205 256 H552" stroke="#53657a" stroke-width="5"/>
          <path d="M385 184 V332" stroke="#53657a" stroke-width="5"/>
          <text x="270" y="246" font-size="18" fill="#43566d">demo.helium.sg</text>
          <text x="336" y="264" font-size="18" fill="#43566d">HTTPS</text>
        </svg>
      </div>
    </section>

    <section class="slide" id="links">
      <div>
        <div class="intro">Submission links</div>
        <h2>Working tool, prompts, and presentation</h2>
        <ul class="bullets">
          <li>Live presentation: this page.</li>
          <li>Working dashboard: https://demo.helium.sg</li>
          <li>Repository and prompts: GitHub README and Stripe take-home document.</li>
        </ul>
        <div class="link-list">
          <a href="https://demo.helium.sg/presentation">https://demo.helium.sg/presentation</a>
          <a href="https://demo.helium.sg">https://demo.helium.sg</a>
          <a href="https://github.com/xyufeng/helium-edm-ops">https://github.com/xyufeng/helium-edm-ops</a>
        </div>
        <div class="notes">Narration: This closes the loop on the Stripe prompt. The workflow is real, the build process is documented, the prompts are visible in the repo, the demo runs on realistic inputs, and the live tool is available at the deployed URL.</div>
      </div>
      <div class="visual">
        <svg viewBox="0 0 760 520" role="img" aria-label="Submission package">
          <rect x="92" y="74" width="576" height="372" rx="8" fill="#fff" stroke="#cbd8e4"/>
          <rect x="130" y="116" width="500" height="70" rx="8" fill="#edf4ff"/>
          <text x="160" y="160" font-size="25" font-weight="800" fill="#102033">Working demo</text>
          <rect x="130" y="224" width="500" height="70" rx="8" fill="#e6f8f1"/>
          <text x="160" y="268" font-size="25" font-weight="800" fill="#102033">Build process + prompts</text>
          <rect x="130" y="332" width="500" height="70" rx="8" fill="#fff5e6"/>
          <text x="160" y="376" font-size="25" font-weight="800" fill="#102033">Recorded narration script</text>
        </svg>
      </div>
    </section>
  </body>
</html>
"""


DASHBOARD_TEMPLATE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <style>{{ BASE_CSS }}</style>
  </head>
  <body>
    <header>
      <div>
        <h1>{{ title }}</h1>
        <p>Prepare a verified, client-branded Sendy campaign draft.</p>
      </div>
      <form method="post" action="{{ url_for('logout') }}"><button class="secondary" type="submit">Logout</button></form>
    </header>

    <main>
      {% with messages = get_flashed_messages() %}
        {% if messages %}<div class="alert">{{ messages[0] }}</div>{% endif %}
      {% endwith %}

      <section class="status-grid">
        {% for name, item in status.items() %}
          <article class="panel">
            <div class="eyebrow">{{ name }}</div>
            <h2>{{ item.status }}</h2>
            <p>{{ item.detail }}</p>
          </article>
        {% endfor %}
      </section>

      <section class="panel">
        <h2>New Campaign</h2>
        <form class="campaign-form" action="{{ url_for('run_campaign') }}" method="post" enctype="multipart/form-data">
          <div class="grid-two">
            <label>Client
              <select id="client-select" name="client">
                {% for client in clients %}
                  <option value="{{ client.slug }}" data-brand-id="{{ client.brand_id }}">{{ client.label }}</option>
                {% endfor %}
              </select>
            </label>
            <label>Subject
              <input name="subject" placeholder="Optional if notes or H1 contains a subject">
            </label>
          </div>
          <div class="notice">
            <strong>Sendy discovery</strong>
            <p>Load brands and lists from the configured Sendy API, then the dashboard will fill the IDs below.</p>
            <div class="grid-two">
              <label>Sendy brand
                <select id="sendy-brand-select">
                  <option value="">Load brands to choose</option>
                </select>
              </label>
              <label>Sendy lists
                <select id="sendy-list-select" name="list_id" multiple required size="8">
                  <option value="">Choose a brand first</option>
                </select>
              </label>
            </div>
            <div class="links">
              <button class="secondary" id="load-brands" type="button">Load Sendy brands</button>
              <button class="secondary" id="load-lists" type="button">Load lists for brand</button>
            </div>
            <p id="sendy-discovery-status" class="muted"></p>
          </div>
          <input id="brand-id-input" name="brand_id" type="hidden">
          <div class="notice">
            <strong>Brand sender defaults</strong>
            <p id="sender-defaults">Loaded from the selected brand config.</p>
          </div>
          <div class="grid-two">
            <label>Contact list CSV
              <input name="contacts" type="file" accept=".csv" required>
            </label>
            <label>EDM HTML
              <input name="edm" type="file" accept=".html,.htm" required>
            </label>
          </div>
          <label>Campaign notes
            <textarea name="notes" rows="5" placeholder="Client instructions, subject ideas, target audience, campaign period, or special handling notes"></textarea>
          </label>
          <label>Suppression list
            <input name="suppression" type="file" accept=".csv,.json">
          </label>
          <label>Operator note
            <textarea name="client_note" rows="3" placeholder="Context for AI preflight"></textarea>
          </label>
          <div class="notice">
            <strong>Consent attestation</strong>
            <p>Helium's operating rule is that uploaded lists have already provided consent. Confirm this for the run so the audit report records it.</p>
            <label>Consent basis
              <select name="consent_basis">
                <option value="provided_client_consent">Client-provided permissioned list</option>
                <option value="newsletter_opt_in">Newsletter opt-in</option>
                <option value="event_registration">Event registration</option>
                <option value="customer_list">Customer list</option>
                <option value="internal_test_list">Internal test list</option>
              </select>
            </label>
            <div class="checks">
              <label><input name="consent_confirmed" type="checkbox"> I confirm this uploaded list has provided consent</label>
            </div>
          </div>
          <div class="notice">
            <strong>Execution mode</strong>
            <div class="checks">
              <label><input name="run_mode" type="radio" value="dry_run" checked> Dry run</label>
              <label><input name="run_mode" type="radio" value="live"> Live API mode</label>
            </div>
          </div>
          <div class="checks">
            <label><input name="import_to_sendy" type="checkbox" checked> Upload accepted contacts to Sendy</label>
            <label><input name="create_campaign" type="checkbox" checked> Create Sendy draft campaign</label>
          </div>
          <div class="notice">
            <strong>Invoice</strong>
            <div class="grid-two">
              <label>Partner
                <input name="invoice_partner" placeholder="Defaults to selected client">
              </label>
              <label>Currency
                <input name="invoice_currency" value="SGD">
              </label>
              <label>Period
                <input name="invoice_period" placeholder="May 2026">
              </label>
              <label>Campaign fee
                <input name="invoice_campaign_fee" inputmode="decimal" placeholder="0.00">
              </label>
              <label>Verification unit fee
                <input name="invoice_verification_unit_fee" inputmode="decimal" placeholder="0.00">
              </label>
              <label>Sending unit fee
                <input name="invoice_sending_unit_fee" inputmode="decimal" placeholder="0.00">
              </label>
              <label>Commission rate
                <input name="invoice_commission_rate" inputmode="decimal" value="0.5">
              </label>
              <label>Discount
                <input name="invoice_discount" inputmode="decimal" placeholder="0.00">
              </label>
            </div>
          </div>
          <button type="submit">Process Campaign</button>
        </form>
      </section>

      {% if latest %}
        <section class="panel">
          <h2>Latest Run</h2>
          <div class="summary-grid">
            <div><strong>{{ latest.summary.mode|upper }}</strong><span>Mode</span></div>
            <div><strong>{{ latest.summary.client }}</strong><span>Client</span></div>
            <div><strong>{{ latest.summary.accepted }}</strong><span>Accepted</span></div>
            <div><strong>{{ latest.summary.rejected }}</strong><span>Rejected</span></div>
            <div><strong>{{ latest.summary.suppressed }}</strong><span>Suppressed</span></div>
          </div>
          <div class="links">
            <a href="{{ url_for('static_run_file', filename='latest/index.html') }}" target="_blank">Human report</a>
            <a href="{{ url_for('static_run_file', filename='latest/rendered_edm.html') }}" target="_blank">Rendered EDM</a>
            <a href="{{ url_for('static_run_file', filename='latest/verified_contacts.csv') }}" target="_blank">Verified CSV</a>
            <a href="{{ url_for('static_run_file', filename='latest/run_report.json') }}" target="_blank">JSON report</a>
            <a href="{{ url_for('static_run_file', filename='latest/invoice.html') }}" target="_blank">Invoice</a>
            <a href="{{ url_for('static_run_file', filename='latest/invoice_rows.csv') }}" target="_blank">Invoice CSV</a>
          </div>
        </section>
      {% endif %}
    </main>
    <script>
      const brandSelect = document.getElementById('sendy-brand-select');
      const listSelect = document.getElementById('sendy-list-select');
      const clientSelect = document.getElementById('client-select');
      const brandInput = document.getElementById('brand-id-input');
      const senderDefaults = document.getElementById('sender-defaults');
      const statusEl = document.getElementById('sendy-discovery-status');

      function normalizeRows(value) {
        if (Array.isArray(value)) return value;
        if (value && Array.isArray(value.brands)) return value.brands;
        if (value && Array.isArray(value.lists)) return value.lists;
        if (value && typeof value === 'object') return Object.values(value);
        return [];
      }

      function rowId(row) {
        return row.id || row.ID || row.brand_id || row.list_id || row.BrandID || row.ListID || '';
      }

      function rowName(row) {
        return row.name || row.Name || row.brand_name || row.list_name || row.title || rowId(row);
      }

      function fillSelect(select, rows, placeholder) {
        select.innerHTML = '';
        if (!select.multiple) {
          const first = document.createElement('option');
          first.value = '';
          first.textContent = placeholder;
          select.appendChild(first);
        }
        rows.forEach((row) => {
          const id = rowId(row);
          if (!id) return;
          const option = document.createElement('option');
          option.value = id;
          option.textContent = `${rowName(row)} (${id})`;
          select.appendChild(option);
        });
        if (select.multiple && !rows.length) {
          const empty = document.createElement('option');
          empty.value = '';
          empty.textContent = placeholder;
          select.appendChild(empty);
        }
      }

      function parseListIds(value) {
        return (value || '').split(',').map((item) => item.trim()).filter(Boolean);
      }

      function selectedListIds() {
        return Array.from(listSelect.selectedOptions).map((option) => option.value).filter(Boolean);
      }

      function selectListIds(value) {
        const ids = parseListIds(value);
        Array.from(listSelect.options).forEach((option) => {
          option.selected = ids.includes(option.value);
        });
      }

      function listStatusText() {
        const count = selectedListIds().length;
        if (!count) return 'Choose one or more Sendy lists.';
        return `${count} Sendy list${count === 1 ? '' : 's'} selected.`;
      }

      async function fetchJson(url) {
        const response = await fetch(url);
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || 'Sendy request failed.');
        return payload;
      }

      let brandsLoaded = false;

      async function loadBrands(preselectBrandId = '') {
        statusEl.textContent = 'Loading Sendy brands...';
        try {
          const payload = await fetchJson('{{ url_for("sendy_brands") }}');
          fillSelect(brandSelect, normalizeRows(payload.brands), 'Choose a brand');
          brandsLoaded = true;
          if (preselectBrandId) brandSelect.value = preselectBrandId;
          statusEl.textContent = preselectBrandId ? 'Client brand loaded.' : 'Brands loaded.';
        } catch (error) {
          statusEl.textContent = error.message;
        }
      }

      async function loadLists(brandId = '', preselectListId = '') {
        brandId = brandId || brandSelect.value || brandInput.value;
        if (!brandId) {
          statusEl.textContent = 'Choose or enter a Sendy brand ID first.';
          return;
        }
        brandInput.value = brandId;
        statusEl.textContent = 'Loading Sendy lists...';
        try {
          const payload = await fetchJson(`{{ url_for("sendy_lists") }}?brand_id=${encodeURIComponent(brandId)}`);
          fillSelect(listSelect, normalizeRows(payload.lists), 'No lists found for this brand');
          if (preselectListId) selectListIds(preselectListId);
          statusEl.textContent = preselectListId ? `Client lists loaded. ${listStatusText()}` : `Lists loaded. ${listStatusText()}`;
        } catch (error) {
          statusEl.textContent = error.message;
        }
      }

      document.getElementById('load-brands').addEventListener('click', () => loadBrands());
      document.getElementById('load-lists').addEventListener('click', () => loadLists());

      brandSelect.addEventListener('change', async () => {
        brandInput.value = brandSelect.value;
        listSelect.innerHTML = '<option value="">Load lists for this brand</option>';
        if (brandSelect.value) await loadLists(brandSelect.value);
      });

      listSelect.addEventListener('change', () => {
        statusEl.textContent = listStatusText();
      });

      async function loadClientConfig() {
        try {
          const payload = await fetchJson(`/client-config/${encodeURIComponent(clientSelect.value)}`);
          const config = payload.config || {};
          brandInput.value = config.sendy_brand_id || '';
          const fromName = config.from_name || '(not configured)';
          const fromEmail = config.from_email || '(not configured)';
          const replyTo = config.reply_to || '(not configured)';
          senderDefaults.textContent = `From: ${fromName} <${fromEmail}>. Reply-to: ${replyTo}.`;
          if (!brandsLoaded) await loadBrands(config.sendy_brand_id || '');
          if (config.sendy_brand_id) {
            brandSelect.value = config.sendy_brand_id;
            await loadLists(config.sendy_brand_id, config.sendy_list_id || '');
          }
          statusEl.textContent = config.sendy_brand_id ? 'Client Sendy destination loaded.' : 'No client Sendy brand configured.';
        } catch (error) {
          statusEl.textContent = error.message;
        }
      }

      clientSelect.addEventListener('change', loadClientConfig);
      window.addEventListener('DOMContentLoaded', loadClientConfig);
    </script>
  </body>
</html>
"""


REPORT_TEMPLATE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }} Run Report</title>
    <style>{{ BASE_CSS }}</style>
  </head>
  <body>
    <header>
      <div>
        <h1>Run Report</h1>
        <p>{{ summary.subject }}</p>
      </div>
    </header>
    <main>
      <section class="panel hero-report">
        <div>
          <div class="eyebrow">Campaign readiness</div>
          <h2>{{ "Ready for review" if ready_for_review else "Needs review" }}</h2>
          <p>
            {% if ready_for_review %}
              The campaign artifacts are generated and the Sendy draft/import payload is ready for operator review.
            {% else %}
              Review warnings, blocking checks, or skipped Sendy actions before sending.
            {% endif %}
          </p>
        </div>
        <div class="links">
          <a href="rendered_edm.html" target="_blank">Rendered EDM</a>
          <a href="verified_contacts.csv" target="_blank">Verified CSV</a>
          <a href="run_report.json" target="_blank">JSON report</a>
          <a href="invoice.html" target="_blank">Invoice</a>
          <a href="invoice_rows.csv" target="_blank">Invoice CSV</a>
        </div>
      </section>
      <section class="status-grid">
        <article class="panel"><div class="eyebrow">Mode</div><h2>{{ summary.mode|upper }}</h2></article>
        <article class="panel"><div class="eyebrow">Client</div><h2>{{ summary.client }}</h2></article>
        <article class="panel"><div class="eyebrow">Accepted</div><h2>{{ summary.accepted }}</h2></article>
        <article class="panel"><div class="eyebrow">Rejected</div><h2>{{ summary.rejected }}</h2></article>
        <article class="panel"><div class="eyebrow">Quarantined</div><h2>{{ summary.quarantined }}</h2></article>
        <article class="panel"><div class="eyebrow">Suppressed</div><h2>{{ summary.suppressed }}</h2></article>
        <article class="panel"><div class="eyebrow">Sendy imports</div><h2>{{ summary.sendy_imported }}</h2></article>
      </section>
      <section class="panel">
        <h2>Consent Attestation</h2>
        <p>Confirmed: {{ "yes" if summary.consent_confirmed else "no" }}</p>
        <p>Basis: {{ summary.consent_basis or "not recorded" }}</p>
      </section>
      <section class="panel">
        <h2>Campaign Metadata</h2>
        <table>
          <tbody>
            <tr><th>Subject</th><td>{{ summary.subject }}</td></tr>
            <tr><th>Email verification</th><td>{{ summary.email_verification }}</td></tr>
            <tr><th>Sendy import</th><td>{{ summary.sendy_import_mode }}</td></tr>
            <tr><th>Sendy campaign</th><td>{{ summary.sendy_campaign_mode }}</td></tr>
            <tr><th>Sendy brand</th><td>{{ summary.sendy_brand_id }}</td></tr>
            <tr><th>Sendy lists</th><td>{{ summary.sendy_list_id }}</td></tr>
            <tr><th>From</th><td>{{ summary.from_name }} &lt;{{ summary.from_email }}&gt;</td></tr>
            <tr><th>Reply-to</th><td>{{ summary.reply_to }}</td></tr>
            <tr><th>Suppression file</th><td>{{ summary.suppression_file or "none" }}</td></tr>
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Invoice</h2>
        <table>
          <tbody>
            <tr><th>Invoice ID</th><td>{{ summary.invoice_id }}</td></tr>
            <tr><th>Partner</th><td>{{ summary.invoice_partner }}</td></tr>
            <tr><th>Period</th><td>{{ summary.invoice_period }}</td></tr>
            <tr><th>Total</th><td>{{ summary.invoice_currency }} {{ summary.invoice_total }}</td></tr>
            <tr><th>Commission</th><td>{{ summary.invoice_currency }} {{ summary.invoice_commission }}</td></tr>
            <tr><th>Payable</th><td>{{ summary.invoice_currency }} {{ summary.invoice_payable }}</td></tr>
          </tbody>
        </table>
        <div class="links">
          <a href="invoice.html" target="_blank">Printable invoice</a>
          <a href="invoice_rows.csv" target="_blank">Google Sheet rows</a>
          <a href="invoice.json" target="_blank">Invoice JSON</a>
        </div>
      </section>
      <section class="panel">
        <h2>Processing Plan</h2>
        <ol>{% for action in plan.actions or [] %}<li>{{ action }}</li>{% endfor %}</ol>
      </section>
      <section class="panel">
        <h2>File Assessment</h2>
        <table>
          <thead><tr><th>Role</th><th>Path</th><th>Confidence</th><th>Reason</th></tr></thead>
          <tbody>
            {% for item in assessments %}
              <tr><td>{{ item.role }}</td><td>{{ item.path }}</td><td>{{ item.confidence }}</td><td>{{ item.reason }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Warnings</h2>
        {% if warnings %}<ul>{% for warning in warnings %}<li>{{ warning }}</li>{% endfor %}</ul>{% else %}<p>No warnings.</p>{% endif %}
      </section>
      <section class="panel">
        <h2>Deliverability Checks</h2>
        {% if blocking_checks %}
          <div class="alert">Blocking checks found. Live campaign creation should not proceed until these are fixed.</div>
        {% endif %}
        <table>
          <thead><tr><th>Severity</th><th>Code</th><th>Message</th></tr></thead>
          <tbody>
            {% for item in delivery_checks %}
              <tr><td>{{ item.severity }}</td><td>{{ item.code }}</td><td>{{ item.message }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </section>
      <section class="panel">
        <h2>Campaign Result</h2>
        <pre>{{ summary.campaign_result }}</pre>
      </section>
    </main>
  </body>
</html>
"""


BASE_CSS = """
:root { color-scheme: light; font-family: Inter, Arial, sans-serif; color: #17202a; background: #f4f6f8; }
* { box-sizing: border-box; }
body { margin: 0; }
header { display: flex; justify-content: space-between; gap: 20px; align-items: center; padding: 28px 40px; background: #ffffff; border-bottom: 1px solid #dde3ea; }
h1, h2, p { margin-top: 0; }
h1 { font-size: 24px; margin-bottom: 6px; }
h2 { font-size: 18px; margin-bottom: 14px; }
p { color: #5d6b7a; }
main { width: min(1120px, calc(100% - 32px)); margin: 24px auto 48px; }
.login-shell { min-height: 100vh; display: grid; place-items: center; }
.login-panel { width: min(420px, calc(100vw - 32px)); }
.panel { background: #ffffff; border: 1px solid #dde3ea; border-radius: 8px; padding: 20px; margin-bottom: 18px; box-shadow: 0 1px 2px rgba(16,24,40,0.04); }
.hero-report { display: flex; justify-content: space-between; gap: 20px; align-items: center; border-color: #b7d8c9; background: #f0fdf4; }
.status-grid, .summary-grid, .grid-two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.summary-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.summary-grid div { border: 1px solid #dde3ea; border-radius: 8px; padding: 14px; }
.summary-grid strong { display: block; font-size: 24px; }
.summary-grid span, .eyebrow { color: #697789; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
label { display: grid; gap: 7px; font-weight: 650; margin-bottom: 14px; }
input, select, textarea { width: 100%; border: 1px solid #c9d3df; border-radius: 6px; padding: 10px 11px; font: inherit; background: #fff; }
textarea { resize: vertical; }
button, .links a { display: inline-flex; align-items: center; justify-content: center; border: 0; border-radius: 6px; padding: 10px 14px; font-weight: 700; background: #1463ff; color: white; text-decoration: none; cursor: pointer; }
button.secondary { background: #eef2f6; color: #263241; }
.links { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.checks { display: flex; flex-wrap: wrap; gap: 18px; margin: 8px 0 18px; }
.checks label { display: flex; align-items: center; gap: 8px; margin: 0; font-weight: 600; }
.checks input { width: auto; }
.alert { background: #fff7ed; color: #9a3412; border: 1px solid #fed7aa; padding: 12px 14px; border-radius: 6px; margin-bottom: 18px; }
.notice { border: 1px solid #c7d2fe; background: #eef2ff; color: #263241; border-radius: 8px; padding: 12px 14px; margin-bottom: 18px; }
.notice p { margin: 6px 0 0; }
.muted { color: #5d6b7a; font-size: 13px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; border-bottom: 1px solid #e6ebf1; padding: 10px; vertical-align: top; }
pre { white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; padding: 14px; border-radius: 8px; overflow: auto; }
@media (max-width: 760px) { header { padding: 20px; align-items: flex-start; } .status-grid, .summary-grid, .grid-two { grid-template-columns: 1fr; } }
"""


def main() -> None:
    app = create_app()
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
