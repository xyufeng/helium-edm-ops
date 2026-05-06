from pathlib import Path

from helium_edm.dashboard import create_app


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

    login = client.post("/login", data={"password": "test-pass"})
    assert login.status_code == 302

    with open("samples/intake/client-list-may.csv", "rb") as contacts:
        with open("samples/intake/export-growth-edm.html", "rb") as edm:
            with open("samples/intake/campaign-notes.txt", "rb") as notes:
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
                            "contacts": (contacts, "client-list-may.csv"),
                            "edm": (edm, "export-growth-edm.html"),
                            "notes": (notes, "campaign-notes.txt"),
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

    report = Path("runs/latest/index.html").read_text(encoding="utf-8")
    assert "Consent Attestation" in report
    assert "Deliverability Checks" in report
    assert "Suppressed" in report

    csv_text = Path("runs/latest/verified_contacts.csv").read_text(encoding="utf-8")
    assert "suppressed: previous unsubscribe" in csv_text
