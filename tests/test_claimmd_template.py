import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from billing_app.services.claimmd_template import load_claimmd_template, process_claimmd_template


SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
HEADERS = [
    "Request ID",
    "Provider",
    "Payer",
    "Pat. Relationship",
    "Service Date",
    "Last Name",
    "First Name",
    "Middle",
    "DOB",
    "Gender",
    "Policy #",
    "Procedure Code",
]


def _inline_cell(reference: str, value: str) -> str:
    if value == "":
        return f'<c r="{reference}" t="inlineStr"><is><t></t></is></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'


def _shared_cell(reference: str, index: int) -> str:
    return f'<c r="{reference}" t="s"><v>{index}</v></c>'


def _number_cell(reference: str, value: str) -> str:
    return f'<c r="{reference}" t="n"><v>{value}</v></c>'


def _build_row(row_number: int, cells: list[str]) -> str:
    return f'<row r="{row_number}">{"".join(cells)}</row>'


def _write_workbook(path: Path, sheet_xml: str, shared_strings: list[str] | None = None) -> None:
    workbook_xml = (
        f'<workbook xmlns="{SPREADSHEET_NS}" xmlns:r="{DOCUMENT_REL_NS}">'
        '<sheets><sheet name="Entrada" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    rels_xml = (
        f'<Relationships xmlns="{PACKAGE_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if shared_strings is not None:
            shared_xml = (
                f'<sst xmlns="{SPREADSHEET_NS}" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">'
                + "".join(f"<si><t>{value}</t></si>" for value in shared_strings)
                + "</sst>"
            )
            archive.writestr("xl/sharedStrings.xml", shared_xml)


class ClaimMDTemplateTests(unittest.TestCase):
    def test_process_claimmd_template_exports_ready_and_incomplete_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workbook_path = Path(tmp_dir) / "claimmd_template.xlsx"
            output_dir = Path(tmp_dir) / "exports"
            sheet_xml = (
                f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>'
                + _build_row(4, [_inline_cell(f"{column}4", header) for column, header in zip("ABCDEFGHIJKL", HEADERS)])
                + _build_row(
                    5,
                    [
                        _inline_cell("A5", "REQ-0001"),
                        _inline_cell("B5", "BLUE HOPE BEHAVI"),
                        _inline_cell("C5", "Sunshine Health"),
                        _inline_cell("D5", "Self"),
                        _number_cell("E5", "46092"),
                        _inline_cell("F5", "Perez"),
                        _inline_cell("G5", "Juan"),
                        _inline_cell("H5", ""),
                        _number_cell("I5", "42019"),
                        _inline_cell("J5", "M"),
                        _inline_cell("K5", "1234567890"),
                        _inline_cell("L5", "97153"),
                    ],
                )
                + _build_row(
                    6,
                    [
                        _inline_cell("A6", "REQ-0002"),
                        _inline_cell("B6", "BLUE HOPE BEHAVI"),
                        _inline_cell("C6", ""),
                        _inline_cell("D6", "Self"),
                        _inline_cell("E6", "03/11/2026"),
                        _inline_cell("F6", "Lopez"),
                        _inline_cell("G6", "Ana"),
                        _inline_cell("H6", ""),
                        _inline_cell("I6", "01/15/2015"),
                        _inline_cell("J6", "F"),
                        _inline_cell("K6", "ABC123"),
                        _inline_cell("L6", ""),
                    ],
                )
                + _build_row(7, [_inline_cell("A7", "REQ-0003")])
                + "</sheetData></worksheet>"
            )
            _write_workbook(workbook_path, sheet_xml)

            result = process_claimmd_template(workbook_path, output_dir)

            self.assertEqual(result.total_rows_scanned, 3)
            self.assertEqual(result.blank_rows, 1)
            self.assertEqual(result.used_rows, 2)
            self.assertEqual(len(result.ready_rows), 1)
            self.assertEqual(len(result.incomplete_rows), 1)
            self.assertEqual(result.ready_rows[0].service_date, "03/11/2026")
            self.assertEqual(result.ready_rows[0].dob, "01/15/2015")
            self.assertEqual(result.ready_rows[0].patient_full_name, "Juan Perez")
            self.assertEqual(result.incomplete_rows[0].estado, "INCOMPLETO")
            self.assertIn("Payer", result.incomplete_rows[0].faltantes)

            summary_path = Path(result.output_files["summary_json"])
            ready_path = Path(result.output_files["ready_csv"])
            incomplete_path = Path(result.output_files["incomplete_csv"])

            self.assertTrue(summary_path.is_file())
            self.assertTrue(ready_path.is_file())
            self.assertTrue(incomplete_path.is_file())

            summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary_payload["ready_rows_count"], 1)
            self.assertEqual(summary_payload["incomplete_rows_count"], 1)
            self.assertEqual(summary_payload["blank_rows"], 1)

            ready_csv = ready_path.read_text(encoding="utf-8-sig")
            incomplete_csv = incomplete_path.read_text(encoding="utf-8-sig")
            self.assertIn("Request ID", ready_csv)
            self.assertIn("REQ-0001", ready_csv)
            self.assertIn("REQ-0002", incomplete_csv)

    def test_load_claimmd_template_supports_shared_strings_and_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workbook_path = Path(tmp_dir) / "claimmd_shared.xlsx"
            shared_strings = HEADERS + [
                "REQ-0100",
                "Provider Demo",
                "Payer Demo",
                "Self",
                "Garcia",
                "Maria",
                "f",
                "POL-1",
                "97151",
                "REQ-0101",
                "Provider Demo",
                "Payer Demo",
                "Self",
                "Rios",
                "Leo",
                "X",
                "POL-2",
            ]
            sheet_xml = (
                f'<worksheet xmlns="{SPREADSHEET_NS}"><sheetData>'
                + _build_row(4, [_shared_cell(f"{column}4", index) for index, column in enumerate("ABCDEFGHIJKL")])
                + _build_row(
                    5,
                    [
                        _shared_cell("A5", 12),
                        _shared_cell("B5", 13),
                        _shared_cell("C5", 14),
                        _shared_cell("D5", 15),
                        _inline_cell("E5", "2026-03-11"),
                        _shared_cell("F5", 16),
                        _shared_cell("G5", 17),
                        _inline_cell("H5", ""),
                        _inline_cell("I5", "2015-01-15"),
                        _shared_cell("J5", 18),
                        _shared_cell("K5", 19),
                        _shared_cell("L5", 20),
                    ],
                )
                + _build_row(
                    6,
                    [
                        _shared_cell("A6", 21),
                        _shared_cell("B6", 22),
                        _shared_cell("C6", 23),
                        _shared_cell("D6", 24),
                        _inline_cell("E6", "03/11/2026"),
                        _shared_cell("F6", 25),
                        _shared_cell("G6", 26),
                        _inline_cell("H6", ""),
                        _inline_cell("I6", "not-a-date"),
                        _shared_cell("J6", 27),
                        _shared_cell("K6", 28),
                        _inline_cell("L6", ""),
                    ],
                )
                + "</sheetData></worksheet>"
            )
            _write_workbook(workbook_path, sheet_xml, shared_strings=shared_strings)

            result = load_claimmd_template(workbook_path)

            self.assertEqual(len(result.ready_rows), 1)
            self.assertEqual(result.ready_rows[0].gender, "F")
            self.assertEqual(result.ready_rows[0].service_date, "03/11/2026")
            self.assertEqual(result.ready_rows[0].dob, "01/15/2015")
            self.assertEqual(len(result.incomplete_rows), 1)
            self.assertIn("DOB (invalid)", result.incomplete_rows[0].faltantes)
            self.assertIn("Gender (invalid)", result.incomplete_rows[0].faltantes)


if __name__ == "__main__":
    unittest.main()
