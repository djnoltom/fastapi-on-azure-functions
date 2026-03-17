from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET


SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAIN_NS = {"main": SPREADSHEET_NS, "r": DOCUMENT_REL_NS}

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "data" / "claimmd_exports"
READY_EXPORT_HEADERS = [
    "Excel Row",
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
    "Patient Full Name",
    "DOB Text",
    "Service Date Text",
]
INCOMPLETE_EXPORT_HEADERS = READY_EXPORT_HEADERS + ["Estado", "Faltantes"]
REQUIRED_FIELDS = {
    "Provider": "Provider",
    "Payer": "Payer",
    "Pat. Relationship": "Relationship",
    "Service Date": "Service Date",
    "Last Name": "Last Name",
    "First Name": "First Name",
    "DOB": "DOB",
    "Gender": "Gender",
    "Policy #": "Policy #",
}
VALID_GENDERS = {"M", "F", "U"}
EXCEL_EPOCH = date(1899, 12, 30)


@dataclass
class ClaimMDTemplateRow:
    row_number: int
    request_id: str = ""
    provider: str = ""
    payer: str = ""
    patient_relationship: str = ""
    service_date: str = ""
    last_name: str = ""
    first_name: str = ""
    middle: str = ""
    dob: str = ""
    gender: str = ""
    policy_number: str = ""
    procedure_code: str = ""
    estado: str = ""
    faltantes: list[str] = field(default_factory=list)
    patient_full_name: str = ""
    dob_text: str = ""
    service_date_text: str = ""

    def to_ready_record(self) -> dict[str, str]:
        return {
            "Excel Row": str(self.row_number),
            "Request ID": self.request_id,
            "Provider": self.provider,
            "Payer": self.payer,
            "Pat. Relationship": self.patient_relationship,
            "Service Date": self.service_date,
            "Last Name": self.last_name,
            "First Name": self.first_name,
            "Middle": self.middle,
            "DOB": self.dob,
            "Gender": self.gender,
            "Policy #": self.policy_number,
            "Procedure Code": self.procedure_code,
            "Patient Full Name": self.patient_full_name,
            "DOB Text": self.dob_text,
            "Service Date Text": self.service_date_text,
        }

    def to_incomplete_record(self) -> dict[str, str]:
        return {
            **self.to_ready_record(),
            "Estado": self.estado,
            "Faltantes": ", ".join(self.faltantes),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.to_ready_record(),
            "Estado": self.estado,
            "Faltantes": ", ".join(self.faltantes),
            "Faltantes List": self.faltantes,
        }


@dataclass
class ClaimMDTemplateResult:
    workbook_path: str
    total_rows_scanned: int
    blank_rows: int
    ready_rows: list[ClaimMDTemplateRow] = field(default_factory=list)
    incomplete_rows: list[ClaimMDTemplateRow] = field(default_factory=list)
    output_files: dict[str, str] = field(default_factory=dict)

    @property
    def used_rows(self) -> int:
        return len(self.ready_rows) + len(self.incomplete_rows)

    def to_dict(self) -> dict[str, object]:
        return {
            "workbook_path": self.workbook_path,
            "total_rows_scanned": self.total_rows_scanned,
            "blank_rows": self.blank_rows,
            "used_rows": self.used_rows,
            "ready_rows_count": len(self.ready_rows),
            "incomplete_rows_count": len(self.incomplete_rows),
            "output_files": self.output_files,
            "ready_rows": [row.to_dict() for row in self.ready_rows],
            "incomplete_rows": [row.to_dict() for row in self.incomplete_rows],
        }


def load_claimmd_template(workbook_path: str | Path) -> ClaimMDTemplateResult:
    workbook = Path(workbook_path).expanduser().resolve()
    if not workbook.is_file():
        raise FileNotFoundError(f"No encontre el template: {workbook}")

    row_maps = _read_sheet_rows(workbook, sheet_name="Entrada", header_row=4, data_start_row=5)
    ready_rows: list[ClaimMDTemplateRow] = []
    incomplete_rows: list[ClaimMDTemplateRow] = []
    blank_rows = 0

    for row_number, row_map in row_maps:
        row = _build_template_row(row_number, row_map)
        if _is_blank_input_row(row):
            blank_rows += 1
            continue
        if row.estado == "LISTO":
            ready_rows.append(row)
        else:
            incomplete_rows.append(row)

    return ClaimMDTemplateResult(
        workbook_path=str(workbook),
        total_rows_scanned=len(row_maps),
        blank_rows=blank_rows,
        ready_rows=ready_rows,
        incomplete_rows=incomplete_rows,
    )


