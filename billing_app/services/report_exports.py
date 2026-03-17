from __future__ import annotations

import html
from datetime import datetime


def _to_excel_html(title: str, headers: list[str], rows: list[list[object]]) -> bytes:
    header_markup = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    row_markup = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        row_markup.append(f"<tr>{cells}</tr>")

    document = f"""<html>
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
</head>
<body>
  <table border="1">
    <thead><tr>{header_markup}</tr></thead>
    <tbody>{''.join(row_markup)}</tbody>
  </table>
</body>
</html>"""
    return document.encode("utf-8")


def claims_export_bytes(claims: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Batch Date",
        "Claim ID",
        "Claim # del payer",
        "Paciente",
        "Member ID",
        "Payer",
        "Fecha servicio",
        "Cargo total",
        "Pagado",
        "Balance",
        "Estatus",
        "Transmission Status",
        "Transmitted At",
        "Tracking ID",
        "Source File",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            item.get("batch_date", ""),
            item.get("claim_id", ""),
            item.get("payer_claim_number", ""),
            item.get("patient_name", ""),
            item.get("member_id", ""),
            item.get("payer_name", ""),
            item.get("service_date", ""),
            f"{float(item.get('total_charge_amount', 0)):.2f}",
            f"{float(item.get('paid_amount', 0)):.2f}",
            f"{float(item.get('balance_amount', 0)):.2f}",
            str(item.get("status", "")).upper(),
            str(item.get("transmission_status", "")).upper(),
            item.get("transmitted_at", ""),
            item.get("tracking_id", ""),
            item.get("source_file_name", ""),
        ]
        for item in claims
    ]
    return _to_excel_html("Claims Export", headers, rows), f"claims_export_{datetime.now():%Y%m%d_%H%M}.xls"


def authorizations_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Authorization #",
        "Line",
        "Paciente",
        "Member ID",
        "Payer",
        "CPT",
        "Inicio",
        "Fin",
        "Total Units",
        "Remaining Units",
        "Estatus",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            item.get("authorization_number", ""),
            item.get("authorization_line_number", ""),
            item.get("patient_name", ""),
            item.get("patient_member_id", ""),
            item.get("payer_name", ""),
            item.get("cpt_code", ""),
            item.get("start_date", ""),
            item.get("end_date", ""),
            f"{float(item.get('total_units', 0)):.0f}",
            f"{float(item.get('remaining_units', 0)):.0f}",
            item.get("status_label", ""),
        ]
        for item in items
    ]
    return _to_excel_html("Authorizations Export", headers, rows), f"authorizations_export_{datetime.now():%Y%m%d_%H%M}.xls"


def roster_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Paciente",
        "Member ID",
        "Payer ID",
        "Provider NPI",
        "Fecha nacimiento",
        "Fecha servicio",
        "Ultimo resultado",
        "Ultima revision",
        "Proxima corrida",
        "Activa",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            f"{item.get('patient_first_name', '')} {item.get('patient_last_name', '')}".strip(),
            item.get("member_id", ""),
            item.get("payer_id", ""),
            item.get("provider_npi", ""),
            item.get("patient_birth_date", ""),
            item.get("service_date", ""),
            item.get("last_result", ""),
            item.get("last_checked_at", ""),
            item.get("next_run_date", ""),
            "Si" if item.get("active", True) else "No",
        ]
        for item in items
    ]
    return _to_excel_html("Eligibility Roster Export", headers, rows), f"eligibility_roster_{datetime.now():%Y%m%d_%H%M}.xls"


def clients_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Client ID",
        "Nombre",
        "Member ID",
        "Payer",
        "Payer ID",
        "Provider NPI",
        "Medicaid ID",
        "Fecha nacimiento",
        "Fecha servicio",
        "Documents Delivered",
        "Documents Total",
        "Documents Progress",
        "Ultimo resultado",
        "Ultima revision",
        "Auto elegibilidad",
        "Activa",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            item.get("client_id", ""),
            f"{item.get('first_name', '')} {item.get('last_name', '')}".strip(),
            item.get("member_id", ""),
            item.get("payer_name", ""),
            item.get("payer_id", ""),
            item.get("provider_npi", ""),
            item.get("medicaid_id", ""),
            item.get("birth_date", ""),
            item.get("service_date", ""),
            int(item.get("delivered_documents", 0) or 0),
            int(item.get("total_documents", 0) or 0),
            f"{int(item.get('progress_percent', 0) or 0)}%",
            item.get("last_eligibility_result", ""),
            item.get("last_eligibility_checked_at", ""),
            "Si" if item.get("auto_eligibility", True) else "No",
            "Si" if item.get("active", True) else "No",
        ]
        for item in items
    ]
    return _to_excel_html("Clients Export", headers, rows), f"clients_export_{datetime.now():%Y%m%d_%H%M}.xls"


