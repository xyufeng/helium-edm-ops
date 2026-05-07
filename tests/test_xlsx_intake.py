from pathlib import Path

from openpyxl import Workbook

from helium_edm.cli import build_intake_plan, convert_xlsx_to_csvs


def write_workbook(path: Path) -> None:
    workbook = Workbook()
    contacts = workbook.active
    contacts.title = "Consented Leads"
    contacts.append(["name", "email", "company"])
    contacts.append(["Alice Tan", "alice@example.com", "Example Co"])
    contacts.append(["Ben Lim", "ben@example.com", "Helium"])

    notes = workbook.create_sheet("Notes")
    notes.append(["Campaign", "May briefing"])
    notes.append(["Owner", "Helium"])

    empty = workbook.create_sheet("Empty Sheet")
    empty.append([None, None])

    workbook.save(path)


def test_convert_xlsx_to_csvs_writes_one_csv_per_non_empty_sheet(tmp_path):
    workbook_path = tmp_path / "client-list.xlsx"
    write_workbook(workbook_path)

    converted = convert_xlsx_to_csvs(workbook_path)

    assert [path.name for path in converted] == [
        "client-list__consented-leads.csv",
        "client-list__notes.csv",
    ]
    assert "alice@example.com" in converted[0].read_text(encoding="utf-8")


def test_convert_xlsx_to_csvs_is_idempotent(tmp_path):
    workbook_path = tmp_path / "client-list.xlsx"
    write_workbook(workbook_path)

    first = convert_xlsx_to_csvs(workbook_path)
    second = convert_xlsx_to_csvs(workbook_path)

    assert [path.name for path in first] == [path.name for path in second]
    assert not list(tmp_path.glob("*-2.csv"))


def test_build_intake_plan_classifies_generated_xlsx_sheet_csv(tmp_path):
    workbook_path = tmp_path / "client-list.xlsx"
    html_path = tmp_path / "campaign.html"
    header_path = tmp_path / "header.html"
    footer_path = tmp_path / "footer.html"
    write_workbook(workbook_path)
    html_path.write_text("<html><body><h1>May briefing</h1><p>Hello</p></body></html>", encoding="utf-8")
    header_path.write_text("<header>Header</header>", encoding="utf-8")
    footer_path.write_text("<footer>Footer</footer>", encoding="utf-8")

    plan, assessments = build_intake_plan(tmp_path, "", "test", header_path, footer_path)

    assert plan.contacts_path.endswith("client-list__consented-leads.csv")
    assert Path(plan.contacts_path).exists()
    assert any(item.path.endswith("client-list.xlsx") and item.role == "unknown" for item in assessments)
    assert any(item.path.endswith("client-list__notes.csv") and item.role == "unknown" for item in assessments)
