from __future__ import annotations

import base64
from html import escape
import mimetypes
from pathlib import Path
import re
from typing import Any

from billing_app.services.local_store import get_agency_logo_bytes, get_current_agency, load_agencies


SERVICE_LOG_SECTION_TITLES = {
    "Case Overview",
    "Authorization Summary",
    "Session Log Table",
    "Totals",
    "Review Status",
    "Signatures",
    "Notes",
}


def export_note(
    *,
    title: str,
    body: str,
    output_dir: Path,
    stem: str,
    format_name: str,
    agency_id: str = "",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized = format_name.lower()
    if normalized == "txt":
        path = output_dir / f"{stem}.txt"
        path.write_text(body, encoding="utf-8")
        return path
    if normalized == "html":
        path = output_dir / f"{stem}.html"
        path.write_text(render_note_html_document(title=title, body=body, agency_id=agency_id), encoding="utf-8")
        return path
    if normalized == "doc":
        path = output_dir / f"{stem}.doc"
        path.write_text(render_note_html_document(title=title, body=body, agency_id=agency_id), encoding="utf-8")
        return path
    if normalized == "pdf":
        path = output_dir / f"{stem}.pdf"
        _write_simple_pdf(path=path, title=title, body=body)
        return path
    raise ValueError(f"Unsupported export format: {format_name}")


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.rstrip(". ") or "document"


def render_note_html_document(*, title: str, body: str, agency_id: str = "") -> str:
    return _render_html(title=title, body=body, agency_id=agency_id)


def _render_html(*, title: str, body: str, agency_id: str = "") -> str:
    normalized_title = title.strip().lower()
    if "appointment note" in normalized_title:
        return _render_appointment_note_html(title=title, body=body, agency_id=agency_id)
    if "service log" in normalized_title:
        return _render_service_log_html(title=title, body=body, agency_id=agency_id)

    lines = "<br>\n".join(escape(line) for line in body.splitlines())
    return _wrap_html(
        title=title,
        body_html=f"<div class=\"content\">{lines}</div>",
        agency_id=agency_id,
    )


def _agency_document_context(agency_id: str = "") -> dict[str, str]:
    clean_agency_id = str(agency_id or "").strip()
    agency = (
        next((item for item in load_agencies() if str(item.get("agency_id", "")).strip() == clean_agency_id), None)
        if clean_agency_id
        else None
    )
    if agency is None:
        agency = get_current_agency() or {}
    agency_id = str(agency.get("agency_id", "")).strip()
    agency_name = str(agency.get("agency_name", "")).strip() or "Blue Hope ABA Solutions"
    agency_code = str(agency.get("agency_code", "")).strip()
    contact_name = str(agency.get("contact_name", "")).strip()
    address_parts = [
        str(agency.get("address", "")).strip(),
        str(agency.get("street_address", "")).strip(),
        str(agency.get("suite", "")).strip(),
        " ".join(
            piece
            for piece in [
                str(agency.get("city", "")).strip(),
                str(agency.get("state", "")).strip(),
                str(agency.get("zip_code", "")).strip(),
            ]
            if piece
        ).strip(),
    ]
    address_html = "<br>".join(escape(part) for part in address_parts if part) or "Address not configured"
    phone = (
        str(agency.get("phone", "")).strip()
        or str(agency.get("office_phone", "")).strip()
        or str(agency.get("contact_phone", "")).strip()
        or "-"
    )
    fax = (
        str(agency.get("fax", "")).strip()
        or str(agency.get("office_fax", "")).strip()
        or str(agency.get("contact_fax", "")).strip()
    )
    email = (
        str(agency.get("notification_email", "")).strip()
        or str(agency.get("email", "")).strip()
        or "-"
    )

    logo_markup = ""
    if agency_id:
        try:
            logo_bytes, filename = get_agency_logo_bytes(agency_id)
        except Exception:
            logo_bytes, filename = b"", ""
        if logo_bytes:
            mime_type = mimetypes.guess_type(filename or "logo.png")[0] or "image/png"
            encoded = base64.b64encode(logo_bytes).decode("ascii")
            logo_markup = (
                '<div class="header-logo-shell">'
                f'<img class="header-logo" src="data:{mime_type};base64,{encoded}" alt="{escape(agency_name)} logo">'
                "</div>"
            )
    if not logo_markup:
        initials = "".join(part[:1] for part in agency_name.split()[:2]).upper() or "BH"
        logo_markup = f'<div class="header-logo-shell header-logo-fallback">{escape(initials)}</div>'

    return {
        "agency_name": agency_name,
        "agency_code": agency_code,
        "contact_name": contact_name,
        "address_html": address_html,
        "phone": phone,
        "fax": fax,
        "email": email,
        "logo_markup": logo_markup,
    }


def _document_header_markup(agency_id: str = "") -> str:
    context = _agency_document_context(agency_id)
    agency_meta = []
    if context["agency_code"]:
        agency_meta.append(f'Code: {escape(context["agency_code"])}')
    if context["contact_name"]:
        agency_meta.append(f'Contact: {escape(context["contact_name"])}')
    if context["fax"]:
        agency_meta.append(f'Fax: {escape(context["fax"])}')
    agency_meta_html = "<br>".join(agency_meta)
    org_lines = [f'<strong>{escape(context["agency_name"])}</strong>']
    if agency_meta_html:
        org_lines.append(agency_meta_html)
    org_lines.append(f'{context["address_html"]}')
    org_lines.append(f'Phone: {escape(context["phone"])}')
    org_lines.append(f'Email: {escape(context["email"])}')
    return (
        '<header class="document-header">'
        + f'{context["logo_markup"]}'
        + '<div class="org">'
        + "<br>".join(org_lines)
        + "</div>"
        + "</header>"
    )


def _wrap_html(*, title: str, body_html: str, agency_id: str = "") -> str:
    return (
        "<!doctype html>\n"
        "<html><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title>"
        "<style>"
        "@page{margin:.55in;}"
        "*{box-sizing:border-box;}"
        "body{font-family:'Open Sans',Helvetica,Arial,sans-serif;margin:0;background:#eef3f8;color:#20364b;-webkit-print-color-adjust:exact;print-color-adjust:exact;}"
        ".page{max-width:1040px;margin:24px auto;background:#fff;padding:32px 36px 40px;border:1px solid #d8e3ef;border-radius:24px;box-shadow:0 18px 48px rgba(20,54,96,.10);}"
        ".document-header,.header{display:grid;grid-template-columns:140px 1fr;gap:24px;align-items:center;padding-bottom:20px;border-bottom:1px solid #d7e1ec;margin-bottom:22px;}"
        ".header img,.header-logo{width:120px;max-width:120px;max-height:88px;object-fit:contain;justify-self:center;}"
        ".header-logo-shell{min-height:88px;display:flex;align-items:center;justify-content:center;border:1px solid #e1e8f0;border-radius:18px;background:#fff;}"
        ".header-logo-fallback{font-size:30px;font-weight:800;color:#1d4f91;background:linear-gradient(135deg,#ecf5ff,#f8fdff);}"
        ".org{font-size:13px;line-height:1.6;color:#587089;}"
        ".org strong{display:block;color:#183652;font-size:20px;font-weight:800;margin-bottom:6px;}"
        ".doc-title{text-align:center;font-size:28px;font-weight:800;letter-spacing:.08em;color:#183652;margin:24px 0 10px;}"
        ".doc-subtitle{text-align:center;color:#5f7892;font-size:13px;margin:0 0 24px;}"
        ".doc-section{margin-top:18px;border:1px solid #d9e4ef;border-radius:18px;overflow:hidden;background:#fbfdff;page-break-inside:avoid;}"
        ".doc-section-header,.section-title{padding:10px 16px;background:#edf1f5;color:#40566f;font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;}"
        ".doc-section-body{padding:16px;}"
        ".overview-grid,.review-grid{display:grid;gap:12px;grid-template-columns:repeat(4,minmax(0,1fr));}"
        ".summary-grid{display:grid;gap:12px;grid-template-columns:repeat(6,minmax(0,1fr));}"
        ".field-card,.summary-card,.total-card,.signature-card{border:1px solid #dbe5ee;border-radius:14px;background:#fff;padding:12px 14px;}"
        ".summary-card{background:linear-gradient(180deg,#f8fbff,#eef5fb);min-height:84px;}"
        ".field-card{min-height:76px;}"
        ".field-label{display:block;margin-bottom:6px;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6f8498;}"
        ".field-value{display:block;font-size:14px;font-weight:600;color:#17344e;line-height:1.45;white-space:pre-wrap;}"
        ".summary-card strong,.total-card strong{display:block;margin-bottom:8px;font-size:11px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;color:#6a8096;}"
        ".summary-card span,.total-card span{display:block;font-size:20px;font-weight:800;color:#17395f;line-height:1.2;}"
        ".summary-card small,.total-card small{display:block;margin-top:6px;font-size:12px;color:#5f7892;line-height:1.35;}"
        ".auth-usage-grid{display:grid;gap:14px;grid-template-columns:repeat(2,minmax(0,1fr));margin-top:16px;}"
        ".auth-usage-card{border:1px solid #dbe5ee;border-radius:16px;background:#fff;padding:14px 16px;}"
        ".auth-usage-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;}"
        ".auth-usage-code{font-size:14px;font-weight:800;color:#17395f;letter-spacing:.04em;}"
        ".auth-usage-values{font-size:12px;color:#5f7892;text-align:right;line-height:1.45;}"
        ".auth-usage-track{display:flex;overflow:hidden;height:10px;border-radius:999px;background:#e7eef6;}"
        ".auth-usage-segment.used{background:#2d7ff9;}"
        ".auth-usage-segment.remaining{background:#7bd3bc;}"
        ".auth-usage-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px;}"
        ".auth-usage-meta span{display:block;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#6f8498;}"
        ".auth-usage-meta strong{display:block;margin-top:4px;font-size:13px;color:#17395f;}"
        ".status-pill{display:inline-flex;align-items:center;justify-content:center;min-height:28px;padding:4px 12px;border-radius:999px;font-size:12px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;}"
        ".status-pill.neutral{background:#edf2f7;color:#486176;}"
        ".status-pill.success{background:#e7f7ef;color:#1d7f54;}"
        ".status-pill.warn{background:#fff4df;color:#a76700;}"
        ".status-pill.danger{background:#fde9ea;color:#b02c3f;}"
        ".table-wrap{border:1px solid #dbe5ee;border-radius:16px;overflow:hidden;background:#fff;}"
        ".service-log-table,table{width:100%;border-collapse:separate;border-spacing:0;}"
        ".service-log-table thead th,table th{background:#f5f8fb;color:#52697f;font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;padding:12px 10px;border-bottom:1px solid #dbe5ee;text-align:left;}"
        ".service-log-table tbody td,table td{padding:12px 10px;font-size:13px;color:#21384f;vertical-align:top;border-bottom:1px solid #e8eef5;}"
        ".service-log-table tbody tr:nth-child(even){background:#fbfdff;}"
        "table{margin:0 0 16px;}"
        ".label{font-weight:700;display:block;margin-bottom:4px;color:#4f667d;font-size:11px;letter-spacing:.05em;text-transform:uppercase;}"
        ".content{white-space:pre-wrap;line-height:1.7;border:1px solid #d7dfeb;padding:18px;background:#fff;border-radius:16px;color:#1e3850;}"
        ".totals-grid{display:grid;gap:12px;grid-template-columns:repeat(3,minmax(0,1fr));}"
        ".totals-highlight{background:linear-gradient(180deg,#f8fbff,#eef5fb);}"
        ".review-grid .field-card{min-height:88px;}"
        ".signature-grid{display:grid;gap:14px;grid-template-columns:repeat(3,minmax(0,1fr));margin-top:18px;}"
        ".signature-preview{min-height:82px;padding-bottom:8px;border-bottom:1px solid #9eb0c3;display:flex;align-items:flex-end;color:#234460;font-size:14px;font-weight:600;}"
        ".signature-preview img{display:block;max-width:100%;max-height:72px;object-fit:contain;}"
        ".signature-date{margin-top:10px;font-size:12px;color:#607991;}"
        ".notes-block{margin-top:18px;border:1px solid #dbe5ee;border-radius:16px;padding:16px;background:#fff;white-space:pre-wrap;line-height:1.6;}"
        ".muted{color:#5c6878;}"
        ".muted-placeholder{color:#93a5b6;font-style:italic;}"
        "@media print{body{background:#fff;}.page{margin:0;max-width:none;border:none;border-radius:0;box-shadow:none;padding:0;}.doc-section{break-inside:avoid;}.auth-usage-grid{grid-template-columns:repeat(2,minmax(0,1fr));}}"
        "</style></head><body><div class=\"page\">"
        f"{_document_header_markup(agency_id)}"
        f"{body_html}</div></body></html>"
    )


def _parse_key_values(lines: list[str], start: int, stop_markers: set[str]) -> tuple[dict[str, str], int]:
    data: dict[str, str] = {}
    index = start
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line in stop_markers:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
        index += 1
    return data, index


def _parse_inline_pairs(line: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for chunk in str(line or "").split("|"):
        piece = chunk.strip()
        if ":" not in piece:
            continue
        key, value = piece.split(":", 1)
        payload[key.strip()] = value.strip()
    return payload


def _value_or_placeholder(value: object, placeholder: str = "Not documented") -> str:
    clean = str(value or "").strip()
    if clean:
        return escape(clean)
    return f'<span class="muted-placeholder">{escape(placeholder)}</span>'


def _status_pill_markup(value: object) -> str:
    clean = str(value or "").strip() or "Pending"
    token = clean.lower()
    if any(item in token for item in {"closed", "approved", "reviewed", "on time"}):
        tone = "success"
    elif any(item in token for item in {"late", "rejected", "expired", "denied"}):
        tone = "danger"
    elif any(item in token for item in {"due", "draft", "warning", "pending"}):
        tone = "warn"
    else:
        tone = "neutral"
    return f'<span class="status-pill {tone}">{escape(clean)}</span>'


def _field_card_markup(label: str, value: object, *, placeholder: str = "Not documented") -> str:
    return (
        '<article class="field-card">'
        f'<span class="field-label">{escape(label)}</span>'
        f'<span class="field-value">{_value_or_placeholder(value, placeholder)}</span>'
        "</article>"
    )


def _summary_card_markup(label: str, value: object, note: str = "") -> str:
    return (
        '<article class="summary-card">'
        f"<strong>{escape(label)}</strong>"
        f"<span>{_value_or_placeholder(value)}</span>"
        + (f"<small>{escape(note)}</small>" if str(note or "").strip() else "")
        + "</article>"
    )


def _parse_units_map(raw_value: object) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for chunk in str(raw_value or "").split(","):
        piece = chunk.strip()
        if ":" not in piece:
            continue
        code, raw_amount = piece.split(":", 1)
        code_clean = code.strip()
        try:
            parsed[code_clean] = int(float(raw_amount.strip()))
        except ValueError:
            continue
    return parsed


def _authorization_usage_cards_markup(summary: dict[str, str]) -> str:
    approved_map = _parse_units_map(summary.get("Approved Units", ""))
    remaining_map = _parse_units_map(
        summary.get("Remaining Units", "") or summary.get("Remaining Authorized Units", "")
    )
    all_codes = sorted(set(approved_map) | set(remaining_map))
    if not all_codes:
        return ""

    cards: list[str] = []
    for code in all_codes:
        approved_units = max(approved_map.get(code, 0), 0)
        remaining_units = max(remaining_map.get(code, 0), 0)
        used_units = max(approved_units - remaining_units, 0)
        if approved_units <= 0:
            used_percent = 0.0
            remaining_percent = 0.0
        else:
            used_percent = min((used_units / approved_units) * 100, 100)
            remaining_percent = min((remaining_units / approved_units) * 100, 100)
        cards.append(
            '<article class="auth-usage-card">'
            '<div class="auth-usage-top">'
            f'<div class="auth-usage-code">{escape(code)}</div>'
            f'<div class="auth-usage-values">Approved {approved_units}<br>Used {used_units} | Remaining {remaining_units}</div>'
            "</div>"
            '<div class="auth-usage-track">'
            f'<div class="auth-usage-segment used" style="width:{used_percent:.2f}%"></div>'
            f'<div class="auth-usage-segment remaining" style="width:{remaining_percent:.2f}%"></div>'
            "</div>"
            '<div class="auth-usage-meta">'
            f'<div><span>Approved</span><strong>{approved_units}</strong></div>'
            f'<div><span>Used</span><strong>{used_units}</strong></div>'
            f'<div><span>Remaining</span><strong>{remaining_units}</strong></div>'
            "</div>"
            "</article>"
        )
    return '<div class="auth-usage-grid">' + "".join(cards) + "</div>"


def _service_log_title_markup(title_line: str, week_range: str) -> str:
    subtitle = f"Week Range: {week_range}" if week_range else "Weekly clinical billing record"
    return (
        f'<div class="doc-title">{escape(title_line)}</div>'
        f'<p class="doc-subtitle">{escape(subtitle)}</p>'
    )


def _service_log_overview_markup(overview: dict[str, str]) -> str:
    return (
        '<section class="doc-section">'
        '<div class="doc-section-header">Section 1 - Case Overview</div>'
        '<div class="doc-section-body"><div class="overview-grid">'
        + _field_card_markup("Recipient", overview.get("Recipient", ""))
        + _field_card_markup("Insurance", overview.get("Insurance", ""))
        + _field_card_markup("Diagnosis", overview.get("Diagnosis", "") or overview.get("Diagnoses", ""))
        + _field_card_markup("Provider", overview.get("Provider", ""))
        + _field_card_markup("Credentials", overview.get("Credentials", ""))
        + _field_card_markup("PA Number", overview.get("PA Number", "") or overview.get("PA #", ""))
        + _field_card_markup("PA Start Date", overview.get("PA Start Date", ""))
        + _field_card_markup("PA End Date", overview.get("PA End Date", ""))
        + "</div></div></section>"
    )


def _authorization_summary_markup(summary: dict[str, str]) -> str:
    status_value = str(summary.get("Status", "")).strip()
    workflow_value = str(summary.get("Workflow", "")).strip()
    workflow_note = ""
    if workflow_value and workflow_value.lower() not in status_value.lower():
        workflow_note = workflow_value
    return (
        '<section class="doc-section">'
        '<div class="doc-section-header">Section 2 - Authorization Summary</div>'
        '<div class="doc-section-body"><div class="summary-grid">'
        + _summary_card_markup("Approved Units", summary.get("Approved Units", ""))
        + _summary_card_markup("Used Units", summary.get("Used Units", ""))
        + _summary_card_markup("Remaining Units", summary.get("Remaining Units", "") or summary.get("Remaining Authorized Units", ""))
        + _summary_card_markup("Total Days", summary.get("Total Days", ""))
        + _summary_card_markup("Note Deadline", summary.get("Note Deadline", ""))
        + (
            '<article class="summary-card"><strong>Status</strong>'
            f"{_status_pill_markup(status_value)}"
            + (
                f"<small>{escape(workflow_note)}</small>"
                if workflow_note
                else ""
            )
            + "</article>"
        )
        + "</div>"
        + _authorization_usage_cards_markup(summary)
        + "</div></section>"
    )


def _extract_code_and_units(raw_units: str) -> tuple[str, str]:
    clean = str(raw_units or "").strip()
    match = re.match(r"([0-9A-Z\-XP,/* ]+?)\s*\(([^)]+)\)", clean)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", clean


def _parse_service_log_rows(lines: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in lines:
        clean = str(line or "").strip()
        if not clean or clean.upper().startswith("DATE |"):
            continue
        parts = [part.strip() for part in clean.split("|")]
        if len(parts) >= 10:
            rows.append(
                {
                    "Date": parts[0],
                    "Time In": parts[1],
                    "Time Out": parts[2],
                    "Hours": parts[3],
                    "Units": parts[4],
                    "CPT Code": parts[5],
                    "Place of Service": parts[6],
                    "Caregiver / Client": parts[7],
                    "Signature Status": parts[8],
                    "Note Deadline": parts[9],
                }
            )
            continue
        if len(parts) >= 9:
            cpt_code, units_value = _extract_code_and_units(parts[4])
            rows.append(
                {
                    "Date": parts[0],
                    "Time In": parts[1],
                    "Time Out": parts[2],
                    "Hours": parts[3],
                    "Units": units_value,
                    "CPT Code": cpt_code or "-",
                    "Place of Service": parts[5],
                    "Caregiver / Client": parts[6],
                    "Signature Status": parts[7],
                    "Note Deadline": parts[8].replace("Note due:", "").strip(),
                }
            )
    return rows


def _session_log_table_markup(rows: list[dict[str, str]]) -> str:
    rows_markup: list[str] = []
    for row in rows:
        rows_markup.append(
            "<tr>"
            f"<td>{_value_or_placeholder(row.get('Date', ''), 'Date pending')}</td>"
            f"<td>{_value_or_placeholder(row.get('Time In', ''), 'Pending')}</td>"
            f"<td>{_value_or_placeholder(row.get('Time Out', ''), 'Pending')}</td>"
            f"<td>{_value_or_placeholder(row.get('Hours', ''), '0.0')}</td>"
            f"<td>{_value_or_placeholder(row.get('Units', ''), '0')}</td>"
            f"<td>{_value_or_placeholder(row.get('CPT Code', ''), 'Not set')}</td>"
            f"<td>{_value_or_placeholder(row.get('Place of Service', ''), 'Not set')}</td>"
            f"<td>{_value_or_placeholder(row.get('Caregiver / Client', ''), 'Not documented')}</td>"
            f"<td>{_value_or_placeholder(row.get('Signature Status', ''), 'Pending')}</td>"
            f"<td>{_value_or_placeholder(row.get('Note Deadline', ''), 'Pending')}</td>"
            "</tr>"
        )
    if not rows_markup:
        rows_markup.append('<tr><td colspan="10"><span class="muted-placeholder">No session rows have been added to this service log yet.</span></td></tr>')
    return (
        '<section class="doc-section">'
        '<div class="doc-section-header">Section 3 - Session Log Table</div>'
        '<div class="doc-section-body"><div class="table-wrap">'
        '<table class="service-log-table"><thead><tr>'
        "<th>Date</th><th>Time In</th><th>Time Out</th><th>Hours</th><th>Units</th><th>CPT Code</th><th>Place of Service</th><th>Caregiver / Client</th><th>Signature Status</th><th>Note Deadline</th>"
        "</tr></thead><tbody>"
        + "".join(rows_markup)
        + "</tbody></table></div></div></section>"
    )


def _service_totals_markup(totals: dict[str, str]) -> str:
    return (
        '<section class="doc-section totals-highlight">'
        '<div class="doc-section-header">Section 4 - Totals</div>'
        '<div class="doc-section-body"><div class="totals-grid">'
        + (
            '<article class="total-card"><strong>Total Hours</strong>'
            f"<span>{_value_or_placeholder(totals.get('Total Hours', '') or totals.get('TOTAL HOURS', ''), '0')}</span>"
            "</article>"
        )
        + (
            '<article class="total-card"><strong>Total Units</strong>'
            f"<span>{_value_or_placeholder(totals.get('Total Units', '') or totals.get('TOTAL UNITS', ''), '0')}</span>"
            "</article>"
        )
        + (
            '<article class="total-card"><strong>Total Billed</strong>'
            f"<span>{_value_or_placeholder(totals.get('Total Billed', '') or totals.get('TOTAL FACTURADO', ''), '$0.00')}</span>"
            "</article>"
        )
        + "</div></div></section>"
    )


def _review_status_markup(review: dict[str, str]) -> str:
    return (
        '<section class="doc-section">'
        '<div class="doc-section-header">Section 5 - Review Status</div>'
        '<div class="doc-section-body"><div class="review-grid">'
        + _field_card_markup("Reviewed by", review.get("Reviewed by", ""))
        + _field_card_markup("Reviewed at", review.get("Reviewed at", ""))
        + _field_card_markup("Closed by", review.get("Closed by", ""))
        + _field_card_markup("Closed at", review.get("Closed at", ""))
        + _field_card_markup("Rejected by", review.get("Rejected by", ""))
        + _field_card_markup("Rejected at", review.get("Rejected at", ""))
        + _field_card_markup("Reject reason", review.get("Reject reason", "") or review.get("Rejected reason", ""))
        + _field_card_markup("Reopened by", review.get("Reopened by", ""))
        + _field_card_markup("Reopened at", review.get("Reopened at", ""))
        + "</div></div></section>"
    )


def _signature_card_markup(label: str, signature_value: object, date_value: object, *, date_label: str = "Date") -> str:
    return (
        '<article class="signature-card">'
        f'<span class="field-label">{escape(label)}</span>'
        f'<div class="signature-preview">{_signature_preview_markup(signature_value, "Pending signature")}</div>'
        f'<div class="signature-date"><strong>{escape(date_label)}:</strong> {_value_or_placeholder(date_value, "Pending")}</div>'
        "</article>"
    )


def _signature_preview_markup(signature_value: object, placeholder: str) -> str:
    clean = str(signature_value or "").strip()
    if not clean:
        return escape(placeholder)
    if clean.startswith("data:image/"):
        return f'<img src="{escape(clean, quote=True)}" alt="Signature">'
    return escape(clean)


def _signature_block_markup(signatures: dict[str, str]) -> str:
    return (
        '<section class="doc-section">'
        '<div class="doc-section-header">Section 6 - Signatures</div>'
        '<div class="doc-section-body"><div class="signature-grid">'
        + _signature_card_markup(
            "Caregiver Signature",
            signatures.get("Caregiver Signature", "") or signatures.get("CAREGIVER SIGNATURE", ""),
            signatures.get("Caregiver Date", "") or signatures.get("Caregiver Sign Date", "") or signatures.get("CAREGIVER SIGN DATE", ""),
            date_label="Caregiver Date",
        )
        + _signature_card_markup(
            "Provider Signature",
            signatures.get("Provider Signature", "") or signatures.get("PROVIDER SIGNATURE", ""),
            signatures.get("Provider Date", "") or signatures.get("Signature Date", "") or signatures.get("SIGNATURE DATE", ""),
            date_label="Provider Date",
        )
        + _signature_card_markup(
            "HR / Supervisor Signature",
            signatures.get("HR / Supervisor Signature", "") or signatures.get("Supervisor Signature", "") or signatures.get("Closed by", "") or signatures.get("Reviewed by", ""),
            signatures.get("HR / Supervisor Date", "") or signatures.get("Closed at", "") or signatures.get("Reviewed at", ""),
        )
        + "</div></div></section>"
    )


def _service_log_notes_markup(notes_text: str) -> str:
    if not str(notes_text or "").strip():
        return ""
    return (
        '<section class="doc-section">'
        '<div class="doc-section-header">Supporting Notes</div>'
        f'<div class="doc-section-body"><div class="notes-block">{escape(notes_text)}</div></div></section>'
    )


def _parse_service_log_document(body: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    if not lines:
        return {
            "title": "SERVICE LOG",
            "meta": {},
            "overview": {},
            "authorization": {},
            "rows": [],
            "totals": {},
            "review": {},
            "signatures": {},
            "notes": "",
        }
    sections: dict[str, list[str]] = {"meta": []}
    current_section = "meta"
    for line in lines[1:]:
        clean = line.strip()
        if clean in SERVICE_LOG_SECTION_TITLES:
            current_section = clean
            sections.setdefault(current_section, [])
            continue
        sections.setdefault(current_section, []).append(clean)
    return {
        "title": lines[0],
        "meta": _parse_service_log_key_values(sections.get("meta", [])),
        "overview": _parse_service_log_key_values(sections.get("Case Overview", [])),
        "authorization": _parse_service_log_key_values(sections.get("Authorization Summary", [])),
        "rows": _parse_service_log_rows(sections.get("Session Log Table", [])),
        "totals": _parse_service_log_key_values(sections.get("Totals", [])),
        "review": _parse_service_log_key_values(sections.get("Review Status", [])),
        "signatures": _parse_service_log_key_values(sections.get("Signatures", [])),
        "notes": "\n".join(sections.get("Notes", [])),
    }


def _parse_service_log_key_values(lines: list[str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for line in lines:
        payload.update(_parse_inline_pairs(line))
        clean = str(line or "").strip()
        if ":" in clean and "|" not in clean:
            key, value = clean.split(":", 1)
            payload[key.strip()] = value.strip()
    return payload


def _render_appointment_note_html(*, title: str, body: str, agency_id: str = "") -> str:
    lines = [line.rstrip() for line in body.splitlines()]
    recipient, index = _parse_key_values(lines, 3, {"Provider Details"})
    provider, index = _parse_key_values(lines, index + 1, {"Appointment Details"})
    appointment, index = _parse_key_values(lines, index + 1, {"Session Summary", "Assessment Summary", "Reassessment Summary", "Supervision Summary", "Supervision Service Summary"})

    summary_title = "Session Summary"
    if index < len(lines):
        summary_title = lines[index].strip()
    summary, index = _parse_key_values(lines, index + 1, {"Signatures"})
    signatures, _ = _parse_key_values(lines, index + 1, {"Closure Rule"})

    body_html = (
        f"<div class=\"doc-title\">{escape(lines[0] if lines else title.upper())}</div>"
        "<table>"
        "<tr><td colspan=\"12\" class=\"section-title\">Recipient Details</td></tr>"
        "<tr>"
        f"<td colspan=\"3\"><span class=\"label\">Name:</span>{escape(recipient.get('Name', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Date of Birth:</span>{escape(recipient.get('Date of Birth', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Insurance #:</span>{escape(recipient.get('Insurance #', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Diagnosis:</span>{escape(recipient.get('Diagnosis', ''))}</td>"
        "</tr>"
        "</table>"
        "<table>"
        "<tr><td colspan=\"12\" class=\"section-title\">Provider Details</td></tr>"
        "<tr>"
        f"<td colspan=\"4\"><span class=\"label\">Provider:</span>{escape(provider.get('Provider', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">Credentials:</span>{escape(provider.get('Credentials', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">PA #:</span>{escape(provider.get('PA #', ''))}</td>"
        f"<td colspan=\"4\"><span class=\"label\">PA Dates:</span>{escape(provider.get('PA Dates', ''))}</td>"
        "</tr>"
        f"<tr><td colspan=\"12\"><span class=\"label\">Approved Units:</span>{escape(provider.get('Approved Units', ''))}</td></tr>"
        "</table>"
        "<table>"
        "<tr><td colspan=\"12\" class=\"section-title\">Appointment Details</td></tr>"
        "<tr>"
        f"<td colspan=\"3\"><span class=\"label\">Date:</span>{escape(appointment.get('Date', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">Time In:</span>{escape(appointment.get('Time In', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">Time Out:</span>{escape(appointment.get('Time Out', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">Place of Service:</span>{escape(appointment.get('Place of Service', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Caregiver:</span>{escape(appointment.get('Caregiver', ''))}</td>"
        "</tr>"
        "</table>"
        "<table>"
        f"<tr><td colspan=\"12\" class=\"section-title\">{escape(summary_title)}</td></tr>"
        f"<tr><td colspan=\"12\"><span class=\"label\">Presenting concerns:</span>{escape(summary.get('Presenting concerns', ''))}</td></tr>"
        f"<tr><td colspan=\"12\"><span class=\"label\">Interventions/activities:</span>{escape(summary.get('Interventions/activities', ''))}</td></tr>"
        f"<tr><td colspan=\"12\"><span class=\"label\">Client response:</span>{escape(summary.get('Client response', ''))}</td></tr>"
        f"<tr><td colspan=\"12\"><span class=\"label\">Barriers/safety concerns:</span>{escape(summary.get('Barriers/safety concerns', ''))}</td></tr>"
        f"<tr><td colspan=\"12\"><span class=\"label\">Plan for next session:</span>{escape(summary.get('Plan for next session', ''))}</td></tr>"
        "</table>"
        "<div class=\"signature-grid\">"
        f"<div class=\"signature-card\"><span class=\"field-label\">Provider Signature</span><div class=\"signature-preview\">{_signature_preview_markup(signatures.get('Provider Signature', ''), 'Pending signature')}</div><div class=\"signature-date\"><strong>Date:</strong> {escape(signatures.get('Provider Signature Date', ''))}</div></div>"
        f"<div class=\"signature-card\"><span class=\"field-label\">Supervisor Signature</span><div class=\"signature-preview\">{_signature_preview_markup(signatures.get('Supervisor Signature', ''), 'Pending signature')}</div><div class=\"signature-date\"><strong>Date:</strong> {escape(signatures.get('Provider Signature Date', ''))}</div></div>"
        f"<div class=\"signature-card\"><span class=\"field-label\">Caregiver Signature</span><div class=\"signature-preview\">{_signature_preview_markup(signatures.get('Caregiver Signature', ''), 'Pending signature')}</div><div class=\"signature-date\"><strong>Date:</strong> {escape(signatures.get('Caregiver Signature Date', ''))}</div></div>"
        "</div>"
    )
    return _wrap_html(title=title, body_html=body_html, agency_id=agency_id)


def _render_service_log_html(*, title: str, body: str, agency_id: str = "") -> str:
    if "Case Overview" not in body or "Authorization Summary" not in body:
        return _render_legacy_service_log_html(title=title, body=body, agency_id=agency_id)

    document = _parse_service_log_document(body)
    review = document["review"]
    signatures = {
        **document["signatures"],
        "Reviewed by": review.get("Reviewed by", ""),
        "Reviewed at": review.get("Reviewed at", ""),
        "Closed by": review.get("Closed by", ""),
        "Closed at": review.get("Closed at", ""),
    }
    body_html = (
        _service_log_title_markup(
            document["title"] or title.upper(),
            document["meta"].get("Week Range", "") or document["meta"].get("Semana", ""),
        )
        + _service_log_overview_markup(document["overview"])
        + _authorization_summary_markup(document["authorization"])
        + _session_log_table_markup(document["rows"])
        + _service_totals_markup(document["totals"])
        + _review_status_markup(review)
        + _signature_block_markup(signatures)
        + _service_log_notes_markup(document["notes"])
    )
    return _wrap_html(title=title, body_html=body_html, agency_id=agency_id)


def _render_legacy_service_log_html(*, title: str, body: str, agency_id: str = "") -> str:
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    title_line = lines[0] if lines else title.upper()
    meta, index = _parse_key_values(lines, 1, {"Recipient"})
    header_data, index = _parse_key_values(lines, index, {"DATE | TIME IN | TIME OUT | HOURS | UNITS | PLACE OF SERVICE | CLIENT/CAREGIVER NAME | SIGNATURE"})
    if index < len(lines):
        index += 1
    row_lines: list[str] = []
    while index < len(lines) and not lines[index].startswith("TOTAL HOURS"):
        row_lines.append(lines[index])
        index += 1
    totals, index = _parse_key_values(lines, index, {"CAREGIVER SIGNATURE"})
    signatures, index = _parse_key_values(lines, index, {"Notes"})
    notes = ""
    if index < len(lines) and lines[index].startswith("Notes:"):
        notes = lines[index].split(":", 1)[1].strip()

    rows_html = []
    for row in row_lines:
        parts = [escape(part.strip()) for part in row.split("|")]
        if len(parts) < 8:
            continue
        rows_html.append("<tr>" + "".join(f"<td>{part}</td>" for part in parts[:8]) + "</tr>")

    body_html = (
        f"<div class=\"doc-title\">{escape(title_line)}</div>"
        f"<div class=\"doc-subtitle\">{escape(meta.get('Semana', ''))}</div>"
        "<table>"
        "<tr><td colspan=\"12\" class=\"section-title\">Recipient and Provider</td></tr>"
        "<tr>"
        f"<td colspan=\"3\"><span class=\"label\">Recipient:</span>{escape(header_data.get('Recipient', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Insurance:</span>{escape(header_data.get('Insurance', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Diagnoses:</span>{escape(header_data.get('Diagnoses', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Provider:</span>{escape(header_data.get('Provider', ''))}</td>"
        "</tr>"
        "<tr>"
        f"<td colspan=\"3\"><span class=\"label\">Credentials:</span>{escape(header_data.get('Credentials', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">PA #:</span>{escape(header_data.get('PA #', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">PA Start Date:</span>{escape(header_data.get('PA Start Date', ''))}</td>"
        f"<td colspan=\"2\"><span class=\"label\">PA End Date:</span>{escape(header_data.get('PA End Date', ''))}</td>"
        f"<td colspan=\"3\"><span class=\"label\">Approved Units:</span>{escape(header_data.get('Approved Units', ''))}</td>"
        "</tr>"
        "</table>"
        "<table>"
        "<tr><th>DATE</th><th>TIME IN</th><th>TIME OUT</th><th>HOURS</th><th>UNITS</th><th>PLACE OF SERVICE</th><th>CLIENT/CAREGIVER NAME</th><th>CLIENT/CAREGIVER SIGNATURE</th></tr>"
        + "".join(rows_html)
        + "</table>"
        "<table>"
        "<tr>"
        f"<td><span class=\"label\">TOTAL HOURS</span>{escape(totals.get('TOTAL HOURS', ''))}</td>"
        f"<td><span class=\"label\">TOTAL UNITS</span>{escape(totals.get('TOTAL UNITS', ''))}</td>"
        f"<td><span class=\"label\">TOTAL FACTURADO</span>{escape(totals.get('TOTAL FACTURADO', ''))}</td>"
        f"<td><span class=\"label\">TOTAL DAYS</span>{escape(header_data.get('Total Days', ''))}</td>"
        "</tr>"
        "</table>"
        "<div class=\"signature-grid\">"
        f"<div class=\"signature-card\"><span class=\"field-label\">Caregiver Signature</span><div class=\"signature-preview\">{_signature_preview_markup(signatures.get('CAREGIVER SIGNATURE', ''), 'Pending signature')}</div><div class=\"signature-date\"><strong>Date:</strong> {escape(signatures.get('CAREGIVER SIGN DATE', ''))}</div></div>"
        f"<div class=\"signature-card\"><span class=\"field-label\">Provider Signature</span><div class=\"signature-preview\">{_signature_preview_markup(signatures.get('PROVIDER SIGNATURE', ''), 'Pending signature')}</div><div class=\"signature-date\"><strong>Date:</strong> {escape(signatures.get('SIGNATURE DATE', ''))}</div></div>"
        f"<div class=\"signature-card\"><span class=\"field-label\">Supporting Notes</span><div class=\"signature-preview\">{escape(notes)}</div><div class=\"signature-date\"><strong>Deadline:</strong> {escape(meta.get('Note deadline', ''))}</div></div>"
        "</div>"
    )
    return _wrap_html(title=title, body_html=body_html, agency_id=agency_id)


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_simple_pdf(*, path: Path, title: str, body: str) -> None:
    lines = [title, ""] + body.splitlines()
    page_height = 792
    start_y = 760
    line_height = 14
    page_lines = 48
    pages: list[str] = []

    for offset in range(0, len(lines), page_lines):
        chunk = lines[offset : offset + page_lines]
        commands = ["BT", "/F1 11 Tf"]
        y = start_y
        for line in chunk:
            commands.append(f"72 {y} Td ({_escape_pdf_text(line[:110])}) Tj")
            y -= line_height
        commands.append("ET")
        pages.append("\n".join(commands))

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{index} 0 R" for index in range(3, 3 + len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())

    font_object_number = 3 + len(pages) * 2
    for page_index, content in enumerate(pages):
        page_object_number = 3 + page_index * 2
        content_object_number = page_object_number + 1
        page_object = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 {page_height}] "
            f"/Resources << /Font << /F1 {font_object_number} 0 R >> >> "
            f"/Contents {content_object_number} 0 R >>"
        )
        stream = content.encode("latin-1", errors="replace")
        content_object = (
            f"<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )
        objects.append(page_object.encode())
        objects.append(content_object)

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode()
    )
    path.write_bytes(pdf)