def payer_enrollments_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Provider",
        "SSN",
        "NPI",
        "Medicaid ID",
        "Payer",
        "Enrollment Status",
        "Credentials Submitted",
        "Effective Date",
        "Expected Completion",
        "Days Remaining",
        "Notes",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            item.get("provider_name", ""),
            item.get("ssn", ""),
            item.get("npi", ""),
            item.get("medicaid_id", ""),
            item.get("payer_name", ""),
            item.get("enrollment_status", ""),
            item.get("credentials_submitted_date", ""),
            item.get("effective_date", ""),
            item.get("expected_completion_date", ""),
            item.get("days_remaining", ""),
            item.get("notes", ""),
        ]
        for item in items
    ]
    return _to_excel_html(
        "Payer Enrollments Export",
        headers,
        rows,
    ), f"payer_enrollments_{datetime.now():%Y%m%d_%H%M}.xls"


def agencies_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = ["Agency ID", "Agency", "Code", "Notification Email", "Contact", "Notes"]
    rows = [
        [
            item.get("agency_id", ""),
            item.get("agency_name", ""),
            item.get("agency_code", ""),
            item.get("notification_email", ""),
            item.get("contact_name", ""),
            item.get("notes", ""),
        ]
        for item in items
    ]
    return _to_excel_html("Agencies Export", headers, rows), f"agencies_{datetime.now():%Y%m%d_%H%M}.xls"


def provider_contracts_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Contract ID",
        "Provider",
        "Type",
        "NPI",
        "Stage",
        "Documents Delivered",
        "Total Documents",
        "Document Progress",
        "Stage Progress",
        "Start Date",
        "Expected Start",
        "Recruiter",
        "Notes",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            item.get("contract_id", ""),
            item.get("provider_name", ""),
            item.get("provider_type", ""),
            item.get("provider_npi", ""),
            item.get("contract_stage", ""),
            int(item.get("delivered_documents", 0) or 0),
            int(item.get("total_documents", 0) or 0),
            f"{int(item.get('progress_percent', 0) or 0)}%",
            f"{int(item.get('stage_progress_percent', 0) or 0)}%",
            item.get("start_date", ""),
            item.get("expected_start_date", ""),
            item.get("recruiter_name", ""),
            item.get("notes", ""),
        ]
        for item in items
    ]
    return _to_excel_html("Provider Contracts Export", headers, rows), f"provider_contracts_{datetime.now():%Y%m%d_%H%M}.xls"


def notifications_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Notification ID",
        "Category",
        "Subject",
        "Message",
        "Recipient",
        "Recipient Email",
        "Email Status",
        "Created At",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            item.get("notification_id", ""),
            item.get("category", ""),
            item.get("subject", ""),
            item.get("message", ""),
            item.get("recipient_label", ""),
            item.get("recipient_email", ""),
            item.get("email_status", ""),
            item.get("created_at", ""),
        ]
        for item in items
    ]
    return _to_excel_html("Notifications Export", headers, rows), f"notifications_{datetime.now():%Y%m%d_%H%M}.xls"


def era_archives_export_bytes(items: list[dict]) -> tuple[bytes, str]:
    headers = [
        "Agency",
        "Archive ID",
        "File Name",
        "Payer",
        "Payee",
        "Payment Amount",
        "Claim Count",
        "Updated Claims",
        "Imported At",
    ]
    rows = [
        [
            item.get("agency_name", ""),
            item.get("archive_id", ""),
            item.get("file_name", ""),
            item.get("payer_name", ""),
            item.get("payee_name", ""),
            f"{float(item.get('payment_amount', 0) or 0):.2f}",
            item.get("claim_count", ""),
            item.get("claim_updates_count", ""),
            item.get("imported_at", ""),
        ]
        for item in items
    ]
    return _to_excel_html("ERA Archives Export", headers, rows), f"era_archives_{datetime.now():%Y%m%d_%H%M}.xls"
