from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, flash, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from helium_edm.cli import (
    FileAssessment,
    IntakePlan,
    build_intake_plan,
    env,
    load_dotenv,
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

    @app.before_request
    def require_login() -> Response | None:
        if request.endpoint in {"login", "login_post"}:
            return None
        if session.get("authenticated"):
            return None
        return redirect(url_for("login"))

    @app.get("/login")
    def login() -> str:
        return render_template_string(LOGIN_TEMPLATE, title=APP_TITLE, BASE_CSS=BASE_CSS)

    @app.post("/login")
    def login_post() -> Response:
        password = request.form.get("password", "")
        expected = env("DASHBOARD_PASSWORD", "admin")
        if password == expected:
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
            sendy_discovery=sendy_discovery(),
            latest=latest,
        )

    @app.post("/run")
    def run_campaign() -> Response:
        try:
            client = slugify_client(request.form.get("client", "default"))
            subject = request.form.get("subject", "").strip()
            list_id = request.form.get("list_id", "").strip()
            brand_id = request.form.get("brand_id", "").strip()
            client_note = request.form.get("client_note", "").strip()
            dry_run = request.form.get("dry_run") == "on"
            import_to_sendy = request.form.get("import_to_sendy") == "on"
            create_campaign = request.form.get("create_campaign") == "on"

            if not list_id:
                raise ValueError("Sendy list ID is required.")
            if create_campaign and not brand_id:
                raise ValueError("Sendy brand ID is required to create a draft campaign.")

            run_id = time.strftime("%Y%m%d-%H%M%S")
            output_dir = Path("runs") / run_id
            input_dir = output_dir / "input"
            input_dir.mkdir(parents=True, exist_ok=True)

            save_upload(request.files.get("contacts"), input_dir, "contacts")
            save_upload(request.files.get("edm"), input_dir, "edm")
            save_optional_upload(request.files.get("notes"), input_dir)

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
                client_note=client_note,
                dry_run=dry_run,
                import_to_sendy=import_to_sendy,
                create_campaign=create_campaign,
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

    return app


def list_clients() -> list[str]:
    client_dir = Path("templates/clients")
    if not client_dir.exists():
        return ["default"]
    clients = sorted(path.name for path in client_dir.iterdir() if path.is_dir())
    return clients or ["default"]


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


def sendy_discovery() -> dict[str, Any]:
    if not env("SENDY_BASE_URL") or not env("SENDY_API_KEY"):
        return {"enabled": False, "brands": None, "error": "Sendy is not configured."}
    try:
        brands = SendyClient(env("SENDY_BASE_URL"), env("SENDY_API_KEY"), dry_run=False).get_brands()
        return {"enabled": True, "brands": brands, "error": ""}
    except Exception as exc:
        return {"enabled": True, "brands": None, "error": str(exc)}


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
    return render_template_string(
        REPORT_TEMPLATE,
        title=APP_TITLE,
        BASE_CSS=BASE_CSS,
        summary=summary,
        plan=plan,
        assessments=assessments,
        warnings=warnings,
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
        {% if sendy_discovery.enabled %}
          <div class="notice">
            <strong>Sendy discovery</strong>
            {% if sendy_discovery.error %}
              <p>{{ sendy_discovery.error }}</p>
            {% else %}
              <pre>{{ sendy_discovery.brands }}</pre>
            {% endif %}
          </div>
        {% endif %}
        <form class="campaign-form" action="{{ url_for('run_campaign') }}" method="post" enctype="multipart/form-data">
          <div class="grid-two">
            <label>Client
              <select name="client">
                {% for client in clients %}
                  <option value="{{ client }}">{{ client }}</option>
                {% endfor %}
              </select>
            </label>
            <label>Subject
              <input name="subject" placeholder="Optional if notes or H1 contains a subject">
            </label>
          </div>
          <div class="grid-two">
            <label>Sendy list ID
              <input name="list_id" required placeholder="Sendy recipient list ID">
            </label>
            <label>Sendy brand ID
              <input name="brand_id" placeholder="Required for draft creation">
            </label>
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
            <input name="notes" type="file" accept=".txt,.md,.json">
          </label>
          <label>Operator note
            <textarea name="client_note" rows="3" placeholder="Context for AI preflight"></textarea>
          </label>
          <div class="checks">
            <label><input name="dry_run" type="checkbox" checked> Dry run</label>
            <label><input name="import_to_sendy" type="checkbox" checked> Upload accepted contacts to Sendy</label>
            <label><input name="create_campaign" type="checkbox" checked> Create Sendy draft campaign</label>
          </div>
          <button type="submit">Process Campaign</button>
        </form>
      </section>

      {% if latest %}
        <section class="panel">
          <h2>Latest Run</h2>
          <div class="summary-grid">
            <div><strong>{{ latest.summary.client }}</strong><span>Client</span></div>
            <div><strong>{{ latest.summary.accepted }}</strong><span>Accepted</span></div>
            <div><strong>{{ latest.summary.rejected }}</strong><span>Rejected</span></div>
            <div><strong>{{ latest.summary.warnings }}</strong><span>Warnings</span></div>
          </div>
          <div class="links">
            <a href="{{ url_for('static_run_file', filename='latest/index.html') }}" target="_blank">Human report</a>
            <a href="{{ url_for('static_run_file', filename='latest/rendered_edm.html') }}" target="_blank">Rendered EDM</a>
            <a href="{{ url_for('static_run_file', filename='latest/verified_contacts.csv') }}" target="_blank">Verified CSV</a>
            <a href="{{ url_for('static_run_file', filename='latest/run_report.json') }}" target="_blank">JSON report</a>
          </div>
        </section>
      {% endif %}
    </main>
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
      <section class="status-grid">
        <article class="panel"><div class="eyebrow">Client</div><h2>{{ summary.client }}</h2></article>
        <article class="panel"><div class="eyebrow">Accepted</div><h2>{{ summary.accepted }}</h2></article>
        <article class="panel"><div class="eyebrow">Rejected</div><h2>{{ summary.rejected }}</h2></article>
        <article class="panel"><div class="eyebrow">Imported</div><h2>{{ summary.sendy_imported }}</h2></article>
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