def process_claimmd_template(
    workbook_path: str | Path,
    output_dir: str | Path | None = None,
) -> ClaimMDTemplateResult:
    result = load_claimmd_template(workbook_path)
    target_dir = Path(output_dir).expanduser() if output_dir else DEFAULT_OUTPUT_DIR
    result.output_files = export_claimmd_template(result, target_dir)
    return result


def export_claimmd_template(
    result: ClaimMDTemplateResult,
    output_dir: str | Path,
) -> dict[str, str]:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ready_path = target_dir / f"claimmd_ready_{timestamp}.csv"
    incomplete_path = target_dir / f"claimmd_incomplete_{timestamp}.csv"
    summary_path = target_dir / f"claimmd_summary_{timestamp}.json"

    _write_csv(ready_path, READY_EXPORT_HEADERS, [row.to_ready_record() for row in result.ready_rows])
    _write_csv(
        incomplete_path,
        INCOMPLETE_EXPORT_HEADERS,
        [row.to_incomplete_record() for row in result.incomplete_rows],
    )
    summary_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "ready_csv": str(ready_path),
        "incomplete_csv": str(incomplete_path),
        "summary_json": str(summary_path),
    }


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _build_template_row(row_number: int, row_map: dict[str, str]) -> ClaimMDTemplateRow:
    raw_service_date = _clean_text(row_map.get("Service Date", ""))
    raw_dob = _clean_text(row_map.get("DOB", ""))
    service_date, invalid_service_date = _normalize_date(raw_service_date)
    dob, invalid_dob = _normalize_date(raw_dob)

    gender = _clean_text(row_map.get("Gender", "")).upper()
    if gender not in VALID_GENDERS and gender:
        invalid_gender = True
    else:
        invalid_gender = False

    row = ClaimMDTemplateRow(
        row_number=row_number,
        request_id=_clean_text(row_map.get("Request ID", "")),
        provider=_clean_text(row_map.get("Provider", "")),
        payer=_clean_text(row_map.get("Payer", "")),
        patient_relationship=_clean_text(row_map.get("Pat. Relationship", "")),
        service_date=service_date,
        last_name=_clean_text(row_map.get("Last Name", "")),
        first_name=_clean_text(row_map.get("First Name", "")),
        middle=_clean_text(row_map.get("Middle", "")),
        dob=dob,
        gender=gender,
        policy_number=_clean_text(row_map.get("Policy #", "")),
        procedure_code=_clean_text(row_map.get("Procedure Code", "")),
    )
    row.patient_full_name = _build_full_name(row.first_name, row.middle, row.last_name)
    row.dob_text = row.dob
    row.service_date_text = row.service_date

    missing_fields = [
        label
        for header, label in REQUIRED_FIELDS.items()
        if not _clean_text(_value_for_header(row_map, header))
    ]
    invalid_fields: list[str] = []
    if raw_service_date and invalid_service_date:
        invalid_fields.append("Service Date (invalid)")
    if raw_dob and invalid_dob:
        invalid_fields.append("DOB (invalid)")
    if invalid_gender:
        invalid_fields.append("Gender (invalid)")

    row.faltantes = missing_fields + invalid_fields
    row.estado = "LISTO" if not row.faltantes else "INCOMPLETO"
    return row


def _is_blank_input_row(row: ClaimMDTemplateRow) -> bool:
    return not any(
        (
            row.provider,
            row.payer,
            row.patient_relationship,
            row.service_date,
            row.last_name,
            row.first_name,
            row.middle,
            row.dob,
            row.gender,
            row.policy_number,
            row.procedure_code,
        )
    )


def _value_for_header(row_map: dict[str, str], header: str) -> str:
    return row_map.get(header, "")


