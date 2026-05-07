from pathlib import Path

from openpyxl import Workbook

from helium_edm.dashboard import create_app


def write_contact_workbook(path: Path) -> None:
    workbook = Workbook()
    contacts = workbook.active
    contacts.title = "Consented Contacts"
    contacts.append(["name", "email"])
    contacts.append(["Alice Example", "alice@example.com"])
    contacts.append(["Bob Example", "bob@example.com"])
    contacts.append(["Chen Mei", "chen.mei@example.com"])
    contacts.append(["Duplicate Alice", "alice@example.com"])
    contacts.append(["Bad Row", "not-an-email"])

    notes = workbook.create_sheet("Other Sheet")
    notes.append(["This sheet should not be selected as contacts"])
    workbook.save(path)


def test_dashboard_login_upload_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    protected = client.get("/")
    assert protected.status_code == 302
    assert protected.location.endswith("/login")

    presentation = client.get("/presentation")
    assert presentation.status_code == 200
    assert b"Helium EDM Intake Agent" in presentation.data
    assert b"speaker-note style narration" not in presentation.data
    assert b"https://demo.helium.sg/presentation" in presentation.data

    login = client.post("/login", data={"password": "test-pass"})
    assert login.status_code == 302

    workbook_path = tmp_path / "client-list-may.xlsx"
    write_contact_workbook(workbook_path)

    with open(workbook_path, "rb") as contacts:
        with open("samples/intake/export-growth-edm.html", "rb") as edm:
            with open("samples/suppression.csv", "rb") as suppression:
                response = client.post(
                    "/run",
                    data={
                        "client": "export-partner",
                        "consent_basis": "provided_client_consent",
                        "consent_confirmed": "on",
                        "dry_run": "on",
                        "import_to_sendy": "on",
                        "create_campaign": "on",
                        "invoice_partner": "Test Partner",
                        "invoice_currency": "SGD",
                        "invoice_campaign_fee": "100",
                        "invoice_verification_unit_fee": "0.01",
                        "invoice_sending_unit_fee": "0.005",
                        "invoice_commission_rate": "0.5",
                        "invoice_discount": "0",
                        "invoice_period": "May 2026",
                        "contacts": (contacts, "client-list-may.xlsx"),
                        "edm": (edm, "export-growth-edm.html"),
                        "notes": "Subject: Dashboard textarea campaign notes\nAudience: Existing consented sample list",
                        "suppression": (suppression, "suppression.csv"),
                    },
                    content_type="multipart/form-data",
                    follow_redirects=True,
                )

    assert response.status_code == 200
    assert b"Campaign processed" in response.data
    assert Path("runs/latest/index.html").exists()
    assert Path("runs/latest/run_report.json").exists()
    assert Path("runs/latest/rendered_edm.html").exists()
    assert Path("runs/latest/verified_contacts.csv").exists()
    assert Path("runs/latest/input/campaign-notes.txt").exists()
    assert Path("runs/latest/input/client-list-may__consented-contacts.csv").exists()
    assert Path("runs/latest/invoice.html").exists()
    assert Path("runs/latest/invoice_rows.csv").exists()
    assert Path("runs/latest/invoice.json").exists()

    report = Path("runs/latest/index.html").read_text(encoding="utf-8")
    assert "Consent Attestation" in report
    assert "Deliverability Checks" in report
    assert "Invoice" in report
    assert "Payable" in report
    assert "Suppressed" in report

    notes_text = Path("runs/latest/input/campaign-notes.txt").read_text(encoding="utf-8")
    assert "Dashboard textarea campaign notes" in notes_text

    csv_text = Path("runs/latest/verified_contacts.csv").read_text(encoding="utf-8")
    assert "suppressed: previous unsubscribe" in csv_text

    invoice_text = Path("runs/latest/invoice_rows.csv").read_text(encoding="utf-8")
    assert "Setup Cost" in invoice_text
    assert "Email Cleaning" in invoice_text
    assert "Email Sending" in invoice_text
    assert "PAYABLE" in invoice_text