def _build_full_name(first_name: str, middle: str, last_name: str) -> str:
    return " ".join(part for part in [first_name, middle, last_name] if part).strip()


def _normalize_date(value: str) -> tuple[str, bool]:
    clean = _clean_text(value)
    if not clean:
        return "", False

    if _looks_like_number(clean):
        try:
            serial_value = float(clean)
            return (EXCEL_EPOCH + timedelta(days=int(serial_value))).strftime("%m/%d/%Y"), False
        except ValueError:
            return clean, True

    for fmt in ("%m/%d/%Y", "%Y%m%d", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(clean, fmt).strftime("%m/%d/%Y"), False
        except ValueError:
            continue
    return clean, True


def _looks_like_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _read_sheet_rows(
    workbook: Path,
    sheet_name: str,
    header_row: int,
    data_start_row: int,
) -> list[tuple[int, dict[str, str]]]:
    with ZipFile(workbook) as archive:
        shared_strings = _load_shared_strings(archive)
        sheet_path = _sheet_path_for_name(archive, sheet_name)
        root = ET.fromstring(archive.read(sheet_path))
        return _sheet_rows_from_xml(root, shared_strings, header_row, data_start_row)


def _sheet_path_for_name(archive: ZipFile, sheet_name: str) -> str:
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
    }

    for sheet in workbook_root.findall("main:sheets/main:sheet", MAIN_NS):
        if sheet.attrib.get("name") != sheet_name:
            continue
        relationship_id = sheet.attrib.get(f"{{{DOCUMENT_REL_NS}}}id")
        if not relationship_id:
            break
        target = relationship_map.get(relationship_id, "")
        if not target:
            break
        if target.startswith("/"):
            return target.lstrip("/")
        if target.startswith("xl/"):
            return target
        return f"xl/{target.lstrip('/')}"
    raise ValueError(f"No encontre la hoja '{sheet_name}' en el template.")


def _load_shared_strings(archive: ZipFile) -> list[str]:
    try:
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    values: list[str] = []
    for item in shared_root.findall(f"{{{SPREADSHEET_NS}}}si"):
        parts = [text_node.text or "" for text_node in item.findall(f".//{{{SPREADSHEET_NS}}}t")]
        values.append("".join(parts))
    return values


def _sheet_rows_from_xml(
    sheet_root: ET.Element,
    shared_strings: list[str],
    header_row: int,
    data_start_row: int,
) -> list[tuple[int, dict[str, str]]]:
    rows: list[tuple[int, dict[str, str]]] = []
    headers: dict[int, str] = {}

    for row in sheet_root.findall(f"{{{SPREADSHEET_NS}}}sheetData/{{{SPREADSHEET_NS}}}row"):
        row_number = int(row.attrib.get("r", "0"))
        cell_map: dict[int, str] = {}
        for cell in row.findall(f"{{{SPREADSHEET_NS}}}c"):
            ref = cell.attrib.get("r", "")
            if not ref:
                continue
            cell_map[_column_number(ref)] = _cell_text(cell, shared_strings)

        if row_number == header_row:
            headers = {
                index: _clean_text(value)
                for index, value in sorted(cell_map.items())
                if _clean_text(value)
            }
            continue

        if row_number < data_start_row or not headers:
            continue

        row_map = {header: cell_map.get(index, "") for index, header in headers.items()}
        rows.append((row_number, row_map))

    return rows


def _cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(text_node.text or "" for text_node in cell.findall(f".//{{{SPREADSHEET_NS}}}t"))

    value_node = cell.find(f"{{{SPREADSHEET_NS}}}v")
    if value_node is None or value_node.text is None:
        return ""

    value = value_node.text
    if cell_type == "s":
        index = int(value)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return _normalize_numeric_text(value)


def _column_number(cell_reference: str) -> int:
    letters = "".join(char for char in cell_reference if char.isalpha())
    index = 0
    for char in letters.upper():
        index = (index * 26) + (ord(char) - 64)
    return index


def _normalize_numeric_text(value: str) -> str:
    try:
        numeric = float(value)
    except ValueError:
        return value
    if numeric.is_integer():
        return str(int(numeric))
    return value
