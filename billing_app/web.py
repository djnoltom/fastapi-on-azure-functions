from __future__ import annotations

import argparse
import base64
import calendar as month_calendar
import html
import json
import mimetypes
import secrets
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from billing_app.connectors.mock_connector import MockClearinghouseConnector
from billing_app.main import _build_claim, _build_eligibility_request
from billing_app.models import Parsed835, Parsed837
from billing_app.services.claim_builder import Claim837Builder
from billing_app.services.claim_parser import Claim837Parser
from billing_app.services.cms1500 import render_cms1500_html
from billing_app.services.aba_notes_portal import (
    SERVICE_CONTEXT_LABELS as ABA_SERVICE_CONTEXT_LABELS,
    add_aba_appointment,
    attach_claim_to_aba_sessions,
    get_aba_appointment_detail,
    get_aba_billing_preview,
    get_aba_service_log_detail,
    list_aba_appointments,
    list_aba_client_options,
    list_aba_provider_options,
    list_aba_service_logs,
    update_aba_session_event,
    update_aba_service_log_workflow,
)
from billing_app.services.aba_notes_engine import render_note_html_document
from billing_app.services.ai_assistant import available_ai_actions, run_ai_assistant_action
from billing_app.services.operations_portal import (
    build_claim_batches,
    build_claim_form_from_session,
    build_operations_dashboards,
    create_claim_from_batch,
    get_operational_session_detail,
    list_shared_calendar_events,
    list_operational_sessions,
)
from billing_app.services.date_utils import add_user_date_months, format_edi_date, format_user_date, parse_user_date, today_user_date
from billing_app.services.eligibility import EligibilityService
from billing_app.services.local_store import (
    add_agency,
    add_authorization,
    add_claim_audit_log,
    add_calendar_event,
    count_eligibility_history,
    add_eligibility_history_entry,
    add_system_audit_log,
    add_client,
    add_claim_record,
    add_era_archive,
    add_notification,
    add_payer_enrollment,
    add_provider_contract,
    add_user_note,
    add_user,
    add_roster_entry,
    add_uploaded_claim_records,
    authenticate_user,
    apply_era_to_claims,
    change_password,
    complete_user_login,
    confirm_mfa_setup,
    consume_authorization_units,
    create_password_reset_token,
    disable_mfa,
    delete_authorization_group,
    ensure_default_admin_user,
    find_client_for_eligibility,
    get_provider_contract_by_id,
    get_client_by_id,
    get_agency_logo_bytes,
    get_authorization_group_records,
    get_claim_by_id,
    get_claim_edi_bytes,
    get_current_agency,
    get_era_archive_bytes,
    get_upload_bytes,
    get_user_public_profile,
    get_user_avatar_bytes,
    get_user_security_profile,
    get_notification_by_id,
    update_notification_state,
    get_payer_configuration_by_id,
    get_payer_configured_unit_price,
    initiate_mfa_setup,
    list_authorizations,
    list_agencies,
    list_calendar_events,
    list_claim_audit_logs,
    list_claims,
    list_clients,
    list_eligibility_history,
    list_eligibility_roster,
    list_era_archives,
    list_notifications,
    list_payer_configurations,
    list_payer_enrollments,
    list_provider_contracts,
    list_provider_required_documents,
    list_client_required_documents,
    list_system_audit_logs,
    list_user_notes,
    list_users,
    get_default_landing_page,
    get_eligibility_check_interval_hours,
    get_mfa_session_timeout_seconds,
    get_password_reset_minutes,
    get_session_timeout_seconds,
    load_system_configuration,
    approve_provider_document,
    reset_password_with_recovery_code,
    run_client_eligibility_checks,
    run_due_eligibility_checks,
    run_provider_document_expiration_checks,
    save_payer_configuration,
    save_required_documents,
    save_system_configuration,
    set_current_agency,
    submit_provider_document,
    transmit_claim_record,
    transmit_daily_batch,
    update_authorization_group,
    update_notification_email_status,
    update_calendar_event_status,
    update_user_profile,
    verify_user_mfa,
)
from billing_app.services.report_exports import (
    agencies_export_bytes,
    authorizations_export_bytes,
    claims_export_bytes,
    clients_export_bytes,
    era_archives_export_bytes,
    notifications_export_bytes,
    payer_enrollments_export_bytes,
    provider_contracts_export_bytes,
    roster_export_bytes,
)
from billing_app.services.remit_parser import Era835Parser
from billing_app.services.rbac import (
    NORMALIZED_ROLES,
    ROLE_LABELS as RBAC_ROLE_LABELS,
    allowed_pages_for_user as rbac_allowed_pages_for_user,
    can_access_page as rbac_can_access_page,
    can_view_paid_amounts,
    can_view_financial_rates,
    can_view_financial_totals,
    default_page_for_role as rbac_default_page_for_role,
    filter_authorizations_for_user,
    filter_claims_for_user,
    filter_clients_for_user,
    filter_provider_contracts_for_user,
    filter_sessions_for_user,
    has_any_permission,
    has_permission,
    is_provider_role,
    normalize_role,
    normalized_role_from_user,
    role_label,
    sidebar_items_for_user,
)


BASE_DIR = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = BASE_DIR / "examples"
ASSETS_DIR = BASE_DIR / "assets"
PLATFORM_LOGO_CANDIDATES = (
    ASSETS_DIR / "blue-hope-program-logo.svg",
    ASSETS_DIR / "blue-hope-logo.svg",
    ASSETS_DIR / "blue hope logo.svg",
)
LOGO_CANDIDATES = (
    "blue-hope-logo.png",
    "blue-hope-logo.jpg",
    "blue-hope-logo.jpeg",
    "blue-hope-logo.webp",
    "blue-hope-logo.svg",
    "blue hope logo.png",
    "blue hope logo.jpg",
    "blue hope logo.jpeg",
    "blue hope logo.webp",
    "blue hope logo.svg",
    "blue-hope-behavioral.png",
    "blue-hope-behavioral.jpg",
    "blue-hope-behavioral.jpeg",
    "blue-hope-behavioral.webp",
    "blue-hope-behavioral.svg",
    "logo.png",
    "logo.jpg",
    "logo.jpeg",
    "logo.webp",
    "logo.svg",
)
BRAND_NAME = "Blue Hope Suite"
BRAND_SHORT_NAME = "Blue Hope"
BRAND_TAGLINE = "Operations Hub for ABA Teams"
BRAND_SERVER_LABEL = "Blue Hope Suite"
LEGACY_BRAND_NAMES = {
    "Blue Hope ABA Solutions",
    "BHAS Blue Hope Aba Solution",
    "Blue Hope Billing Server",
}
CPT_CATALOG = {
    "97151": {
        "description": "BEHAVIOR ASSESSMENT (24 UNITS MAX)",
        "hcpcs": "H0031",
        "unit_price": 19.05,
    },
    "97151-TS": {
        "description": "BEHAVIOR RE-ASSESSMENT (18 UNITS MAX)",
        "hcpcs": "H0032",
        "unit_price": 19.05,
    },
    "97153": {
        "description": "BEHAVIOR TREATMENT (RBT)",
        "hcpcs": "H2014",
        "unit_price": 12.19,
    },
    "97153-XP": {
        "description": "CONCURRENT SUPERVISION (RBT) [NON-REIMBURSABLE]",
        "hcpcs": "",
        "unit_price": 0.01,
    },
    "97155": {
        "description": "BEHAVIOR TREATMENT (BCBA)",
        "hcpcs": "H2019",
        "unit_price": 19.05,
    },
    "97155-HN": {
        "description": "BEHAVIOR TREATMENT (BCaBA)",
        "hcpcs": "H2012",
        "unit_price": 15.24,
    },
    "97155-XP": {
        "description": "CONCURRENT SUPERVISION (BCaBA) [NON-REIMBURSABLE]",
        "hcpcs": "",
        "unit_price": 0.01,
    },
    "97156": {
        "description": "FAMILY TRAINING (BCBA)",
        "hcpcs": "H2019",
        "unit_price": 19.05,
    },
    "97156-HN": {
        "description": "FAMILY TRAINING (BCaBA)",
        "hcpcs": "H2012",
        "unit_price": 15.24,
    },
}
SUPPORTED_CPT_CODES = tuple(CPT_CATALOG.keys())
MAX_AUTHORIZATION_LINES = 5
WORKFORCE_CATEGORIES = (
    ("PROVIDER", "Provider Clinico"),
    ("OFFICE", "Oficina"),
)
PROVIDER_TYPES = ("BCBA", "BCaBA", "RBT", "Mental Health")
OFFICE_DEPARTMENTS = (
    "Recursos Humanos",
    "Clinico",
    "Quality Control",
    "Billing Agent",
    "Accounting",
    "Credentialing",
)
SITE_LOCATIONS = (
    ("Cape Coral", "Lee"),
    ("Miami", "Miami-Dade"),
    ("Tampa", "Hillsborough"),
)
CONTRACT_STAGES = (
    ("NEW", "Nuevo"),
    ("COLLECTING_DOCS", "Documentos"),
    ("INTERVIEW", "Entrevista"),
    ("OFFER_SENT", "Oferta"),
    ("ONBOARDING", "Onboarding"),
    ("ACTIVE", "Activo"),
)
ROLE_LABELS = dict(RBAC_ROLE_LABELS)
PERMISSION_PAGE_LABELS = {
    "dashboard": "Dashboard",
    "hr": "Recursos Humanos",
    "claims": "Claims",
    "eligibility": "Elegibilidad",
    "clients": "Clientes",
    "aba_notes": "Notas ABA",
    "payments": "Remesas",
    "enrollments": "Enrollments",
    "payers": "Payers",
    "agencies": "Agencias",
    "providers": "Providers",
    "agenda": "Agenda",
    "notifications": "Notificaciones",
    "users": "Usuarios",
    "security": "Configuracion",
}
PAYER_PLAN_TYPES = (
    ("COMMERCIAL", "Commercial"),
    ("MEDICAID", "Medicaid"),
    ("MEDICARE", "Medicare"),
    ("TRICARE", "Tricare"),
    ("OTHER", "Other"),
)
CLEARINGHOUSE_OPTIONS = (
    "Claim.MD",
    "Office Ally",
    "Availity",
    "Waystar",
    "Trizetto",
    "Therapy Brands",
    "Change Healthcare",
    "Other",
)
SESSION_COOKIE_NAME = "tf_billing_session"
MFA_COOKIE_NAME = "tf_billing_pre_mfa"
SESSION_TIMEOUT_SECONDS = 60 * 30
MFA_SESSION_TIMEOUT_SECONDS = 60 * 10
SESSIONS: dict[str, dict[str, object]] = {}
MFA_SESSIONS: dict[str, dict[str, object]] = {}


@dataclass
class UploadedFile:
    filename: str
    content_type: str
    content: bytes


def _load_example(filename: str) -> str:
    return (EXAMPLES_DIR / filename).read_text(encoding="utf-8")


def _pretty_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=True)


def _friendly_result_body(result_title: str, result_body: str) -> str:
    clean_body = str(result_body or "").strip()
    if not clean_body.startswith(("{", "[")):
        return clean_body
    try:
        payload = json.loads(clean_body)
    except json.JSONDecodeError:
        return clean_body
    if not isinstance(payload, dict):
        return clean_body

    def format_money(value: object) -> str:
        try:
            return f"${float(value or 0):,.2f}"
        except (TypeError, ValueError):
            return str(value or "0.00")

    if result_title == "Contratacion guardada":
        provider_name = str(payload.get("provider_name", "")).strip()
        completed_documents = int(payload.get("completed_documents", payload.get("delivered_documents", 0)) or 0)
        total_documents = int(payload.get("total_documents", 0) or 0)
        expired_documents = int(payload.get("expired_documents", 0) or 0)
        summary_lines = [
            f"Provider: {provider_name or 'Sin nombre'}",
            f"Tipo: {str(payload.get('provider_type', '')).strip() or 'Sin tipo'}",
            f"Contract ID: {str(payload.get('contract_id', '')).strip() or 'Sin ID'}",
            f"Checklist completado: {completed_documents}/{total_documents}",
            f"Expirados: {expired_documents}",
        ]
        return "\n".join(summary_lines)

    if result_title == "Agencia guardada":
        summary_lines = [
            f"Agencia: {str(payload.get('agency_name', '')).strip() or 'Sin nombre'}",
            f"Codigo: {str(payload.get('agency_code', '')).strip() or 'Sin codigo'}",
            f"Email de notificaciones: {str(payload.get('notification_email', '')).strip() or 'Pendiente'}",
            f"Contacto: {str(payload.get('contact_name', '')).strip() or 'Pendiente'}",
        ]
        return "\n".join(summary_lines)

    if result_title == "Enrollment guardado":
        summary_lines = [
            f"Provider: {str(payload.get('provider_name', '')).strip() or 'Sin nombre'}",
            f"Payer: {str(payload.get('payer_name', '')).strip() or 'Sin payer'}",
            f"Contract ID: {str(payload.get('contract_id', '')).strip() or 'Sin contrato'}",
            f"Lugar: {str(payload.get('site_location', '')).strip() or 'Sin lugar'}",
            f"Credenciales: {str(payload.get('credentialing_owner_name', '')).strip() or 'Sin asignar'}",
            f"Dias restantes: {str(payload.get('days_remaining', '')) or '0'}",
        ]
        return "\n".join(summary_lines)

    if result_title == "Usuario guardado":
        summary_lines = [
            f"Usuario: {str(payload.get('username', '')).strip() or 'Sin username'}",
            f"Nombre: {str(payload.get('full_name', '')).strip() or 'Sin nombre'}",
            f"Rango: {str(payload.get('role', '')).strip() or 'Sin rango'}",
            f"Lugar: {str(payload.get('site_location', '')).strip() or 'Sin lugar'}",
            f"County: {str(payload.get('county_name', '')).strip() or 'Sin county'}",
        ]
        return "\n".join(summary_lines)

    if result_title == "Cliente guardado":
        summary_lines = [
            f"Cliente: {str(payload.get('first_name', '')).strip()} {str(payload.get('last_name', '')).strip()}".strip() or "Sin nombre",
            f"Member ID: {str(payload.get('member_id', '')).strip() or 'Pendiente'}",
            f"Payer: {str(payload.get('payer_name', '')).strip() or 'Pendiente'}",
            f"Lugar: {str(payload.get('site_location', '')).strip() or 'Sin lugar'}",
            f"County: {str(payload.get('county_name', '')).strip() or 'Sin county'}",
        ]
        return "\n".join(summary_lines)

    if result_title == "Payer guardado":
        active_rate_count = int(payload.get("active_rate_count", 0) or 0)
        summary_lines = [
            f"Payer: {str(payload.get('payer_name', '')).strip() or 'Sin nombre'}",
            f"Payer ID: {str(payload.get('payer_id', '')).strip() or 'Pendiente'}",
            f"Plan: {str(payload.get('plan_type', '')).strip() or 'Pendiente'}",
            f"Clearinghouse: {str(payload.get('clearinghouse_name', '')).strip() or 'Pendiente'}",
            f"CPTs configurados: {active_rate_count}",
        ]
        return "\n".join(summary_lines)

    if result_title == "Perfil actualizado":
        summary_lines = [
            f"Nombre: {str(payload.get('full_name', '')).strip() or 'Sin nombre'}",
            f"Email: {str(payload.get('email', '')).strip() or 'Pendiente'}",
            f"Telefono: {str(payload.get('phone', '')).strip() or 'Pendiente'}",
            f"Lugar: {str(payload.get('site_location', '')).strip() or 'Sin lugar'}",
            f"County: {str(payload.get('county_name', '')).strip() or 'Sin county'}",
        ]
        return "\n".join(summary_lines)

    if result_title == "Claim transmitido":
        summary_lines = [
            f"Claim ID: {str(payload.get('claim_id', '')).strip() or 'Sin claim'}",
            f"Tracking ID: {str(payload.get('tracking_id', '')).strip() or 'Pendiente'}",
            f"Estatus: {str(payload.get('transmission_status', '')).strip() or 'Sin estatus'}",
            "El claim ya quedo marcado como transmitido en el batch.",
        ]
        return "\n".join(summary_lines)

    if result_title == "Batch transmitido":
        batch_date = str(payload.get("batch_date", "")).strip() or "Sin fecha"
        count = int(payload.get("count", 0) or 0)
        results = payload.get("results", [])
        first_tracking = ""
        if isinstance(results, list) and results:
            first_tracking = str(results[0].get("tracking_id", "")).strip()
        summary_lines = [
            f"Batch date: {batch_date}",
            f"Claims transmitidos: {count}",
            f"Primer tracking: {first_tracking or 'Pendiente'}",
            "Los claims del batch ya quedaron marcados como transmitidos.",
        ]
        return "\n".join(summary_lines)

    if result_title == "Archivo 837 guardado en batch":
        parsed_837 = payload.get("parsed_837", {}) if isinstance(payload.get("parsed_837", {}), dict) else {}
        batch_record = payload.get("batch_record", {}) if isinstance(payload.get("batch_record", {}), dict) else {}
        service_lines = parsed_837.get("service_lines", [])
        summary_lines = [
            f"Claim ID: {str(batch_record.get('claim_id', '')).strip() or str(parsed_837.get('claim_id', '')).strip() or 'Sin claim'}",
            f"Paciente: {str(batch_record.get('patient_name', '')).strip() or str(parsed_837.get('patient_name', '')).strip() or 'Sin paciente'}",
            f"Payer: {str(batch_record.get('payer_name', '')).strip() or str(parsed_837.get('payer_name', '')).strip() or 'Sin payer'}",
            f"Fecha servicio: {str(batch_record.get('service_date', '')).strip() or str(parsed_837.get('service_date', '')).strip() or 'Pendiente'}",
            f"Lineas: {len(service_lines) if isinstance(service_lines, list) else 0}",
            f"Total: {format_money(batch_record.get('total_charge_amount', parsed_837.get('total_charge_amount', 0)))}",
            "El claim quedo guardado en el batch para transmitirlo despues.",
        ]
        return "\n".join(summary_lines)

    if result_title in {"Archivo ERA 835 importado", "Resultado de remesa ERA 835"}:
        era_payload = payload.get("era", payload)
        archive_payload = payload.get("archive", {}) if isinstance(payload.get("archive", {}), dict) else {}
        claim_updates = payload.get("claim_updates", [])
        if not isinstance(era_payload, dict):
            return clean_body
        if not isinstance(claim_updates, list):
            claim_updates = []
        claim_details = era_payload.get("claim_details", [])
        claim_count = int(
            archive_payload.get("claim_count", 0)
            or (len(claim_details) if isinstance(claim_details, list) else 0)
            or (len(era_payload.get("claim_statuses", [])) if isinstance(era_payload.get("claim_statuses", []), list) else 0)
        )
        claim_updates_count = int(archive_payload.get("claim_updates_count", 0) or len(claim_updates))
        summary_lines = [
            f"Payer: {str(era_payload.get('payer_name', '')).strip() or 'Sin payer'}",
            f"Payee: {str(era_payload.get('payee_name', '')).strip() or 'Sin payee'}",
            f"Control #: {str(era_payload.get('transaction_set_control_number', '')).strip() or 'Sin control'}",
            f"Pago total: {format_money(era_payload.get('payment_amount', 0))}",
            f"Claims en archivo: {claim_count}",
            f"Claims actualizados: {claim_updates_count}",
        ]
        archive_id = str(archive_payload.get("archive_id", "")).strip()
        if archive_id:
            summary_lines.append(f"Archivo archivado: {archive_id}")
        if claim_updates:
            for update in claim_updates[:3]:
                summary_lines.append(
                    f"{str(update.get('claim_id', '')).strip() or 'Sin claim'}: "
                    f"{str(update.get('status', '')).strip() or 'sin estatus'} | "
                    f"pagado {format_money(update.get('paid_amount', 0))}"
                )
        else:
            summary_lines.append("No encontre claims guardados en el sistema para actualizar con este ERA.")
        return "\n".join(summary_lines)

    return clean_body


def _find_logo_asset() -> Path | None:
    for filename in LOGO_CANDIDATES:
        candidate = ASSETS_DIR / filename
        if candidate.is_file():
            return candidate
    return None


def _find_platform_logo_asset() -> Path | None:
    for candidate in PLATFORM_LOGO_CANDIDATES:
        if candidate.is_file():
            return candidate
    return _find_logo_asset()


def _inline_image_markup(asset: Path, alt_text: str) -> str:
    content_type = mimetypes.guess_type(asset.name)[0] or "image/png"
    encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
    return f'<img src="data:{content_type};base64,{encoded}" alt="{html.escape(alt_text)}">'


def _logo_svg() -> str:
    return f"""
    <svg viewBox="0 0 1800 900" role="img" aria-label="{html.escape(BRAND_NAME)} logo">
      <defs>
        <linearGradient id="suiteOrbOuter" x1="12%" y1="10%" x2="88%" y2="88%">
          <stop offset="0%" stop-color="#45c2ff"></stop>
          <stop offset="45%" stop-color="#1266d7"></stop>
          <stop offset="100%" stop-color="#0b3b8f"></stop>
        </linearGradient>
        <linearGradient id="suiteOrbInner" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stop-color="#0cc6d7"></stop>
          <stop offset="100%" stop-color="#7ff6de"></stop>
        </linearGradient>
        <linearGradient id="suiteWordBlue" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#1784df"></stop>
          <stop offset="100%" stop-color="#0a3ea2"></stop>
        </linearGradient>
        <linearGradient id="suiteWordTeal" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#16b9cf"></stop>
          <stop offset="100%" stop-color="#0e87b6"></stop>
        </linearGradient>
        <filter id="suiteGlow" x="-25%" y="-25%" width="150%" height="150%">
          <feGaussianBlur stdDeviation="14" result="blur"></feGaussianBlur>
          <feColorMatrix
            in="blur"
            type="matrix"
            values="1 0 0 0 0
                    0 1 0 0 0
                    0 0 1 0 0
                    0 0 0 0.22 0"
            result="glow">
          </feColorMatrix>
          <feBlend in="SourceGraphic" in2="glow" mode="screen"></feBlend>
        </filter>
      </defs>
      <g transform="translate(120 132)" filter="url(#suiteGlow)">
        <circle cx="270" cy="310" r="212" fill="url(#suiteOrbOuter)"></circle>
        <circle cx="270" cy="310" r="178" fill="#0b4aa8" opacity="0.94"></circle>
        <path
          d="M46 420C114 526 233 584 378 554C452 486 489 391 478 278C447 369 385 445 278 514C196 571 122 547 46 420Z"
          fill="url(#suiteOrbInner)"
          opacity="0.96">
        </path>
        <path
          d="M120 364C190 248 270 187 350 163C333 241 288 338 212 441C162 507 115 551 59 579C91 497 109 431 120 364Z"
          fill="rgba(255,255,255,0.92)">
        </path>
        <path
          d="M86 420C180 345 281 291 374 148"
          fill="none"
          stroke="#ffffff"
          stroke-width="30"
          stroke-linecap="round">
        </path>
        <circle cx="208" cy="236" r="34" fill="#ffffff"></circle>
        <path
          d="M432 58L449 103L494 120L449 137L432 184L415 137L370 120L415 103Z"
          fill="#ffffff">
        </path>
        <path
          d="M432 58L449 103L494 120L449 137L432 184L415 137L370 120L415 103Z"
          fill="none"
          stroke="#39c0ff"
          stroke-width="10"
          stroke-linejoin="round">
        </path>
      </g>
      <g transform="translate(650 220)">
        <text x="0" y="172" font-family="'Trebuchet MS', Verdana, sans-serif" font-size="208" font-weight="800" letter-spacing="-6" fill="url(#suiteWordBlue)">Blue Hope</text>
        <text x="270" y="326" font-family="'Trebuchet MS', Verdana, sans-serif" font-size="132" font-style="italic" font-weight="800" letter-spacing="-3" fill="url(#suiteWordTeal)">Suite</text>
        <path d="M0 355H238" stroke="#12a8c4" stroke-width="7" stroke-linecap="round"></path>
        <path d="M640 355H980" stroke="#12a8c4" stroke-width="7" stroke-linecap="round"></path>
        <text x="0" y="432" font-family="'Trebuchet MS', Verdana, sans-serif" font-size="64" font-weight="800" letter-spacing="5" fill="#11488f">OPERATIONS HUB FOR ABA TEAMS</text>
      </g>
    </svg>
    """


def _logo_markup(current_agency: dict[str, object] | None = None, *, inline: bool = False) -> str:
    if current_agency and current_agency.get("logo_file_path"):
        if inline:
            try:
                body, filename = get_agency_logo_bytes(str(current_agency.get("agency_id", "")))
                content_type = mimetypes.guess_type(filename)[0] or "image/png"
                encoded = base64.b64encode(body).decode("ascii")
                return f'<img src="data:{content_type};base64,{encoded}" alt="Logo {html.escape(BRAND_NAME)}">'
            except Exception:
                return _logo_svg()
        agency_id = html.escape(str(current_agency.get("agency_id", "")))
        return f'<img src="/agency-logo?agency_id={agency_id}" alt="Logo {html.escape(BRAND_NAME)}">'
    asset = _find_logo_asset()
    if asset is not None:
        return f'<img src="/assets/{html.escape(asset.name)}" alt="Logo {html.escape(BRAND_NAME)}">'
    return _logo_svg()


def _platform_logo_markup(*, inline: bool = False) -> str:
    asset = _find_platform_logo_asset()
    if asset is None:
        return _logo_svg()
    if inline:
        return _inline_image_markup(asset, f"Logo {BRAND_NAME}")
    if asset.parent == ASSETS_DIR:
        return f'<img src="/assets/{html.escape(asset.name)}" alt="Logo {html.escape(BRAND_NAME)}">'
    return _inline_image_markup(asset, f"Logo {BRAND_NAME}")


def _brand_logo_is_wordmark(current_agency: dict[str, object] | None = None) -> bool:
    if current_agency and current_agency.get("logo_file_path"):
        return True
    return _find_logo_asset() is not None


def _platform_logo_is_wordmark() -> bool:
    return _find_platform_logo_asset() is not None


def _display_context_agency_name(value: object) -> str:
    clean = str(value or "").strip()
    if clean in LEGACY_BRAND_NAMES:
        return BRAND_NAME
    return clean or "Sin agencia seleccionada"


def _avatar_markup(current_user: dict[str, object] | None) -> str:
    if not current_user:
        return ""
    username = str(current_user.get("username", "")).strip()
    full_name = str(current_user.get("full_name", "")).strip() or username
    if current_user.get("avatar_file_path"):
        return f'<img src="/user-avatar?username={html.escape(username)}" alt="{html.escape(full_name)}">'
    initials = "".join(part[:1].upper() for part in full_name.split()[:2]) or "U"
    profile_color = html.escape(str(current_user.get("profile_color", "#0d51b8")))
    return f'<span class="avatar-fallback" style="background:{profile_color};">{html.escape(initials)}</span>'


def _module_label(active_panel: str) -> str:
    labels = {
        "claim": "Reclamaciones 837P",
        "claim_upload": "Carga de archivo 837",
        "claim_batch": "Batch de claims",
        "eligibility": "Consulta de elegibilidad",
        "clients": "Base de clientes",
        "aba_notes": "Notas ABA y sesiones",
        "edi837": "Lectura de 837",
        "era": "Analisis de remesas ERA 835",
        "authorization": "Autorizaciones",
        "payer_config": "Catalogo de payers",
        "payer_roster": "Roster de enrolamiento",
        "roster": "Roster automatico",
        "agency": "Agencias",
        "provider_contract": "Contratacion de providers",
        "agenda": "Agenda y tareas",
        "note": "Notas de trabajo",
        "notification": "Centro de notificaciones",
        "system_config": "Configuracion central",
    }
    return labels.get(active_panel, "Dashboard general")


def _nav_class(current: str, target: str) -> str:
    return "nav-link active" if current == target else "nav-link"


def _page_href(page: str) -> str:
    return "/" if page == "dashboard" else f"/{page}"


def _nav_icon(page: str) -> str:
    return {
        "dashboard": "DB",
        "hr": "HR",
        "claims": "83",
        "eligibility": "EL",
        "clients": "PT",
        "aba_notes": "NB",
        "payments": "85",
        "enrollments": "EN",
        "payers": "PY",
        "agencies": "AG",
        "providers": "PR",
        "agenda": "CA",
        "notifications": "NT",
        "users": "US",
        "security": "SF",
        "integrations": "IN",
    }.get(page, page[:2].upper())


def _nav_link(
    current_page: str,
    allowed_pages: set[str],
    page: str,
    title: str,
    copy: str,
    href: str | None = None,
    active_pages: tuple[str, ...] | None = None,
    icon: str | None = None,
) -> str:
    is_active = current_page in (active_pages or (page,))
    nav_class = "nav-link active" if is_active else "nav-link"
    return (
        f'<a class="{nav_class}" href="{html.escape(href or _page_href(page))}"{_nav_hidden(allowed_pages, page)}>'
        f'<span class="nav-icon">{html.escape(icon or _nav_icon(page))}</span>'
        '<span class="nav-copy">'
        f"<strong>{html.escape(title)}</strong>"
        "</span>"
        "</a>"
    )


def _nav_sub_link(
    current_page: str,
    allowed_pages: set[str],
    page: str,
    title: str,
    href: str | None = None,
    active_pages: tuple[str, ...] | None = None,
) -> str:
    clean_active_pages = active_pages or (page,)
    nav_class = "nav-sublink active" if current_page in clean_active_pages else "nav-sublink"
    return f'<a class="{nav_class}" href="{html.escape(href or _page_href(page))}"{_nav_hidden(allowed_pages, page)}>{html.escape(title)}</a>'


def _sidebar_nav_markup(
    current_page: str,
    allowed_pages: set[str],
    current_user: dict[str, object] | None,
    provider_contracts: list[dict[str, object]],
) -> str:
    markup: list[str] = []
    for item in sidebar_items_for_user(current_user, provider_contracts):
        page = str(item.get("page", "")).strip()
        if not page or page not in allowed_pages:
            continue
        active_pages = item.get("active_pages")
        active_pages_tuple = tuple(active_pages) if isinstance(active_pages, (list, tuple)) else None
        markup.append(
            _nav_link(
                current_page,
                allowed_pages,
                page,
                str(item.get("title", PERMISSION_PAGE_LABELS.get(page, page.title()))),
                str(item.get("copy", "")),
                active_pages=active_pages_tuple,
                icon=str(item.get("icon", "")) or None,
            )
        )
    return "".join(markup)


def _section_hidden(current_page: str, *allowed_pages: str) -> str:
    return "" if current_page in allowed_pages else " hidden"


def _details_open(active_panel: str, *targets: str) -> str:
    return " open" if active_panel in targets else ""


def _module_card_class(active_panel: str, target: str, tone: str) -> str:
    base = f"panel module-card {tone}"
    return f"{base} active" if active_panel == target else base


def _status_banner(result_title: str, result_body: str, error: str) -> str:
    if error:
        return f"""
        <section class="panel result-panel error-panel">
          <div class="result-head">
            <span class="result-kicker">Revision requerida</span>
            <h2>Error al procesar la solicitud</h2>
          </div>
          <pre>{html.escape(error)}</pre>
        </section>
        """

    if result_body:
        safe_title = html.escape(result_title or "Resultado")
        friendly_body = _friendly_result_body(result_title, result_body)
        safe_body = html.escape(friendly_body)
        body_markup = (
            f"<pre>{safe_body}</pre>"
            if safe_body.lstrip().startswith(("{", "["))
            else f'<div class="result-copy">{safe_body.replace(chr(10), "<br>")}</div>'
        )
        return f"""
        <section class="panel result-panel success-panel">
          <div class="result-head">
            <span class="result-kicker">Operacion completada</span>
            <h2>{safe_title}</h2>
          </div>
          {body_markup}
        </section>
        """

    return ""


def _ai_action_form_markup(
    action_name: str,
    label: str,
    *,
    return_page: str,
    active_panel: str = "",
    hidden_fields: dict[str, object] | None = None,
    small: bool = True,
) -> str:
    inputs = [
        f'<input type="hidden" name="action_name" value="{html.escape(action_name)}">',
        f'<input type="hidden" name="return_page" value="{html.escape(return_page)}">',
    ]
    if active_panel:
        inputs.append(f'<input type="hidden" name="return_panel" value="{html.escape(active_panel)}">')
    for key, value in (hidden_fields or {}).items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        inputs.append(f'<input type="hidden" name="{html.escape(clean_key)}" value="{html.escape(str(value or ""))}">')
    button_class = "small-button ai-action-button" if small else "ai-action-button"
    return (
        '<form class="table-action-form ai-action-form" method="post" action="/ai-assistant-action">'
        + "".join(inputs)
        + f'<button class="{button_class}" type="submit">{html.escape(label)}</button>'
        + "</form>"
    )


def _ai_risk_tone(value: object) -> str:
    clean = str(value or "").strip().lower()
    if clean == "high":
        return "danger"
    if clean == "low":
        return "success"
    return "warn"


def _render_ai_result_card(ai_result: dict[str, object] | None) -> str:
    if not ai_result:
        return ""
    findings = ai_result.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    next_steps = ai_result.get("next_steps", [])
    if not isinstance(next_steps, list):
        next_steps = []
    planned_scopes = ai_result.get("planned_tool_scopes", [])
    if not isinstance(planned_scopes, list):
        planned_scopes = []
    primary_output = str(ai_result.get("primary_output", "")).strip()
    primary_output_markup = (
        '<article class="ai-result-panel">'
        f'<span class="eyebrow">{html.escape(str(ai_result.get("primary_output_label", "")).strip() or "AI Output")}</span>'
        f'<pre>{html.escape(primary_output)}</pre>'
        "</article>"
        if primary_output
        else ""
    )
    findings_markup = (
        "<ul>"
        + "".join(f"<li>{html.escape(str(item))}</li>" for item in findings[:6] if str(item).strip())
        + "</ul>"
        if findings
        else '<p class="helper-note">La IA no detecto hallazgos extra para este caso.</p>'
    )
    next_steps_markup = (
        "<ol>"
        + "".join(f"<li>{html.escape(str(item))}</li>" for item in next_steps[:6] if str(item).strip())
        + "</ol>"
        if next_steps
        else '<p class="helper-note">No hay siguientes pasos sugeridos todavia.</p>'
    )
    metadata_badges = [
        f'<span class="profile-pill neutral">{html.escape(str(ai_result.get("action_label", "")).strip() or "AI Action")}</span>',
        f'<span class="profile-pill {_ai_risk_tone(ai_result.get("risk_level", ""))}">Risk {html.escape(str(ai_result.get("risk_level", "")).strip().title() or "Medium")}</span>',
        '<span class="profile-pill success">Minimal structured data</span>' if bool(ai_result.get("structured_only")) else "",
    ]
    if planned_scopes:
        metadata_badges.append(
            f'<span class="profile-pill neutral">Future tools: {html.escape(", ".join(str(item) for item in planned_scopes[:4]))}</span>'
        )
    footer_parts = []
    if str(ai_result.get("model", "")).strip():
        footer_parts.append(f"Model: {str(ai_result.get('model', '')).strip()}")
    if str(ai_result.get("response_id", "")).strip():
        footer_parts.append(f"Response: {str(ai_result.get('response_id', '')).strip()}")
    footer_copy = " | ".join(footer_parts)
    return (
        '<section id="ai-assistant-result" class="panel section-card ai-result-card" data-skip-auto-collapsible="1">'
        '<div class="ai-result-head">'
        '<div class="page-title-stack">'
        '<span class="eyebrow">AI Assistant</span>'
        f"<h2>{html.escape(str(ai_result.get('headline', '')).strip() or 'AI Assistant')}</h2>"
        f"<p>{html.escape(str(ai_result.get('summary', '')).strip())}</p>"
        "</div>"
        f"<div class=\"profile-pill-row\">{''.join(item for item in metadata_badges if item)}</div>"
        "</div>"
        f"{primary_output_markup}"
        '<div class="ai-result-grid">'
        '<article class="ai-result-panel">'
        "<h3>Findings</h3>"
        f"{findings_markup}"
        "</article>"
        '<article class="ai-result-panel">'
        "<h3>Next Steps</h3>"
        f"{next_steps_markup}"
        "</article>"
        "</div>"
        + (f'<p class="helper-note">{html.escape(footer_copy)}</p>' if footer_copy else "")
        + "</section>"
    )


def _operation_summary(result_title: str, error: str, active_panel: str, current_user_name: str = "") -> tuple[str, str, str]:
    if error:
        return ("Requiere revision", "Corrige los datos del formulario antes de reenviar.", "summary-error")
    if result_title:
        return (result_title, f"Ultima operacion registrada en {_module_label(active_panel)}.", "summary-success")
    return (
        current_user_name or "Usuario activo",
        "Portal listo para reclamaciones, elegibilidad, remesas y seguimiento administrativo.",
        "summary-neutral",
    )


def _time_of_day_greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Buenos dias"
    if hour < 18:
        return "Buenas tardes"
    return "Buenas noches"


def _workspace_primary_action(
    current_page: str,
    allowed_pages: set[str],
    can_manage_provider_contracts: bool,
    *,
    can_edit_clients: bool,
    can_submit_claims: bool,
    can_manage_sessions: bool,
    can_manage_users: bool,
) -> tuple[str, str]:
    if current_page == "dashboard":
        if "agenda" in allowed_pages and can_manage_sessions:
            return ("Nueva tarea", f"{_page_href('agenda')}#agenda-form")
        if "clients" in allowed_pages and can_edit_clients:
            return ("Nuevo cliente", f"{_page_href('clients')}?new_client=1#clientsdb")
    if current_page == "agenda" and "agenda" in allowed_pages:
        return ("Nueva tarea", "#agenda-form") if can_manage_sessions else ("", "")
    if current_page == "clients" and "clients" in allowed_pages and can_edit_clients:
        return ("Nuevo cliente", f"{_page_href('clients')}?new_client=1#clientsdb")
    if current_page == "providers" and "providers" in allowed_pages:
        if can_manage_provider_contracts:
            return ("Nuevo provider", _new_provider_href())
        return ("", "")
    if current_page == "hr" and "providers" in allowed_pages and can_manage_provider_contracts:
        return ("Nuevo hire", _new_provider_href())
    if current_page in {"claims", "payments", "payers"} and "claims" in allowed_pages and can_submit_claims:
        return ("Nuevo claim", f"{_page_href('claims')}#claims837")
    if current_page == "notifications" and "notifications" in allowed_pages:
        return ("Nuevo email", "#notifications-compose")
    if current_page == "aba_notes" and "aba_notes" in allowed_pages and can_manage_sessions:
        return ("Nueva sesion", "#aba-notes-form")
    if current_page == "users" and "users" in allowed_pages and can_manage_users:
        return ("Nuevo usuario", "#users-directory-form")
    if "dashboard" in allowed_pages:
        return ("Ir al dashboard", _page_href("dashboard"))
    return ("", "")


def _normalize_cpt_code(value: str) -> str:
    clean = str(value or "").strip().upper().replace("CPT-", "").replace("CPT ", "")
    clean = " ".join(clean.split())
    if not clean:
        return ""
    if " " in clean and "-" not in clean:
        parts = clean.split()
        if len(parts) == 2 and parts[1]:
            clean = f"{parts[0]}-{parts[1]}"
    return clean


def _cpt_details(code: str) -> dict[str, object]:
    return CPT_CATALOG.get(_normalize_cpt_code(code), {})


def _default_unit_price(code: str, payer_name: str = "", payer_id: str = "") -> float | None:
    payer_price = get_payer_configured_unit_price(code, payer_name, payer_id)
    if payer_price is not None:
        return payer_price
    details = _cpt_details(code)
    unit_price = details.get("unit_price")
    return float(unit_price) if unit_price is not None else None


def _cpt_option_label(code: str, *, include_unit_price: bool = True) -> str:
    normalized = _normalize_cpt_code(code)
    details = _cpt_details(normalized)
    label_parts = [normalized]
    description = str(details.get("description", "")).strip()
    hcpcs = str(details.get("hcpcs", "")).strip()
    if description:
        label_parts.append(description)
    if hcpcs:
        label_parts.append(hcpcs)
    unit_price = _default_unit_price(normalized)
    if include_unit_price and unit_price is not None:
        label_parts.append(f"${unit_price:.2f}/unit")
    return " | ".join(label_parts)


def _cpt_table_label(code: str) -> str:
    normalized = _normalize_cpt_code(code)
    details = _cpt_details(normalized)
    description = str(details.get("description", "")).strip()
    return f"{normalized} | {description}" if description else normalized


def _display_unit_price(code: str, current_value: object, payer_name: str = "", payer_id: str = "") -> str:
    current_text = str(current_value or "").strip()
    if current_text:
        try:
            return f"{float(current_text):.2f}"
        except ValueError:
            return current_text
    default_price = _default_unit_price(code, payer_name, payer_id)
    return f"{default_price:.2f}" if default_price is not None else ""


def _line_unit_price(line: dict) -> str:
    charge_amount = float(line.get("charge_amount", 0) or 0)
    units = int(line.get("units", 0) or 0)
    if units <= 0:
        return ""
    return f"{charge_amount / units:.2f}"


def _claim_service_preview(values: dict[str, object]) -> dict[str, object]:
    preview: dict[str, object] = {
        "lines": 0,
        "units": 0,
        "minutes": 0,
        "total_charge_amount": 0.0,
        "total_charge_amount_value": "",
        "total_charge_amount_text": "$0.00",
    }
    payer_name = str(values.get("insurance_payer_name", "")).strip()
    payer_id = str(values.get("insurance_payer_id", "")).strip()

    for index in range(1, 4):
        code = _normalize_cpt_code(str(values.get(f"service_line_{index}_procedure_code", "")))
        unit_price_text = str(values.get(f"service_line_{index}_unit_price", "")).strip()
        units_text = str(values.get(f"service_line_{index}_units", "")).strip()
        charge_text = str(values.get(f"service_line_{index}_charge_amount", "")).strip()

        has_line = any([code, unit_price_text, units_text, charge_text])
        units = 0
        unit_price = None
        charge_amount = 0.0

        if units_text:
            try:
                units = max(int(float(units_text)), 0)
            except ValueError:
                units = 0
        if unit_price_text:
            try:
                unit_price = float(unit_price_text)
            except ValueError:
                unit_price = None
        if unit_price is None and code:
            unit_price = _default_unit_price(code, payer_name, payer_id)
        if charge_text:
            try:
                charge_amount = float(charge_text)
            except ValueError:
                charge_amount = 0.0
        elif unit_price is not None and units > 0:
            charge_amount = round(unit_price * units, 2)

        minutes = units * 15
        if has_line:
            preview["lines"] = int(preview["lines"]) + 1
            preview["units"] = int(preview["units"]) + units
            preview["minutes"] = int(preview["minutes"]) + minutes
            preview["total_charge_amount"] = float(preview["total_charge_amount"]) + charge_amount

        preview[f"line_{index}"] = {
            "charge_amount": charge_amount,
            "charge_amount_value": f"{charge_amount:.2f}" if has_line else "",
            "charge_text": f"${charge_amount:.2f}" if has_line else "--",
            "minutes": minutes,
            "minutes_text": f"{minutes} min" if has_line else "--",
        }

    total_charge_amount = round(float(preview["total_charge_amount"]), 2)
    preview["total_charge_amount"] = total_charge_amount
    preview["total_charge_amount_value"] = f"{total_charge_amount:.2f}" if int(preview["lines"]) else ""
    preview["total_charge_amount_text"] = f"${total_charge_amount:.2f}"
    return preview


def _send_via_outlook(
    recipient_email: str,
    subject: str,
    message: str,
    preferred_sender_email: str = "",
    mode: str = "draft",
) -> dict[str, str]:
    recipient_email = str(recipient_email).strip()
    subject = str(subject).strip()
    message = str(message).strip()
    preferred_sender_email = str(preferred_sender_email).strip()
    mode = "send" if str(mode).strip().lower() == "send" else "draft"

    if not recipient_email:
        raise ValueError("La notificacion no tiene un email destinatario.")
    if not subject:
        raise ValueError("Falta el asunto del email.")
    if not message:
        raise ValueError("Falta el mensaje del email.")

    payload_b64 = base64.b64encode(
        json.dumps(
            {
                "recipient_email": recipient_email,
                "subject": subject,
                "message": message,
                "preferred_sender_email": preferred_sender_email,
                "mode": mode,
            }
        ).encode("utf-8")
    ).decode("ascii")
    script = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$payloadJson = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{payload_b64}'))
$payload = $payloadJson | ConvertFrom-Json
function Invoke-OutlookRetry([scriptblock]$Operation, [int]$MaxAttempts = 12, [int]$DelayMs = 500) {{
  for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {{
    try {{
      return & $Operation
    }} catch [System.Runtime.InteropServices.COMException] {{
      $message = [string]$_.Exception.Message
      if ($_.Exception.HResult -eq -2147418111 -or $message -like '*Call was rejected by callee*') {{
        Start-Sleep -Milliseconds $DelayMs
        continue
      }}
      throw
    }}
  }}
  throw 'Outlook no respondio despues de varios intentos. Cierra ventanas emergentes de Outlook y vuelve a intentar.'
}}
try {{
  try {{
    $outlook = [Runtime.InteropServices.Marshal]::GetActiveObject('Outlook.Application')
  }} catch {{
    $outlook = New-Object -ComObject Outlook.Application
  }}

  $session = Invoke-OutlookRetry {{ $outlook.Session }}
  $null = Invoke-OutlookRetry {{ $session.Accounts.Count }}
  $mail = Invoke-OutlookRetry {{ $outlook.CreateItem(0) }}
  $mail.To = [string]$payload.recipient_email
  $mail.Subject = [string]$payload.subject
  $mail.Body = [string]$payload.message

  $usedAccount = ''
  if ([string]::IsNullOrWhiteSpace([string]$payload.preferred_sender_email) -eq $false) {{
    $preferredSender = [string]$payload.preferred_sender_email
    $account = $session.Accounts | Where-Object {{ [string]$_.SmtpAddress -ieq $preferredSender }} | Select-Object -First 1
    if ($account) {{
      $mail.SendUsingAccount = $account
      $usedAccount = [string]$account.SmtpAddress
    }}
  }}

  if (-not $usedAccount) {{
    try {{
      if ($session.Accounts.Count -gt 0) {{
        $usedAccount = [string]$session.Accounts.Item(1).SmtpAddress
      }}
    }} catch {{
      $usedAccount = ''
    }}
  }}

  if ([string]$payload.mode -eq 'send') {{
    Invoke-OutlookRetry {{ $mail.Send() }} | Out-Null
    Write-Output ('OK|SENT|' + $usedAccount)
  }} else {{
    Invoke-OutlookRetry {{ $mail.Save() }} | Out-Null
    try {{
      Invoke-OutlookRetry {{ $mail.Display() }} | Out-Null
    }} catch {{
      # If Outlook is still busy displaying the draft, keep the saved draft and return success.
    }}
    Write-Output ('OK|DRAFT|' + $usedAccount)
  }}
}} catch {{
  $message = ([string]$_.Exception.Message -replace '\\r?\\n', ' ').Trim()
  if ([string]::IsNullOrWhiteSpace($message)) {{
    $message = 'No pude conectar Outlook en esta computadora.'
  }}
  Write-Output ('ERROR|' + $message)
  exit 1
}}
"""
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_script],
        capture_output=True,
        text=True,
        timeout=45,
    )
    output_lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    final_output = output_lines[-1] if output_lines else ""
    if final_output.upper().startswith("ERROR|"):
        raise ValueError(final_output.partition("|")[2].strip() or "No pude conectar Outlook en esta computadora.")
    if result.returncode != 0 and not final_output:
        detail = (result.stderr or "").strip() or "No pude conectar Outlook en esta computadora."
        raise ValueError(detail)
    if final_output.upper().startswith("OK|"):
        action, _, used_account = final_output[3:].partition("|")
    else:
        action, _, used_account = final_output.partition("|")
    action = action.strip().upper() or ("SENT" if mode == "send" else "DRAFT")
    return {
        "mode": "send" if action == "SENT" else "draft",
        "status": "outlook_sent" if action == "SENT" else "outlook_draft",
        "account": used_account.strip(),
    }


def _claim_form_defaults() -> dict[str, str]:
    sample = json.loads(_load_example("claim.json"))
    provider = sample.get("provider", {})
    patient = sample.get("patient", {})
    address = patient.get("address", {})
    insurance = sample.get("insurance", {})
    diagnosis_codes = sample.get("diagnosis_codes", [])
    service_lines = sample.get("service_lines", [])

    def line(index: int) -> dict:
        return service_lines[index] if len(service_lines) > index else {}

    line_1 = line(0)
    line_2 = line(1)
    line_3 = line(2)
    line_1_code = _normalize_cpt_code(str(line_1.get("procedure_code", "")))
    line_2_code = _normalize_cpt_code(str(line_2.get("procedure_code", "")))
    line_3_code = _normalize_cpt_code(str(line_3.get("procedure_code", "")))
    payer_name = str(insurance.get("payer_name", ""))
    payer_id = str(insurance.get("payer_id", ""))

    return {
        "claim_id": str(sample.get("claim_id", "")),
        "service_date": format_user_date(sample.get("service_date", "")),
        "provider_npi": str(provider.get("npi", "")),
        "provider_taxonomy_code": str(provider.get("taxonomy_code", "")),
        "provider_first_name": str(provider.get("first_name", "")),
        "provider_last_name": str(provider.get("last_name", "")),
        "provider_organization_name": str(provider.get("organization_name", "")),
        "patient_member_id": str(patient.get("member_id", "")),
        "patient_first_name": str(patient.get("first_name", "")),
        "patient_last_name": str(patient.get("last_name", "")),
        "patient_birth_date": format_user_date(patient.get("birth_date", "")),
        "patient_gender": str(patient.get("gender", "")),
        "patient_address_line1": str(address.get("line1", "")),
        "patient_city": str(address.get("city", "")),
        "patient_state": str(address.get("state", "")),
        "patient_zip_code": str(address.get("zip_code", "")),
        "insurance_payer_name": str(insurance.get("payer_name", "")),
        "insurance_payer_id": str(insurance.get("payer_id", "")),
        "insurance_policy_number": str(insurance.get("policy_number", "")),
        "insurance_plan_name": str(insurance.get("plan_name", "")),
        "diagnosis_code_1": str(diagnosis_codes[0] if len(diagnosis_codes) > 0 else ""),
        "diagnosis_code_2": str(diagnosis_codes[1] if len(diagnosis_codes) > 1 else ""),
        "diagnosis_code_3": str(diagnosis_codes[2] if len(diagnosis_codes) > 2 else ""),
        "service_line_1_procedure_code": line_1_code,
        "service_line_1_unit_price": _display_unit_price(
            line_1_code,
            line_1.get("unit_price", _line_unit_price(line_1)),
            payer_name,
            payer_id,
        ),
        "service_line_1_charge_amount": str(line_1.get("charge_amount", "")),
        "service_line_1_units": str(line_1.get("units", "")),
        "service_line_1_diagnosis_pointer": str(line_1.get("diagnosis_pointer", "1")),
        "service_line_2_procedure_code": line_2_code,
        "service_line_2_unit_price": _display_unit_price(
            line_2_code,
            line_2.get("unit_price", _line_unit_price(line_2)),
            payer_name,
            payer_id,
        ),
        "service_line_2_charge_amount": str(line_2.get("charge_amount", "")),
        "service_line_2_units": str(line_2.get("units", "")),
        "service_line_2_diagnosis_pointer": str(line_2.get("diagnosis_pointer", "2")),
        "service_line_3_procedure_code": line_3_code,
        "service_line_3_unit_price": _display_unit_price(
            line_3_code,
            line_3.get("unit_price", _line_unit_price(line_3)),
            payer_name,
            payer_id,
        ),
        "service_line_3_charge_amount": str(line_3.get("charge_amount", "")),
        "service_line_3_units": str(line_3.get("units", "")),
        "service_line_3_diagnosis_pointer": str(line_3.get("diagnosis_pointer", "3")),
    }


def _eligibility_form_defaults() -> dict[str, str]:
    sample = json.loads(_load_example("eligibility.json"))
    return {
        "member_id": str(sample.get("member_id", "")),
        "patient_first_name": str(sample.get("patient_first_name", "")),
        "patient_last_name": str(sample.get("patient_last_name", "")),
        "patient_middle_name": str(sample.get("patient_middle_name", "")),
        "patient_birth_date": format_user_date(sample.get("patient_birth_date", "")),
        "patient_gender": str(sample.get("patient_gender", "")),
        "service_date": format_user_date(sample.get("service_date", "")),
    }


def _merge_form_values(defaults: dict[str, str], values: dict[str, str] | None) -> dict[str, str]:
    merged = defaults.copy()
    if values:
        for key, value in values.items():
            merged[key] = value
    return merged


def _field_value(values: dict[str, str], key: str) -> str:
    return html.escape(str(values.get(key, "")))


def _selected(values: dict[str, str], key: str, expected: str) -> str:
    return " selected" if str(values.get(key, "")) == expected else ""


def _selected_value(current_value: str, expected: str) -> str:
    return " selected" if current_value == expected else ""


def _checked(values: dict[str, str], key: str) -> str:
    value = str(values.get(key, "")).lower()
    return " checked" if value in {"1", "true", "yes", "on"} else ""


def _cpt_options_markup(
    selected_value: str,
    *,
    include_unit_price: bool = True,
    include_price_attr: bool = True,
) -> str:
    selected_code = _normalize_cpt_code(selected_value)
    options = ['<option value="">Selecciona un CPT</option>']
    for code in SUPPORTED_CPT_CODES:
        unit_price = _default_unit_price(code)
        price_attr = (
            f' data-unit-price="{unit_price:.2f}"'
            if include_price_attr and unit_price is not None
            else ""
        )
        options.append(
            f'<option value="{html.escape(code)}"{_selected_value(selected_code, code)}{price_attr}>{html.escape(_cpt_option_label(code, include_unit_price=include_unit_price))}</option>'
        )
    return "".join(options)


def _provider_type_options_markup(selected_value: str) -> str:
    options = []
    for provider_type in PROVIDER_TYPES:
        options.append(
            f'<option value="{html.escape(provider_type)}"{_selected_value(selected_value, provider_type)}>{html.escape(provider_type)}</option>'
        )
    return "".join(options)


def _workforce_category_options_markup(selected_value: str) -> str:
    options = []
    for category_value, category_label in WORKFORCE_CATEGORIES:
        options.append(
            f'<option value="{html.escape(category_value)}"{_selected_value(selected_value, category_value)}>{html.escape(category_label)}</option>'
        )
    return "".join(options)


def _office_department_options_markup(selected_value: str) -> str:
    options = ['<option value="">Selecciona un departamento</option>']
    for department in OFFICE_DEPARTMENTS:
        options.append(
            f'<option value="{html.escape(department)}"{_selected_value(selected_value, department)}>{html.escape(department)}</option>'
        )
    return "".join(options)


def _provider_contract_options_markup(
    items: list[dict[str, object]],
    selected_value: str,
    *,
    empty_label: str = "Sin asignar",
) -> str:
    options = [f'<option value="">{html.escape(empty_label)}</option>']
    sorted_items = sorted(
        items,
        key=lambda item: (
            str(item.get("provider_name", "")).strip().lower(),
            str(item.get("provider_type", "")).strip().lower(),
        ),
    )
    for item in sorted_items:
        contract_id = str(item.get("contract_id", "")).strip()
        if not contract_id:
            continue
        provider_name = str(item.get("provider_name", "")).strip() or contract_id
        provider_type = str(item.get("provider_type", "")).strip()
        site_location = str(item.get("site_location", "")).strip()
        label_parts = [provider_name]
        meta = " | ".join(part for part in (provider_type, site_location) if part)
        if meta:
            label_parts.append(meta)
        options.append(
            f'<option value="{html.escape(contract_id)}"{_selected_value(selected_value, contract_id)}>{html.escape(" - ".join(label_parts))}</option>'
        )
    return "".join(options)


def _location_options_markup(selected_value: str) -> str:
    options = ['<option value="">Selecciona un lugar</option>']
    for location_name, county_name in SITE_LOCATIONS:
        options.append(
            f'<option value="{html.escape(location_name)}"{_selected_value(selected_value, location_name)} data-county="{html.escape(county_name)}">{html.escape(location_name)}</option>'
        )
    return "".join(options)


def _contract_stage_options_markup(selected_value: str) -> str:
    options = []
    for stage_value, stage_label in CONTRACT_STAGES:
        options.append(
            f'<option value="{html.escape(stage_value)}"{_selected_value(selected_value, stage_value)}>{html.escape(stage_label)}</option>'
        )
    return "".join(options)


def _contract_stage_label(value: str) -> str:
    clean_value = str(value or "").strip()
    for stage_value, stage_label in CONTRACT_STAGES:
        if stage_value == clean_value:
            return stage_label
    return clean_value or "Sin etapa"


def _role_options_markup(selected_value: str) -> str:
    options = []
    normalized_selected = normalize_role(selected_value)
    for role_value in NORMALIZED_ROLES:
        role_label_text = ROLE_LABELS.get(role_value, role_value.title())
        options.append(
            f'<option value="{html.escape(role_value)}"{_selected_value(normalized_selected, role_value)}>{html.escape(role_label_text)}</option>'
        )
    return "".join(options)


def _page_options_markup(selected_value: str) -> str:
    options = []
    for page_key, page_label in PERMISSION_PAGE_LABELS.items():
        options.append(
            f'<option value="{html.escape(page_key)}"{_selected_value(selected_value, page_key)}>{html.escape(page_label)}</option>'
        )
    return "".join(options)


def _authorization_line_count_options_markup(selected_value: str) -> str:
    selected = str(selected_value or "5")
    options = []
    for count in range(1, MAX_AUTHORIZATION_LINES + 1):
        options.append(
            f'<option value="{count}"{_selected_value(selected, str(count))}>{count} codigos</option>'
        )
    return "".join(options)


def _authorization_line_rows_markup(values: dict[str, str]) -> str:
    rows = []
    try:
        visible_count = int(str(values.get("authorization_line_count", "5") or "5"))
    except ValueError:
        visible_count = MAX_AUTHORIZATION_LINES
    visible_count = max(1, min(MAX_AUTHORIZATION_LINES, visible_count))
    for index in range(1, MAX_AUTHORIZATION_LINES + 1):
        cpt_key = f"authorization_line_{index}_cpt_code"
        total_key = f"authorization_line_{index}_total_units"
        remaining_key = f"authorization_line_{index}_remaining_units"
        rows.append(
            f'<tr class="authorization-line-row" data-line-index="{index}"{" hidden" if index > visible_count else ""}>'
            f"<td>{index}</td>"
            f"<td><select name=\"{cpt_key}\">{_cpt_options_markup(str(values.get(cpt_key, '')), include_unit_price=False, include_price_attr=False)}</select></td>"
            f"<td><input name=\"{total_key}\" value=\"{_field_value(values, total_key)}\" placeholder=\"0\"></td>"
            f"<td><input name=\"{remaining_key}\" value=\"{_field_value(values, remaining_key)}\" placeholder=\"Si viene transferido, escribe lo restante\"></td>"
            "</tr>"
        )
    return "".join(rows)


def _pages_for_role(role: str) -> set[str]:
    defaults = rbac_allowed_pages_for_user({"role": role, "module_permissions": {}}, None)
    return defaults or {"dashboard"}


def _pages_for_user(user: dict[str, object] | None) -> set[str]:
    return rbac_allowed_pages_for_user(user)


def _allowed_aba_provider_ids_for_user(user: dict[str, object] | None) -> set[str]:
    if not user:
        return set()
    user_payload = dict(user)
    if not str(user_payload.get("linked_provider_name", "")).strip() and str(user_payload.get("username", "")).strip():
        try:
            user_payload = {**user_payload, **get_user_security_profile(str(user_payload.get("username", "")))}
        except Exception:
            pass
    normalized_role = normalized_role_from_user(user_payload, list_provider_contracts())
    linked_provider_name = str(user_payload.get("linked_provider_name", "")).strip().lower()
    if normalized_role in {"BCBA", "BCABA", "RBT"} and not linked_provider_name:
        return set()
    allowed_ids: set[str] = set()
    for item in list_provider_contracts():
        provider_type = str(item.get("provider_type", "")).strip().upper().replace(" ", "")
        if provider_type not in {"BCBA", "BCABA", "RBT"}:
            continue
        provider_name = str(item.get("provider_name", "")).strip().lower()
        if normalized_role in {"BCBA", "BCABA", "RBT"} and linked_provider_name and provider_name != linked_provider_name:
            continue
        contract_id = str(item.get("contract_id", "")).strip()
        if contract_id:
            allowed_ids.add(contract_id)
    return allowed_ids


def _current_user_provider_contract(user: dict[str, object] | None) -> dict[str, object] | None:
    if not user:
        return None
    linked_provider_name = str(user.get("linked_provider_name", "")).strip().lower()
    if not linked_provider_name:
        return None
    return next(
        (
            item
            for item in list_provider_contracts()
            if str(item.get("provider_name", "")).strip().lower() == linked_provider_name
        ),
        None,
    )


def _can_close_aba_note(user: dict[str, object] | None) -> bool:
    if not user:
        return False
    if has_permission(user, "notes.close") or has_permission(user, "notes.review"):
        return True
    provider_contract = _current_user_provider_contract(user)
    return normalize_role(user.get("role", ""), linked_provider_type=str((provider_contract or {}).get("provider_type", ""))) in {"BCBA", "BCABA"}


def _nav_hidden(allowed_pages: set[str], target_page: str) -> str:
    return "" if target_page in allowed_pages else " hidden"


def _default_page_for_role(role: str) -> str:
    return rbac_default_page_for_role(role)


def _default_page_for_user(user: dict[str, object] | None) -> str:
    if not user:
        return "dashboard"
    allowed = _pages_for_user(user)
    configured_default = get_default_landing_page()
    if configured_default in allowed:
        return configured_default
    preferred = _default_page_for_role(str(user.get("role", "")))
    if preferred in allowed:
        return preferred
    ordered_pages = [key for key in PERMISSION_PAGE_LABELS if key in allowed]
    return ordered_pages[0] if ordered_pages else "dashboard"


def _cookie_header(name: str, value: str, max_age: int | None = None) -> str:
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if max_age is not None:
        parts.insert(1, f"Max-Age={max_age}")
    return "; ".join(parts)


def _expired_cookie_header(name: str) -> str:
    return _cookie_header(name, "", 0)


def _session_active(session: dict[str, object] | None, timeout_seconds: int) -> bool:
    if not session:
        return False
    try:
        last_seen_at = float(session.get("last_seen_at", 0) or 0)
    except (TypeError, ValueError):
        return False
    return (time.time() - last_seen_at) <= timeout_seconds


def _permission_checked(values: dict[str, str], key: str, role: str) -> str:
    if f"perm_{key}" in values:
        return _checked(values, f"perm_{key}")
    return " checked" if key in _pages_for_role(role) else ""


def _module_permissions_markup(values: dict[str, str]) -> str:
    role = str(values.get("role", "MANAGER"))
    items = []
    for key, label in PERMISSION_PAGE_LABELS.items():
        items.append(
            "<label class=\"field permission-box\">"
            f"<span><input type=\"checkbox\" name=\"perm_{html.escape(key)}\"{_permission_checked(values, key, role)}> {html.escape(label)}</span>"
            "</label>"
        )
    return "".join(items)


def _authorization_form_defaults() -> dict[str, str]:
    defaults = {
        "authorization_group_id": "",
        "client_id": "",
        "patient_member_id": "",
        "patient_name": "",
        "payer_name": "",
        "authorization_number": "",
        "start_date": today_user_date(),
        "end_date": add_user_date_months(today_user_date(), 6),
        "authorization_line_count": "5",
        "notes": "",
    }
    for index in range(1, MAX_AUTHORIZATION_LINES + 1):
        defaults[f"authorization_line_{index}_cpt_code"] = ""
        defaults[f"authorization_line_{index}_total_units"] = ""
        defaults[f"authorization_line_{index}_remaining_units"] = ""
    return defaults


def _authorization_form_from_client(client: dict[str, object]) -> dict[str, str]:
    values = _authorization_form_defaults()
    patient_name = f"{client.get('first_name', '')} {client.get('last_name', '')}".strip()
    values.update(
        {
            "client_id": str(client.get("client_id", "")),
            "patient_member_id": str(client.get("member_id", "")),
            "patient_name": patient_name,
            "payer_name": str(client.get("payer_name", "")),
            "notes": str(client.get("notes", "")),
        }
    )
    return values


def _authorization_form_from_group(client: dict[str, object], group_items: list[dict[str, object]]) -> dict[str, str]:
    values = _authorization_form_from_client(client)
    if not group_items:
        return values
    sorted_items = sorted(
        group_items,
        key=lambda item: int(float(item.get("authorization_line_number", 0) or 0)),
    )
    first = sorted_items[0]
    values.update(
        {
            "authorization_group_id": str(first.get("authorization_group_id", "")),
            "authorization_number": str(first.get("authorization_number", "")),
            "start_date": str(first.get("start_date", "")),
            "end_date": str(first.get("end_date", "")),
            "notes": str(first.get("notes", "")),
            "authorization_line_count": str(len(sorted_items)),
        }
    )
    for index, item in enumerate(sorted_items, start=1):
        if index > MAX_AUTHORIZATION_LINES:
            break
        values[f"authorization_line_{index}_cpt_code"] = str(item.get("cpt_code", ""))
        values[f"authorization_line_{index}_total_units"] = str(int(float(item.get("total_units", 0) or 0)))
        values[f"authorization_line_{index}_remaining_units"] = str(int(float(item.get("remaining_units", 0) or 0)))
    return values


def _payer_plan_type_options_markup(selected_value: str) -> str:
    return "".join(
        f'<option value="{html.escape(value)}"{_selected_value(selected_value, value)}>{html.escape(label)}</option>'
        for value, label in PAYER_PLAN_TYPES
    )


def _clearinghouse_options_markup(selected_value: str) -> str:
    options = ['<option value="">Selecciona clearinghouse</option>']
    for name in CLEARINGHOUSE_OPTIONS:
        options.append(f'<option value="{html.escape(name)}"{_selected_value(selected_value, name)}>{html.escape(name)}</option>')
    return "".join(options)


def _payer_config_form_defaults() -> dict[str, str]:
    defaults = {
        "payer_config_id": "",
        "payer_name": "",
        "payer_id": "",
        "plan_type": "COMMERCIAL",
        "brand_color": "#0d51b8",
        "clearinghouse_name": "",
        "clearinghouse_payer_id": "",
        "clearinghouse_receiver_id": "",
        "notes": "",
        "active": "on",
    }
    for index, cpt_code in enumerate(SUPPORTED_CPT_CODES, start=1):
        details = _cpt_details(cpt_code)
        defaults[f"payer_rate_{index}_cpt_code"] = cpt_code
        defaults[f"payer_rate_{index}_billing_code"] = cpt_code
        defaults[f"payer_rate_{index}_hcpcs_code"] = str(details.get("hcpcs", "") or "")
        defaults[f"payer_rate_{index}_unit_price"] = _display_unit_price(cpt_code, "")
    return defaults


def _payer_config_form_from_record(record: dict[str, object]) -> dict[str, str]:
    values = _payer_config_form_defaults()
    values.update(
        {
            "payer_config_id": str(record.get("payer_config_id", "")),
            "payer_name": str(record.get("payer_name", "")),
            "payer_id": str(record.get("payer_id", "")),
            "plan_type": str(record.get("plan_type", "COMMERCIAL") or "COMMERCIAL"),
            "brand_color": str(record.get("brand_color", "#0d51b8") or "#0d51b8"),
            "clearinghouse_name": str(record.get("clearinghouse_name", "")),
            "clearinghouse_payer_id": str(record.get("clearinghouse_payer_id", "")),
            "clearinghouse_receiver_id": str(record.get("clearinghouse_receiver_id", "")),
            "notes": str(record.get("notes", "")),
            "active": "on" if bool(record.get("active", True)) else "",
        }
    )
    line_map = {
        _normalize_cpt_code(str(line.get("cpt_code", ""))): line
        for line in (record.get("rate_lines", []) if isinstance(record.get("rate_lines", []), list) else [])
    }
    for index, cpt_code in enumerate(SUPPORTED_CPT_CODES, start=1):
        line = line_map.get(_normalize_cpt_code(cpt_code), {})
        values[f"payer_rate_{index}_cpt_code"] = cpt_code
        values[f"payer_rate_{index}_billing_code"] = str(line.get("billing_code", cpt_code) or cpt_code)
        values[f"payer_rate_{index}_hcpcs_code"] = str(line.get("hcpcs_code", _cpt_details(cpt_code).get("hcpcs", "")) or "")
        values[f"payer_rate_{index}_unit_price"] = _display_unit_price(cpt_code, line.get("unit_price", ""))
    return values


def _payer_rate_rows_markup(values: dict[str, str]) -> str:
    rows = []
    for index, cpt_code in enumerate(SUPPORTED_CPT_CODES, start=1):
        details = _cpt_details(cpt_code)
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(cpt_code)}</strong><input type=\"hidden\" name=\"payer_rate_{index}_cpt_code\" value=\"{html.escape(cpt_code)}\"></td>"
            f"<td>{html.escape(str(details.get('description', '')) or '-')}</td>"
            f"<td><input name=\"payer_rate_{index}_billing_code\" value=\"{_field_value(values, f'payer_rate_{index}_billing_code')}\" placeholder=\"{html.escape(cpt_code)}\"></td>"
            f"<td><input name=\"payer_rate_{index}_hcpcs_code\" value=\"{_field_value(values, f'payer_rate_{index}_hcpcs_code')}\" placeholder=\"HCPCS\"></td>"
            f"<td><input name=\"payer_rate_{index}_unit_price\" value=\"{_field_value(values, f'payer_rate_{index}_unit_price')}\" placeholder=\"0.00\"></td>"
            "</tr>"
        )
    return "".join(rows)


def _roster_form_defaults() -> dict[str, str]:
    return {
        "payer_id": "",
        "provider_npi": "",
        "member_id": "",
        "patient_first_name": "",
        "patient_last_name": "",
        "patient_birth_date": today_user_date(),
        "service_date": today_user_date(),
    }


def _email_form_defaults() -> dict[str, str]:
    return {
        "recipient_label": "",
        "recipient_email": "",
        "subject": "",
        "message": "",
        "save_to_notifications": "on",
    }


def _era_form_defaults() -> dict[str, str]:
    parsed = Era835Parser().parse(_load_example("sample_835.txt"))
    detail = parsed.claim_details[0] if parsed.claim_details else {}
    return {
        "transaction_set_control_number": parsed.transaction_set_control_number or "0001",
        "payer_name": str(parsed.payer_name or ""),
        "payee_name": str(parsed.payee_name or ""),
        "payment_amount": "" if parsed.payment_amount is None else f"{parsed.payment_amount:.2f}",
        "claim_id": str(detail.get("claim_id", "")),
        "payer_claim_number": str(detail.get("payer_claim_number", "")),
        "claim_status_code": str(detail.get("claim_status_code", "1")),
        "charge_amount": "" if detail.get("charge_amount") is None else f"{float(detail.get('charge_amount', 0)):.2f}",
        "paid_amount": "" if detail.get("paid_amount") is None else f"{float(detail.get('paid_amount', 0)):.2f}",
    }


def _edi837_form_defaults() -> dict[str, str]:
    parsed = Claim837Parser().parse(_load_example("sample_837.txt"))
    line_1 = parsed.service_lines[0] if len(parsed.service_lines) > 0 else {}
    line_2 = parsed.service_lines[1] if len(parsed.service_lines) > 1 else {}
    line_3 = parsed.service_lines[2] if len(parsed.service_lines) > 2 else {}
    return {
        "transaction_set_control_number": parsed.transaction_set_control_number or "0001",
        "payer_name": str(parsed.payer_name or ""),
        "provider_name": str(parsed.provider_name or ""),
        "provider_npi": str(parsed.provider_npi or ""),
        "patient_name": str(parsed.patient_name or ""),
        "member_id": str(parsed.member_id or ""),
        "claim_id": str(parsed.claim_id or ""),
        "service_date": str(parsed.service_date or ""),
        "total_charge_amount": "" if parsed.total_charge_amount is None else f"{parsed.total_charge_amount:.2f}",
        "service_line_1_procedure_code": _normalize_cpt_code(str(line_1.get("procedure_code", ""))),
        "service_line_1_charge_amount": "" if line_1.get("charge_amount") is None else f"{float(line_1.get('charge_amount', 0)):.2f}",
        "service_line_1_units": str(line_1.get("units", "")),
        "service_line_2_procedure_code": _normalize_cpt_code(str(line_2.get("procedure_code", ""))),
        "service_line_2_charge_amount": "" if line_2.get("charge_amount") is None else f"{float(line_2.get('charge_amount', 0)):.2f}",
        "service_line_2_units": str(line_2.get("units", "")),
        "service_line_3_procedure_code": _normalize_cpt_code(str(line_3.get("procedure_code", ""))),
        "service_line_3_charge_amount": "" if line_3.get("charge_amount") is None else f"{float(line_3.get('charge_amount', 0)):.2f}",
        "service_line_3_units": str(line_3.get("units", "")),
    }


def _client_form_defaults() -> dict[str, str]:
    sample = json.loads(_load_example("eligibility.json"))
    defaults = {
        "client_id": "",
        "first_name": str(sample.get("patient_first_name", "")),
        "last_name": str(sample.get("patient_last_name", "")),
        "preferred_language": "English",
        "diagnosis": "",
        "member_id": str(sample.get("member_id", "")),
        "birth_date": format_user_date(sample.get("patient_birth_date", "")),
        "service_date": format_user_date(sample.get("service_date", "")),
        "payer_name": "Demo Health Plan",
        "payer_id": str(sample.get("payer_id", "")),
        "insurance_effective_date": "",
        "subscriber_name": "",
        "subscriber_id": "",
        "provider_npi": str(sample.get("provider_npi", "")),
        "site_location": "Cape Coral",
        "county_name": "Lee",
        "gender": "M",
        "medicaid_id": "",
        "address_line1": "",
        "address_city": "",
        "address_state": "FL",
        "address_zip_code": "",
        "caregiver_name": "",
        "caregiver_relationship": "",
        "caregiver_phone": "",
        "caregiver_email": "",
        "physician_name": "",
        "physician_npi": "",
        "physician_phone": "",
        "physician_address": "",
        "bcba_contract_id": "",
        "bcaba_contract_id": "",
        "rbt_contract_id": "",
        "notes": "",
        "active": "on",
        "auto_eligibility": "on",
    }
    for index, _document_name in enumerate(list_client_required_documents()):
        defaults[f"client_document_{index}_issued_date"] = ""
        defaults[f"client_document_{index}_expiration_date"] = ""
        defaults[f"client_document_{index}_status"] = "Pending"
    return defaults


def _client_form_from_record(record: dict[str, object]) -> dict[str, str]:
    values = _client_form_defaults()
    values.update(
        {
            "client_id": str(record.get("client_id", "")),
            "first_name": str(record.get("first_name", "")),
            "last_name": str(record.get("last_name", "")),
            "preferred_language": str(record.get("preferred_language", "")),
            "diagnosis": str(record.get("diagnosis", "")),
            "member_id": str(record.get("member_id", "")),
            "birth_date": str(record.get("birth_date", "")),
            "service_date": str(record.get("service_date", "")),
            "payer_name": str(record.get("payer_name", "")),
            "payer_id": str(record.get("payer_id", "")),
            "insurance_effective_date": str(record.get("insurance_effective_date", "")),
            "subscriber_name": str(record.get("subscriber_name", "")),
            "subscriber_id": str(record.get("subscriber_id", "")),
            "provider_npi": str(record.get("provider_npi", "")),
            "site_location": str(record.get("site_location", "")),
            "county_name": str(record.get("county_name", "")),
            "gender": str(record.get("gender", "")),
            "medicaid_id": str(record.get("medicaid_id", "")),
            "address_line1": str(record.get("address_line1", "")),
            "address_city": str(record.get("address_city", "")),
            "address_state": str(record.get("address_state", "")),
            "address_zip_code": str(record.get("address_zip_code", "")),
            "caregiver_name": str(record.get("caregiver_name", "")),
            "caregiver_relationship": str(record.get("caregiver_relationship", "")),
            "caregiver_phone": str(record.get("caregiver_phone", "")),
            "caregiver_email": str(record.get("caregiver_email", "")),
            "physician_name": str(record.get("physician_name", "")),
            "physician_npi": str(record.get("physician_npi", "")),
            "physician_phone": str(record.get("physician_phone", "")),
            "physician_address": str(record.get("physician_address", "")),
            "bcba_contract_id": str(record.get("bcba_contract_id", "")),
            "bcaba_contract_id": str(record.get("bcaba_contract_id", "")),
            "rbt_contract_id": str(record.get("rbt_contract_id", "")),
            "notes": str(record.get("notes", "")),
            "active": "on" if bool(record.get("active", True)) else "",
            "auto_eligibility": "on" if bool(record.get("auto_eligibility", True)) else "",
        }
    )
    document_map = {
        str(document.get("document_name", "")): document
        for document in (record.get("documents", []) if isinstance(record.get("documents", []), list) else [])
    }
    for index, document_name in enumerate(list_client_required_documents()):
        document = document_map.get(document_name, {})
        values[f"client_document_{index}_issued_date"] = str(document.get("issued_date", ""))
        values[f"client_document_{index}_expiration_date"] = str(document.get("expiration_date", ""))
        values[f"client_document_{index}_status"] = str(document.get("requested_status", "") or document.get("status", "Pending"))
    return values


def _payer_enrollment_form_defaults() -> dict[str, str]:
    return {
        "contract_id": "",
        "provider_name": "Demo Clinic",
        "ssn": "",
        "npi": "1234567893",
        "medicaid_id": "",
        "payer_name": "Demo Health Plan",
        "enrollment_status": "SUBMITTED",
        "credentials_submitted_date": today_user_date(),
        "effective_date": "",
        "site_location": "Cape Coral",
        "county_name": "Lee",
        "credentialing_owner_name": "",
        "supervisor_name": "",
        "notes": "",
    }


def _agency_form_defaults() -> dict[str, str]:
    return {
        "agency_id": "",
        "agency_name": "Blue Hope Main",
        "agency_code": "BH-MAIN",
        "notification_email": "",
        "contact_name": "",
        "notes": "",
    }


def _provider_contract_form_defaults() -> dict[str, str]:
    defaults = {
        "contract_id": "",
        "provider_name": "",
        "worker_category": "PROVIDER",
        "provider_type": "BCBA",
        "office_department": "",
        "provider_npi": "",
        "contract_stage": "NEW",
        "start_date": today_user_date(),
        "expected_start_date": today_user_date(),
        "site_location": "Cape Coral",
        "county_name": "Lee",
        "recruiter_name": "",
        "supervisor_name": "",
        "credentialing_owner_name": "",
        "office_reviewer_name": "",
        "assigned_clients": "",
        "credentialing_start_date": today_user_date(),
        "notes": "",
        "provider_document_name": "",
        "provider_document_issued_date": "",
        "provider_document_expiration_date": "",
    }
    return defaults


def _provider_contract_form_from_record(record: dict[str, object]) -> dict[str, str]:
    values = _provider_contract_form_defaults()
    values.update(
        {
            "contract_id": str(record.get("contract_id", "")),
            "provider_name": str(record.get("provider_name", "")),
            "worker_category": str(record.get("worker_category", "PROVIDER") or "PROVIDER"),
            "provider_type": str(record.get("provider_type", "")),
            "office_department": str(record.get("office_department", "")),
            "provider_npi": str(record.get("provider_npi", "")),
            "contract_stage": str(record.get("contract_stage", "NEW") or "NEW"),
            "start_date": str(record.get("start_date", "")),
            "expected_start_date": str(record.get("expected_start_date", "")),
            "site_location": str(record.get("site_location", "")),
            "county_name": str(record.get("county_name", "")),
            "recruiter_name": str(record.get("recruiter_name", "")),
            "supervisor_name": str(record.get("supervisor_name", "")),
            "credentialing_owner_name": str(record.get("credentialing_owner_name", "")),
            "office_reviewer_name": str(record.get("office_reviewer_name", "")),
            "assigned_clients": str(record.get("assigned_clients", "")),
            "credentialing_start_date": str(record.get("credentialing_start_date", "")),
            "notes": str(record.get("notes", "")),
        }
    )
    document_map = {
        str(document.get("document_name", "")): document
        for document in (record.get("documents", []) if isinstance(record.get("documents", []), list) else [])
    }
    for index, document_name in enumerate(list_provider_required_documents()):
        document = document_map.get(document_name, {})
        values[f"provider_document_{index}_issued_date"] = str(document.get("issued_date", ""))
        values[f"provider_document_{index}_expiration_date"] = str(document.get("expiration_date", ""))
        values[f"provider_document_{index}_status"] = str(document.get("requested_status", "") or document.get("status", "Pending"))
    return values


def _provider_contract_hidden_fields_markup(values: dict[str, str], exclude_fields: set[str] | None = None) -> str:
    excluded = exclude_fields or set()
    hidden_fields = (
        "contract_id",
        "provider_name",
        "worker_category",
        "provider_type",
        "office_department",
        "provider_npi",
        "contract_stage",
        "start_date",
        "expected_start_date",
        "site_location",
        "county_name",
        "recruiter_name",
        "supervisor_name",
        "credentialing_owner_name",
        "office_reviewer_name",
        "assigned_clients",
        "credentialing_start_date",
        "notes",
    )
    return "".join(
        f'<input type="hidden" name="{field_name}" value="{html.escape(str(values.get(field_name, "")))}">'
        for field_name in hidden_fields
        if field_name not in excluded
    )


def _user_form_defaults() -> dict[str, str]:
    return {
        "full_name": "",
        "username": "",
        "email": "",
        "phone": "",
        "job_title": "",
        "bio": "",
        "site_location": "Cape Coral",
        "county_name": "Lee",
        "linked_provider_name": "",
        "profile_color": "#0d51b8",
        "send_welcome_email": "on",
        "role": "MANAGER",
        "active": "on",
    }


def _agenda_form_defaults() -> dict[str, str]:
    return {
        "title": "",
        "category": "task",
        "event_date": today_user_date(),
        "due_date": today_user_date(),
        "assigned_username": "",
        "related_provider": "",
        "description": "",
        "notify_email": "on",
    }


def _note_form_defaults() -> dict[str, str]:
    return {
        "title": "",
        "body": "",
    }


def _aba_notes_form_defaults() -> dict[str, str]:
    return {
        "provider_contract_id": "",
        "client_id": "",
        "service_context": "direct",
        "appointment_date": today_user_date(),
        "start_time": "09:00",
        "end_time": "10:00",
        "place_of_service": "Home (12)",
        "caregiver_name": "",
        "caregiver_signature": "",
        "provider_signature": "",
        "supervisor_name": "",
        "session_note": "",
        "workflow_reason": "",
        "selected_log_id": "",
    }


def _system_config_form_defaults(config: dict[str, object] | None = None) -> dict[str, str]:
    current = config or load_system_configuration()
    return {
        "portal_label": str(current.get("portal_label", "")),
        "default_landing_page": str(current.get("default_landing_page", "dashboard")),
        "session_timeout_minutes": str(current.get("session_timeout_minutes", 30)),
        "mfa_timeout_minutes": str(current.get("mfa_timeout_minutes", 10)),
        "password_reset_minutes": str(current.get("password_reset_minutes", 30)),
        "lockout_attempts": str(current.get("lockout_attempts", 5)),
        "lockout_minutes": str(current.get("lockout_minutes", 15)),
        "billing_unit_minutes": str(current.get("billing_unit_minutes", 15)),
        "eligibility_run_days": ", ".join(str(day) for day in current.get("eligibility_run_days", [1, 15])),
        "eligibility_check_interval_hours": str(current.get("eligibility_check_interval_hours", 6)),
    }


def _edi837_default() -> str:
    return ""


def _build_parsed_835_from_form(form_data: dict[str, str]) -> Parsed835:
    claim_id = form_data.get("claim_id", "").strip()
    if not claim_id:
        raise ValueError("Falta el claim ID del 835.")

    try:
        payment_amount = float(form_data.get("payment_amount", "0").strip() or "0")
    except ValueError as exc:
        raise ValueError("El pago total del 835 no es valido.") from exc

    try:
        charge_amount = float(form_data.get("charge_amount", "0").strip() or "0")
    except ValueError as exc:
        raise ValueError("El charge amount del claim no es valido.") from exc

    try:
        paid_amount = float(form_data.get("paid_amount", "0").strip() or "0")
    except ValueError as exc:
        raise ValueError("El paid amount del claim no es valido.") from exc

    payer_claim_number = form_data.get("payer_claim_number", "").strip()
    claim_status_code = form_data.get("claim_status_code", "").strip() or "1"
    detail = {
        "claim_id": claim_id,
        "claim_status_code": claim_status_code,
        "charge_amount": charge_amount,
        "paid_amount": paid_amount,
        "payer_claim_number": payer_claim_number,
    }
    claim_statuses = [
        " ".join(
            [
                f"claim={claim_id}",
                f"payer_claim={payer_claim_number or 'N/A'}",
                f"status={claim_status_code}",
                f"charge={charge_amount:.2f}",
                f"paid={paid_amount:.2f}",
            ]
        )
    ]
    return Parsed835(
        transaction_set_control_number=form_data.get("transaction_set_control_number", "").strip() or "0001",
        payer_name=form_data.get("payer_name", "").strip() or None,
        payee_name=form_data.get("payee_name", "").strip() or None,
        payment_amount=payment_amount,
        claim_statuses=claim_statuses,
        claim_details=[detail],
    )


def _build_parsed_837_from_form(form_data: dict[str, str]) -> Parsed837:
    claim_id = form_data.get("claim_id", "").strip()
    if not claim_id:
        raise ValueError("Falta el claim ID del 837.")

    service_date = ""
    if form_data.get("service_date", "").strip():
        service_date = format_user_date(form_data.get("service_date", "").strip())
    total_charge_text = form_data.get("total_charge_amount", "").strip()
    total_charge_amount = None
    if total_charge_text:
        try:
            total_charge_amount = float(total_charge_text)
        except ValueError as exc:
            raise ValueError("El total charge del 837 no es valido.") from exc

    service_lines: list[dict] = []
    for index in range(1, 4):
        procedure_code = _normalize_cpt_code(form_data.get(f"service_line_{index}_procedure_code", "").strip())
        charge_text = form_data.get(f"service_line_{index}_charge_amount", "").strip()
        units_text = form_data.get(f"service_line_{index}_units", "").strip()
        if not any([procedure_code, charge_text, units_text]):
            continue
        if not procedure_code:
            raise ValueError(f"Falta el CPT o HCPCS en la linea {index} del 837.")
        try:
            charge_amount = float(charge_text or "0")
        except ValueError as exc:
            raise ValueError(f"El charge amount de la linea {index} no es valido.") from exc
        try:
            units = int(units_text or "0")
        except ValueError as exc:
            raise ValueError(f"Las units de la linea {index} no son validas.") from exc
        service_lines.append(
            {
                "procedure_code": procedure_code,
                "charge_amount": charge_amount,
                "units": units,
            }
        )

    if total_charge_amount is None and service_lines:
        total_charge_amount = round(sum(float(line["charge_amount"]) for line in service_lines), 2)

    return Parsed837(
        transaction_set_control_number=form_data.get("transaction_set_control_number", "").strip() or "0001",
        payer_name=form_data.get("payer_name", "").strip() or None,
        patient_name=form_data.get("patient_name", "").strip() or None,
        member_id=form_data.get("member_id", "").strip() or None,
        provider_name=form_data.get("provider_name", "").strip() or None,
        provider_npi=form_data.get("provider_npi", "").strip() or None,
        claim_id=claim_id,
        service_date=service_date or None,
        total_charge_amount=total_charge_amount,
        service_lines=service_lines,
    )


def _build_835_payload(parsed: Parsed835) -> str:
    detail = parsed.claim_details[0] if parsed.claim_details else {}
    return "~\n".join(
        [
            f"ISA*00*          *00*          *ZZ*PAYER          *ZZ*PROVIDER       *{format_edi_date(today_user_date())[-6:]}*1200*^*00501*000000905*1*T*:",
            f"GS*HP*PAYER*PROVIDER*{format_edi_date(today_user_date())}*1200*1*X*005010X221A1",
            f"ST*835*{parsed.transaction_set_control_number or '0001'}",
            f"BPR*I*{float(parsed.payment_amount or 0):.2f}*C*CHK************{format_edi_date(today_user_date())}",
            f"N1*PR*{parsed.payer_name or ''}",
            f"N1*PE*{parsed.payee_name or ''}",
            (
                f"CLP*{detail.get('claim_id', '')}*{detail.get('claim_status_code', '1')}*"
                f"{float(detail.get('charge_amount', 0) or 0):.2f}*{float(detail.get('paid_amount', 0) or 0):.2f}*"
                f"{max(float(detail.get('charge_amount', 0) or 0) - float(detail.get('paid_amount', 0) or 0), 0):.2f}*12*"
                f"{detail.get('payer_claim_number', '')}*11*1"
            ),
            "SE*8*0001",
            "GE*1*1",
            "IEA*1*000000905",
            "",
        ]
    )


def _claim_payload_from_form(form_data: dict[str, str]) -> dict:
    diagnosis_codes = [
        form_data.get("diagnosis_code_1", "").strip(),
        form_data.get("diagnosis_code_2", "").strip(),
        form_data.get("diagnosis_code_3", "").strip(),
    ]
    diagnosis_codes = [code for code in diagnosis_codes if code]

    service_lines = []
    total_charge_amount = 0.0
    payer_name = form_data.get("insurance_payer_name", "").strip()
    payer_id = form_data.get("insurance_payer_id", "").strip()

    for index in range(1, 4):
        procedure_code = _normalize_cpt_code(form_data.get(f"service_line_{index}_procedure_code", "").strip())
        unit_price_text = form_data.get(f"service_line_{index}_unit_price", "").strip()
        units_text = form_data.get(f"service_line_{index}_units", "").strip()
        diagnosis_pointer = form_data.get(f"service_line_{index}_diagnosis_pointer", "").strip() or str(index)

        if not any([procedure_code, unit_price_text, units_text]):
            continue
        if not procedure_code:
            raise ValueError(f"Falta el CPT o procedure code en la linea {index}.")
        if not unit_price_text:
            default_unit_price = _default_unit_price(procedure_code, payer_name, payer_id)
            if default_unit_price is None:
                raise ValueError(f"Falta el precio por unidad en la linea {index}.")
            unit_price_text = f"{default_unit_price:.2f}"

        try:
            unit_price = float(unit_price_text)
        except ValueError as exc:
            raise ValueError(f"El precio por unidad de la linea {index} no es valido.") from exc

        try:
            units = int(units_text or "1")
        except ValueError as exc:
            raise ValueError(f"Las unidades de la linea {index} no son validas.") from exc

        if units <= 0:
            raise ValueError(f"Las unidades de la linea {index} deben ser mayores que cero.")

        charge_amount = round(unit_price * units, 2)

        service_lines.append(
            {
                "procedure_code": procedure_code,
                "charge_amount": charge_amount,
                "units": units,
                "unit_price": unit_price,
                "diagnosis_pointer": diagnosis_pointer,
            }
        )
        total_charge_amount += charge_amount

    if not service_lines:
        raise ValueError("Agrega por lo menos una linea de servicio con CPT y cargo.")

    return {
        "claim_id": form_data.get("claim_id", "").strip(),
        "provider": {
            "npi": form_data.get("provider_npi", "").strip(),
            "taxonomy_code": form_data.get("provider_taxonomy_code", "").strip(),
            "first_name": form_data.get("provider_first_name", "").strip(),
            "last_name": form_data.get("provider_last_name", "").strip(),
            "organization_name": form_data.get("provider_organization_name", "").strip() or None,
        },
        "patient": {
            "member_id": form_data.get("patient_member_id", "").strip(),
            "first_name": form_data.get("patient_first_name", "").strip(),
            "last_name": form_data.get("patient_last_name", "").strip(),
            "birth_date": format_user_date(form_data.get("patient_birth_date", "").strip()),
            "gender": form_data.get("patient_gender", "").strip(),
            "address": {
                "line1": form_data.get("patient_address_line1", "").strip(),
                "city": form_data.get("patient_city", "").strip(),
                "state": form_data.get("patient_state", "").strip(),
                "zip_code": form_data.get("patient_zip_code", "").strip(),
            },
        },
        "insurance": {
            "payer_name": form_data.get("insurance_payer_name", "").strip(),
            "payer_id": form_data.get("insurance_payer_id", "").strip(),
            "policy_number": form_data.get("insurance_policy_number", "").strip(),
            "plan_name": form_data.get("insurance_plan_name", "").strip() or None,
        },
        "service_date": format_user_date(form_data.get("service_date", "").strip()),
        "diagnosis_codes": diagnosis_codes,
        "service_lines": service_lines,
        "total_charge_amount": total_charge_amount,
    }


def _eligibility_payload_from_form(form_data: dict[str, str]) -> dict:
    return {
        "payer_id": "",
        "provider_npi": "",
        "member_id": form_data.get("member_id", "").strip(),
        "patient_first_name": form_data.get("patient_first_name", "").strip(),
        "patient_last_name": form_data.get("patient_last_name", "").strip(),
        "patient_middle_name": form_data.get("patient_middle_name", "").strip(),
        "patient_birth_date": format_user_date(form_data.get("patient_birth_date", "").strip()),
        "patient_gender": form_data.get("patient_gender", "").strip(),
        "service_date": format_user_date(form_data.get("service_date", "").strip()),
    }


def _render_claim_rows(
    items: list[dict[str, object]],
    *,
    include_totals: bool = True,
    include_paid: bool = True,
) -> str:
    if not items:
        return '<tr><td colspan="10">Todavia no hay claims guardados.</td></tr>'

    rows = []
    for item in items:
        charge_markup = f"${float(item.get('total_charge_amount', 0)):.2f}" if include_totals else "Restricted"
        paid_markup = f"${float(item.get('paid_amount', 0)):.2f}" if include_paid else "Restricted"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('claim_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_claim_number', '')) or 'Pendiente')}</td>"
            f"<td>{html.escape(str(item.get('patient_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('service_date', '')))}</td>"
            f"<td>{html.escape(charge_markup)}</td>"
            f"<td>{html.escape(paid_markup)}</td>"
            f"<td>{html.escape(str(item.get('status', '')).upper())}</td>"
            f"<td>{html.escape(str(item.get('transmission_status', 'queued')).upper())}</td>"
            f"<td><a href=\"/cms1500?claim_id={html.escape(str(item.get('claim_id', '')))}\">CMS-1500</a> | <a href=\"/claim-edi?claim_id={html.escape(str(item.get('claim_id', '')))}\">837</a></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_batch_claim_rows(
    items: list[dict[str, object]],
    *,
    include_totals: bool = True,
    can_transmit: bool = True,
) -> str:
    if not items:
        return '<tr><td colspan="11">Todavia no hay claims en el batch.</td></tr>'

    rows = []
    for item in items:
        claim_id = html.escape(str(item.get("claim_id", "")))
        transmission_status = str(item.get("transmission_status", "queued"))
        charge_markup = f"${float(item.get('total_charge_amount', 0)):.2f}" if include_totals else "Restricted"
        action_markup = (
            "<form class=\"table-action-form\" method=\"post\" action=\"/transmit-claim\">"
            f"<input type=\"hidden\" name=\"claim_id\" value=\"{claim_id}\">"
            "<button class=\"small-button\" type=\"submit\">Transmit</button>"
            "</form>"
            if can_transmit and transmission_status != "transmitted"
            else html.escape(str(item.get("transmitted_at", "")) or "Transmitido")
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('batch_date', '')))}</td>"
            f"<td>{claim_id}</td>"
            f"<td>{html.escape(str(item.get('patient_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('service_date', '')))}</td>"
            f"<td>{html.escape(charge_markup)}</td>"
            f"<td>{html.escape(str(item.get('source_file_name', '')) or 'Captura manual')}</td>"
            f"<td>{html.escape(transmission_status.upper())}</td>"
            f"<td>{html.escape(str(item.get('tracking_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('transmitted_at', '')))}</td>"
            f"<td>{action_markup} <a href=\"/claim-edi?claim_id={claim_id}\">837</a></td>"
            "</tr>"
        )
    return "".join(rows)


def _compact_number(value: object) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return str(value or "0")
    if abs(number - round(number)) < 0.001:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _ordinal_day(day_number: int) -> str:
    if 10 <= day_number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_number % 10, "th")
    return f"{day_number}{suffix}"


def _authorization_pretty_date(raw_value: object) -> str:
    try:
        parsed = parse_user_date(str(raw_value or ""))
    except ValueError:
        return str(raw_value or "-")
    return f"{parsed.strftime('%B')} {_ordinal_day(parsed.day)} {parsed.year}"


def _authorization_relative_date(raw_value: object) -> str:
    try:
        parsed = parse_user_date(str(raw_value or ""))
    except ValueError:
        return ""
    delta_days = (parsed - datetime.now().date()).days
    if delta_days == 0:
        return "today"
    absolute_days = abs(delta_days)
    if absolute_days < 45:
        unit_label = "day" if absolute_days == 1 else "days"
        return f"{absolute_days} {unit_label} ago" if delta_days < 0 else f"in {absolute_days} {unit_label}"
    if absolute_days < 540:
        months = max(1, int(round(absolute_days / 30)))
        unit_label = "month" if months == 1 else "months"
        return f"{months} {unit_label} ago" if delta_days < 0 else f"in {months} {unit_label}"
    years = max(1, int(round(absolute_days / 365)))
    unit_label = "year" if years == 1 else "years"
    return f"{years} {unit_label} ago" if delta_days < 0 else f"in {years} {unit_label}"


def _claim_summary_from_items(items: list[dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {
        "pending": 0,
        "paid": 0,
        "partial": 0,
        "denied": 0,
        "queued": 0,
        "transmitted": 0,
        "total": len(items),
        "recent": [],
    }
    for claim in items:
        status = str(claim.get("status", "pending")).strip().lower()
        transmission_status = str(claim.get("transmission_status", "queued")).strip().lower()
        summary[status] = int(summary.get(status, 0) or 0) + 1
        summary[transmission_status] = int(summary.get(transmission_status, 0) or 0) + 1
    summary["recent"] = sorted(
        items,
        key=lambda item: str(item.get("updated_at", "") or item.get("created_at", "") or ""),
        reverse=True,
    )[:8]
    return summary


def _authorization_span_weeks(start_date: date, end_date: date) -> float:
    inclusive_days = (end_date - start_date).days + 1
    if inclusive_days == 0:
        return -1 / 7
    return inclusive_days / 7


def _authorization_hours_per_week(units: object, start_date: date, end_date: date) -> float:
    weeks = _authorization_span_weeks(start_date, end_date)
    if abs(weeks) < 1e-9:
        return 0.0
    return (float(units or 0) / 4.0) / weeks


def _authorization_usage_fill_class(ratio: float, mode: str) -> str:
    clean_ratio = max(0.0, min(float(ratio or 0), 1.0))
    if clean_ratio <= 0:
        return "empty"
    if mode == "remaining":
        return "good"
    if clean_ratio < 0.5:
        return "good"
    if clean_ratio < 0.85:
        return "warn"
    return "bad"


def _authorization_usage_bar(label: str, ratio: float, mode: str) -> str:
    clean_ratio = max(0.0, min(float(ratio or 0), 1.0))
    fill_class = _authorization_usage_fill_class(clean_ratio, mode)
    return (
        f'<div class="authorization-usage-track {mode}-track">'
        f'<span class="authorization-usage-fill {fill_class}" style="width:{clean_ratio * 100:.2f}%"></span>'
        f'<span class="authorization-usage-label">{html.escape(label)}</span>'
        "</div>"
    )


def _render_authorization_usage_cards(items: list[dict[str, object]]) -> str:
    if not items:
        return ""

    grouped: dict[str, dict[str, object]] = {}
    for item in items:
        group_id = str(item.get("authorization_group_id", "") or item.get("authorization_id", "") or item.get("authorization_number", "")).strip()
        if not group_id:
            continue
        group = grouped.setdefault(
            group_id,
            {
                "authorization_number": str(item.get("authorization_number", "")),
                "patient_name": str(item.get("patient_name", "")),
                "payer_name": str(item.get("payer_name", "")),
                "start_date": str(item.get("start_date", "")),
                "end_date": str(item.get("end_date", "")),
                "lines": [],
            },
        )
        lines = group.setdefault("lines", [])
        if isinstance(lines, list):
            lines.append(item)

    if not grouped:
        return ""

    def _group_sort_key(group: dict[str, object]) -> tuple[datetime, str]:
        try:
            parsed_end = datetime.combine(parse_user_date(str(group.get("end_date", ""))), datetime.min.time())
        except ValueError:
            parsed_end = datetime.min
        return (parsed_end, str(group.get("authorization_number", "")))

    cards = []
    today = datetime.now().date()
    for index, group in enumerate(sorted(grouped.values(), key=_group_sort_key, reverse=True), start=1):
        start_raw = str(group.get("start_date", ""))
        end_raw = str(group.get("end_date", ""))
        try:
            start_date = parse_user_date(start_raw)
            end_date = parse_user_date(end_raw)
        except ValueError:
            start_date = today
            end_date = today
        estimated_rows = []
        remaining_rows = []
        lines = group.get("lines", [])
        if not isinstance(lines, list):
            lines = []
        for line in sorted(lines, key=lambda item: int(float(item.get("authorization_line_number", 0) or 0))):
            total_units = max(float(line.get("total_units", 0) or 0), 0.0)
            remaining_units = max(float(line.get("remaining_units", 0) or 0), 0.0)
            used_units = max(total_units - remaining_units, 0.0)
            used_ratio = used_units / total_units if total_units else 0.0
            remaining_ratio = remaining_units / total_units if total_units else 0.0
            cpt_code = str(line.get("cpt_code", "")).strip().upper()
            cpt_label = f"CPT-{cpt_code}" if cpt_code else "CPT"
            estimated_rate = _authorization_hours_per_week(total_units, start_date, end_date)
            remaining_rate = _authorization_hours_per_week(remaining_units, today, end_date)
            estimated_rows.append(
                _authorization_usage_bar(
                    f"{cpt_label}: {_compact_number(used_units)}/{_compact_number(total_units)} ({_compact_number(estimated_rate)} hours/week)",
                    used_ratio,
                    "estimated",
                )
            )
            remaining_rows.append(
                _authorization_usage_bar(
                    f"{cpt_label}: {_compact_number(remaining_units)}/{_compact_number(total_units)} ({_compact_number(remaining_rate)} hours/week)",
                    remaining_ratio,
                    "remaining",
                )
            )

        patient_name = str(group.get("patient_name", "")).strip()
        payer_name = str(group.get("payer_name", "")).strip()
        subtitle_parts = [part for part in (patient_name, payer_name) if part]
        subtitle = " | ".join(subtitle_parts)
        estimated_markup = "".join(estimated_rows) if estimated_rows else '<p class="helper-note">No hay lineas CPT para mostrar.</p>'
        remaining_markup = "".join(remaining_rows) if remaining_rows else '<p class="helper-note">No hay lineas CPT para mostrar.</p>'
        cards.append(
            '<article class="authorization-usage-card">'
            f'<div class="authorization-usage-head"><strong>{index}: {html.escape(str(group.get("authorization_number", "")) or "Sin numero")}</strong>'
            f'<span>{html.escape(subtitle or "Autorizacion activa")}</span></div>'
            f'<div class="authorization-usage-dates"><span>Start date: {html.escape(_authorization_pretty_date(start_raw))} ({html.escape(_authorization_relative_date(start_raw))})</span>'
            f'<span>End date: {html.escape(_authorization_pretty_date(end_raw))} ({html.escape(_authorization_relative_date(end_raw))})</span></div>'
            '<div class="authorization-usage-section"><strong>Estimated usage:</strong>'
            f"{estimated_markup}"
            "</div>"
            '<div class="authorization-usage-section"><strong>Remaining usage:</strong>'
            f"{remaining_markup}"
            "</div>"
            "</article>"
        )
    return '<div class="authorization-usage-grid">' + "".join(cards) + "</div>"


def _render_authorization_rows(
    items: list[dict[str, object]],
    *,
    client: dict[str, object] | None = None,
    can_manage: bool = False,
) -> str:
    if not items:
        colspan = "10" if client is not None and can_manage else "9"
        return f'<tr><td colspan="{colspan}">Todavia no hay autorizaciones registradas.</td></tr>'

    rows = []
    for item in items:
        action_markup = ""
        if client is not None and can_manage:
            group_id = str(item.get("authorization_group_id", "")).strip() or str(item.get("authorization_id", "")).strip()
            client_id = str(client.get("client_id", "")).strip()
            edit_href = (
                f'{_page_href("clients")}?open_client_id={quote(client_id)}&edit_authorization_group_id={quote(group_id)}#client-authorizations'
                if group_id and client_id
                else ""
            )
            action_markup = (
                '<div class="quick-links">'
                + (f'<a class="quick-link" href="{html.escape(edit_href)}">Editar</a>' if edit_href else "")
                + (
                    '<form class="table-action-form" method="post" action="/delete-authorization-group">'
                    f'<input type="hidden" name="client_id" value="{html.escape(client_id)}">'
                    f'<input type="hidden" name="authorization_group_id" value="{html.escape(group_id)}">'
                    '<button class="small-button" type="submit">Borrar</button>'
                    '</form>'
                    if group_id and client_id
                    else ""
                )
                + '</div>'
            )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('authorization_number', '')))}</td>"
            f"<td>{html.escape(str(item.get('authorization_line_number', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('patient_name', '')))}</td>"
            f"<td>{html.escape(_cpt_table_label(str(item.get('cpt_code', ''))))}</td>"
            f"<td>{html.escape(str(item.get('start_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('end_date', '')))}</td>"
            f"<td>{float(item.get('total_units', 0)):.0f}</td>"
            f"<td>{float(item.get('remaining_units', 0)):.0f}</td>"
            f"<td>{html.escape(str(item.get('status_label', '')))}</td>"
            + (f"<td>{action_markup}</td>" if client is not None and can_manage else "")
            + "</tr>"
        )
    return "".join(rows)


def _render_roster_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="7">Todavia no hay pacientes en la lista automatica.</td></tr>'

    rows = []
    for item in items:
        patient_name = f"{item.get('patient_first_name', '')} {item.get('patient_last_name', '')}".strip()
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(patient_name))}</td>"
            f"<td>{html.escape(str(item.get('member_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('last_result', 'pendiente')))}</td>"
            f"<td>{html.escape(str(item.get('last_checked_at', '')))}</td>"
            f"<td>{html.escape(str(item.get('next_run_date', '')))}</td>"
            f"<td>{'Activa' if item.get('active', True) else 'Inactiva'}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_client_rows(
    items: list[dict[str, object]],
    *,
    can_edit_client: bool = False,
    can_manage_authorizations: bool = False,
    can_run_eligibility: bool = False,
) -> str:
    if not items:
        return '<tr><td colspan="13">Todavia no hay clientes guardados.</td></tr>'

    rows = []
    for item in items:
        patient_name = f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
        delivered_documents = int(item.get("delivered_documents", 0) or 0)
        total_documents = int(item.get("total_documents", 0) or 0)
        client_id = str(item.get("client_id", "")).strip()
        search_text = " ".join(
            [
                patient_name,
                str(item.get("member_id", "")),
                str(item.get("payer_name", "")),
                str(item.get("site_location", "")),
                str(item.get("county_name", "")),
                str(item.get("last_eligibility_result", "")),
                str(item.get("bcba_provider_name", "")),
                str(item.get("bcaba_provider_name", "")),
                str(item.get("rbt_provider_name", "")),
            ]
        ).lower()
        action_markup = (
            f"<a class=\"small-button\" href=\"{html.escape(_client_expediente_href(item))}\">Abrir cliente</a>"
            + (
                f"<a class=\"small-button\" href=\"{html.escape(_page_href('clients'))}?edit_client_id={quote(client_id)}#clientsdb\">Editar expediente</a>"
                if can_edit_client and client_id
                else ""
            )
            + (
                "<form class=\"table-action-form\" method=\"post\" action=\"/check-client-eligibility\">"
                f"<input type=\"hidden\" name=\"client_id\" value=\"{html.escape(client_id)}\">"
                "<button class=\"small-button\" type=\"submit\">Elegibilidad</button>"
                "</form>"
                if can_run_eligibility and client_id
                else ""
            )
            + (
                "<form class=\"table-action-form\" method=\"post\" action=\"/add-eligibility-to-roster\">"
                f"<input type=\"hidden\" name=\"member_id\" value=\"{html.escape(str(item.get('member_id', '')))}\">"
                f"<input type=\"hidden\" name=\"patient_first_name\" value=\"{html.escape(str(item.get('first_name', '')))}\">"
                f"<input type=\"hidden\" name=\"patient_last_name\" value=\"{html.escape(str(item.get('last_name', '')))}\">"
                f"<input type=\"hidden\" name=\"patient_birth_date\" value=\"{html.escape(str(item.get('birth_date', '')))}\">"
                f"<input type=\"hidden\" name=\"service_date\" value=\"{html.escape(str(item.get('service_date', '')))}\">"
                f"<input type=\"hidden\" name=\"payer_id\" value=\"{html.escape(str(item.get('payer_id', '')))}\">"
                f"<input type=\"hidden\" name=\"provider_npi\" value=\"{html.escape(str(item.get('provider_npi', '')))}\">"
                "<button class=\"small-button\" type=\"submit\">Roster</button>"
                "</form>"
                if can_run_eligibility
                else ""
            )
            + (
                f"<a class=\"small-button\" href=\"{html.escape(_client_expediente_href(item, 'client-authorizations'))}\">Autorizacion</a>"
                if can_manage_authorizations
                else ""
            )
        )
        rows.append(
            f"<tr data-directory-row=\"clients\" data-status=\"{html.escape(_client_directory_status(item))}\" data-search=\"{html.escape(search_text)}\">"
            f"<td>{html.escape(str(patient_name))}</td>"
            f"<td>{html.escape(str(item.get('member_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('provider_npi', '')))}</td>"
            f"<td>{html.escape(str(item.get('site_location', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('county_name', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('medicaid_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('service_date', '')))}</td>"
            f"<td>{delivered_documents}/{total_documents}</td>"
            f"<td>{html.escape(str(item.get('last_eligibility_result', 'pendiente')))}</td>"
            f"<td>{html.escape(str(item.get('last_eligibility_checked_at', '')))}</td>"
            f"<td>{'Si' if item.get('auto_eligibility', True) else 'No'}</td>"
            f"<td>{action_markup}</td>"
            "</tr>"
        )
    return "".join(rows)


def _client_authorization_items(client: dict[str, object], authorizations: list[dict[str, object]]) -> list[dict[str, object]]:
    client_id = str(client.get("client_id", "")).strip()
    member_id = str(client.get("member_id", "")).strip()
    return [
        item
        for item in authorizations
        if (
            client_id
            and str(item.get("client_id", "")).strip() == client_id
        )
        or (
            member_id
            and str(item.get("patient_member_id", "")).strip() == member_id
        )
    ]


def _group_client_authorizations(items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for item in items:
        group_id = str(item.get("authorization_group_id", "") or item.get("authorization_id", "") or item.get("authorization_number", "")).strip()
        if not group_id:
            continue
        group = grouped.setdefault(
            group_id,
            {
                "authorization_number": str(item.get("authorization_number", "")),
                "payer_name": str(item.get("payer_name", "")),
                "start_date": str(item.get("start_date", "")),
                "end_date": str(item.get("end_date", "")),
                "lines": [],
            },
        )
        lines = group.setdefault("lines", [])
        if isinstance(lines, list):
            lines.append(item)

    def _sort_key(group: dict[str, object]) -> tuple[datetime, str]:
        try:
            parsed_end = datetime.combine(parse_user_date(str(group.get("end_date", ""))), datetime.min.time())
        except ValueError:
            parsed_end = datetime.min
        return (parsed_end, str(group.get("authorization_number", "")))

    return sorted(grouped.values(), key=_sort_key, reverse=True)


def _authorization_session_stats(
    client: dict[str, object],
    sessions: list[dict[str, object]],
) -> dict[str, object]:
    client_id = str(client.get("client_id", "")).strip()
    related_sessions = [
        item
        for item in sessions
        if str(item.get("client_id", "")).strip() == client_id
    ]
    assigned_provider_names = sorted(
        {
            *{
                str(name).strip()
                for name in client.get("care_team_names", [])
                if str(name).strip()
            },
            *{
                str(item.get("provider_name", "")).strip()
                for item in related_sessions
                if str(item.get("provider_name", "")).strip()
            },
        }
    )
    reserved_units = sum(int(float(item.get("authorization_reserved_units", 0) or 0)) for item in related_sessions)
    consumed_units = sum(int(float(item.get("authorization_consumed_units", 0) or 0)) for item in related_sessions)
    linked_sessions = [
        item
        for item in related_sessions
        if str(item.get("authorization_number", "")).strip() or str(item.get("authorization_id", "")).strip()
    ]
    return {
        "session_count": len(related_sessions),
        "linked_session_count": len(linked_sessions),
        "reserved_units": reserved_units,
        "consumed_units": consumed_units,
        "assigned_provider_names": assigned_provider_names,
    }


def _authorization_session_summary_markup(
    client: dict[str, object],
    sessions: list[dict[str, object]],
) -> str:
    stats = _authorization_session_stats(client, sessions)
    client_name = f"{client.get('first_name', '')} {client.get('last_name', '')}".strip() or "Sin nombre"
    provider_label = ", ".join(stats["assigned_provider_names"][:3]) if stats["assigned_provider_names"] else "Sin providers asignados"
    if len(stats["assigned_provider_names"]) > 3:
        provider_label += f" +{len(stats['assigned_provider_names']) - 3}"
    return (
        '<article class="panel section-card" data-skip-auto-collapsible="1">'
        "<h2>Autorizacion vinculada al calendario del cliente</h2>"
        "<p>La autorizacion vive solo dentro de este cliente. El calendario del cliente usa esta misma autorizacion para los appointments del equipo asignado, y desde ahi se reservan y consumen units.</p>"
        '<div class="mini-table">'
        f'<div class="mini-row"><strong>Cliente</strong><span>{html.escape(client_name)}</span></div>'
        f'<div class="mini-row"><strong>Sesiones del caso</strong><span>{int(stats["session_count"])}</span></div>'
        f'<div class="mini-row"><strong>Sesiones con auth ligada</strong><span>{int(stats["linked_session_count"])}</span></div>'
        f'<div class="mini-row"><strong>Units reservadas por appointments</strong><span>{int(stats["reserved_units"])}</span></div>'
        f'<div class="mini-row"><strong>Units consumidas al cerrar</strong><span>{int(stats["consumed_units"])}</span></div>'
        f'<div class="mini-row"><strong>Providers asignados al cliente</strong><span>{html.escape(provider_label)}</span></div>'
        "</div>"
        '<p class="helper-note compact-note">El scheduler, la nota ABA, el service log, billing y claims leen esta misma autorizacion del cliente. Cuando el appointment se cierra, la sesion termina de consumir units y deja rastro para el claim.</p>'
        "</article>"
    )


def _client_authorization_audit_items(
    client: dict[str, object],
    audit_logs: list[dict[str, object]],
) -> list[dict[str, object]]:
    client_id = str(client.get("client_id", "")).strip()
    member_id = str(client.get("member_id", "")).strip()
    return [
        item
        for item in audit_logs
        if str(item.get("category", "")).strip().lower() == "authorization"
        and (
            str(item.get("entity_id", "")).strip() == client_id
            or (member_id and member_id in str(item.get("details", "")))
        )
    ][:12]


def _client_expediente_href(item: dict[str, object], anchor: str = "client-profile") -> str:
    client_id = quote(str(item.get("client_id", "")).strip())
    return f"/clients?open_client_id={client_id}#{anchor}"


def _client_care_team_summary_markup(item: dict[str, object]) -> str:
    rows = []
    for role_key, role_label in (("bcba", "BCBA"), ("bcaba", "BCaBA"), ("rbt", "RBT")):
        provider_name = str(item.get(f"{role_key}_provider_name", "")).strip()
        provider_npi = str(item.get(f"{role_key}_provider_npi", "")).strip()
        description = provider_name or "Sin asignar"
        if provider_name and provider_npi:
            description += f" | NPI {provider_npi}"
        rows.append(
            '<div class="directory-detail-row">'
            f"<strong>{html.escape(role_label)}</strong>"
            f"<span>{html.escape(description)}</span>"
            "</div>"
        )
    return (
        '<div class="directory-card-detail">'
        '<div class="directory-detail-title">Equipo ABA</div>'
        f"{''.join(rows)}"
        "</div>"
    )


def _client_authorization_summary_markup(client: dict[str, object], authorizations: list[dict[str, object]]) -> str:
    grouped = _group_client_authorizations(_client_authorization_items(client, authorizations))
    if not grouped:
        return (
            '<div class="directory-card-detail">'
            '<div class="directory-detail-title">Autorizaciones</div>'
            '<p class="helper-note compact-note">Todavia no hay autorizaciones guardadas para este cliente.</p>'
            "</div>"
        )

    active_groups = sum(
        1
        for group in grouped
        if any(
            bool(line.get("active", True)) and float(line.get("remaining_units", 0) or 0) > 0
            for line in (group.get("lines", []) if isinstance(group.get("lines", []), list) else [])
        )
    )
    progress_lines: list[dict[str, object]] = []
    all_lines: list[dict[str, object]] = []
    for group in grouped:
        lines = group.get("lines", [])
        if not isinstance(lines, list):
            continue
        for line in lines:
            all_lines.append(line)
            if bool(line.get("active", True)) or float(line.get("remaining_units", 0) or 0) > 0:
                progress_lines.append(line)
    if not progress_lines:
        progress_lines = all_lines

    total_assigned_units = 0.0
    total_remaining_units = 0.0
    for line in progress_lines:
        try:
            total_assigned_units += max(float(line.get("total_units", 0) or 0), 0.0)
        except (TypeError, ValueError):
            pass
        try:
            total_remaining_units += max(float(line.get("remaining_units", 0) or 0), 0.0)
        except (TypeError, ValueError):
            pass
    total_used_units = max(total_assigned_units - total_remaining_units, 0.0)
    used_ratio = (total_used_units / total_assigned_units) if total_assigned_units else 0.0
    remaining_ratio = (total_remaining_units / total_assigned_units) if total_assigned_units else 0.0
    aggregate_markup = (
        '<div class="client-auth-summary">'
        '<div class="client-auth-totals">'
        f'<div class="mini-row"><strong>Units asignadas</strong><span>{html.escape(_compact_number(total_assigned_units))}</span></div>'
        f'<div class="mini-row"><strong>Usadas</strong><span>{html.escape(_compact_number(total_used_units))}</span></div>'
        f'<div class="mini-row"><strong>Restantes</strong><span>{html.escape(_compact_number(total_remaining_units))}</span></div>'
        "</div>"
        '<div class="client-auth-bars">'
        f'{_authorization_usage_bar(f"Usado: {_compact_number(total_used_units)}/{_compact_number(total_assigned_units)} units", used_ratio, "estimated")}'
        f'{_authorization_usage_bar(f"Restante: {_compact_number(total_remaining_units)}/{_compact_number(total_assigned_units)} units", remaining_ratio, "remaining")}'
        "</div>"
        "</div>"
    )
    rows = []
    for group in grouped[:2]:
        lines = group.get("lines", [])
        if not isinstance(lines, list):
            lines = []
        cpt_codes = sorted(
            {
                _normalize_cpt_code(str(line.get("cpt_code", "")))
                for line in lines
                if _normalize_cpt_code(str(line.get("cpt_code", "")))
            }
        )
        is_active = any(bool(line.get("active", True)) and float(line.get("remaining_units", 0) or 0) > 0 for line in lines)
        cpt_label = ", ".join(cpt_codes) if cpt_codes else "Sin CPT"
        end_label = str(group.get("end_date", "")).strip()
        if end_label:
            end_label = _authorization_pretty_date(end_label)
        else:
            end_label = "Sin fecha final"
        rows.append(
            '<div class="directory-detail-row">'
            f"<strong>{html.escape(str(group.get('authorization_number', '')) or 'Sin numero')}</strong>"
            f"<span>{html.escape(('Activa' if is_active else 'Cerrada') + ' | ' + cpt_label + ' | vence ' + end_label)}</span>"
            "</div>"
        )
    overflow_label = ""
    if len(grouped) > 2:
        overflow_label = f'<p class="helper-note compact-note">+{len(grouped) - 2} autorizacion(es) adicionales.</p>'
    return (
        '<div class="directory-card-detail">'
        f'<div class="directory-detail-title">Autorizaciones ({active_groups} activas)</div>'
        f"{aggregate_markup}"
        f"{''.join(rows)}"
        f"{overflow_label}"
        "</div>"
    )


def _progress_fill_style(percent: object) -> str:
    try:
        clean_percent = int(round(float(percent or 0)))
    except (TypeError, ValueError):
        clean_percent = 0
    clean_percent = max(0, min(clean_percent, 100))
    start_hue = int(round(2 + (clean_percent * 1.18)))
    end_hue = min(start_hue + 14, 132)
    return (
        f"width:{clean_percent}%;"
        f"background:linear-gradient(90deg, hsl({start_hue} 80% 56%) 0%, hsl({end_hue} 72% 42%) 100%);"
    )


def _directory_snapshot_row(title: str, summary: str, *, percent: object | None = None, note: str = "") -> str:
    progress_markup = ""
    if percent is not None:
        progress_markup = (
            '<div class="directory-snapshot-progress">'
            f'<span style="{_progress_fill_style(percent)}"></span>'
            "</div>"
        )
    note_markup = f'<small class="directory-snapshot-note">{html.escape(note)}</small>' if note else ""
    return (
        '<div class="directory-snapshot-row">'
        '<div class="directory-snapshot-head">'
        f"<strong>{html.escape(title)}</strong>"
        f"<span>{html.escape(summary)}</span>"
        "</div>"
        f"{progress_markup}"
        f"{note_markup}"
        "</div>"
    )


def _client_directory_snapshot_markup(item: dict[str, object], authorizations: list[dict[str, object]]) -> str:
    delivered_documents = int(item.get("delivered_documents", 0) or 0)
    total_documents = int(item.get("total_documents", 0) or 0)
    document_percent = int(round((delivered_documents / total_documents) * 100)) if total_documents else 0
    active_percent = 100 if item.get("active", True) else 20
    coverage_status = str(item.get("last_eligibility_result", "")).strip().title() or "Pendiente"
    coverage_percent_map = {
        "Active": 100,
        "Covered": 100,
        "Pending": 40,
        "Pendiente": 40,
        "Inactive": 15,
        "Denied": 12,
        "Not Found": 10,
    }
    coverage_percent = coverage_percent_map.get(coverage_status, 28)
    plan_name = str(item.get("last_plan_name", "")).strip() or str(item.get("payer_name", "")).strip() or "Sin plan"
    grouped_authorizations = _group_client_authorizations(_client_authorization_items(item, authorizations))
    active_authorizations = sum(
        1
        for group in grouped_authorizations
        if any(
            bool(line.get("active", True)) and float(line.get("remaining_units", 0) or 0) > 0
            for line in (group.get("lines", []) if isinstance(group.get("lines", []), list) else [])
        )
    )
    team_names = [
        str(item.get("bcba_provider_name", "")).strip(),
        str(item.get("bcaba_provider_name", "")).strip(),
        str(item.get("rbt_provider_name", "")).strip(),
    ]
    clean_team_names = [value for value in team_names if value]
    team_summary = ", ".join(clean_team_names) if clean_team_names else "Sin equipo ABA asignado"
    return (
        '<div class="directory-card-snapshot">'
        + _directory_snapshot_row(
            "Expediente",
            f'{"Activo" if item.get("active", True) else "Inactivo"} | {active_percent}%',
            percent=active_percent,
            note=str(item.get("member_id", "")).strip() or "Sin Member ID",
        )
        + _directory_snapshot_row(
            "Checklist",
            f"Checklist {delivered_documents}/{total_documents} | {document_percent}% completo",
            percent=document_percent,
        )
        + _directory_snapshot_row(
            "Cobertura",
            f"{coverage_status} | {plan_name}",
            percent=coverage_percent,
            note=f"{active_authorizations} autorizacion(es) activa(s)",
        )
        + _directory_snapshot_row(
            "Equipo ABA",
            team_summary,
            note=str(item.get("payer_name", "")).strip() or "Sin payer asignado",
        )
        + "</div>"
    )


def _client_related_claims(client: dict[str, object], claims: list[dict[str, object]]) -> list[dict[str, object]]:
    member_id = str(client.get("member_id", "")).strip().lower()
    client_name = f"{client.get('first_name', '')} {client.get('last_name', '')}".strip().lower()
    matches: list[dict[str, object]] = []
    for item in claims:
        claim_member_id = str(item.get("member_id", "") or item.get("policy_number", "")).strip().lower()
        claim_name = str(item.get("patient_name", "")).strip().lower()
        if member_id and claim_member_id == member_id:
            matches.append(item)
            continue
        if client_name and claim_name == client_name:
            matches.append(item)
    return matches


def _render_client_case_session_rows(items: list[dict[str, object]], *, note_focus: bool = False) -> str:
    if not items:
        message = "No hay sesiones ligadas a este caso todavia."
        if note_focus:
            message = "No hay notas pendientes dentro de este caso."
        return f'<tr><td colspan="6">{html.escape(message)}</td></tr>'
    rows = []
    for item in items[:6]:
        session_id = quote(str(item.get("session_id", "")))
        client_id = quote(str(item.get("client_id", "")))
        authorization_number = str(item.get("authorization_number", "")).strip() or "Sin auth"
        cpt_code = str(item.get("cpt_code", "")).strip() or "-"
        caregiver_signature_status = "Signed" if bool(item.get("caregiver_signature_present")) else "Pending"
        note_label = str(item.get("note_status", "")).strip() or "Draft"
        actions = [
            f'<a class="quick-link" href="{_page_href("aba_notes")}?appointment_id={session_id}#session-ops-detail">Workspace</a>'
        ]
        actions.append(
            f'<a class="quick-link" href="{_page_href("clients")}?open_client_id={client_id}#client-authorizations">Auth</a>'
        )
        if note_focus and str(item.get("service_log_id", "")).strip():
            actions.append(
                f'<a class="quick-link" href="{_page_href("aba_notes")}?log_id={quote(str(item.get("service_log_id", "")))}#aba-note-preview">Nota</a>'
            )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('service_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('provider_name', '')) or '-')}</td>"
            f"<td><strong>{html.escape(str(item.get('event_type', '')) or '-')}</strong><br><small>{html.escape(cpt_code)} | {html.escape(authorization_number)}</small></td>"
            f"<td>{_session_status_badge(item.get('session_status', 'Scheduled'))}<br><small>Caregiver: {html.escape(caregiver_signature_status)}</small></td>"
            f"<td>{_session_status_badge(note_label if note_focus else str(item.get('billing_queue_status', '')).replace('_', ' ').title())}<br><small>Note: {html.escape(note_label)}</small></td>"
            f"<td><div class=\"quick-links\">{''.join(actions)}</div></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_provider_shared_session_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="9">Todavia no hay sesiones compartidas para este provider.</td></tr>'
    rows = []
    for item in items[:8]:
        session_id = quote(str(item.get("session_id", "")))
        client_id = quote(str(item.get("client_id", "")))
        service_log_id = quote(str(item.get("service_log_id", "")))
        claim_id = quote(str(item.get("claim_id", "")))
        authorization_number = str(item.get("authorization_number", "")).strip() or "Sin auth"
        caregiver_signature_status = "Signed" if bool(item.get("caregiver_signature_present")) else "Pending"
        note_status = str(item.get("note_status", "")).strip() or "Draft"
        billing_status = str(item.get("billing_queue_status", "")).strip().replace("_", " ").title() or "Not Ready"
        claim_status = str(item.get("claim_status", "")).strip() or "Draft"
        cpt_code = str(item.get("cpt_code", "")).strip() or "-"
        actions = [
            f'<a class="quick-link" href="{_page_href("aba_notes")}?appointment_id={session_id}#session-ops-detail">Workspace</a>',
            f'<a class="quick-link" href="{_page_href("clients")}?open_client_id={client_id}#client-profile">Cliente</a>',
            f'<a class="quick-link" href="{_page_href("clients")}?open_client_id={client_id}#client-authorizations">Auth</a>',
        ]
        if str(item.get("service_log_id", "")).strip():
            actions.append(
                f'<a class="quick-link" href="{_page_href("aba_notes")}?log_id={service_log_id}#aba-note-preview">Service log</a>'
            )
        if str(item.get("claim_id", "")).strip():
            actions.append(f'<a class="quick-link" href="/claim-edi?claim_id={claim_id}">837</a>')
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('service_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('client_name', '')) or '-')}</td>"
            f"<td><strong>{html.escape(cpt_code)}</strong><br><small>{html.escape(authorization_number)}</small></td>"
            f"<td>{_session_status_badge(caregiver_signature_status)}</td>"
            f"<td>{_session_status_badge(item.get('session_status', 'Scheduled'))}</td>"
            f"<td>{_session_status_badge(note_status)}</td>"
            f"<td>{_session_status_badge(billing_status)}</td>"
            f"<td>{_session_status_badge(claim_status)}</td>"
            f"<td><div class=\"quick-links\">{''.join(actions)}</div></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_client_case_claim_rows(
    items: list[dict[str, object]],
    *,
    include_totals: bool = True,
    include_paid: bool = True,
    include_actions: bool = True,
) -> str:
    if not items:
        return '<tr><td colspan="6">Todavia no hay claims ligados a este cliente.</td></tr>'
    rows = []
    for item in items[:6]:
        claim_id = str(item.get("claim_id", "")).strip()
        claim_id_href = quote(claim_id)
        billed_markup = f"${float(item.get('billed_amount', 0) or 0):,.2f}" if include_totals else "Restricted"
        paid_markup = f"${float(item.get('paid_amount', 0) or 0):,.2f}" if include_paid else "Restricted"
        action_markup = (
            f"<td><div class=\"quick-links\"><a class=\"quick-link\" href=\"/cms1500?claim_id={claim_id_href}\">CMS-1500</a><a class=\"quick-link\" href=\"/claim-edi?claim_id={claim_id_href}\">837</a></div></td>"
            if include_actions
            else "<td><span class=\"helper-note compact-note\">Claims center only</span></td>"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(claim_id or '-')}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')) or '-')}</td>"
            f"<td>{_session_status_badge(str(item.get('status', '')).title() or 'Draft')}</td>"
            f"<td>{html.escape(billed_markup)}</td>"
            f"<td>{html.escape(paid_markup)}</td>"
            + action_markup
            + "</tr>"
        )
    return "".join(rows)


def _render_client_case_document_rows(client: dict[str, object]) -> str:
    documents = client.get("documents", [])
    if not isinstance(documents, list) or not documents:
        return '<tr><td colspan="4">Todavia no hay documentos cargados para este cliente.</td></tr>'
    rows = []
    for item in documents[:8]:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('document_name', '')) or '-')}</td>"
            f"<td>{_session_status_badge(str(item.get('status', '') or item.get('requested_status', '') or 'Missing').title())}</td>"
            f"<td>{html.escape(str(item.get('expiration_date', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('file_name', '')) or 'Pendiente')}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_client_workspace_panel(
    selected_client: dict[str, object] | None,
    authorizations: list[dict[str, object]],
    sessions: list[dict[str, object]],
    claims: list[dict[str, object]],
    *,
    include_claim_totals: bool = True,
    include_claim_paid: bool = True,
    include_claim_actions: bool = True,
    can_edit_client: bool = False,
    can_manage_authorizations: bool = False,
    can_run_eligibility: bool = False,
) -> str:
    if selected_client is None:
        return (
            '<article id="client-profile" class="panel section-card client-focus-panel" data-skip-auto-collapsible="1">'
            '<h2>Client Workflow Center</h2>'
            '<p>Select a client from the roster to open the main workflow for sessions, notes, billing, documents, team, and admin in one workspace.</p>'
            '<div class="mini-table">'
            '<div class="mini-row"><strong>Selection</strong><span>Click a client card to open the client workflow.</span></div>'
            '<div class="mini-row"><strong>Workflow</strong><span>This workspace is the starting point for the full process: session, note, validation, claim, and payment.</span></div>'
            '<div class="mini-row"><strong>Workspace</strong><span>Inside the client workspace, show: overview, schedule, notes, billing, documents, team, and admin.</span></div>'
            '<div class="mini-row"><strong>Session-first flow</strong><span>All operational work starts from the session and moves through documentation, validation, billing, and payment.</span></div>'
            "</div>"
            "</article>"
        )

    expediente_href = _client_expediente_href(selected_client)
    auth_href = _client_expediente_href(selected_client, "client-authorizations")
    client_name = f"{selected_client.get('first_name', '')} {selected_client.get('last_name', '')}".strip() or "Cliente"
    coverage_status = str(selected_client.get("last_eligibility_result", "")).strip().title() or "Pendiente"
    plan_name = str(selected_client.get("last_plan_name", "")).strip() or "Pendiente"
    subscriber_id = str(selected_client.get("last_subscriber_id", "")).strip() or str(selected_client.get("member_id", "")).strip() or "Pendiente"
    delivered_documents = int(selected_client.get("delivered_documents", 0) or 0)
    total_documents = int(selected_client.get("total_documents", 0) or 0)
    diagnosis = str(selected_client.get("diagnosis", "")).strip() or "Pendiente"
    caregiver_name = str(selected_client.get("caregiver_name", "")).strip() or "Pendiente"
    physician_name = str(selected_client.get("physician_name", "")).strip() or "Pendiente"
    address_line = ", ".join(
        value
        for value in (
            str(selected_client.get("address_line1", "")).strip(),
            str(selected_client.get("address_city", "")).strip(),
            str(selected_client.get("address_state", "")).strip(),
            str(selected_client.get("address_zip_code", "")).strip(),
        )
        if value
    ) or "Pendiente"
    client_id = str(selected_client.get("client_id", "")).strip()
    client_sessions = [item for item in sessions if str(item.get("client_id", "")).strip() == client_id]
    sorted_schedule = sorted(
        client_sessions,
        key=lambda item: (parse_user_date(str(item.get("service_date", ""))) or date.max, str(item.get("scheduled_start_time", ""))),
    )
    pending_note_sessions = [
        item for item in client_sessions if str(item.get("note_status", "")).strip() in {"Draft", "Submitted", "Under Review", "Rejected"}
    ]
    ready_billing_sessions = [item for item in client_sessions if str(item.get("billing_queue_status", "")).strip().lower() == "ready"]
    client_claims = _client_related_claims(selected_client, claims)
    open_claims = [
        item for item in client_claims if str(item.get("status", "")).strip().lower() not in {"paid", "closed", "resolved"}
    ]
    active_authorizations = _client_authorization_items(selected_client, authorizations)
    case_status = "Active"
    if not bool(selected_client.get("active", True)):
        case_status = "On Hold"
    elif not active_authorizations:
        case_status = "Pending Authorization"
    elif not str(selected_client.get("payer_name", "")).strip() or not str(selected_client.get("member_id", "")).strip():
        case_status = "Missing Info"
    missing_documents = max(total_documents - delivered_documents, 0)
    billing_readiness = int(round((len(ready_billing_sessions) / len(client_sessions)) * 100)) if client_sessions else 0
    latest_note_preview = str((pending_note_sessions[0] if pending_note_sessions else client_sessions[0] if client_sessions else {}).get("session_note", "")).strip()
    if len(latest_note_preview) > 280:
        latest_note_preview = latest_note_preview[:277].rstrip() + "..."
    quick_actions_markup = (
        '<div class="page-actions case-hub-actions">'
        + _page_action_card_markup("New Appointment", "Crear o mover una sesion del caso.", "Calendar / Session", f"{_page_href('aba_notes')}#aba-notes-form")
        + _page_action_card_markup("New Note", "Abrir la nota o service log del cliente.", "Clinical", f"{_page_href('aba_notes')}#aba-service-logs")
        + _page_action_card_markup("Upload Document", "Ir a la tab documental del caso abierto.", "Documents", "#client-case-documents")
        + _page_action_card_markup("View Claim Queue", "Revisar billing queue y claims ligados.", "Billing", f"{_page_href('claims')}#claims-billing-queue")
        + _page_action_card_markup("Add Authorization", "Abrir el tracker de autorizaciones.", "Auth", auth_href)
        + "</div>"
    )
    return (
        '<article id="client-profile" class="panel section-card client-focus-panel" data-skip-auto-collapsible="1">'
        '<div class="case-hub-header">'
        '<div class="page-title-stack">'
        '<span class="eyebrow">Client Workflow Center</span>'
        f"<h2>{html.escape(client_name)}</h2>"
        "<p>This is the main workflow entry point for sessions, notes, validation, billing, claims, documents, team, and admin.</p>"
        "</div>"
        '<div class="profile-pill-row">'
        f'{_session_status_badge(case_status)}'
        f'{_session_status_badge(coverage_status)}'
        f'{_session_status_badge(str(selected_client.get("payer_name", "")) or "Sin payer")}'
        "</div>"
        "</div>"
        '<section class="metric-grid case-hub-metrics">'
        + _metric_card_markup("Upcoming Sessions", len(sorted_schedule), "Sesiones ligadas al caso.", tone="neutral", note=str(sorted_schedule[0].get("service_date", "")) if sorted_schedule else "Sin agenda", href="#client-case-schedule")
        + _metric_card_markup("Pending Notes", len(pending_note_sessions), "Notas que siguen abiertas o necesitan correccion.", tone="warm" if pending_note_sessions else "success", note="Clinical", href="#client-case-notes")
        + _metric_card_markup("Ready for Billing", len(ready_billing_sessions), "Sesiones listas para pasar a billing.", tone="success" if ready_billing_sessions else "neutral", note=f"{billing_readiness}% readiness", href="#client-case-billing")
        + _metric_card_markup("Open Claims", len(open_claims), "Claims que siguen en seguimiento o riesgo.", tone="danger" if open_claims else "success", note=str(len(client_claims)) + " total", href="#client-case-billing")
        + _metric_card_markup("Documents", f"{delivered_documents}/{total_documents}", "Expediente documental del cliente.", tone="warm" if missing_documents else "success", note=f"{missing_documents} missing", href="#client-case-documents")
        + "</section>"
        + quick_actions_markup
        + '<div class="segmented-tabs" data-tab-group="client-case-hub">'
        + '<button class="segment active" type="button" data-tab-target="overview" aria-pressed="true">Overview</button>'
        + '<button class="segment" type="button" data-tab-target="schedule">Schedule</button>'
        + '<button class="segment" type="button" data-tab-target="notes">Notes</button>'
        + '<button class="segment" type="button" data-tab-target="clinical">Clinical</button>'
        + '<button class="segment" type="button" data-tab-target="documents">Documents</button>'
        + '<button class="segment" type="button" data-tab-target="billing">Billing</button>'
        + '<button class="segment" type="button" data-tab-target="team">Team</button>'
        + '<button class="segment" type="button" data-tab-target="admin">Admin</button>'
        + "</div>"
        + '<div class="tab-panels case-hub-panels">'
        + '<section id="client-case-overview" class="tab-panel case-hub-panel" data-tab-panel="overview">'
        + '<div class="case-summary-grid">'
        + '<article class="case-summary-card">'
        + "<h3>Workflow Summary</h3>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Member ID</strong><span>{html.escape(str(selected_client.get("member_id", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>DOB / Age</strong><span>{html.escape(str(selected_client.get("birth_date", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>Diagnosis</strong><span>{html.escape(diagnosis)}</span></div>'
        + f'<div class="mini-row"><strong>Language</strong><span>{html.escape(str(selected_client.get("preferred_language", "")) or "Pendiente")}</span></div>'
        + "</div>"
        + "</article>"
        + '<article class="case-summary-card">'
        + "<h3>Payer & Authorization</h3>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Payer</strong><span>{html.escape(str(selected_client.get("payer_name", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>Plan</strong><span>{html.escape(plan_name)}</span></div>'
        + f'<div class="mini-row"><strong>Subscriber ID</strong><span>{html.escape(subscriber_id)}</span></div>'
        + f'<div class="mini-row"><strong>Autorizaciones</strong><span>{len(active_authorizations)}</span></div>'
        + "</div>"
        + "</article>"
        + '<article class="case-summary-card">'
        + "<h3>Next Sessions</h3>"
        + '<div class="mini-table">'
        + "".join(
            f'<div class="mini-row"><strong>{html.escape(str(item.get("service_date", "")) or "-")}</strong><span>{html.escape(str(item.get("provider_name", "")) or "-")} | {html.escape(str(item.get("cpt_code", "")) or "-")}</span></div>'
            for item in sorted_schedule[:3]
        )
        + ('<div class="mini-row"><strong>Agenda</strong><span>Sin sesiones ligadas al caso.</span></div>' if not sorted_schedule else "")
        + "</div>"
        + "</article>"
        + '<article class="case-summary-card">'
        + "<h3>What Needs Attention</h3>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Pending notes</strong><span>{len(pending_note_sessions)}</span></div>'
        + f'<div class="mini-row"><strong>Open claims</strong><span>{len(open_claims)}</span></div>'
        + f'<div class="mini-row"><strong>Missing docs</strong><span>{missing_documents}</span></div>'
        + f'<div class="mini-row"><strong>Billing readiness</strong><span>{billing_readiness}%</span></div>'
        + "</div>"
        + "</article>"
        + "</div>"
        + '<div class="case-hub-split">'
        + f"{_client_authorization_summary_markup(selected_client, authorizations)}"
        + f"{_client_care_team_summary_markup(selected_client)}"
        + "</div>"
        + "</section>"
        + '<section id="client-case-schedule" class="tab-panel case-hub-panel" data-tab-panel="schedule" hidden>'
        + "<h3>Schedule</h3>"
        + "<p>This is the live calendar the provider also sees. Every appointment reads this client's authorization, reserves and consumes units, captures caregiver signature, and becomes the starting point for note, service log, validation, and claim work.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>DOS</th><th>Provider</th><th>Event</th><th>Session</th><th>Billing</th><th>Action</th></tr></thead><tbody>'
        + _render_client_case_session_rows(sorted_schedule)
        + "</tbody></table></div>"
        + "</section>"
        + '<section id="client-case-notes" class="tab-panel case-hub-panel" data-tab-panel="notes" hidden>'
        + "<h3>Notes</h3>"
        + "<p>Each completed appointment generates the note workflow automatically. The provider writes and signs the note here, the caregiver signature stays tied to the appointment, and the service log reflects that same signed session.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>DOS</th><th>Provider</th><th>Event</th><th>Session</th><th>Note</th><th>Action</th></tr></thead><tbody>'
        + _render_client_case_session_rows(pending_note_sessions, note_focus=True)
        + "</tbody></table></div>"
        + "</section>"
        + '<section id="client-case-clinical" class="tab-panel case-hub-panel" data-tab-panel="clinical" hidden>'
        + "<h3>Clinical</h3>"
        + "<p>Keep the provider workflow simple: session summary, behaviors, interventions, response, caregiver participation, and next steps. The technical document engine stays behind the scenes.</p>"
        + '<div class="case-summary-grid">'
        + '<article class="case-summary-card">'
        + "<h3>Diagnosis & Goals</h3>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Diagnosis</strong><span>{html.escape(diagnosis)}</span></div>'
        + f'<div class="mini-row"><strong>BCBA</strong><span>{html.escape(str(selected_client.get("bcba_provider_name", "")) or "Sin asignar")}</span></div>'
        + f'<div class="mini-row"><strong>Caregiver</strong><span>{html.escape(caregiver_name)}</span></div>'
        + "</div>"
        + "</article>"
        + '<article class="case-summary-card">'
        + "<h3>Latest Note Preview</h3>"
        + f'<p class="module-note">{html.escape(latest_note_preview or "No note has been captured for this workflow yet.")}</p>'
        + "</article>"
        + "</div>"
        + "</section>"
        + '<section id="client-case-documents" class="tab-panel case-hub-panel" data-tab-panel="documents" hidden>'
        + "<h3>Documents</h3>"
        + "<p>Documents should support readiness, compliance, and billing; this is not just a folder of PDFs.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>Documento</th><th>Status</th><th>Expira</th><th>Archivo</th></tr></thead><tbody>'
        + _render_client_case_document_rows(selected_client)
        + "</tbody></table></div>"
        + "</section>"
        + '<section id="client-case-billing" class="tab-panel case-hub-panel" data-tab-panel="billing" hidden>'
        + "<h3>Billing</h3>"
        + "<p>Sessions flow here from the calendar after note and signature work is complete. The system groups standalone claims by client, provider, payer, authorization, and period so they can become a batch 837 for insurance.</p>"
        + '<div class="case-summary-grid">'
        + '<article class="case-summary-card">'
        + "<h3>Billing Readiness</h3>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Ready sessions</strong><span>{len(ready_billing_sessions)}</span></div>'
        + f'<div class="mini-row"><strong>Open claims</strong><span>{len(open_claims)}</span></div>'
        + f'<div class="mini-row"><strong>Authorization ready</strong><span>{len(active_authorizations)}</span></div>'
        + f'<div class="mini-row"><strong>Readiness</strong><span>{billing_readiness}%</span></div>'
        + "</div>"
        + "</article>"
        + '<article class="case-summary-card">'
        + "<h3>Claim Queue</h3>"
        + '<div class="quick-links">'
        + f'<a class="quick-link" href="{_page_href("claims")}#claims-billing-queue">Billing Queue</a>'
        + f'<a class="quick-link" href="{_page_href("claims")}#claims-follow-up">Follow-up</a>'
        + f'<a class="quick-link" href="{html.escape(auth_href)}">Authorization Tracker</a>'
        + "</div>"
        + "</article>"
        + "</div>"
        + '<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Payer</th><th>Status</th><th>Billed</th><th>Paid</th><th>Action</th></tr></thead><tbody>'
        + _render_client_case_claim_rows(
            client_claims,
            include_totals=include_claim_totals,
            include_paid=include_claim_paid,
            include_actions=include_claim_actions,
        )
        + "</tbody></table></div>"
        + "</section>"
        + '<section id="client-case-team" class="tab-panel case-hub-panel" data-tab-panel="team" hidden>'
        + "<h3>Team</h3>"
        + "<p>Keep the operational team visible so everyone knows who treats, supervises, and coordinates the client workflow.</p>"
        + '<div class="case-summary-grid">'
        + f"{_client_care_team_summary_markup(selected_client)}"
        + '<article class="case-summary-card">'
        + "<h3>Caregiver / Physician</h3>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Caregiver</strong><span>{html.escape(caregiver_name)}</span></div>'
        + f'<div class="mini-row"><strong>Relationship</strong><span>{html.escape(str(selected_client.get("caregiver_relationship", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>Phone</strong><span>{html.escape(str(selected_client.get("caregiver_phone", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>Physician</strong><span>{html.escape(physician_name)}</span></div>'
        + "</div>"
        + "</article>"
        + "</div>"
        + "</section>"
        + '<section id="client-case-admin" class="tab-panel case-hub-panel" data-tab-panel="admin" hidden>'
        + "<h3>Admin</h3>"
        + "<p>The core client data stays here so admin updates can happen without leaving the workflow context.</p>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Address</strong><span>{html.escape(address_line)}</span></div>'
        + f'<div class="mini-row"><strong>Insurance effective</strong><span>{html.escape(str(selected_client.get("insurance_effective_date", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>Subscriber</strong><span>{html.escape(str(selected_client.get("subscriber_name", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>Physician phone</strong><span>{html.escape(str(selected_client.get("physician_phone", "")) or "Pendiente")}</span></div>'
        + f'<div class="mini-row"><strong>Edit form</strong><span>Usa el formulario de abajo para cambiar cualquier dato del caso.</span></div>'
        + "</div>"
        + '<div class="directory-card-actions directory-card-actions-left">'
        + (
            f'<a class="small-button" href="{html.escape(_page_href("clients"))}?edit_client_id={quote(client_id)}#clientsdb">Editar datos</a>'
            if can_edit_client
            else ""
        )
        + (
            f'<a class="small-button" href="{html.escape(auth_href)}">Abrir autorizacion</a>'
            if can_manage_authorizations
            else ""
        )
        + (
            '<form class="table-action-form" method="post" action="/check-client-eligibility">'
            + f'<input type="hidden" name="client_id" value="{html.escape(client_id)}">'
            + '<button class="small-button" type="submit">Eligibilidad</button>'
            + "</form>"
            if can_run_eligibility
            else ""
        )
        + (
            '<p class="helper-note compact-note">Esta vista conserva el workflow del cliente, pero las acciones administrativas estan ocultas para tu rango.</p>'
            if not any((can_edit_client, can_manage_authorizations, can_run_eligibility))
            else ""
        )
        + "</div>"
        + "</section>"
        + "</div>"
        "</article>"
    )


def _render_client_form_markup(
    *,
    values: dict[str, str],
    title: str,
    copy: str,
    save_label: str,
    bcba_contracts: list[dict[str, object]],
    bcaba_contracts: list[dict[str, object]],
    rbt_contracts: list[dict[str, object]],
) -> str:
    return (
        '<form id="clientsdb" class="panel section-card" data-skip-auto-collapsible="1" method="post" action="/add-client" enctype="multipart/form-data">'
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(copy)}</p>"
        f'<input type="hidden" name="client_id" value="{_field_value(values, "client_id")}">'
        '<div class="segmented-tabs" data-tab-group="client-form-tabs">'
        '<button class="segment active" type="button" data-tab-target="overview" aria-pressed="true">Overview</button>'
        '<button class="segment" type="button" data-tab-target="caregiver">Caregiver</button>'
        '<button class="segment" type="button" data-tab-target="insurance">Insurance</button>'
        '<button class="segment" type="button" data-tab-target="team">Team</button>'
        '<button class="segment" type="button" data-tab-target="documents">Documents</button>'
        "</div>"
        '<div class="tab-panels">'
        '<section class="tab-panel" data-tab-panel="overview">'
        '<div class="field-grid">'
        '<label class="field"><span>First Name</span><input name="first_name" value="' + _field_value(values, "first_name") + '"></label>'
        '<label class="field"><span>Last Name</span><input name="last_name" value="' + _field_value(values, "last_name") + '"></label>'
        '<label class="field"><span>Fecha nacimiento</span><input name="birth_date" value="' + _field_value(values, "birth_date") + '" placeholder="MM/DD/YYYY"></label>'
        '<label class="field"><span>Gender</span><select name="gender">'
        f'<option value="M"{_selected(values, "gender", "M")}>Masculino</option>'
        f'<option value="F"{_selected(values, "gender", "F")}>Femenino</option>'
        f'<option value="U"{_selected(values, "gender", "U")}>No especificado</option>'
        '</select></label>'
        '<label class="field"><span>Preferred language</span><input name="preferred_language" value="' + _field_value(values, "preferred_language") + '" placeholder="English"></label>'
        '<label class="field"><span>Diagnosis</span><input name="diagnosis" value="' + _field_value(values, "diagnosis") + '" placeholder="F84.0, F90.2"></label>'
        '<label class="field"><span>Member ID</span><input name="member_id" value="' + _field_value(values, "member_id") + '"></label>'
        '<label class="field"><span>Fecha servicio</span><input name="service_date" value="' + _field_value(values, "service_date") + '" placeholder="MM/DD/YYYY"></label>'
        '<label class="field"><span>Address</span><input name="address_line1" value="' + _field_value(values, "address_line1") + '" placeholder="123 Main St"></label>'
        '<label class="field"><span>City</span><input name="address_city" value="' + _field_value(values, "address_city") + '" placeholder="Cape Coral"></label>'
        '<label class="field"><span>State</span><input name="address_state" value="' + _field_value(values, "address_state") + '" placeholder="FL"></label>'
        '<label class="field"><span>Zip Code</span><input name="address_zip_code" value="' + _field_value(values, "address_zip_code") + '" placeholder="33990"></label>'
        '</div>'
        '</section>'
        '<section class="tab-panel" data-tab-panel="caregiver" hidden>'
        '<div class="field-grid">'
        '<label class="field"><span>Caregiver name</span><input name="caregiver_name" value="' + _field_value(values, "caregiver_name") + '"></label>'
        '<label class="field"><span>Relationship</span><input name="caregiver_relationship" value="' + _field_value(values, "caregiver_relationship") + '" placeholder="Mother"></label>'
        '<label class="field"><span>Phone</span><input name="caregiver_phone" value="' + _field_value(values, "caregiver_phone") + '" placeholder="(786) 555-0100"></label>'
        '<label class="field"><span>Email</span><input name="caregiver_email" value="' + _field_value(values, "caregiver_email") + '" placeholder="caregiver@email.com"></label>'
        '</div>'
        '<label class="field"><span>Notas del caso</span><textarea name="notes" placeholder="Clinical or admin notes for the case.">' + _field_value(values, "notes") + '</textarea></label>'
        '</section>'
        '<section class="tab-panel" data-tab-panel="insurance" hidden>'
        '<div class="field-grid">'
        '<label class="field"><span>Payer Name</span><input name="payer_name" value="' + _field_value(values, "payer_name") + '"></label>'
        '<label class="field"><span>Payer ID</span><input name="payer_id" value="' + _field_value(values, "payer_id") + '"></label>'
        '<label class="field"><span>Insurance effective date</span><input name="insurance_effective_date" value="' + _field_value(values, "insurance_effective_date") + '" placeholder="MM/DD/YYYY"></label>'
        '<label class="field"><span>Subscriber Name</span><input name="subscriber_name" value="' + _field_value(values, "subscriber_name") + '"></label>'
        '<label class="field"><span>Subscriber ID</span><input name="subscriber_id" value="' + _field_value(values, "subscriber_id") + '"></label>'
        '<label class="field"><span>Provider NPI</span><input name="provider_npi" value="' + _field_value(values, "provider_npi") + '"></label>'
        '<label class="field"><span>Physician name</span><input name="physician_name" value="' + _field_value(values, "physician_name") + '"></label>'
        '<label class="field"><span>Physician NPI</span><input name="physician_npi" value="' + _field_value(values, "physician_npi") + '"></label>'
        '<label class="field"><span>Physician phone</span><input name="physician_phone" value="' + _field_value(values, "physician_phone") + '"></label>'
        '</div>'
        '<label class="field"><span>Physician address</span><input name="physician_address" value="' + _field_value(values, "physician_address") + '"></label>'
        '</section>'
        '<section class="tab-panel" data-tab-panel="team" hidden>'
        '<div class="field-grid">'
        '<label class="field"><span>BCBA asignado</span><select name="bcba_contract_id">' + _provider_contract_options_markup(bcba_contracts, str(values.get("bcba_contract_id", "")), empty_label="Selecciona un BCBA") + '</select></label>'
        '<label class="field"><span>BCaBA asignado</span><select name="bcaba_contract_id">' + _provider_contract_options_markup(bcaba_contracts, str(values.get("bcaba_contract_id", "")), empty_label="Selecciona un BCaBA") + '</select></label>'
        '<label class="field"><span>RBT asignado</span><select name="rbt_contract_id">' + _provider_contract_options_markup(rbt_contracts, str(values.get("rbt_contract_id", "")), empty_label="Selecciona un RBT") + '</select></label>'
        '<label class="field"><span>Lugar</span><select name="site_location" data-location-select="client">' + _location_options_markup(str(values.get("site_location", "Cape Coral"))) + '</select></label>'
        '<label class="field"><span>County</span><input name="county_name" data-county-input="client" value="' + _field_value(values, "county_name") + '" placeholder="Lee"></label>'
        '<label class="field"><span>Medicaid ID</span><input name="medicaid_id" value="' + _field_value(values, "medicaid_id") + '"></label>'
        '</div>'
        '<div class="field-grid">'
        '<label class="field"><span><input type="checkbox" name="active"' + _checked(values, "active") + '> Cliente activo</span></label>'
        '<label class="field"><span><input type="checkbox" name="auto_eligibility"' + _checked(values, "auto_eligibility") + '> Incluir en elegibilidad automatica</span></label>'
        '</div>'
        '</section>'
        '<section class="tab-panel" data-tab-panel="documents" hidden>'
        '<div class="form-section">'
        '<div class="section-label">Documentos del cliente</div>'
        '<p class="module-note">Sube aqui el expediente del paciente o caregiver. Los documentos en <strong>Delivered</strong> llenan la barra; los <strong>Ignored</strong> quedan archivados sin contar como completados. Cada archivo tambien se copia automaticamente a una carpeta con el nombre del cliente dentro de tu OneDrive cuando la carpeta esta disponible. Usa los botones de <strong>6 meses</strong>, <strong>1 ano</strong>, <strong>2 anos</strong> o <strong>5 anos</strong> para llenar mas rapido la fecha de vencimiento.</p>'
        '<div class="table-wrap document-checklist client-document-checklist"><table><thead><tr><th>Name</th><th>Issued date</th><th>Expiration date</th><th>Status</th><th>Action</th></tr></thead><tbody>'
        + _client_document_checklist_markup(values)
        + "</tbody></table></div>"
        "</div>"
        "</section>"
        "</div>"
        f'<button type="submit">{html.escape(save_label)}</button>'
        "</form>"
    )


def _render_session_workspace_panel(
    selected_session: dict[str, object] | None,
    *,
    note_preview: str,
    auth_summary: str,
    actions_markup: str,
    claim_actions_markup: str,
    amount_markup: str,
) -> str:
    if selected_session is None:
        return (
            '<section id="session-ops-detail" class="panel section-card">'
            '<h2>Session Workspace</h2>'
            '<p>Selecciona una sesion desde el roster para abrir el workspace con tabs de session, clinical, billing, documents, claim y timeline.</p>'
            "</section>"
        )

    participants = ", ".join(value for value in selected_session.get("participants", []) if str(value).strip()) or "Pendiente"
    claim_status = str(selected_session.get("claim_status", "")).strip() or "Draft"
    claim_id = str(selected_session.get("claim_id", "")).strip() or "Pendiente"
    document_rows = (
        "<tr><td>Appointment Note</td><td>"
        + _session_status_badge(selected_session.get("note_status", "Draft"))
        + "</td><td>Generado desde la sesion</td></tr>"
        + "<tr><td>Service Log</td><td>"
        + _session_status_badge(selected_session.get("clinical_document_status", "Draft"))
        + "</td><td>Se alimenta desde este appointment</td></tr>"
        + "<tr><td>Linked Signatures</td><td>"
        + _session_status_badge("Signed" if str(selected_session.get("note_status", "")).strip() not in {"", "Draft"} else "Pending")
        + "</td><td>Caregiver en appointment + provider en nota / log</td></tr>"
    )
    return (
        '<section id="session-ops-detail" class="panel section-card session-workspace-shell">'
        + '<div class="case-hub-header">'
        + '<div class="page-title-stack">'
        + '<span class="eyebrow">Session Workspace</span>'
        + f"<h2>{html.escape(str(selected_session.get('client_name', '')) or 'Session')}</h2>"
        + "<p>El appointment es el centro operativo: usa la autorizacion del cliente, consume units por CPT segun el provider, captura la firma del caregiver y despues genera nota, service log, validacion y claim.</p>"
        + "</div>"
        + '<div class="profile-pill-row">'
        + f'{_session_status_badge(selected_session.get("session_status", "Scheduled"))}'
        + f'{_session_status_badge(selected_session.get("note_status", "Draft"))}'
        + f'{_session_status_badge(str(selected_session.get("billing_queue_status", "")).replace("_", " ").title())}'
        + f'{_session_status_badge(claim_status)}'
        + "</div>"
        + "</div>"
        + f'<div class="session-progress-track"><span class="session-progress-fill" style="width:{int(selected_session.get("progress_percent", 0) or 0)}%"></span></div>'
        + f'<small>{int(selected_session.get("progress_percent", 0) or 0)}% del lifecycle operativo de la sesion</small>'
        + '<div class="quick-links">' + actions_markup + claim_actions_markup + "</div>"
        + '<div class="segmented-tabs" data-tab-group="session-workspace-tabs">'
        + '<button class="segment active" type="button" data-tab-target="session" aria-pressed="true">Session</button>'
        + '<button class="segment" type="button" data-tab-target="clinical">Clinical</button>'
        + '<button class="segment" type="button" data-tab-target="billing">Billing</button>'
        + '<button class="segment" type="button" data-tab-target="documents">Documents</button>'
        + '<button class="segment" type="button" data-tab-target="claim">Claim</button>'
        + '<button class="segment" type="button" data-tab-target="timeline">Timeline</button>'
        + "</div>"
        + '<div class="tab-panels">'
        + '<section class="tab-panel" data-tab-panel="session">'
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Event type</strong><span>{html.escape(str(selected_session.get("event_type", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Client</strong><span>{html.escape(str(selected_session.get("client_name", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Provider</strong><span>{html.escape(str(selected_session.get("provider_name", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Date</strong><span>{html.escape(str(selected_session.get("service_date", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Start / End</strong><span>{html.escape(str(selected_session.get("scheduled_start_time", "")) or "-")} - {html.escape(str(selected_session.get("scheduled_end_time", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Actual time</strong><span>{html.escape(str(selected_session.get("actual_start_time", "")) or "-")} - {html.escape(str(selected_session.get("actual_end_time", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Location</strong><span>{html.escape(str(selected_session.get("location", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Participants</strong><span>{html.escape(participants)}</span></div>'
        + f'<div class="mini-row"><strong>Service setting</strong><span>{html.escape(str(selected_session.get("place_of_service", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Units / Hours</strong><span>{int(float(selected_session.get("units", 0) or 0))} | {float(selected_session.get("hours", 0) or 0):.2f}h</span></div>'
        + f'<div class="mini-row"><strong>Engine status</strong><span>{html.escape(str(selected_session.get("session_engine_status", "")) or "-")}</span></div>'
        + "</div>"
        + _render_session_action_form(selected_session)
        + "</section>"
        + '<section class="tab-panel" data-tab-panel="clinical" hidden>'
        + '<div class="case-summary-grid">'
        + '<article class="case-summary-card">'
        + "<h3>Clinical Summary</h3>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Note status</strong><span>{html.escape(str(selected_session.get("note_status", "")) or "Draft")}</span></div>'
        + f'<div class="mini-row"><strong>Document status</strong><span>{html.escape(str(selected_session.get("clinical_document_status", "")) or "Draft")}</span></div>'
        + f'<div class="mini-row"><strong>CPT</strong><span>{html.escape(str(selected_session.get("cpt_code", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Caregiver signature</strong><span>{"Present" if bool(selected_session.get("caregiver_signature_present")) else "Pending"}</span></div>'
        + f'<div class="mini-row"><strong>Provider signature</strong><span>{"Present" if bool(selected_session.get("provider_signature_present")) else "Pending"}</span></div>'
        + '<div class="mini-row"><strong>Service log link</strong><span>La firma del caregiver de este appointment tambien se refleja en el service log semanal.</span></div>'
        + "</div>"
        + "</article>"
        + '<article class="case-summary-card">'
        + "<h3>Provider-facing note</h3>"
        + f'<p class="module-note">{html.escape(note_preview or "Todavia no hay texto de nota capturado para esta sesion.")}</p>'
        + '<div class="quick-links ai-inline-actions">'
        + _ai_action_form_markup(
            "improve_session_note",
            "Improve Session Note",
            return_page="aba_notes",
            active_panel="aba_notes",
            hidden_fields={
                "session_id": str(selected_session.get("session_id", "")).strip(),
                "selected_log_id": str(selected_session.get("service_log_id", "")).strip(),
            },
        )
        + "</div>"
        + "</article>"
        + "</div>"
        + "</section>"
        + '<section class="tab-panel" data-tab-panel="billing" hidden>'
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Payer</strong><span>{html.escape(str(selected_session.get("payer_name", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Insurance ID</strong><span>{html.escape(str(selected_session.get("member_id", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Authorization</strong><span>{html.escape(auth_summary)}</span></div>'
        + f'<div class="mini-row"><strong>Validation result</strong><span>{html.escape(str(selected_session.get("billing_validation_status", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Billing queue</strong><span>{html.escape(str(selected_session.get("billing_queue_status", "")) or "-")}</span></div>'
        + amount_markup
        + "</div>"
        + '<div class="profile-pill-row">'
        + _validation_pills_markup(selected_session.get("validation_results", []))
        + "</div>"
        + f'<p class="helper-note">{html.escape(str(selected_session.get("billing_hold_reason", "")) or "Sin hold activo. La sesion puede seguir al claim builder.")}</p>'
        + '<div class="table-wrap"><table><thead><tr><th>Check</th><th>Status</th><th>Detalle</th></tr></thead><tbody>'
        + _render_session_validation_rows(selected_session.get("validation_results", []))
        + "</tbody></table></div>"
        + "</section>"
        + '<section class="tab-panel" data-tab-panel="documents" hidden>'
        + "<p>Los documentos se generan desde la sesion, pero al provider solo se le muestra una experiencia simple de nota, firma y service log.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>Documento</th><th>Status</th><th>Origen</th></tr></thead><tbody>'
        + document_rows
        + "</tbody></table></div>"
        + "</section>"
        + '<section class="tab-panel" data-tab-panel="claim" hidden>'
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Claim ID</strong><span>{html.escape(claim_id)}</span></div>'
        + f'<div class="mini-row"><strong>Claim status</strong><span>{html.escape(claim_status)}</span></div>'
        + f'<div class="mini-row"><strong>Batch period</strong><span>{html.escape(str(selected_session.get("period_start", "")) or "-")} - {html.escape(str(selected_session.get("period_end", "")) or "-")}</span></div>'
        + f'<div class="mini-row"><strong>Tracking</strong><span>{html.escape(str(selected_session.get("claim_tracking_id", "")) or "Pendiente")}</span></div>'
        + "</div>"
        + '<div class="quick-links">' + actions_markup + claim_actions_markup + "</div>"
        + "</section>"
        + '<section class="tab-panel" data-tab-panel="timeline" hidden>'
        + _render_session_timeline_markup(selected_session.get("timeline", []))
        + "</section>"
        + "</div>"
        + "</section>"
    )


def _client_card_avatar_markup(item: dict[str, object]) -> str:
    patient_name = f"{item.get('first_name', '')} {item.get('last_name', '')}".strip() or str(item.get("member_id", "")).strip() or "Cliente"
    initials = "".join(part[:1].upper() for part in patient_name.split()[:2]) or "PT"
    seed = sum(ord(char) for char in patient_name)
    palette = (
        ("#f97316", "#fdba74"),
        ("#14b8a6", "#99f6e4"),
        ("#3b82f6", "#93c5fd"),
        ("#8b5cf6", "#c4b5fd"),
        ("#ef4444", "#fca5a5"),
        ("#10b981", "#86efac"),
    )
    color_a, color_b = palette[seed % len(palette)]
    return (
        f'<span class="directory-avatar-fallback" style="background:linear-gradient(135deg, {color_a} 0%, {color_b} 100%);">'
        f"{html.escape(initials)}"
        "</span>"
    )


def _directory_chip(label: str, tone: str = "neutral") -> str:
    return f'<span class="directory-chip {html.escape(tone)}">{html.escape(label)}</span>'


def _client_directory_status(item: dict[str, object]) -> str:
    return "active" if item.get("active", True) else "inactive"


def _user_directory_status(item: dict[str, object]) -> str:
    return "active" if item.get("active", True) else "inactive"


def _render_clients_directory(items: list[dict[str, object]], authorizations: list[dict[str, object]]) -> str:
    if not items:
        return '<p class="helper-note">Todavia no hay clientes guardados.</p>'

    cards = []
    for item in items:
        patient_name = f"{item.get('first_name', '')} {item.get('last_name', '')}".strip() or "Paciente"
        status_label = "ACTIVE" if item.get("active", True) else "INACTIVE"
        member_id = str(item.get("member_id", "")).strip()
        payer_name = str(item.get("payer_name", "")).strip()
        payer_label = payer_name[:16].upper() if payer_name else "SIN PAYER"
        site_location = str(item.get("site_location", "")).strip() or "Cape Coral"
        county_name = str(item.get("county_name", "")).strip() or "Florida"
        eligibility_label = "AUTO ELIG" if item.get("auto_eligibility", True) else "MANUAL"
        delivered_documents = int(item.get("delivered_documents", 0) or 0)
        total_documents = int(item.get("total_documents", 0) or 0)
        last_result = str(item.get("last_eligibility_result", "pendiente")).strip() or "pendiente"
        search_text = " ".join(
            [
                patient_name,
                member_id,
                payer_name,
                site_location,
                county_name,
                last_result,
                str(item.get("bcba_provider_name", "")),
                str(item.get("bcaba_provider_name", "")),
                str(item.get("rbt_provider_name", "")),
            ]
        ).lower()
        expediente_href = _client_expediente_href(item)
        cards.append(
            '<article class="directory-card client-card"'
            f' data-directory-card="clients" data-status="{html.escape(_client_directory_status(item))}"'
            f' data-search="{html.escape(search_text)}">'
            f'<a class="directory-card-hero" href="{html.escape(expediente_href)}">'
            f'<div class="directory-avatar">{_client_card_avatar_markup(item)}</div>'
            f'<strong class="directory-card-title">{html.escape(patient_name)}</strong>'
            f'<p class="directory-card-subtitle">{html.escape(member_id or "Sin Member ID")}</p>'
            "</a>"
            '<div class="directory-chip-row">'
            f'{_directory_chip(status_label, "active" if item.get("active", True) else "inactive")}'
            f'{_directory_chip(payer_label, "neutral")}'
            f'{_directory_chip(eligibility_label, "info")}'
            "</div>"
            '<div class="directory-card-meta">'
            f'<span>{html.escape(site_location)} | {html.escape(county_name)}</span>'
            f'<span>{html.escape(payer_name or "Sin payer asignado")}</span>'
            "</div>"
            f"{_client_directory_snapshot_markup(item, authorizations)}"
            '<div class="directory-card-actions">'
            f"<a class=\"small-button\" href=\"{html.escape(expediente_href)}\">Abrir cliente</a>"
            "</div>"
            "</article>"
        )
    return '<div class="directory-grid" data-directory-grid="clients">' + "".join(cards) + "</div>"


def _render_users_directory(items: list[dict[str, object]]) -> str:
    if not items:
        return '<p class="helper-note">Todavia no hay usuarios registrados.</p>'

    cards = []
    for item in items:
        role_value = str(item.get("role", "")).upper()
        role_label = ROLE_LABELS.get(role_value, role_value.title() or "Usuario")
        username = str(item.get("username", "")).strip()
        site_location = str(item.get("site_location", "")).strip() or "Cape Coral"
        county_name = str(item.get("county_name", "")).strip() or "Florida"
        linked_provider_name = str(item.get("linked_provider_name", "")).strip()
        job_title = str(item.get("job_title", "")).strip()
        search_text = " ".join(
            [
                str(item.get("full_name", "")).strip(),
                username,
                role_label,
                site_location,
                county_name,
                linked_provider_name,
                job_title,
            ]
        ).lower()
        cards.append(
            '<article class="directory-card user-card"'
            f' data-directory-card="users" data-status="{html.escape(_user_directory_status(item))}"'
            f' data-search="{html.escape(search_text)}">'
            f'<div class="directory-avatar profile-avatar">{_avatar_markup(item)}</div>'
            f'<strong class="directory-card-title">{html.escape(str(item.get("full_name", "")).strip() or username or "Usuario")}</strong>'
            f'<p class="directory-card-subtitle">{html.escape(job_title or username or role_label)}</p>'
            '<div class="directory-chip-row">'
            f'{_directory_chip("ACTIVE" if item.get("active", True) else "INACTIVE", "active" if item.get("active", True) else "inactive")}'
            f'{_directory_chip(username.upper() or "USER", "info")}'
            f'{_directory_chip("FL", "neutral")}'
            f'{_directory_chip(site_location.upper(), "neutral")}'
            f'{_directory_chip(county_name.upper(), "neutral")}'
            f'{_directory_chip(role_label.upper(), "neutral")}'
            "</div>"
            '<div class="directory-card-meta">'
            f'<span>MFA: {"Activo" if item.get("mfa_enabled", False) else "Pendiente"}</span>'
            f'<span>Provider: {html.escape(linked_provider_name or "-")}</span>'
            "</div>"
            '<div class="directory-card-actions">'
            '<a class="small-button" href="#users-directory-form">Editar arriba</a>'
            "</div>"
            "</article>"
        )
    return '<div class="directory-grid" data-directory-grid="users">' + "".join(cards) + "</div>"


def _directory_toolbar_markup(
    *,
    directory_name: str,
    action_href: str,
    active_count: int,
    inactive_count: int,
    agency_name: str,
    default_status: str = "active",
    default_view: str = "card",
    action_label: str = "Actions",
) -> str:
    directory_label = {
        "clients": "Clients",
        "providers": "Providers",
        "users": "Users",
        "payers": "Payers",
    }.get(directory_name, directory_name.title() or "Directory")
    clean_default_status = str(default_status or "active").strip().lower()
    clean_default_view = str(default_view or "card").strip().lower()
    action_markup = (
        f'<a class="directory-action-button" href="{html.escape(action_href)}">{html.escape(action_label)}</a>'
        if str(action_label).strip() and str(action_href).strip()
        else ""
    )
    toolbar_class = "directory-toolbar" + ("" if action_markup else " no-directory-action")
    return (
        f'<div class="directory-breadcrumb">AGENCY {html.escape(agency_name)} &rsaquo; {html.escape(directory_label)}</div>'
        f'<div class="{toolbar_class}"'
        f' data-directory-toolbar="{html.escape(directory_name)}">'
        '<label class="directory-search">'
        '<span>Search:</span>'
        f'<input type="search" placeholder="Search here" data-directory-search="{html.escape(directory_name)}">'
        "</label>"
        '<label class="directory-filter">'
        '<span>View:</span>'
        f'<select data-directory-view="{html.escape(directory_name)}">'
        f'<option value="card"{_selected_value(clean_default_view, "card")}>Card</option>'
        f'<option value="table"{_selected_value(clean_default_view, "table")}>Table</option>'
        "</select>"
        "</label>"
        '<label class="directory-filter">'
        '<span>Status:</span>'
        f'<select data-directory-status="{html.escape(directory_name)}">'
        f'<option value="active"{_selected_value(clean_default_status, "active")}>Active ({active_count})</option>'
        f'<option value="inactive"{_selected_value(clean_default_status, "inactive")}>Inactive ({inactive_count})</option>'
        f'<option value="all"{_selected_value(clean_default_status, "all")}>All ({active_count + inactive_count})</option>'
        "</select>"
        "</label>"
        f"{action_markup}"
        "</div>"
    )


def _render_workspace_hub(
    title: str,
    copy: str,
    tiles: list[dict[str, object]],
) -> str:
    visible_tiles = [tile for tile in tiles if tile]
    if not visible_tiles:
        return ""
    cards = []
    for tile in visible_tiles:
        href = str(tile.get("href", "")).strip() or "#"
        cards.append(
            f'<a class="tool-tile" href="{html.escape(href)}">'
            f'<span class="tool-icon">{html.escape(str(tile.get("icon", ".."))[:2].upper())}</span>'
            f"<strong>{html.escape(str(tile.get('title', 'Tarjeta')))}</strong>"
            f"<p>{html.escape(str(tile.get('copy', '')))}</p>"
            f"<span>{html.escape(str(tile.get('meta', '')))}</span>"
            "</a>"
        )
    return (
        '<section class="panel section-card" data-skip-auto-collapsible="1">'
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(copy)}</p>"
        f'<div class="tool-grid hub-grid">{"".join(cards)}</div>'
        "</section>"
    )


def _page_action_card_markup(title: str, copy: str, meta: str, href: str) -> str:
    return (
        f'<a class="page-action-card" href="{html.escape(href)}">'
        f"<strong>{html.escape(title)}</strong>"
        f"<p>{html.escape(copy)}</p>"
        f"<span>{html.escape(meta)}</span>"
        "</a>"
    )


def _simple_page_intro_markup(
    *,
    current_page: str,
    page_key: str,
    kicker: str,
    title: str,
    copy: str,
    pills: list[str] | None = None,
    primary_label: str = "",
    primary_href: str = "",
    secondary_label: str = "",
    secondary_href: str = "",
) -> str:
    clean_pills = [str(item).strip() for item in (pills or []) if str(item).strip()]
    pills_markup = (
        '<div class="profile-pill-row">'
        + "".join(f'<span class="profile-pill neutral">{html.escape(item)}</span>' for item in clean_pills)
        + "</div>"
        if clean_pills
        else ""
    )
    button_markup = ""
    if primary_label and primary_href:
        button_markup += f'<a class="page-primary-button" href="{html.escape(primary_href)}">{html.escape(primary_label)}</a>'
    if secondary_label and secondary_href:
        button_markup += f'<a class="page-secondary-button" href="{html.escape(secondary_href)}">{html.escape(secondary_label)}</a>'
    command_markup = f'<div class="page-command-bar">{button_markup}</div>' if button_markup else ""
    return (
        '<section class="page-intro"'
        + _section_hidden(current_page, page_key)
        + ">"
        '<article class="panel section-card page-title-card page-title-card-inline">'
        '<div class="page-title-stack">'
        f'<span class="eyebrow">{html.escape(kicker)}</span>'
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(copy)}</p>"
        f"{pills_markup}"
        "</div>"
        f"{command_markup}"
        "</article>"
        "</section>"
    )


def _metric_card_markup(
    title: str,
    value: object,
    copy: str,
    *,
    tone: str = "neutral",
    note: str = "",
    href: str = "",
) -> str:
    tag_name = "a" if href else "article"
    href_markup = f' href="{html.escape(href)}"' if href else ""
    note_markup = f'<span class="metric-card-note">{html.escape(note)}</span>' if note else ""
    return (
        f'<{tag_name} class="metric-card metric-{html.escape(tone)}"{href_markup}>'
        f"<span>{html.escape(title)}</span>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<p>{html.escape(copy)}</p>"
        f"{note_markup}"
        f"</{tag_name}>"
    )


def _queue_row_markup(title: str, value: object, copy: str, href: str = "") -> str:
    tag_name = "a" if href else "div"
    href_markup = f' href="{html.escape(href)}"' if href else ""
    return (
        f'<{tag_name} class="queue-row"{href_markup}>'
        '<div class="queue-row-head">'
        f"<strong>{html.escape(title)}</strong>"
        f"<span>{html.escape(str(value))}</span>"
        "</div>"
        f"<p>{html.escape(copy)}</p>"
        f"</{tag_name}>"
    )


def _dashboard_list_row_markup(
    title: str,
    subtitle: str,
    meta: str = "",
    *,
    tone: str = "neutral",
) -> str:
    meta_markup = (
        f'<span class="dashboard-row-meta dashboard-row-meta-{html.escape(tone)}">{html.escape(meta)}</span>'
        if meta
        else ""
    )
    return (
        '<div class="dashboard-row">'
        '<div class="dashboard-row-copy">'
        f"<strong>{html.escape(title)}</strong>"
        f"<small>{html.escape(subtitle)}</small>"
        "</div>"
        f"{meta_markup}"
        "</div>"
    )


def _dashboard_module_card_markup(
    title: str,
    rows_markup: str,
    *,
    action_label: str = "",
    action_href: str = "",
) -> str:
    action_markup = (
        f'<a class="dashboard-card-action" href="{html.escape(action_href)}">{html.escape(action_label)}</a>'
        if action_label and action_href
        else ""
    )
    return (
        '<article class="panel section-card dashboard-module-card">'
        '<div class="dashboard-module-head">'
        f"<h3>{html.escape(title)}</h3>"
        f"{action_markup}"
        "</div>"
        f'<div class="dashboard-module-body">{rows_markup}</div>'
        "</article>"
    )


def _dashboard_shortcut_card_markup(title: str, copy: str, meta: str, href: str) -> str:
    return (
        f'<a class="dashboard-shortcut-card" href="{html.escape(href)}">'
        f"<strong>{html.escape(title)}</strong>"
        f"<p>{html.escape(copy)}</p>"
        f"<span>{html.escape(meta)}</span>"
        "</a>"
    )


def _dashboard_performance_stat_markup(label: str, value: object) -> str:
    return (
        '<div class="dashboard-performance-stat">'
        f"<small>{html.escape(label)}</small>"
        f"<strong>{html.escape(str(value))}</strong>"
        "</div>"
    )


def _build_dashboard_weekly_hours_series(
    sessions: list[dict[str, object]],
    *,
    total_weeks: int = 10,
) -> list[dict[str, object]]:
    if total_weeks <= 0:
        return []
    today = datetime.now().date()
    current_week_start = today - timedelta(days=today.weekday())
    week_starts = [
        current_week_start - timedelta(weeks=offset)
        for offset in range(total_weeks - 1, -1, -1)
    ]
    totals: dict[date, float] = {week_start: 0.0 for week_start in week_starts}
    for session in sessions:
        service_value = parse_user_date(str(session.get("service_date", "")).strip())
        if service_value is None:
            continue
        service_day = service_value.date() if isinstance(service_value, datetime) else service_value
        week_start = service_day - timedelta(days=service_day.weekday())
        if week_start not in totals:
            continue
        try:
            totals[week_start] += float(session.get("hours", 0) or 0)
        except (TypeError, ValueError):
            continue
    return [
        {
            "label": week_start.strftime("%b"),
            "hours": round(totals.get(week_start, 0.0), 2),
        }
        for week_start in week_starts
    ]


def _render_dashboard_hours_chart(sessions: list[dict[str, object]]) -> str:
    series = _build_dashboard_weekly_hours_series(sessions)
    if not series:
        return '<div class="dashboard-empty-state">No hay sesiones suficientes para graficar horas semanales.</div>'
    max_hours = max(float(item.get("hours", 0) or 0) for item in series) or 1.0
    bars = []
    for item in series:
        hours = float(item.get("hours", 0) or 0)
        height_percent = max(12, int(round((hours / max_hours) * 100))) if hours else 8
        bars.append(
            '<div class="dashboard-bar-item">'
            f'<div class="dashboard-bar" style="height:{height_percent}%"></div>'
            f'<span>{html.escape(str(item.get("label", "")))}</span>'
            "</div>"
        )
    return (
        '<div class="dashboard-chart-shell">'
        '<div class="dashboard-bars">'
        f"{''.join(bars)}"
        "</div>"
        "</div>"
    )


def _render_dashboard_billing_overview(claim_summary: dict[str, object]) -> str:
    approved = int(claim_summary.get("paid", 0) or 0)
    pending = int(claim_summary.get("pending", 0) or 0) + int(claim_summary.get("queued", 0) or 0) + int(claim_summary.get("partial", 0) or 0)
    denied = int(claim_summary.get("denied", 0) or 0)
    total = max(approved + pending + denied, 1)
    approved_percent = round((approved / total) * 100, 2)
    pending_percent = round((pending / total) * 100, 2)
    denied_percent = max(0.0, round(100 - approved_percent - pending_percent, 2))
    pending_end = round(approved_percent + pending_percent, 2)
    donut_style = (
        "background: conic-gradient("
        f"#59c38a 0% {approved_percent}%, "
        f"#f7a84d {approved_percent}% {pending_end}%, "
        f"#e76a5e {pending_end}% 100%);"
    )
    legend_rows = (
        f'<div class="dashboard-legend-row"><span class="dashboard-legend-dot approved"></span><strong>{approved}</strong><small>Approved</small></div>'
        f'<div class="dashboard-legend-row"><span class="dashboard-legend-dot pending"></span><strong>{pending}</strong><small>Pending</small></div>'
        f'<div class="dashboard-legend-row"><span class="dashboard-legend-dot denied"></span><strong>{denied}</strong><small>Denied</small></div>'
    )
    return (
        '<div class="dashboard-donut-layout">'
        f'<div class="dashboard-donut" style="{donut_style}"><div class="dashboard-donut-core"></div></div>'
        f'<div class="dashboard-legend">{legend_rows}</div>'
        "</div>"
    )


def _render_payer_enrollment_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="16">Todavia no hay enrolamientos guardados.</td></tr>'

    rows = []
    for item in items:
        progress_percent = int(item.get("credentialing_progress_percent", 0) or 0)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('contract_id', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('provider_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('ssn', '')))}</td>"
            f"<td>{html.escape(str(item.get('npi', '')))}</td>"
            f"<td>{html.escape(str(item.get('medicaid_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('site_location', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('county_name', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('credentialing_owner_name', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('supervisor_name', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('enrollment_status', '')))}</td>"
            f"<td>{html.escape(str(item.get('credentials_submitted_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('effective_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('expected_completion_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('days_remaining', '')))}</td>"
            f"<td><div class=\"progress\"><span style=\"width:{progress_percent}%\"></span></div><small>{progress_percent}%</small></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_agency_rows(items: list[dict[str, object]], current_agency_id: str) -> str:
    if not items:
        return '<tr><td colspan="8">Todavia no hay agencias registradas.</td></tr>'

    rows = []
    for item in items:
        agency_id = str(item.get("agency_id", ""))
        action_markup = (
            "<span class=\"pill success-pill\">Agencia activa</span>"
            if agency_id == current_agency_id
            else (
                "<form class=\"table-action-form\" method=\"post\" action=\"/set-current-agency\">"
                f"<input type=\"hidden\" name=\"agency_id\" value=\"{html.escape(agency_id)}\">"
                "<button class=\"small-button\" type=\"submit\">Usar agencia</button>"
                "</form>"
            )
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(agency_id)}</td>"
            f"<td>{html.escape(str(item.get('agency_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('agency_code', '')))}</td>"
            f"<td>{html.escape(str(item.get('notification_email', '')))}</td>"
            f"<td>{html.escape(str(item.get('contact_name', '')))}</td>"
            f"<td>{'Cargado' if item.get('logo_file_path') else 'Pendiente'}</td>"
            f"<td>{html.escape(str(item.get('notes', '')))}</td>"
            f"<td>{action_markup}</td>"
            "</tr>"
        )
    return "".join(rows)


def _provider_document_status_options_markup(selected_value: str) -> str:
    options = []
    for status_value in ("Pending", "Delivered", "Ignored"):
        options.append(
            f'<option value="{status_value}"{_selected_value(selected_value, status_value)}>{status_value}</option>'
        )
    return "".join(options)


def _document_status_class(selected_value: str) -> str:
    status_value = str(selected_value or "Pending").strip().lower()
    if status_value == "delivered":
        return "delivered"
    if status_value == "ignored":
        return "ignored"
    if status_value == "expired":
        return "expired"
    if status_value == "pending approval":
        return "pending-approval"
    return "pending"


def _document_status_badge(label: str) -> str:
    return f'<span class="pill {html.escape(_document_status_class(label))}-pill">{html.escape(label)}</span>'


def _provider_document_checklist_markup(values: dict[str, str]) -> str:
    rows = []
    for index, document_name in enumerate(list_provider_required_documents()):
        status_value = str(values.get(f"provider_document_{index}_status", "Pending"))
        issued_input_id = f"provider_document_{index}_issued_date"
        expiration_input_id = f"provider_document_{index}_expiration_date"
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(document_name)}</strong></td>"
            f"<td><input class=\"document-date-input\" id=\"{issued_input_id}\" name=\"provider_document_{index}_issued_date\" value=\"{html.escape(str(values.get(f'provider_document_{index}_issued_date', '')))}\" placeholder=\"MM/DD/YYYY\"></td>"
            "<td>"
            f"<div class=\"document-expiration-cell\"><input class=\"document-date-input\" id=\"{expiration_input_id}\" name=\"provider_document_{index}_expiration_date\" value=\"{html.escape(str(values.get(f'provider_document_{index}_expiration_date', '')))}\" placeholder=\"MM/DD/YYYY\">"
            "<div class=\"document-shortcuts\">"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"months\" data-amount=\"6\">6 meses</button>"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"years\" data-amount=\"1\">1 ano</button>"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"years\" data-amount=\"2\">2 anos</button>"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"years\" data-amount=\"5\">5 anos</button>"
            "</div></div>"
            "</td>"
            f"<td><select class=\"document-status-select {html.escape(_document_status_class(status_value))}\" name=\"provider_document_{index}_status\">{_provider_document_status_options_markup(status_value)}</select></td>"
            "<td>"
            "<label class=\"document-action-button\">"
            "<span>Upload / replace</span>"
            f"<input type=\"file\" name=\"provider_document_{index}_file\" accept=\".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx\">"
            "</label>"
            "</td>"
            "</tr>"
        )
    return "".join(rows)


def _client_document_status_options_markup(selected_value: str) -> str:
    options = []
    for status_value in ("Pending", "Delivered", "Ignored"):
        options.append(
            f'<option value="{status_value}"{_selected_value(selected_value, status_value)}>{status_value}</option>'
        )
    return "".join(options)


def _client_document_status_class(selected_value: str) -> str:
    return _document_status_class(selected_value)


def _client_document_checklist_markup(values: dict[str, str]) -> str:
    rows = []
    for index, document_name in enumerate(list_client_required_documents()):
        status_value = str(values.get(f"client_document_{index}_status", "Pending"))
        issued_input_id = f"client_document_{index}_issued_date"
        expiration_input_id = f"client_document_{index}_expiration_date"
        rows.append(
            "<tr>"
            "<td>"
            "<div class=\"document-name-cell\">"
            "<span class=\"document-caret\">&rsaquo;</span>"
            "<span class=\"document-file-icon\">[]</span>"
            f"<strong>{html.escape(document_name)}</strong>"
            "</div>"
            "</td>"
            f"<td><input class=\"document-date-input\" id=\"{issued_input_id}\" name=\"client_document_{index}_issued_date\" value=\"{html.escape(str(values.get(f'client_document_{index}_issued_date', '')))}\" placeholder=\"MM/DD/YYYY\"></td>"
            "<td>"
            f"<div class=\"document-expiration-cell\"><input class=\"document-date-input\" id=\"{expiration_input_id}\" name=\"client_document_{index}_expiration_date\" value=\"{html.escape(str(values.get(f'client_document_{index}_expiration_date', '')))}\" placeholder=\"MM/DD/YYYY\">"
            "<div class=\"document-shortcuts\">"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"months\" data-amount=\"6\">6 meses</button>"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"years\" data-amount=\"1\">1 ano</button>"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"years\" data-amount=\"2\">2 anos</button>"
            f"<button class=\"document-shortcut\" type=\"button\" data-issued-input=\"{issued_input_id}\" data-expiration-input=\"{expiration_input_id}\" data-unit=\"years\" data-amount=\"5\">5 anos</button>"
            "</div></div>"
            "</td>"
            f"<td><select class=\"document-status-select {html.escape(_client_document_status_class(status_value))}\" name=\"client_document_{index}_status\">{_client_document_status_options_markup(status_value)}</select></td>"
            "<td>"
            "<label class=\"document-action-button\">"
            "<span>Upload / replace</span>"
            f"<input type=\"file\" name=\"client_document_{index}_file\" accept=\".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx\">"
            "</label>"
            "</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_provider_contract_rows(items: list[dict[str, object]], can_manage: bool = False) -> str:
    if not items:
        return '<tr><td colspan="6">Todavia no hay providers en contratacion.</td></tr>'

    rows = []
    for item in items:
        completed_documents = int(item.get("completed_documents", item.get("delivered_documents", 0)) or 0)
        total_documents = int(item.get("total_documents", 0) or 0)
        credential_status = str(item.get("credentialing_status_summary", "") or "Sin credenciales")
        assigned_clients = str(item.get("assigned_clients", "") or "").strip()
        provider_name = str(item.get("provider_name", "")).strip() or "Provider"
        provider_role = str(item.get("provider_type", "")).strip() or str(item.get("worker_category", "")).strip() or "Provider"
        site_location = str(item.get("site_location", "")).strip() or "Florida"
        county_name = str(item.get("county_name", "")).strip() or "Florida"
        exp_date = str(item.get("credentialing_due_date", "")).strip() or "-"
        search_text = " ".join(
            [
                provider_name,
                str(item.get("worker_category", "")),
                provider_role,
                assigned_clients or "-",
                str(item.get("contract_id", "")),
                site_location,
                county_name,
                str(item.get("provider_npi", "")),
                str(item.get("contract_stage", "")),
                credential_status,
                str(item.get("recruiter_name", "")),
                str(item.get("supervisor_name", "")),
                str(item.get("credentialing_owner_name", "")),
            ]
        ).lower()
        credentials_label = credential_status
        if total_documents > 0:
            credentials_label = f"{credential_status} | {completed_documents}/{total_documents} docs"
        action_markup = (
            '<div class="table-action-row">'
            f'<a class="small-button" href="{_provider_expediente_href(item)}">View</a>'
            + (
                f'<a class="small-button" href="{_provider_expediente_href(item, "provider-contract-form")}">Edit</a>'
                if can_manage
                else ""
            )
            + "</div>"
        )
        rows.append(
            f"<tr data-directory-row=\"providers\" data-status=\"{html.escape(_provider_directory_status(item))}\" data-search=\"{html.escape(search_text)}\">"
            "<td>"
            '<div class="table-profile-cell">'
            f'<div class="table-avatar">{_provider_card_avatar_markup(item)}</div>'
            '<div class="table-profile-copy">'
            f'<a class="quick-link" href="{_provider_expediente_href(item)}">{html.escape(provider_name)}</a>'
            f"<small>{html.escape(site_location)} | {html.escape(county_name)}</small>"
            "</div>"
            "</div>"
            "</td>"
            f"<td>{html.escape(provider_role)}</td>"
            f"<td>{_provider_table_status_badge_markup(item)}</td>"
            f"<td>{html.escape(credentials_label)}</td>"
            f"<td>{html.escape(exp_date)}</td>"
            f"<td>{action_markup}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_supervision_rows(
    items: list[dict[str, object]],
    current_user: dict[str, object] | None,
    users: list[dict[str, object]],
) -> str:
    if not items:
        return '<tr><td colspan="7">Todavia no hay providers en supervision.</td></tr>'

    user_lookup = _user_display_lookup(users)
    rows = []
    for item in items:
        status = _provider_status_summary_values(item, current_user, user_lookup)
        contract_id = quote(str(item.get("contract_id", "")))
        contract_flag = (
            '<span class="provider-owner-flag">Tu supervision</span>'
            if status.get("contract_owner_is_current")
            else ""
        )
        credential_flag = (
            '<span class="provider-owner-flag">Tu credencializacion</span>'
            if status.get("credential_owner_is_current")
            else ""
        )
        rows.append(
            "<tr>"
            f"<td><a class=\"quick-link\" href=\"{_page_href('providers')}?edit_contract_id={contract_id}#provider-detail\">{html.escape(str(item.get('provider_name', '')))}</a></td>"
            f"<td>{html.escape(str(status.get('contract_stage_label', '')))}</td>"
            "<td>"
            f"<div class=\"progress\"><span style=\"width:{int(status.get('contract_progress_percent', 0))}%\"></span></div>"
            f"<small>{int(status.get('contract_progress_percent', 0))}% | {html.escape(str(status.get('contract_assignment_label', 'Sin responsables asignados')))}</small>"
            f"{contract_flag}"
            "</td>"
            f"<td>{html.escape(str(status.get('supervisor_label', 'Sin asignar')))}</td>"
            "<td>"
            f"<strong>{html.escape(str(status.get('credential_status_label', 'Sin credenciales')))}</strong>"
            f"<div class=\"progress\"><span style=\"width:{int(status.get('credential_progress_percent', 0))}%\"></span></div>"
            f"<small>{int(status.get('credential_progress_percent', 0))}% | {html.escape(str(status.get('credential_assignment_label', 'Sin credenciales asignadas')))}</small>"
            f"<small>{html.escape(str(status.get('credential_due_label', 'Sin meta')))}</small>"
            f"{credential_flag}"
            "</td>"
            f"<td>{html.escape(str(item.get('assigned_clients', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('site_location', '')) or '-')}</td>"
            "</tr>"
        )
    return "".join(rows)


def _provider_document_summary_values(item: dict[str, object]) -> dict[str, object]:
    progress_percent = int(item.get("progress_percent", 0) or 0)
    completed_documents = int(item.get("completed_documents", item.get("delivered_documents", 0)) or 0)
    total_documents = int(item.get("total_documents", 0) or 0)
    expired_documents = int(item.get("expired_documents", 0) or 0)
    expiring_documents = int(item.get("expiring_documents", 0) or 0)
    checklist_label = f"Checklist {completed_documents}/{total_documents}"
    if expired_documents:
        checklist_label += f" | Expired {expired_documents}"
    elif expiring_documents:
        checklist_label += f" | Por vencer {expiring_documents}"
    credential_due = str(item.get("credentialing_due_date", "")).strip()
    credential_label = f"Credenciales hasta {credential_due}" if credential_due else "Sin fecha credencial"
    return {
        "progress_percent": progress_percent,
        "completed_documents": completed_documents,
        "total_documents": total_documents,
        "checklist_label": checklist_label,
        "credential_label": credential_label,
    }


def _user_display_lookup(items: list[dict[str, object]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for item in items:
        username = str(item.get("username", "")).strip()
        full_name = str(item.get("full_name", "")).strip() or username
        if username:
            lookup[username.lower()] = full_name
        if full_name:
            lookup[full_name.lower()] = full_name
    return lookup


def _assignment_display_name(value: object, user_lookup: dict[str, str]) -> str:
    clean_value = str(value or "").strip()
    if not clean_value:
        return "Sin asignar"
    return user_lookup.get(clean_value.lower(), clean_value)


def _is_current_assignment(value: object, current_user: dict[str, object] | None) -> bool:
    clean_value = str(value or "").strip().lower()
    if not clean_value:
        return False
    current_username = str((current_user or {}).get("username", "")).strip().lower()
    current_full_name = str((current_user or {}).get("full_name", "")).strip().lower()
    return clean_value in {current_username, current_full_name}


def _provider_status_summary_values(
    item: dict[str, object],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> dict[str, object]:
    contract_progress = int(item.get("stage_progress_percent", 0) or 0)
    credential_progress = int(item.get("credentialing_progress_percent", 0) or 0)
    if not credential_progress and str(item.get("credentialing_start_date", "")).strip():
        try:
            days_remaining = int(item.get("credentialing_days_remaining", 0) or 0)
        except (TypeError, ValueError):
            days_remaining = 0
        credential_progress = min(int(round((max(90 - days_remaining, 0) / 90) * 100)), 100)

    recruiter_raw = item.get("recruiter_name", "")
    supervisor_raw = item.get("supervisor_name", "")
    credentialing_raw = item.get("credentialing_owner_name", "")
    office_reviewer_raw = item.get("office_reviewer_name", "")
    credential_due = str(item.get("credentialing_due_date", "")).strip()
    contract_parts: list[str] = []
    recruiter_label = _assignment_display_name(recruiter_raw, user_lookup)
    supervisor_label = _assignment_display_name(supervisor_raw, user_lookup)
    credentialing_label = _assignment_display_name(credentialing_raw, user_lookup)
    office_reviewer_label = _assignment_display_name(office_reviewer_raw, user_lookup)

    if recruiter_label != "Sin asignar":
        contract_parts.append(f"Recruiter: {recruiter_label}")
    if supervisor_label != "Sin asignar":
        contract_parts.append(f"Supervisor: {supervisor_label}")

    credential_parts: list[str] = []
    if credentialing_label != "Sin asignar":
        credential_parts.append(f"A cargo: {credentialing_label}")
    if credential_due:
        credential_parts.append(f"Meta: {credential_due}")

    return {
        "contract_progress_percent": contract_progress,
        "contract_stage_label": _contract_stage_label(str(item.get("contract_stage", ""))),
        "contract_assignment_label": " | ".join(contract_parts) if contract_parts else "Sin responsables asignados",
        "contract_owner_is_current": _is_current_assignment(recruiter_raw, current_user) or _is_current_assignment(supervisor_raw, current_user),
        "recruiter_label": recruiter_label,
        "supervisor_label": supervisor_label,
        "credential_progress_percent": credential_progress,
        "credential_status_label": str(item.get("credentialing_status_summary", "") or "Sin credenciales"),
        "credential_assignment_label": " | ".join(credential_parts) if credential_parts else "Sin credenciales asignadas",
        "credential_owner_is_current": _is_current_assignment(credentialing_raw, current_user),
        "credential_owner_label": credentialing_label,
        "credential_due_label": credential_due or "Sin meta",
        "office_reviewer_label": office_reviewer_label,
    }


def _render_provider_status_tracks(
    item: dict[str, object],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> str:
    status = _provider_status_summary_values(item, current_user, user_lookup)
    return (
        '<div class="provider-status-grid">'
        '<div class="provider-status-track contract-track">'
        '<div class="provider-status-head">'
        f'<strong>Contratacion: {html.escape(str(status.get("contract_stage_label", "")))}</strong>'
        f'<span>{int(status.get("contract_progress_percent", 0))}%</span>'
        "</div>"
        f'<div class="progress provider-status-progress"><span style="width:{int(status.get("contract_progress_percent", 0))}%"></span></div>'
        f'<small>{html.escape(str(status.get("contract_assignment_label", "")))}</small>'
        + (
            '<span class="provider-owner-flag">Tu supervision</span>'
            if status.get("contract_owner_is_current")
            else ""
        )
        + "</div>"
        + '<div class="provider-status-track credential-track">'
        + '<div class="provider-status-head">'
        f'<strong>Credencializacion: {html.escape(str(status.get("credential_status_label", "")))}</strong>'
        f'<span>{int(status.get("credential_progress_percent", 0))}%</span>'
        "</div>"
        f'<div class="progress provider-status-progress"><span style="width:{int(status.get("credential_progress_percent", 0))}%"></span></div>'
        f'<small>{html.escape(str(status.get("credential_assignment_label", "")))}</small>'
        + (
            '<span class="provider-owner-flag">Tu credencializacion</span>'
            if status.get("credential_owner_is_current")
            else ""
        )
        + "</div>"
        + "</div>"
    )


def _render_provider_document_table_rows(item: dict[str, object], can_approve: bool) -> str:
    documents = item.get("documents", [])
    if not isinstance(documents, list):
        documents = []

    rows = []
    for document in documents:
        file_markup = "-"
        has_uploaded_file = bool(document.get("file_path") or document.get("file_name"))
        if document.get("file_path"):
            file_markup = (
                f'<a href="/provider-document?contract_id={quote(str(item.get("contract_id", "")))}&document_name={quote(str(document.get("document_name", "")))}">'
                f'{html.escape(str(document.get("file_name", "")) or "Abrir archivo")}</a>'
            )
        elif document.get("file_name"):
            file_markup = html.escape(str(document.get("file_name", "")))
        approval_value = str(document.get("approval_status", "") or "").strip().lower()
        approval_markup = _document_status_badge(str(document.get("status", "Pending")))
        submitted_by = str(document.get("submitted_by_name", "") or document.get("submitted_by_username", "")).strip()
        submitted_at = str(document.get("submitted_at", "")).strip()
        approved_by = str(document.get("approved_by_name", "") or document.get("approved_by_username", "")).strip()
        approved_at = str(document.get("approved_at", "")).strip()
        days_until = document.get("days_until_expiration")
        if document.get("is_expired"):
            approval_markup += "<br><small>Este documento ya vencio y necesita reemplazo.</small>"
        elif document.get("expiring_soon") and days_until is not None:
            approval_markup += f"<br><small>Vence en {html.escape(str(days_until))} dia(s).</small>"
        if submitted_by and has_uploaded_file:
            approval_markup += f"<br><small>Subio: {html.escape(submitted_by)}</small>"
        if submitted_at and has_uploaded_file:
            approval_markup += f"<br><small>Fecha: {html.escape(submitted_at)}</small>"
        if approval_value == "pending" and can_approve:
            approval_markup = (
                approval_markup
                + "<br>"
                + "<form class=\"table-action-form\" method=\"post\" action=\"/approve-provider-document\">"
                + f"<input type=\"hidden\" name=\"contract_id\" value=\"{html.escape(str(item.get('contract_id', '')))}\">"
                + f"<input type=\"hidden\" name=\"document_name\" value=\"{html.escape(str(document.get('document_name', '')))}\">"
                + "<button class=\"small-button\" type=\"submit\">Aprobar</button>"
                + "</form>"
            )
        elif approval_value == "approved" and approved_by:
            approval_markup += f"<br><small>Aprobo: {html.escape(approved_by)}</small>"
            if approved_at:
                approval_markup += f"<br><small>Fecha aprobacion: {html.escape(approved_at)}</small>"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(document.get('document_name', '')))}</td>"
            f"<td>{html.escape(str(document.get('issued_date', '')) or '-')}</td>"
            f"<td>{html.escape(str(document.get('expiration_date', '')) or '-')}</td>"
            f"<td>{approval_markup}</td>"
            f"<td>{file_markup}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_single_provider_document_card(
    item: dict[str, object],
    can_approve: bool,
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> str:
    summary = _provider_document_summary_values(item)
    return (
        "<article class=\"panel section-card provider-doc-card\">"
        f"<h2>{html.escape(str(item.get('provider_name', '')))}</h2>"
        f"<p>{html.escape(str(item.get('provider_type', '')))} | {html.escape(str(item.get('site_location', '')) or 'Sin lugar')} | {html.escape(str(item.get('county_name', '')) or 'Sin county')} | Etapa {html.escape(str(item.get('contract_stage', '')))} | {html.escape(str(summary.get('checklist_label', '')))} | {html.escape(str(summary.get('credential_label', '')))}</p>"
        f"{_render_provider_status_tracks(item, current_user, user_lookup)}"
        f"<div class=\"progress large-progress\"><span style=\"width:{int(summary.get('progress_percent', 0))}%\"></span></div>"
        f"<small>{int(summary.get('progress_percent', 0))}% del expediente documental completado</small>"
        "<div class=\"table-wrap\">"
        "<table>"
        "<thead><tr><th>Documento</th><th>Fecha emitido</th><th>Fecha expira</th><th>Estatus</th><th>Archivo</th></tr></thead>"
        f"<tbody>{_render_provider_document_table_rows(item, can_approve)}</tbody>"
        "</table>"
        "</div>"
        "</article>"
    )


def _render_provider_document_cards(
    items: list[dict[str, object]],
    current_user: dict[str, object] | None,
    users: list[dict[str, object]],
) -> str:
    if not items:
        return '<article class="panel section-card"><h2>Expediente documental</h2><p>Todavia no hay providers con checklist documental.</p></article>'

    can_approve = has_permission(current_user, "providers.documents.verify")
    user_lookup = _user_display_lookup(users)
    if len(items) == 1:
        return _render_single_provider_document_card(items[0], can_approve, current_user, user_lookup)

    roster_items = []
    for item in items:
        summary = _provider_document_summary_values(item)
        contract_id = quote(str(item.get("contract_id", "")))
        roster_items.append(
            "<details class=\"panel section-card provider-roster-item\">"
            "<summary>"
            "<span class=\"provider-roster-summary\">"
            f"<span class=\"provider-roster-name\">{html.escape(str(item.get('provider_name', '')))}</span>"
            f"<span class=\"provider-roster-caption\">{html.escape(str(item.get('provider_type', '')))} | {html.escape(str(item.get('site_location', '')) or 'Sin lugar')} | {html.escape(str(item.get('county_name', '')) or 'Sin county')}</span>"
            f"{_render_provider_status_tracks(item, current_user, user_lookup)}"
            "</span>"
            f"<span class=\"provider-roster-pill\">Etapa {html.escape(str(item.get('contract_stage', '')))}</span>"
            f"<span class=\"provider-roster-pill\">{html.escape(str(summary.get('checklist_label', '')))}</span>"
            f"<span class=\"provider-roster-pill\">{html.escape(str(summary.get('credential_label', '')))}</span>"
            f"<span class=\"provider-roster-pill\">{int(summary.get('progress_percent', 0))}% completo</span>"
            "</summary>"
            "<div class=\"provider-roster-body\">"
            "<div class=\"quick-links\">"
            f"<a class=\"quick-link\" href=\"{_page_href('providers')}?edit_contract_id={contract_id}#provider-detail\">Abrir expediente</a>"
            "</div>"
            f"<div class=\"progress large-progress\"><span style=\"width:{int(summary.get('progress_percent', 0))}%\"></span></div>"
            f"<small>{int(summary.get('progress_percent', 0))}% del expediente documental completado</small>"
            "<div class=\"table-wrap\">"
            "<table>"
            "<thead><tr><th>Documento</th><th>Fecha emitido</th><th>Fecha expira</th><th>Estatus</th><th>Archivo</th></tr></thead>"
            f"<tbody>{_render_provider_document_table_rows(item, can_approve)}</tbody>"
            "</table>"
            "</div>"
            "</div>"
            "</details>"
        )
    return (
        "<article class=\"panel section-card provider-doc-card\">"
        "<h2>Roster documental de providers</h2>"
        "<p>Toca el nombre del provider para abrir su expediente documental sin cargar todos los documentos al mismo tiempo.</p>"
        f"<div class=\"provider-roster\">{''.join(roster_items)}</div>"
        "</article>"
    )


def _provider_expediente_href(item: dict[str, object], anchor: str = "provider-detail") -> str:
    contract_id = quote(str(item.get("contract_id", "")).strip())
    return f"/providers?edit_contract_id={contract_id}#{anchor}"


def _new_provider_href(anchor: str = "provider-contract-form") -> str:
    return f"/providers?new_provider=1#{anchor}"


def _provider_card_avatar_markup(item: dict[str, object]) -> str:
    provider_name = str(item.get("provider_name", "")).strip() or str(item.get("provider_type", "")).strip() or "Provider"
    initials = "".join(part[:1].upper() for part in provider_name.split()[:2]) or "PR"
    seed = sum(ord(char) for char in provider_name)
    palette = (
        ("#0d51b8", "#93c5fd"),
        ("#0c9249", "#86efac"),
        ("#b45309", "#fdba74"),
        ("#7c3aed", "#c4b5fd"),
        ("#be123c", "#fda4af"),
        ("#0f766e", "#99f6e4"),
    )
    color_a, color_b = palette[seed % len(palette)]
    return (
        f'<span class="directory-avatar-fallback" style="background:linear-gradient(135deg, {color_a} 0%, {color_b} 100%);">'
        f"{html.escape(initials)}"
        "</span>"
    )


def _provider_directory_status(item: dict[str, object]) -> str:
    return "active" if str(item.get("contract_stage", "")).strip().upper() == "ACTIVE" else "inactive"


def _provider_table_status_badge_markup(item: dict[str, object]) -> str:
    stage = str(item.get("contract_stage", "")).strip().upper()
    if stage == "ACTIVE":
        label = "Active"
        tone = "success"
    elif stage in {"INACTIVE", "TERMINATED"}:
        label = "Inactive"
        tone = "danger"
    else:
        label = "Pending"
        tone = "warn"
    return f'<span class="table-status-badge {html.escape(tone)}">{html.escape(label)}</span>'


def _provider_assignment_summary_markup(
    item: dict[str, object],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> str:
    status = _provider_status_summary_values(item, current_user, user_lookup)
    rows = [
        (
            "Recruiter",
            str(status.get("recruiter_label", "")).strip() or "Sin asignar",
        ),
        (
            "Supervisor",
            str(status.get("supervisor_label", "")).strip() or "Sin asignar",
        ),
        (
            "Credenciales",
            str(status.get("credential_owner_label", "")).strip() or "Sin asignar",
        ),
    ]
    return (
        '<details class="directory-card-detail directory-detail-toggle">'
        '<summary class="directory-detail-summary">'
        '<span class="directory-detail-summary-copy">'
        '<strong class="directory-detail-title">Responsables</strong>'
        '<small>Recruiter, supervisor y credenciales</small>'
        "</span>"
        '<span class="directory-detail-summary-hint">Abrir</span>'
        "</summary>"
        '<div class="directory-detail-body">'
        + "".join(
            '<div class="directory-detail-row">'
            f"<strong>{html.escape(label)}</strong>"
            f"<span>{html.escape(value)}</span>"
            "</div>"
            for label, value in rows
        )
        + "</div>"
        + "</details>"
    )


def _provider_note_preview(value: object, limit: int = 96) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        return ""
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


def _provider_notes_summary_markup(
    item: dict[str, object] | None,
    *,
    title: str = "Notas",
    collapsible: bool = True,
) -> str:
    notes = str((item or {}).get("notes", "")).strip()
    preview = _provider_note_preview(notes) or "Sin notas guardadas."
    note_body = html.escape(notes).replace("\n", "<br>") if notes else "Todavia no hay notas guardadas para este expediente."
    if not collapsible:
        return (
            '<div class="directory-card-detail provider-notes-panel">'
            f'<div class="directory-detail-title">{html.escape(title)}</div>'
            f'<p class="provider-note-body">{note_body}</p>'
            "</div>"
        )
    return (
        '<details class="directory-card-detail directory-detail-toggle provider-notes-toggle">'
        '<summary class="directory-detail-summary">'
        '<span class="directory-detail-summary-copy">'
        f'<strong class="directory-detail-title">{html.escape(title)}</strong>'
        f"<small>{html.escape(preview)}</small>"
        "</span>"
        '<span class="directory-detail-summary-hint">Abrir</span>'
        "</summary>"
        '<div class="directory-detail-body">'
        f'<p class="provider-note-body">{note_body}</p>'
        "</div>"
        "</details>"
    )


def _provider_profile_section_markup(
    title: str,
    description: str,
    body_markup: str,
    *,
    full_width: bool = False,
) -> str:
    section_class = "provider-profile-section provider-profile-section-full" if full_width else "provider-profile-section"
    return (
        f'<section class="{section_class}">'
        '<div class="provider-profile-section-head">'
        f"<h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(description)}</p>"
        "</div>"
        f"{body_markup}"
        "</section>"
    )


def _provider_client_names(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _provider_workflow_status_label(status: str) -> str:
    labels = {
        "complete": "Completo",
        "in_progress": "En curso",
        "blocked": "Bloqueado",
        "upcoming": "Proximo",
        "na": "N/A",
    }
    return labels.get(status, status.replace("_", " ").title())


def _provider_requires_credentialing(item: dict[str, object]) -> bool:
    return str(item.get("worker_category", "")).strip().upper() != "OFFICE"


def _provider_requires_client_assignment(item: dict[str, object]) -> bool:
    return str(item.get("worker_category", "")).strip().upper() != "OFFICE"


def _provider_workflow_summary_values(
    item: dict[str, object],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> dict[str, object]:
    status = _provider_status_summary_values(item, current_user, user_lookup)
    summary = _provider_document_summary_values(item)
    stage_value = str(item.get("contract_stage", "")).strip().upper()
    stage_label = str(status.get("contract_stage_label", "")).strip() or "Pendiente"
    provider_name = str(item.get("provider_name", "")).strip() or "Provider"
    role_label = str(item.get("provider_type", "")).strip() or str(item.get("office_department", "")).strip() or "Sin tipo"
    site_location = str(item.get("site_location", "")).strip()
    county_name = str(item.get("county_name", "")).strip()
    start_date = str(item.get("start_date", "")).strip()
    expected_start_date = str(item.get("expected_start_date", "")).strip()
    recruiter_label = str(status.get("recruiter_label", "")).strip() or "Sin asignar"
    supervisor_label = str(status.get("supervisor_label", "")).strip() or "Sin asignar"
    credential_owner_label = str(status.get("credential_owner_label", "")).strip() or "Sin asignar"
    office_reviewer_label = str(status.get("office_reviewer_label", "")).strip() or "Sin asignar"
    credential_status = str(status.get("credential_status_label", "")).strip() or "Sin credenciales"
    credential_due_label = str(status.get("credential_due_label", "")).strip() or "Sin meta"
    credential_progress_percent = int(status.get("credential_progress_percent", 0) or 0)
    stage_progress_percent = int(status.get("contract_progress_percent", 0) or 0)
    completed_documents = int(summary.get("completed_documents", 0) or 0)
    total_documents = int(summary.get("total_documents", 0) or 0)
    checklist_percent = int(summary.get("progress_percent", 0) or 0)
    expired_documents = int(item.get("expired_documents", 0) or 0)
    client_names = _provider_client_names(item.get("assigned_clients", ""))
    client_count = len(client_names)
    requires_credentialing = _provider_requires_credentialing(item)
    requires_clients = _provider_requires_client_assignment(item)
    has_recruiter = recruiter_label != "Sin asignar"
    has_supervisor = supervisor_label != "Sin asignar"
    has_credential_owner = credential_owner_label != "Sin asignar"
    has_core_profile = bool(provider_name and role_label and site_location and county_name)
    has_schedule_dates = bool(start_date or expected_start_date)
    credential_tokens = [
        token.strip().lower()
        for token in credential_status.replace("|", ",").split(",")
        if token.strip()
    ]
    credential_complete = any(
        token == "active" or token.startswith("enrolled") or token.startswith("completed")
        for token in credential_tokens
    )
    credential_open = any(
        token.startswith("submitted")
        or token.startswith("pending")
        or token.startswith("follow up")
        or token.startswith("rejected")
        for token in credential_tokens
    )
    documents_ready = total_documents > 0 and completed_documents >= total_documents and expired_documents == 0

    steps: list[dict[str, object]] = []

    if has_core_profile and has_recruiter and has_supervisor and has_schedule_dates:
        intake_status = "complete"
        intake_percent = 100
        intake_detail = f"Recruiter {recruiter_label} y supervisor {supervisor_label} ya estan definidos."
        intake_action = "Intake listo para seguir el expediente."
    elif has_core_profile and (has_recruiter or has_supervisor):
        intake_status = "in_progress"
        intake_percent = 68
        intake_detail = f"Falta cerrar responsables o fechas base para {provider_name}."
        if not has_recruiter:
            intake_action = "Asigna recruiter para mover el pipeline."
        elif not has_supervisor:
            intake_action = "Define supervisor para cerrar la contratacion."
        else:
            intake_action = "Completa fechas base y deja el intake listo."
    else:
        intake_status = "blocked"
        intake_percent = 16
        intake_detail = "Falta completar la ficha base del provider, lugar o responsables."
        intake_action = "Completa datos generales y asigna recruiter o supervisor."
    steps.append(
        {
            "key": "intake",
            "title": "Intake",
            "status": intake_status,
            "percent": intake_percent,
            "detail": intake_detail,
            "owner": recruiter_label if has_recruiter else supervisor_label if has_supervisor else "Recursos Humanos",
            "action": intake_action,
        }
    )

    if documents_ready:
        checklist_status = "complete"
        checklist_detail = f"Checklist {completed_documents}/{total_documents} listo y sin expirados."
        checklist_action = "Checklist documental listo para seguir."
        checklist_percent = 100
    elif expired_documents:
        checklist_status = "blocked"
        checklist_detail = f"Hay {expired_documents} documento(s) vencido(s) dentro del expediente."
        checklist_action = "Reemplaza los documentos vencidos antes de avanzar."
        checklist_percent = max(min(checklist_percent, 88), 18)
    elif completed_documents:
        checklist_status = "in_progress"
        checklist_detail = f"Checklist {completed_documents}/{total_documents} aun incompleto."
        checklist_action = "Completa los documentos pendientes del expediente."
        checklist_percent = max(checklist_percent, 32)
    else:
        checklist_status = "blocked"
        checklist_detail = "Todavia no se ha entregado el paquete documental requerido."
        checklist_action = "Empieza la recoleccion documental del provider."
        checklist_percent = 10
    steps.append(
        {
            "key": "checklist",
            "title": "Checklist",
            "status": checklist_status,
            "percent": checklist_percent,
            "detail": checklist_detail,
            "owner": supervisor_label if has_supervisor else recruiter_label if has_recruiter else "Expediente",
            "action": checklist_action,
        }
    )

    if not requires_credentialing:
        credential_status_name = "na"
        credential_percent = 100
        credential_detail = "No aplica para personal de oficina."
        credential_action = "Sin credenciales requeridas para este perfil."
    elif credential_complete:
        credential_status_name = "complete"
        credential_percent = 100
        credential_detail = f"Credenciales cerradas. Meta actual: {credential_due_label}."
        credential_action = "Credencializacion lista."
    elif has_credential_owner or credential_open or str(item.get("credentialing_start_date", "")).strip():
        credential_status_name = "in_progress"
        credential_percent = max(credential_progress_percent, 40)
        credential_detail = f"{credential_status}. Meta: {credential_due_label}."
        if not has_credential_owner:
            credential_action = "Asigna credentialing owner y abre el enrollment."
        elif credential_open:
            credential_action = "Da follow-up al enrollment y documenta la gestion."
        else:
            credential_action = "Inicia submission de credenciales con payer."
    else:
        credential_status_name = "blocked"
        credential_percent = 12
        credential_detail = "Todavia no hay owner ni submission de credenciales."
        credential_action = "Asigna owner de credenciales y crea la primera submission."
    steps.append(
        {
            "key": "credentialing",
            "title": "Credencializacion",
            "status": credential_status_name,
            "percent": credential_percent,
            "detail": credential_detail,
            "owner": credential_owner_label if has_credential_owner else "Credentialing",
            "action": credential_action,
        }
    )

    ready_to_activate = (
        stage_value != "ACTIVE"
        and intake_status == "complete"
        and checklist_status == "complete"
        and credential_status_name in {"complete", "na"}
    )
    active_without_clients = requires_clients and stage_value == "ACTIVE" and client_count == 0

    if not requires_clients:
        client_status = "na"
        client_percent = 100
        client_detail = "No aplica para staff de oficina."
        client_action = "Sin cartera clinica para este perfil."
    elif client_count > 0:
        client_status = "complete"
        client_percent = 100
        client_detail = f"{client_count} cliente(s) vinculados a este expediente."
        client_action = "Carga de casos ya vinculada."
    elif stage_value == "ACTIVE":
        client_status = "blocked"
        client_percent = 24
        client_detail = "El provider esta activo pero todavia no tiene clientes asignados."
        client_action = "Vincula al menos un cliente para empezar a operar."
    elif ready_to_activate or stage_value in {"ONBOARDING", "OFFER_SENT"}:
        client_status = "in_progress"
        client_percent = 56
        client_detail = "Prepara la asignacion de casos y supervision antes de activar."
        client_action = "Amarra el caso ABA y la supervision final."
    else:
        client_status = "upcoming"
        client_percent = 0
        client_detail = "La asignacion de clientes llega cuando cierre intake, docs y credenciales."
        client_action = "Todavia no toca vincular clientes."
    steps.append(
        {
            "key": "caseload",
            "title": "Clientes",
            "status": client_status,
            "percent": client_percent,
            "detail": client_detail,
            "owner": office_reviewer_label if office_reviewer_label != "Sin asignar" else supervisor_label if has_supervisor else "Oficina",
            "action": client_action,
        }
    )

    if stage_value == "ACTIVE":
        activation_status = "complete"
        activation_percent = 100
        activation_detail = f"Expediente activo. Start date: {start_date or expected_start_date or 'Pendiente'}."
        activation_action = "Provider ya esta listo para operar."
    elif ready_to_activate:
        activation_status = "in_progress"
        activation_percent = 94
        activation_detail = "El expediente ya esta listo para moverlo a Active."
        activation_action = "Cambia la etapa a Active y entrega acceso."
    elif stage_value in {"ONBOARDING", "OFFER_SENT"}:
        activation_status = "in_progress"
        activation_percent = max(stage_progress_percent, 72)
        activation_detail = f"Etapa actual: {stage_label}. Falta el cierre final para activar."
        activation_action = "Cierra pendientes finales y prepara acceso."
    else:
        activation_status = "upcoming"
        activation_percent = max(stage_progress_percent, 8)
        activation_detail = f"Etapa actual: {stage_label}. Aun no esta listo para activar."
        activation_action = "Completa docs y credenciales antes de activar."
    steps.append(
        {
            "key": "activation",
            "title": "Activacion",
            "status": activation_status,
            "percent": activation_percent,
            "detail": activation_detail,
            "owner": office_reviewer_label if office_reviewer_label != "Sin asignar" else supervisor_label if has_supervisor else "Administracion",
            "action": activation_action,
        }
    )

    relevant_steps = [step for step in steps if str(step.get("status", "")) != "na"]
    total_steps = len(relevant_steps)
    complete_steps = sum(1 for step in relevant_steps if str(step.get("status", "")) == "complete")
    blocker_count = sum(1 for step in relevant_steps if str(step.get("status", "")) == "blocked")
    overall_percent = int(round(sum(int(step.get("percent", 0) or 0) for step in relevant_steps) / total_steps)) if total_steps else 100
    next_step = next(
        (
            step
            for step in relevant_steps
            if str(step.get("status", "")) in {"blocked", "in_progress", "upcoming"}
        ),
        None,
    )
    if next_step is None and relevant_steps:
        next_step = relevant_steps[-1]

    if active_without_clients:
        queue_key = "active_without_clients"
    elif ready_to_activate:
        queue_key = "ready_to_activate"
    elif intake_status != "complete":
        queue_key = "intake"
    elif checklist_status != "complete":
        queue_key = "documents"
    elif credential_status_name not in {"complete", "na"}:
        queue_key = "credentialing"
    elif client_status not in {"complete", "na"}:
        queue_key = "caseload"
    else:
        queue_key = "stable"

    if stage_value == "ACTIVE" and blocker_count == 0:
        status_label = "Activo"
    elif ready_to_activate:
        status_label = "Listo para activar"
    elif blocker_count:
        status_label = "Bloqueado"
    elif complete_steps:
        status_label = "En progreso"
    else:
        status_label = "Nuevo"

    return {
        "status_label": status_label,
        "overall_percent": overall_percent,
        "complete_steps": complete_steps,
        "total_steps": total_steps,
        "blocker_count": blocker_count,
        "next_step_title": str((next_step or {}).get("title", "")) or "Workflow completo",
        "next_action": str((next_step or {}).get("action", "")) or "Sin pendientes mayores.",
        "next_owner": str((next_step or {}).get("owner", "")) or "Sin owner",
        "queue_key": queue_key,
        "ready_to_activate": ready_to_activate,
        "active_without_clients": active_without_clients,
        "steps": steps,
    }


def _provider_workflow_card_hint_markup(
    item: dict[str, object],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> str:
    workflow = _provider_workflow_summary_values(item, current_user, user_lookup)
    blocker_note = (
        f"{int(workflow.get('blocker_count', 0) or 0)} bloqueo(s)"
        if int(workflow.get("blocker_count", 0) or 0)
        else "Sin bloqueos criticos"
    )
    return (
        '<div class="directory-card-detail provider-workflow-hint">'
        '<div class="directory-detail-title">Workflow</div>'
        '<div class="directory-detail-row">'
        f"<strong>{html.escape(str(workflow.get('next_step_title', '')))}</strong>"
        f"<span>{html.escape(str(workflow.get('status_label', '')))}</span>"
        "</div>"
        f'<p class="helper-note compact-note">{html.escape(str(workflow.get("next_action", "")))}</p>'
        f'<p class="helper-note compact-note">{html.escape(blocker_note)}</p>'
        "</div>"
    )


def _provider_workflow_queue_counts(
    items: list[dict[str, object]],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> dict[str, int]:
    counts = {
        "intake": 0,
        "documents": 0,
        "credentialing": 0,
        "ready_to_activate": 0,
        "active_without_clients": 0,
    }
    for item in items:
        queue_key = str(_provider_workflow_summary_values(item, current_user, user_lookup).get("queue_key", "")).strip()
        if queue_key in counts:
            counts[queue_key] += 1
    return counts


def _hr_workflow_tone(value: str) -> str:
    clean = str(value or "").strip().lower()
    if "activo" in clean or clean == "complete":
        return "success"
    if "bloqueado" in clean or clean == "blocked":
        return "danger"
    if "listo" in clean or "ready" in clean:
        return "warm"
    return "neutral"


def _hr_priority_provider_items(
    items: list[dict[str, object]],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
    *,
    limit: int = 6,
) -> list[dict[str, object]]:
    queue_order = {
        "ready_to_activate": 0,
        "documents": 1,
        "credentialing": 2,
        "intake": 3,
        "active_without_clients": 4,
        "caseload": 5,
        "stable": 6,
    }
    decorated: list[tuple[tuple[int, int, int, str], dict[str, object]]] = []
    for item in items:
        workflow = _provider_workflow_summary_values(item, current_user, user_lookup)
        queue_key = str(workflow.get("queue_key", "")).strip() or "stable"
        if queue_key == "stable" and not bool(workflow.get("active_without_clients", False)):
            continue
        blocker_count = int(workflow.get("blocker_count", 0) or 0)
        overall_percent = int(workflow.get("overall_percent", 0) or 0)
        provider_name = str(item.get("provider_name", "")).strip().lower()
        decorated.append(
            (
                (
                    queue_order.get(queue_key, 9),
                    -blocker_count,
                    overall_percent,
                    provider_name,
                ),
                item,
            )
        )
    decorated.sort(key=lambda pair: pair[0])
    return [item for _, item in decorated[:limit]]


def _render_hr_candidate_cards(
    items: list[dict[str, object]],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> str:
    priority_items = _hr_priority_provider_items(items, current_user, user_lookup)
    if not priority_items:
        return '<p class="helper-note">No hay expedientes de recruiting u onboarding abiertos ahora mismo.</p>'

    cards = []
    for item in priority_items:
        workflow = _provider_workflow_summary_values(item, current_user, user_lookup)
        status = _provider_status_summary_values(item, current_user, user_lookup)
        summary = _provider_document_summary_values(item)
        contract_id = str(item.get("contract_id", "")).strip()
        provider_name = str(item.get("provider_name", "")).strip() or "Provider"
        role_label = str(item.get("provider_type", "")).strip() or str(item.get("office_department", "")).strip() or "Sin tipo"
        stage_label = str(status.get("contract_stage_label", "")).strip() or "Pendiente"
        overall_percent = int(workflow.get("overall_percent", 0) or 0)
        next_step = str(workflow.get("next_step_title", "")).strip() or "Workflow completo"
        next_action = str(workflow.get("next_action", "")).strip() or "Sin pendientes mayores."
        next_owner = str(workflow.get("next_owner", "")).strip() or "Sin owner"
        recruiter_label = str(status.get("recruiter_label", "")).strip() or "Sin asignar"
        credential_owner_label = str(status.get("credential_owner_label", "")).strip() or "Sin asignar"
        credential_status = str(status.get("credential_status_label", "")).strip() or "Sin credenciales"
        county_name = str(item.get("county_name", "")).strip() or "Sin county"
        site_location = str(item.get("site_location", "")).strip() or "Sin lugar"
        client_count = len(_provider_client_names(item.get("assigned_clients", "")))
        checklist_label = f"{int(summary.get('completed_documents', 0) or 0)}/{int(summary.get('total_documents', 0) or 0)} docs"
        focus_href = f"/hr?edit_contract_id={quote(contract_id)}#hr-focus" if contract_id else "#hr-focus"
        provider_href = _provider_expediente_href(item, "provider-detail")
        cards.append(
            '<article class="panel section-card hr-flow-card">'
            '<div class="hr-flow-head">'
            '<div class="page-title-stack">'
            '<span class="eyebrow">Provider candidate</span>'
            f"<h3>{html.escape(provider_name)}</h3>"
            f"<p>{html.escape(role_label)} | {html.escape(site_location)} | {html.escape(county_name)}</p>"
            "</div>"
            f'<span class="profile-pill {_hr_workflow_tone(str(workflow.get("status_label", "")))}">{html.escape(str(workflow.get("status_label", "")))}</span>'
            "</div>"
            '<div class="profile-pill-row">'
            f'<span class="profile-pill neutral">{html.escape(stage_label)}</span>'
            f'<span class="profile-pill neutral">{html.escape(checklist_label)}</span>'
            f'<span class="profile-pill neutral">{html.escape(credential_status)}</span>'
            f'<span class="profile-pill neutral">{client_count} cliente(s)</span>'
            "</div>"
            '<div class="mini-table">'
            f'<div class="mini-row"><strong>Siguiente etapa</strong><span>{html.escape(next_step)}</span></div>'
            f'<div class="mini-row"><strong>Owner sugerido</strong><span>{html.escape(next_owner)}</span></div>'
            f'<div class="mini-row"><strong>Recruiter</strong><span>{html.escape(recruiter_label)}</span></div>'
            f'<div class="mini-row"><strong>Credenciales</strong><span>{html.escape(credential_owner_label)}</span></div>'
            "</div>"
            f'<p class="helper-note">{html.escape(next_action)}</p>'
            '<div class="hr-progress-row">'
            "<span>Workflow</span>"
            f'<div class="progress"><span style="width:{overall_percent}%"></span></div>'
            f"<small>{overall_percent}%</small>"
            "</div>"
            '<div class="hr-flow-actions">'
            f'<a class="small-button" href="{html.escape(focus_href)}">Ver en HR</a>'
            f'<a class="small-button" href="{html.escape(provider_href)}">Expediente</a>'
            "</div>"
            "</article>"
        )
    return '<div class="hr-flow-grid">' + "".join(cards) + "</div>"


def _render_hr_office_cards(items: list[dict[str, object]], *, limit: int = 4) -> str:
    office_items = [
        item
        for item in items
        if not is_provider_role(item)
    ][:limit]
    if not office_items:
        return '<p class="helper-note">Todavia no hay personal administrativo guardado.</p>'

    cards = []
    for item in office_items:
        full_name = str(item.get("full_name", "")).strip() or str(item.get("username", "")).strip() or "Empleado"
        role_label = ROLE_LABELS.get(str(item.get("role", "")).upper(), str(item.get("role", "")).title() or "Usuario")
        job_title = str(item.get("job_title", "")).strip() or "Sin puesto"
        site_location = str(item.get("site_location", "")).strip() or "Sin lugar"
        county_name = str(item.get("county_name", "")).strip() or "Sin county"
        linked_provider_name = str(item.get("linked_provider_name", "")).strip()
        mfa_enabled = bool(item.get("mfa_enabled", False))
        mfa_label = "MFA activo" if mfa_enabled else "MFA pendiente"
        mfa_tone = "success" if mfa_enabled else "warm"
        linked_provider_markup = (
            f'<span class="profile-pill neutral">{html.escape(linked_provider_name)}</span>'
            if linked_provider_name
            else ""
        )
        cards.append(
            '<article class="panel section-card hr-flow-card">'
            '<div class="hr-flow-head">'
            '<div class="page-title-stack">'
            '<span class="eyebrow">Office team</span>'
            f"<h3>{html.escape(full_name)}</h3>"
            f"<p>{html.escape(job_title)} | {html.escape(site_location)} | {html.escape(county_name)}</p>"
            "</div>"
            f'<span class="profile-pill neutral">{html.escape(role_label)}</span>'
            "</div>"
            '<div class="profile-pill-row">'
            f'<span class="profile-pill {mfa_tone}">{html.escape(mfa_label)}</span>'
            f"{linked_provider_markup}"
            "</div>"
            '<div class="mini-table">'
            f'<div class="mini-row"><strong>Username</strong><span>{html.escape(str(item.get("username", "")))}</span></div>'
            f'<div class="mini-row"><strong>Email</strong><span>{html.escape(str(item.get("email", "")) or "Pendiente")}</span></div>'
            f'<div class="mini-row"><strong>Telefono</strong><span>{html.escape(str(item.get("phone", "")) or "Pendiente")}</span></div>'
            f'<div class="mini-row"><strong>Activo</strong><span>{"Si" if item.get("active", True) else "No"}</span></div>'
            "</div>"
            f'<a class="small-button" href="{html.escape(_page_href("users"))}#users-directory">Abrir users</a>'
            "</article>"
        )
    return '<div class="hr-flow-grid">' + "".join(cards) + "</div>"


def _provider_recent_activity_markup(items: list[dict[str, object]]) -> str:
    if not items:
        return (
            '<div class="provider-activity-card">'
            '<div class="directory-detail-title">Actividad reciente</div>'
            '<p class="helper-note">Todavia no hay eventos auditados para este expediente.</p>'
            "</div>"
        )
    rows = []
    for item in items[:4]:
        rows.append(
            '<div class="provider-activity-row">'
            f"<strong>{html.escape(str(item.get('action', '')))}</strong>"
            f"<span>{html.escape(str(item.get('created_at', '')))}</span>"
            f"<small>{html.escape(str(item.get('details', '')))}</small>"
            "</div>"
        )
    return (
        '<div class="provider-activity-card">'
        '<div class="directory-detail-title">Actividad reciente</div>'
        f"{''.join(rows)}"
        "</div>"
    )


def _provider_workflow_overview_markup(
    item: dict[str, object],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> str:
    workflow = _provider_workflow_summary_values(item, current_user, user_lookup)
    steps = workflow.get("steps", [])
    if not isinstance(steps, list):
        steps = []
    step_cards = []
    for step in steps:
        step_status = str(step.get("status", "upcoming"))
        if step_status == "complete":
            pill_tone = "success"
        elif step_status == "blocked":
            pill_tone = "danger"
        elif step_status in ("upcoming", "na"):
            pill_tone = "neutral"
        else:
            pill_tone = "warn"
        step_cards.append(
            '<article class="workflow-step-card workflow-step-'
            + html.escape(step_status)
            + '">'
            '<div class="workflow-step-head">'
            f"<strong>{html.escape(str(step.get('title', 'Etapa')))}</strong>"
            f'<span class="profile-pill {pill_tone}">{html.escape(_provider_workflow_status_label(step_status))}</span>'
            "</div>"
            '<div class="progress provider-status-progress">'
            f'<span style="{_progress_fill_style(step.get("percent", 0))}"></span>'
            "</div>"
            f'<p>{html.escape(str(step.get("detail", "")))}</p>'
            f'<small>Owner: {html.escape(str(step.get("owner", "")))}</small>'
            f'<div class="workflow-step-action">{html.escape(str(step.get("action", "")))}</div>'
            "</article>"
        )
    blocker_copy = (
        f"{int(workflow.get('blocker_count', 0) or 0)} bloqueo(s) abiertos"
        if int(workflow.get("blocker_count", 0) or 0)
        else "Sin bloqueos criticos"
    )
    return (
        '<section class="provider-workflow-card">'
        '<div class="provider-workflow-head">'
        '<div class="provider-workflow-copy">'
        '<span class="eyebrow">Provider Workflow</span>'
        f"<h3>{int(workflow.get('complete_steps', 0) or 0)}/{int(workflow.get('total_steps', 0) or 0)} etapas cerradas</h3>"
        f'<p>Siguiente movimiento: {html.escape(str(workflow.get("next_action", "")))}</p>'
        "</div>"
        '<div class="provider-workflow-score">'
        f"<strong>{int(workflow.get('overall_percent', 0) or 0)}%</strong>"
        f"<span>{html.escape(str(workflow.get('status_label', '')))}</span>"
        f"<small>{html.escape(blocker_copy)}</small>"
        "</div>"
        "</div>"
        '<div class="progress provider-status-progress provider-workflow-progress">'
        f'<span style="{_progress_fill_style(workflow.get("overall_percent", 0))}"></span>'
        "</div>"
        '<div class="provider-workflow-meta">'
        f'<div class="mini-row"><strong>Siguiente etapa</strong><span>{html.escape(str(workflow.get("next_step_title", "")))}</span></div>'
        f'<div class="mini-row"><strong>Owner sugerido</strong><span>{html.escape(str(workflow.get("next_owner", "")))}</span></div>'
        "</div>"
        '<div class="provider-workflow-grid">'
        f"{''.join(step_cards)}"
        "</div>"
        "</section>"
    )


def _render_provider_profile_panel(
    item: dict[str, object] | None,
    current_user: dict[str, object] | None,
    users: list[dict[str, object]],
    can_manage: bool,
    user_role: str,
    recent_activity: list[dict[str, object]] | None = None,
    shared_sessions: list[dict[str, object]] | None = None,
) -> str:
    if item is None:
        return (
            '<section class="provider-profile-shell">'
            '<article id="provider-detail" class="panel section-card provider-profile-card">'
            '<span class="eyebrow">Provider Profile</span>'
            '<div class="page-title-stack">'
            '<h2>Selecciona un provider</h2>'
            '<p>Abre cualquier tarjeta del directorio para convertir esta zona en el expediente central del provider.</p>'
            "</div>"
            '<div class="mini-table">'
            '<div class="mini-row"><strong>Primero</strong><span>Busca o filtra en el directorio superior.</span></div>'
            '<div class="mini-row"><strong>Despues</strong><span>Haz clic en Abrir expediente para cargar el perfil.</span></div>'
            '<div class="mini-row"><strong>Aqui veras</strong><span>Contratacion, checklist, credenciales, clientes y notas.</span></div>'
            "</div>"
            "</article>"
            '<aside class="panel section-card provider-profile-side">'
            '<span class="eyebrow">Lo Primero</span>'
            "<h2>Que mirar antes de editar</h2>"
            '<div class="attention-list">'
            '<div class="attention-item"><strong>Checklist</strong><span>Docs</span><small>Revisa si el expediente viene incompleto o vencido.</small></div>'
            '<div class="attention-item"><strong>Credenciales</strong><span>Due</span><small>Confirma la meta de 90 dias y el payer enrollment.</small></div>'
            '<div class="attention-item"><strong>Clientes</strong><span>ABA</span><small>Valida si ya esta vinculado al caso correcto.</small></div>'
            "</div>"
            "</aside>"
            "</section>"
        )

    user_lookup = _user_display_lookup(users)
    status = _provider_status_summary_values(item, current_user, user_lookup)
    summary = _provider_document_summary_values(item)
    provider_name = str(item.get("provider_name", "")).strip() or "Provider"
    worker_category = str(item.get("worker_category", "")).strip().upper() or "PROVIDER"
    provider_type = str(item.get("provider_type", "")).strip()
    office_department = str(item.get("office_department", "")).strip()
    role_label = provider_type or office_department or worker_category.title()
    can_view_provider_credentials = has_permission(current_user, "providers.credentials.view")
    can_view_provider_documents = (
        has_permission(current_user, "providers.documents.view")
        or has_permission(current_user, "providers.documents.verify")
        or can_manage
    )
    provider_npi = str(item.get("provider_npi", "")).strip() or "Pendiente"
    medicaid_id = str(item.get("medicaid_id", "")).strip() or "Pendiente"
    site_location = str(item.get("site_location", "")).strip() or "Sin lugar"
    county_name = str(item.get("county_name", "")).strip() or "Sin county"
    contract_id = str(item.get("contract_id", "")).strip() or "Pendiente"
    start_date = str(item.get("start_date", "")).strip() or "Pendiente"
    expected_start_date = str(item.get("expected_start_date", "")).strip() or "Pendiente"
    credentialing_start_date = str(item.get("credentialing_start_date", "")).strip() or "Pendiente"
    recruiter_label = str(status.get("recruiter_label", "")).strip() or "Sin asignar"
    supervisor_label = str(status.get("supervisor_label", "")).strip() or "Sin asignar"
    credential_owner_label = str(status.get("credential_owner_label", "")).strip() or "Sin asignar"
    office_reviewer_label = str(status.get("office_reviewer_label", "")).strip() or "Sin asignar"
    stage_label = str(status.get("contract_stage_label", "")).strip() or "Pendiente"
    credential_status = str(status.get("credential_status_label", "")).strip() or "Sin credenciales"
    credential_due_label = str(status.get("credential_due_label", "")).strip() or "Sin meta"
    profile_progress = 100 if _provider_directory_status(item) == "active" else int(item.get("stage_progress_percent", 0) or 0)
    client_names = _provider_client_names(item.get("assigned_clients", ""))
    client_count = len(client_names)
    expired_documents = int(item.get("expired_documents", 0) or 0)
    expiring_documents = int(item.get("expiring_documents", 0) or 0)
    credential_days_remaining = None
    try:
        credential_days_remaining = int(item.get("credentialing_days_remaining", 0) or 0)
    except (TypeError, ValueError):
        credential_days_remaining = None

    stage_tone = "success" if _provider_directory_status(item) == "active" else "warn"
    credential_tone = "neutral"
    clean_credential_status = credential_status.lower()
    if clean_credential_status in {"enrolled", "active", "completed"}:
        credential_tone = "success"
    elif clean_credential_status in {"pending", "submitted", "follow up"}:
        credential_tone = "warn"
    elif clean_credential_status in {"sin credenciales", "rejected"}:
        credential_tone = "danger"

    client_markup = (
        '<div class="profile-pill-row">'
        + "".join(f'<span class="profile-pill neutral">{html.escape(name)}</span>' for name in client_names[:6])
        + "</div>"
    ) if client_names else '<p class="helper-note">Todavia no hay clientes vinculados a este provider.</p>'
    extra_clients_markup = (
        f'<p class="helper-note compact-note">+{client_count - 6} cliente(s) adicionales.</p>'
        if client_count > 6
        else ""
    )

    attention_items: list[str] = []
    if expired_documents:
        attention_items.append(
            f'<div class="attention-item"><strong>Documentos vencidos</strong><span>{expired_documents}</span><small>Reemplaza esos archivos antes de activar o mantener el expediente.</small></div>'
        )
    elif expiring_documents:
        attention_items.append(
            f'<div class="attention-item"><strong>Documentos por vencer</strong><span>{expiring_documents}</span><small>Renueva esos documentos antes de que el sistema los marque como expirados.</small></div>'
        )
    if credential_days_remaining is not None and 0 <= credential_days_remaining <= 30:
        attention_items.append(
            f'<div class="attention-item"><strong>Credenciales cerca</strong><span>{credential_days_remaining} dias</span><small>Meta actual: {html.escape(credential_due_label)}.</small></div>'
        )
    elif clean_credential_status in {"pending", "submitted", "follow up"}:
        attention_items.append(
            f'<div class="attention-item"><strong>Credencializacion abierta</strong><span>{html.escape(credential_status)}</span><small>A cargo de {html.escape(credential_owner_label)}.</small></div>'
        )
    if not client_names:
        attention_items.append(
            '<div class="attention-item"><strong>Sin clientes</strong><span>0</span><small>Este expediente todavia no tiene casos ABA vinculados.</small></div>'
        )
    if not attention_items:
        attention_items.append(
            '<div class="attention-item"><strong>Sin alertas mayores</strong><span>OK</span><small>El expediente tiene una lectura clara y sin pendientes criticos inmediatos.</small></div>'
        )

    document_state = "Completo" if bool(item.get("documents_complete")) and not expired_documents else "Pendiente"
    document_tone = "success" if document_state == "Completo" else "warn"
    compliance_state = "Ready for assignment" if _provider_directory_status(item) == "active" else "Needs review"
    compliance_tone = "success" if compliance_state == "Ready for assignment" else "warn"

    actions = [
        f'<a class="small-button" href="{html.escape(_provider_expediente_href(item, "providers-directory"))}">Volver al directorio</a>',
        _ai_action_form_markup(
            "check_missing_provider_documents",
            "Check Missing Docs",
            return_page="providers",
            hidden_fields={"contract_id": contract_id},
        ),
    ]
    if can_view_provider_documents:
        actions.insert(
            1,
            f'<a class="small-button" href="{html.escape(_provider_expediente_href(item, "provider-documents"))}">Ver documentos</a>',
        )
    if can_manage:
        actions.insert(
            0,
            f'<a class="small-button" href="{html.escape(_provider_expediente_href(item, "provider-contract-form"))}">Editar completo</a>',
        )
        actions.insert(
            1,
            f'<a class="small-button" href="{html.escape(_provider_expediente_href(item, "provider-upload"))}">Subir documento</a>',
        )
    elif is_provider_role({"role": user_role, "linked_provider_type": str(item.get("provider_type", ""))}):
        actions.insert(
            0,
            f'<a class="small-button" href="{html.escape(_provider_expediente_href(item, "provider-self-upload"))}">Subir mi documento</a>',
        )

    personal_information_markup = _provider_profile_section_markup(
        "Personal Information",
        "Core profile identity, internal ownership, and where this provider operates.",
        (
            '<div class="mini-table">'
            f'<div class="mini-row"><strong>Full name</strong><span>{html.escape(provider_name)}</span></div>'
            f'<div class="mini-row"><strong>Worker category</strong><span>{html.escape(worker_category.title())}</span></div>'
            f'<div class="mini-row"><strong>Site location</strong><span>{html.escape(site_location)}</span></div>'
            f'<div class="mini-row"><strong>County</strong><span>{html.escape(county_name)}</span></div>'
            f'<div class="mini-row"><strong>Recruiter</strong><span>{html.escape(recruiter_label)}</span></div>'
            f'<div class="mini-row"><strong>Supervisor</strong><span>{html.escape(supervisor_label)}</span></div>'
            "</div>"
            '<div class="provider-clients">'
            '<div class="directory-detail-title">Assigned Clients</div>'
            f"{client_markup}"
            f"{extra_clients_markup}"
            "</div>"
        ),
    )
    professional_information_markup = _provider_profile_section_markup(
        "Professional Information",
        "Operational identifiers, role data, and credentialing ownership used across onboarding and billing.",
        (
            '<div class="mini-table">'
            f'<div class="mini-row"><strong>Role</strong><span>{html.escape(role_label)}</span></div>'
            f'<div class="mini-row"><strong>Contract ID</strong><span>{html.escape(contract_id)}</span></div>'
            f'<div class="mini-row"><strong>NPI</strong><span>{html.escape(provider_npi if can_view_provider_credentials else "Restricted")}</span></div>'
            f'<div class="mini-row"><strong>Medicaid ID</strong><span>{html.escape(medicaid_id if can_view_provider_credentials else "Restricted")}</span></div>'
            f'<div class="mini-row"><strong>Credential owner</strong><span>{html.escape(credential_owner_label if can_view_provider_credentials else "Restricted")}</span></div>'
            f'<div class="mini-row"><strong>Office review</strong><span>{html.escape(office_reviewer_label)}</span></div>'
            f'<div class="mini-row"><strong>Start date</strong><span>{html.escape(start_date)}</span></div>'
            f'<div class="mini-row"><strong>Expected start</strong><span>{html.escape(expected_start_date)}</span></div>'
            f'<div class="mini-row"><strong>Credentialing start</strong><span>{html.escape(credentialing_start_date if can_view_provider_credentials else "Restricted")}</span></div>'
            f'<div class="mini-row"><strong>Credential due</strong><span>{html.escape(credential_due_label if can_view_provider_credentials else "Restricted")}</span></div>'
            "</div>"
        ),
    )
    profile_status_markup = _provider_profile_section_markup(
        "Profile Status",
        "Read the current stage, readiness, and assignment blockers before making changes.",
        (
            '<div class="profile-pill-row">'
            f'<span class="profile-pill {stage_tone}">{html.escape(stage_label)}</span>'
            f'<span class="profile-pill {credential_tone}">{html.escape(credential_status)}</span>'
            f'<span class="profile-pill {document_tone}">{html.escape(document_state)}</span>'
            f'<span class="profile-pill {compliance_tone}">{html.escape(compliance_state)}</span>'
            "</div>"
            f"{_render_provider_status_tracks(item, current_user, user_lookup)}"
            '<div class="provider-stat-grid">'
            '<div class="provider-stat-box">'
            '<span>Contratacion</span>'
            f"<strong>{profile_progress}%</strong>"
            f"<small>{html.escape(stage_label)}</small>"
            "</div>"
            '<div class="provider-stat-box">'
            '<span>Checklist</span>'
            f"<strong>{html.escape(str(summary.get('completed_documents', 0)))}/{html.escape(str(summary.get('total_documents', 0)))}</strong>"
            f"<small>{html.escape(str(summary.get('checklist_label', '')))}</small>"
            "</div>"
            '<div class="provider-stat-box">'
            '<span>Credenciales</span>'
            f"<strong>{html.escape(str(credential_days_remaining) + ' dias' if credential_days_remaining is not None else credential_due_label)}</strong>"
            f"<small>{html.escape(credential_due_label)}</small>"
            "</div>"
            '<div class="provider-stat-box">'
            '<span>Clientes</span>'
            f"<strong>{client_count}</strong>"
            f"<small>{html.escape('Casos vinculados' if client_count else 'Sin casos vinculados')}</small>"
            "</div>"
            "</div>"
        ),
        full_width=True,
    )
    documents_markup = _provider_profile_section_markup(
        "Documents",
        "Required documents, expirations, and quick actions for the expediente stay together here.",
        (
            (
                '<div class="mini-table">'
                f'<div class="mini-row"><strong>Checklist</strong><span>{html.escape(str(summary.get("checklist_label", "")))}</span></div>'
                f'<div class="mini-row"><strong>Credenciales</strong><span>{html.escape(str(summary.get("credential_label", "")))}</span></div>'
                f'<div class="mini-row"><strong>Expired</strong><span>{expired_documents}</span></div>'
                f'<div class="mini-row"><strong>Expiring soon</strong><span>{expiring_documents}</span></div>'
                f'<div class="mini-row"><strong>Document state</strong><span>{html.escape(document_state)}</span></div>'
                "</div>"
                if can_view_provider_documents
                else '<p class="helper-note">Document details are restricted for your role.</p>'
            )
            + '<div class="directory-card-actions directory-card-actions-left provider-profile-top-actions">'
            + f"{''.join(actions)}"
            + "</div>"
        ),
    )
    compliance_markup = _provider_profile_section_markup(
        "Compliance",
        "Track onboarding, credentialing, checklist completion, and next actions from one workflow view.",
        (
            _provider_workflow_overview_markup(item, current_user, user_lookup)
            if can_view_provider_credentials or has_permission(current_user, "providers.documents.verify")
            else '<p class="helper-note">Credentialing and compliance details are restricted for your role.</p>'
        ),
        full_width=True,
    )
    notes_markup = _provider_profile_section_markup(
        "Notes",
        "Keep internal follow-up, recruiting context, and administrative comments visible without losing the workflow.",
        _provider_notes_summary_markup(item, title="Notes", collapsible=False),
        full_width=True,
    )
    shared_calendar_markup = _provider_profile_section_markup(
        "Shared Calendar",
        "The provider works from the client's live calendar. Each appointment reads the client's authorization, uses the CPT tied to the provider role, captures caregiver signature, feeds the note, and later rolls into the claim batch.",
        (
            '<div class="mini-table">'
            f'<div class="mini-row"><strong>Upcoming sessions</strong><span>{len(shared_sessions or [])}</span></div>'
            f'<div class="mini-row"><strong>Assigned clients</strong><span>{client_count}</span></div>'
            f'<div class="mini-row"><strong>Next DOS</strong><span>{html.escape(str((shared_sessions or [{}])[0].get("service_date", "")) if shared_sessions else "Sin agenda")}</span></div>'
            f'<div class="mini-row"><strong>Signed appointments</strong><span>{sum(1 for session in (shared_sessions or []) if session.get("caregiver_signature_present"))}</span></div>'
            "</div>"
            '<p class="helper-note compact-note">La autorizacion del cliente marca cuantas units se pueden gastar. El appointment captura la firma del caregiver, genera la nota y luego empuja el service log y el claim batch 837 por payer.</p>'
            '<div class="table-wrap"><table><thead><tr><th>DOS</th><th>Cliente</th><th>Auth / CPT</th><th>Caregiver</th><th>Session</th><th>Note</th><th>Billing</th><th>Claim</th><th>Acciones</th></tr></thead><tbody>'
            f"{_render_provider_shared_session_rows(shared_sessions or [])}"
            "</tbody></table></div>"
        ),
        full_width=True,
    )

    return (
        '<section class="provider-profile-shell">'
        '<article id="provider-detail" class="panel section-card provider-profile-card">'
        '<div class="profile-header">'
        f'<div class="directory-avatar profile-avatar">{_provider_card_avatar_markup(item)}</div>'
        '<div class="provider-profile-copy">'
        '<span class="eyebrow">Provider Profile</span>'
        f"<h2>{html.escape(provider_name)}</h2>"
        f"<p>{html.escape(role_label)} | {html.escape(site_location)} | {html.escape(county_name)}</p>"
        "</div>"
        "</div>"
        '<div class="provider-profile-sections">'
        f"{personal_information_markup}"
        f"{professional_information_markup}"
        f"{profile_status_markup}"
        f"{shared_calendar_markup}"
        f"{documents_markup}"
        f"{compliance_markup}"
        f"{notes_markup}"
        "</div>"
        "</article>"
        '<aside class="panel section-card provider-profile-side">'
        '<span class="eyebrow">Atencion</span>'
        '<h2>Lo primero que debes mirar</h2>'
        '<div class="attention-list">'
        f"{''.join(attention_items)}"
        "</div>"
        f"{_provider_recent_activity_markup(recent_activity or [])}"
        "</aside>"
        "</section>"
    )


def _provider_progress_summary_markup(
    item: dict[str, object],
    current_user: dict[str, object] | None,
    user_lookup: dict[str, str],
) -> str:
    summary = _provider_document_summary_values(item)
    status = _provider_status_summary_values(item, current_user, user_lookup)
    client_names = _provider_client_names(item.get("assigned_clients", ""))
    assigned_clients = ", ".join(client_names[:2]) if client_names else "Sin clientes asignados"
    if len(client_names) > 2:
        assigned_clients += f" +{len(client_names) - 2}"
    provider_active = _provider_directory_status(item) == "active"
    profile_percent = 100 if provider_active else int(item.get("stage_progress_percent", 0) or 15)
    stage_label = str(status.get("contract_stage_label", "")) or _contract_stage_label(str(item.get("contract_stage", "")))
    credential_status = str(status.get("credential_status_label", "")).strip() or "Sin credenciales"
    provider_type = str(item.get("provider_type", "")).strip() or "Sin tipo"
    worker_category = str(item.get("worker_category", "")).strip().title() or "Provider"
    site_location = str(item.get("site_location", "")).strip() or "Sin lugar"
    county_name = str(item.get("county_name", "")).strip() or "Sin county"
    can_view_provider_credentials = has_permission(current_user, "providers.credentials.view")
    provider_npi = str(item.get("provider_npi", "")).strip() or "Pendiente"
    medicaid_id = str(item.get("medicaid_id", "")).strip() or "Pendiente"
    checklist_percent = int(summary.get("progress_percent", 0) or 0)
    credential_percent = int(status.get("credential_progress_percent", 0) or 0)
    return (
        '<div class="directory-card-snapshot provider-summary-sections">'
        '<section class="provider-summary-block">'
        '<strong class="provider-summary-title">Personal</strong>'
        '<div class="provider-summary-list">'
        f'<div class="provider-summary-item"><strong>Location</strong><span>{html.escape(site_location)}</span></div>'
        f'<div class="provider-summary-item"><strong>County</strong><span>{html.escape(county_name)}</span></div>'
        f'<div class="provider-summary-item"><strong>Clients</strong><span>{html.escape(assigned_clients)}</span></div>'
        "</div>"
        "</section>"
        '<section class="provider-summary-block">'
        '<strong class="provider-summary-title">Professional</strong>'
        '<div class="provider-summary-list">'
        f'<div class="provider-summary-item"><strong>Role</strong><span>{html.escape(provider_type)}</span></div>'
        f'<div class="provider-summary-item"><strong>Category</strong><span>{html.escape(worker_category)}</span></div>'
        f'<div class="provider-summary-item"><strong>NPI</strong><span>{html.escape(provider_npi if can_view_provider_credentials else "Restricted")}</span></div>'
        f'<div class="provider-summary-item"><strong>Medicaid</strong><span>{html.escape(medicaid_id if can_view_provider_credentials else "Restricted")}</span></div>'
        "</div>"
        "</section>"
        '<section class="provider-summary-block">'
        '<strong class="provider-summary-title">Profile Status</strong>'
        '<div class="provider-summary-progress">'
        '<div class="provider-summary-progress-row">'
        '<div class="provider-summary-progress-head">'
        '<strong>Expediente</strong>'
        f'<span>{"Activo" if provider_active else "Pipeline"} | {profile_percent}%</span>'
        '</div>'
        f'<div class="directory-snapshot-progress"><span style="{_progress_fill_style(profile_percent)}"></span></div>'
        '</div>'
        '<div class="provider-summary-progress-row">'
        '<div class="provider-summary-progress-head">'
        '<strong>Checklist</strong>'
        f'<span>{int(summary.get("completed_documents", 0) or 0)}/{int(summary.get("total_documents", 0) or 0)} | {checklist_percent}%</span>'
        '</div>'
        f'<div class="directory-snapshot-progress"><span style="{_progress_fill_style(checklist_percent)}"></span></div>'
        '</div>'
        '<div class="provider-summary-progress-row">'
        '<div class="provider-summary-progress-head">'
        '<strong>Credenciales</strong>'
        f'<span>{html.escape(credential_status)} | {credential_percent}%</span>'
        '</div>'
        f'<div class="directory-snapshot-progress"><span style="{_progress_fill_style(credential_percent)}"></span></div>'
        '</div>'
        f'<small class="provider-summary-note">{html.escape(stage_label)} | {html.escape(str(summary.get("credential_label", "")))}</small>'
        '</div>'
        '</section>'
        "</div>"
    )


def _render_providers_directory(
    items: list[dict[str, object]],
    current_user: dict[str, object] | None,
    users: list[dict[str, object]],
    can_manage: bool,
) -> str:
    if not items:
        return '<p class="helper-note">Todavia no hay providers guardados.</p>'

    user_lookup = _user_display_lookup(users)
    cards = []
    for item in items:
        provider_name = str(item.get("provider_name", "")).strip() or "Provider"
        provider_type = str(item.get("provider_type", "")).strip() or "Sin tipo"
        worker_category = str(item.get("worker_category", "")).strip() or "PROVIDER"
        site_location = str(item.get("site_location", "")).strip() or "Cape Coral"
        county_name = str(item.get("county_name", "")).strip() or "Florida"
        assigned_clients = str(item.get("assigned_clients", "")).strip() or "Sin clientes"
        credential_status = str(item.get("credentialing_status_summary", "")).strip() or "Sin credenciales"
        contract_stage = _contract_stage_label(str(item.get("contract_stage", "")))
        docs_done = int(item.get("completed_documents", item.get("delivered_documents", 0)) or 0)
        total_docs = int(item.get("total_documents", 0) or 0)
        provider_npi = str(item.get("provider_npi", "")).strip()
        status_summary = _provider_status_summary_values(item, current_user, user_lookup)
        search_text = " ".join(
            [
                provider_name,
                provider_type,
                worker_category,
                site_location,
                county_name,
                assigned_clients,
                credential_status,
                contract_stage,
                str(status_summary.get("recruiter_label", "")),
                str(status_summary.get("supervisor_label", "")),
                str(status_summary.get("credential_owner_label", "")),
            ]
        ).lower()
        contract_key = str(item.get("contract_id", "")).strip()
        toggle_target_id = f"provider-summary-{contract_key or len(cards)}"
        expediente_href = _provider_expediente_href(item)
        full_form_href = _provider_expediente_href(item, "provider-contract-form")
        chips = [
            _directory_chip("ACTIVE" if _provider_directory_status(item) == "active" else "PIPELINE", "active" if _provider_directory_status(item) == "active" else "inactive"),
            _directory_chip(provider_type.upper(), "neutral"),
            _directory_chip(worker_category.upper(), "info"),
        ]
        if status_summary.get("contract_owner_is_current"):
            chips.append(_directory_chip("TU SUP.", "info"))
        if status_summary.get("credential_owner_is_current"):
            chips.append(_directory_chip("TU CRED.", "info"))
        cards.append(
            '<article class="directory-card provider-card"'
            f' data-directory-card="providers" data-status="{html.escape(_provider_directory_status(item))}"'
            f' data-search="{html.escape(search_text)}">'
            f'<a class="directory-card-hero" href="{html.escape(expediente_href)}">'
            f'<div class="directory-avatar">{_provider_card_avatar_markup(item)}</div>'
            f'<strong class="directory-card-title">{html.escape(provider_name)}</strong>'
            f'<p class="directory-card-subtitle">{html.escape(provider_npi or provider_type)}</p>'
            "</a>"
            '<div class="directory-chip-row">'
            f"{''.join(chips)}"
            "</div>"
            '<div class="directory-card-meta">'
            f'<span>{html.escape(site_location)} | {html.escape(county_name)}</span>'
            f'<span>NPI: {html.escape(provider_npi or "Pendiente")}</span>'
            "</div>"
            '<div class="directory-card-detail provider-summary-toggle">'
            f'<button class="provider-summary-toggle-button" type="button" data-provider-summary-toggle aria-expanded="false" aria-controls="{html.escape(toggle_target_id)}">Abrir perfil rapido</button>'
            f'<div id="{html.escape(toggle_target_id)}" class="provider-summary-toggle-body" hidden>'
            f"{_provider_progress_summary_markup(item, current_user, user_lookup)}"
            "</div>"
            "</div>"
            '<div class="directory-card-actions">'
            f'<a class="small-button" href="{html.escape(expediente_href)}">Abrir expediente</a>'
            + (
                f'<a class="small-button" href="{html.escape(full_form_href)}">Editar completo</a>'
                if can_manage
                else ""
            )
            + "</div>"
            + "</article>"
        )
    return '<div class="directory-grid" data-directory-grid="providers">' + "".join(cards) + "</div>"


def _payer_plan_type_label(value: object) -> str:
    clean_value = str(value or "").strip().upper()
    for plan_value, plan_label in PAYER_PLAN_TYPES:
        if clean_value == plan_value:
            return plan_label
    return clean_value.title() if clean_value else "Other"


def _payer_directory_status(item: dict[str, object]) -> str:
    return "active" if item.get("active", True) else "inactive"


def _payer_expediente_href(item: dict[str, object], anchor: str = "payer-config-form") -> str:
    payer_config_id = quote(str(item.get("payer_config_id", "")).strip())
    return f"/payers?edit_payer_id={payer_config_id}#{anchor}"


def _payer_card_avatar_markup(item: dict[str, object]) -> str:
    payer_name = str(item.get("payer_name", "")).strip() or "Payer"
    initials = "".join(part[:1].upper() for part in payer_name.split()[:2]) or "PY"
    brand_color = str(item.get("brand_color", "#0d51b8")).strip() or "#0d51b8"
    return f'<span class="directory-avatar-fallback" style="background:{html.escape(brand_color)};">{html.escape(initials)}</span>'


def _render_payer_rate_summary_markup(item: dict[str, object]) -> str:
    rate_lines = item.get("rate_lines", [])
    if not isinstance(rate_lines, list):
        rate_lines = []
    active_lines = []
    for line in rate_lines:
        try:
            unit_price = float(line.get("unit_price", 0) or 0)
        except (TypeError, ValueError):
            unit_price = 0.0
        if unit_price <= 0:
            continue
        active_lines.append(
            (
                str(line.get("cpt_code", "")).strip() or "CPT",
                f"${unit_price:.2f}",
            )
        )
    rows = active_lines[:3]
    if not rows:
        rows = [("Tarifas", "Configura CPTs y unit prices")]
    return (
        '<div class="directory-card-detail">'
        '<div class="directory-detail-title">Tarifas CPT</div>'
        + "".join(
            '<div class="directory-detail-row">'
            f"<strong>{html.escape(code)}</strong>"
            f"<span>{html.escape(price)}</span>"
            "</div>"
            for code, price in rows
        )
        + "</div>"
    )


def _render_payers_directory(items: list[dict[str, object]]) -> str:
    if not items:
        return '<p class="helper-note">Todavia no hay payers configurados para esta agencia.</p>'

    cards = []
    for item in items:
        payer_name = str(item.get("payer_name", "")).strip() or "Payer"
        payer_id = str(item.get("payer_id", "")).strip()
        plan_label = _payer_plan_type_label(item.get("plan_type", "COMMERCIAL"))
        clearinghouse_name = str(item.get("clearinghouse_name", "")).strip() or "Sin clearinghouse"
        clearinghouse_payer_id = str(item.get("clearinghouse_payer_id", "")).strip() or "Pendiente"
        receiver_id = str(item.get("clearinghouse_receiver_id", "")).strip() or "Pendiente"
        active_rate_count = int(item.get("active_rate_count", 0) or 0)
        updated_at = str(item.get("updated_at", "")).strip()
        updated_label = updated_at[:16].replace("T", " ") if updated_at else "Pendiente"
        notes = str(item.get("notes", "")).strip()
        search_text = " ".join(
            [
                payer_name,
                payer_id,
                plan_label,
                clearinghouse_name,
                clearinghouse_payer_id,
                receiver_id,
                notes,
            ]
            + [str(line.get("cpt_code", "")) for line in (item.get("rate_lines", []) if isinstance(item.get("rate_lines", []), list) else [])]
        ).lower()
        status = _payer_directory_status(item)
        chips = [
            _directory_chip("ACTIVE" if status == "active" else "INACTIVE", status),
            _directory_chip(plan_label.upper(), "neutral"),
        ]
        if payer_id:
            chips.append(_directory_chip(payer_id.upper(), "info"))
        if clearinghouse_name:
            chips.append(_directory_chip(clearinghouse_name.upper()[:16], "neutral"))
        cards.append(
            '<article class="directory-card payer-card"'
            f' data-directory-card="payers" data-status="{html.escape(status)}"'
            f' data-search="{html.escape(search_text)}">'
            f'<a class="directory-card-hero" href="{html.escape(_payer_expediente_href(item))}">'
            f'<div class="directory-avatar">{_payer_card_avatar_markup(item)}</div>'
            f'<strong class="directory-card-title">{html.escape(payer_name)}</strong>'
            f'<p class="directory-card-subtitle">{html.escape(payer_id or plan_label)}</p>'
            "</a>"
            '<div class="directory-chip-row">'
            f"{''.join(chips)}"
            "</div>"
            '<div class="directory-card-meta">'
            f'<span>Clearinghouse: {html.escape(clearinghouse_name)}</span>'
            f'<span>CPTs activos: {active_rate_count} | Update: {html.escape(updated_label)}</span>'
            "</div>"
            '<div class="directory-card-detail">'
            '<div class="directory-detail-title">Conexion</div>'
            '<div class="directory-detail-row">'
            '<strong>Payer / Receiver</strong>'
            f'<span>{html.escape(clearinghouse_payer_id)} / {html.escape(receiver_id)}</span>'
            "</div>"
            "</div>"
            f"{_render_payer_rate_summary_markup(item)}"
            '<div class="directory-card-actions">'
            f'<a class="small-button" href="{html.escape(_payer_expediente_href(item))}">Configurar</a>'
            f'<a class="small-button" href="{html.escape(_page_href("claims"))}#claims837">Usar en claims</a>'
            "</div>"
            "</article>"
        )
    return '<div class="directory-grid" data-directory-grid="payers">' + "".join(cards) + "</div>"


def _render_payer_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="8">Todavia no hay payers configurados.</td></tr>'

    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('payer_name', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('payer_id', '')) or '-')}</td>"
            f"<td>{html.escape(_payer_plan_type_label(item.get('plan_type', 'COMMERCIAL')))}</td>"
            f"<td>{html.escape(str(item.get('clearinghouse_name', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('clearinghouse_payer_id', '')) or '-')}</td>"
            f"<td>{int(item.get('active_rate_count', 0) or 0)}</td>"
            f"<td>{'Active' if item.get('active', True) else 'Inactive'}</td>"
            f"<td><a href=\"{html.escape(_payer_expediente_href(item))}\">Configurar</a></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_payer_workspace_panel(selected_payer: dict[str, object] | None) -> str:
    if selected_payer is None:
        return (
            '<article class="panel section-card">'
            '<h2>Centro del payer</h2>'
            '<p>Selecciona un payer del directorio para editar su clearinghouse, CPT billing codes y unit prices desde la misma pagina.</p>'
            '<div class="mini-table">'
            '<div class="mini-row"><strong>Payer cargado</strong><span>Selecciona una tarjeta del roster.</span></div>'
            '<div class="mini-row"><strong>Clearinghouse</strong><span>Pendiente</span></div>'
            '<div class="mini-row"><strong>Tarifas CPT</strong><span>Sin configuracion cargada</span></div>'
            "</div>"
            "</article>"
        )

    payer_name = str(selected_payer.get("payer_name", "")).strip() or "Payer"
    payer_id = str(selected_payer.get("payer_id", "")).strip() or "Pendiente"
    clearinghouse_name = str(selected_payer.get("clearinghouse_name", "")).strip() or "Pendiente"
    active_rate_count = int(selected_payer.get("active_rate_count", 0) or 0)
    rate_lines = selected_payer.get("rate_lines", [])
    if not isinstance(rate_lines, list):
        rate_lines = []
    rate_rows = []
    for line in rate_lines:
        try:
            unit_price = float(line.get("unit_price", 0) or 0)
        except (TypeError, ValueError):
            unit_price = 0.0
        if unit_price <= 0:
            continue
        rate_rows.append(
            "<tr>"
            f"<td>{html.escape(str(line.get('cpt_code', '')) or '-')}</td>"
            f"<td>{html.escape(str(line.get('billing_code', '')) or '-')}</td>"
            f"<td>{html.escape(str(line.get('hcpcs_code', '')) or '-')}</td>"
            f"<td>${unit_price:.2f}</td>"
            "</tr>"
        )
    rate_table_markup = (
        "<div class=\"table-wrap\">"
        "<table>"
        "<thead><tr><th>CPT</th><th>Billing code</th><th>HCPCS</th><th>Unit price</th></tr></thead>"
        f"<tbody>{''.join(rate_rows)}</tbody>"
        "</table>"
        "</div>"
        if rate_rows
        else '<p class="helper-note">Todavia no hay CPTs con tarifa activa para este payer.</p>'
    )
    return (
        '<article class="panel section-card">'
        '<h2>Centro del payer</h2>'
        '<p>Desde aqui ves el seguro cargado, el clearinghouse ligado y las tarifas por CPT que el equipo de billing usara como referencia.</p>'
        '<div class="mini-table">'
        f'<div class="mini-row"><strong>Payer cargado</strong><span>{html.escape(payer_name)}</span></div>'
        f'<div class="mini-row"><strong>Payer ID</strong><span>{html.escape(payer_id)}</span></div>'
        f'<div class="mini-row"><strong>Plan</strong><span>{html.escape(_payer_plan_type_label(selected_payer.get("plan_type", "COMMERCIAL")))}</span></div>'
        f'<div class="mini-row"><strong>Clearinghouse</strong><span>{html.escape(clearinghouse_name)}</span></div>'
        f'<div class="mini-row"><strong>Receiver</strong><span>{html.escape(str(selected_payer.get("clearinghouse_receiver_id", "")) or "Pendiente")}</span></div>'
        f'<div class="mini-row"><strong>Tarifas activas</strong><span>{active_rate_count}</span></div>'
        "</div>"
        '<div class="directory-card-actions directory-card-actions-left">'
        f'<a class="small-button" href="{html.escape(_payer_expediente_href(selected_payer))}">Editar payer</a>'
        f'<a class="small-button" href="{html.escape(_page_href("claims"))}#claims837">Ir a claims</a>'
        "</div>"
        f"{rate_table_markup}"
        "</article>"
    )


def _provider_credential_status_map(items: list[dict[str, object]]) -> dict[str, str]:
    grouped: dict[str, dict[str, int]] = {}
    for item in items:
        provider_name = str(item.get("provider_name", "")).strip()
        if not provider_name:
            continue
        status = str(item.get("enrollment_status", "")).strip().upper() or "PENDING"
        grouped.setdefault(provider_name.lower(), {})[status] = grouped.setdefault(provider_name.lower(), {}).get(status, 0) + 1

    labels = {
        "SUBMITTED": "Submitted",
        "PENDING": "Pending",
        "ENROLLED": "Enrolled",
        "FOLLOW_UP": "Follow Up",
        "REJECTED": "Rejected",
    }
    summaries: dict[str, str] = {}
    for provider_key, counts in grouped.items():
        ordered = []
        for status in ("ENROLLED", "PENDING", "SUBMITTED", "FOLLOW_UP", "REJECTED"):
            amount = counts.get(status, 0)
            if amount:
                ordered.append(f"{labels.get(status, status.title())}: {amount}")
        summaries[provider_key] = " | ".join(ordered) if ordered else "Sin credenciales"
    return summaries


def _render_client_document_cards(items: list[dict[str, object]]) -> str:
    if not items:
        return '<article class="panel section-card"><h2>Expediente del cliente</h2><p>Todavia no hay clientes con documentos archivados.</p></article>'

    cards = []
    for item in items:
        documents = item.get("documents", [])
        if not isinstance(documents, list):
            documents = []
        rows = []
        for document in documents:
            file_markup = "-"
            if document.get("file_path"):
                file_markup = (
                    f'<a href="/client-document?client_id={quote(str(item.get("client_id", "")))}&document_name={quote(str(document.get("document_name", "")))}">'
                    f'{html.escape(str(document.get("file_name", "")) or "Abrir archivo")}</a>'
                )
            elif document.get("file_name"):
                file_markup = html.escape(str(document.get("file_name", "")))
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(document.get('document_name', '')))}</td>"
                f"<td>{html.escape(str(document.get('issued_date', '')) or '-')}</td>"
                f"<td>{html.escape(str(document.get('expiration_date', '')) or '-')}</td>"
                f"<td>{html.escape(str(document.get('status', 'Pending')))}</td>"
                f"<td>{file_markup}</td>"
                "</tr>"
            )
        progress_percent = int(item.get("progress_percent", 0) or 0)
        delivered_documents = int(item.get("delivered_documents", 0) or 0)
        total_documents = int(item.get("total_documents", 0) or 0)
        cards.append(
            "<article class=\"panel section-card provider-doc-card\">"
            f"<h2>{html.escape(str(item.get('first_name', '')))} {html.escape(str(item.get('last_name', '')))}</h2>"
            f"<p>Policy {html.escape(str(item.get('member_id', '')))} | Payer {html.escape(str(item.get('payer_name', '')))} | Documentos {delivered_documents}/{total_documents}</p>"
            f"<div class=\"progress large-progress\"><span style=\"width:{progress_percent}%\"></span></div>"
            f"<small>{progress_percent}% del expediente documental del cliente completado</small>"
            "<div class=\"table-wrap\">"
            "<table>"
            "<thead><tr><th>Documento</th><th>Fecha emitido</th><th>Fecha expira</th><th>Estatus</th><th>Archivo</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
            "</div>"
            "</article>"
        )
    return "".join(cards)


def _provider_document_warning_markup(
    items: list[dict[str, object]],
    current_user: dict[str, object] | None,
) -> str:
    if not items:
        return ""

    role = str((current_user or {}).get("role", "")).upper()
    provider_expired: list[str] = []
    provider_expiring: list[str] = []
    for item in items:
        provider_name = str(item.get("provider_name", "")).strip() or "Provider"
        expired_names = [str(name) for name in item.get("expired_document_names", []) if str(name).strip()]
        expiring_names = [str(name) for name in item.get("expiring_document_names", []) if str(name).strip()]
        if expired_names:
            provider_expired.append(f"{provider_name}: {', '.join(expired_names[:3])}")
        if expiring_names:
            provider_expiring.append(f"{provider_name}: {', '.join(expiring_names[:3])}")

    if not provider_expired and not provider_expiring:
        return ""

    if is_provider_role({"role": role}) and provider_expired:
        title = "Tienes documentos expirados"
        detail = "Sube una version vigente para que Recursos Humanos la apruebe."
    elif is_provider_role({"role": role}):
        title = "Tienes documentos por vencer"
        detail = "Revisa tu expediente y reemplaza los archivos antes de que lleguen a Expired."
    elif provider_expired:
        title = "Hay providers con documentos expirados"
        detail = "El sistema ya los marco como Expired y comenzo a enviar alertas."
    else:
        title = "Hay documentos de providers por vencer"
        detail = "El sistema comenzo a avisar desde 30 dias antes del vencimiento."

    details = provider_expired or provider_expiring
    details_markup = "".join(f"<li>{html.escape(item)}</li>" for item in details[:5])
    return (
        '<article class="panel section-card warning-card">'
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(detail)}</p>"
        f"<ul>{details_markup}</ul>"
        "</article>"
    )


def _render_notification_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="8">Todavia no hay notificaciones en cola.</td></tr>'

    rows = []
    for item in items:
        recipient_label = str(item.get("recipient_label", "")).strip() or "Sin destinatario"
        recipient_email = str(item.get("recipient_email", "")).strip()
        recipient_markup = html.escape(recipient_label)
        if recipient_email:
            recipient_markup += f"<br><small>{html.escape(recipient_email)}</small>"
        email_status = str(item.get("email_status", "")).strip()
        email_error = str(item.get("email_error", "")).strip()
        status_markup = html.escape(email_status or "queued")
        if email_error:
            status_markup += f"<br><small>{html.escape(email_error)}</small>"
        notification_id = html.escape(str(item.get("notification_id", "")))
        action_parts: list[str] = []
        if recipient_email:
            action_parts.append(
                "<form class=\"table-action-form\" method=\"post\" action=\"/send-notification-outlook\">"
                f"<input type=\"hidden\" name=\"notification_id\" value=\"{notification_id}\">"
                "<button class=\"small-button\" type=\"submit\" name=\"email_mode\" value=\"draft\">Draft Outlook</button>"
                "<button class=\"small-button\" type=\"submit\" name=\"email_mode\" value=\"send\">Enviar Outlook</button>"
                "</form>"
            )
        else:
            action_parts.append("<span class=\"helper-note\">Sin email</span>")
        action_parts.append(
            "<form class=\"table-action-form\" method=\"post\" action=\"/resolve-notification\">"
            f"<input type=\"hidden\" name=\"notification_id\" value=\"{notification_id}\">"
            "<button class=\"small-button\" type=\"submit\">Atender</button>"
            "</form>"
        )
        action_parts.append(
            "<form class=\"table-action-form\" method=\"post\" action=\"/delete-notification\">"
            f"<input type=\"hidden\" name=\"notification_id\" value=\"{notification_id}\">"
            "<button class=\"small-button\" type=\"submit\">Borrar</button>"
            "</form>"
        )
        action_markup = '<div class="notification-action-stack">' + "".join(action_parts) + "</div>"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('created_at', '')))}</td>"
            f"<td>{html.escape(str(item.get('category', '')))}</td>"
            f"<td>{html.escape(str(item.get('subject', '')))}</td>"
            f"<td>{recipient_markup}</td>"
            f"<td>{status_markup}</td>"
            f"<td>{action_markup}</td>"
            f"<td>{html.escape(str(item.get('related_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('message', '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_era_archive_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="7">Todavia no hay archivos ERA archivados.</td></tr>'

    rows = []
    for item in items:
        archive_id = html.escape(str(item.get("archive_id", "")))
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('imported_at', '')))}</td>"
            f"<td>{html.escape(str(item.get('file_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>${float(item.get('payment_amount', 0) or 0):.2f}</td>"
            f"<td>{html.escape(str(item.get('claim_count', '')))}</td>"
            f"<td>{html.escape(str(item.get('claim_updates_count', '')))}</td>"
            f"<td><a href=\"/era-download?archive_id={archive_id}\">Descargar 835</a></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_user_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="12">Todavia no hay usuarios registrados.</td></tr>'

    rows = []
    for item in items:
        role_value = str(item.get("role", "")).upper()
        pages = ", ".join(PERMISSION_PAGE_LABELS.get(page, page) for page in _pages_for_user(item))
        search_text = " ".join(
            [
                str(item.get("full_name", "")),
                str(item.get("username", "")),
                str(item.get("job_title", "")),
                str(item.get("site_location", "")),
                str(item.get("county_name", "")),
                str(item.get("linked_provider_name", "")),
                ROLE_LABELS.get(role_value, role_value.title()),
            ]
        ).lower()
        rows.append(
            f"<tr data-directory-row=\"users\" data-status=\"{html.escape(_user_directory_status(item))}\" data-search=\"{html.escape(search_text)}\">"
            f"<td>{html.escape(str(item.get('full_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('username', '')))}</td>"
            f"<td>{html.escape(str(item.get('job_title', '')))}</td>"
            f"<td>{html.escape(str(item.get('email', '')))}</td>"
            f"<td>{html.escape(str(item.get('phone', '')))}</td>"
            f"<td>{html.escape(str(item.get('site_location', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('county_name', '')) or '-')}</td>"
            f"<td>{html.escape(ROLE_LABELS.get(role_value, role_value.title()))}</td>"
            f"<td>{html.escape(str(item.get('linked_provider_name', '')) or '-')}</td>"
            f"<td>{'Activo' if item.get('active', True) else 'Inactivo'}</td>"
            f"<td>{'Activo' if item.get('mfa_enabled', False) else 'No configurado'}</td>"
            f"<td>{html.escape(pages)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_claim_audit_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="6">Todavia no hay auditoria de claims.</td></tr>'

    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('created_at', '')))}</td>"
            f"<td>{html.escape(str(item.get('claim_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('action', '')))}</td>"
            f"<td>{html.escape(str(item.get('actor_name', '')) or str(item.get('actor_username', '')))}</td>"
            f"<td>{html.escape(str(item.get('actor_username', '')))}</td>"
            f"<td>{html.escape(str(item.get('details', '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_system_audit_rows(items: list[dict[str, object]], empty_message: str) -> str:
    if not items:
        return f'<tr><td colspan="7">{html.escape(empty_message)}</td></tr>'

    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('created_at', '')))}</td>"
            f"<td>{html.escape(str(item.get('entity_name', '')) or str(item.get('entity_id', '')))}</td>"
            f"<td>{html.escape(str(item.get('action', '')))}</td>"
            f"<td>{html.escape(str(item.get('actor_name', '')) or str(item.get('actor_username', '')))}</td>"
            f"<td>{html.escape(str(item.get('actor_username', '')))}</td>"
            f"<td>{html.escape(str(item.get('category', '')))}</td>"
            f"<td>{html.escape(str(item.get('details', '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _task_status_options_markup(selected_value: str) -> str:
    options = []
    for value in ("PENDING", "IN_PROGRESS", "DONE"):
        options.append(f'<option value="{value}"{_selected_value(selected_value, value)}>{value.replace("_", " ").title()}</option>')
    return "".join(options)


def _event_category_options_markup(selected_value: str) -> str:
    options = []
    for value in ("task", "meeting", "follow_up", "credentialing", "deadline"):
        label = value.replace("_", " ").title()
        options.append(f'<option value="{value}"{_selected_value(selected_value, value)}>{html.escape(label)}</option>')
    return "".join(options)


def _user_select_options_markup(items: list[dict[str, object]], selected_value: str) -> str:
    options = ['<option value="">Sin asignar</option>']
    for item in items:
        username = str(item.get("username", ""))
        label = f"{item.get('full_name', '')} ({username})"
        options.append(f'<option value="{html.escape(username)}"{_selected_value(selected_value, username)}>{html.escape(label)}</option>')
    return "".join(options)


def _provider_document_options_markup(contract: dict[str, object] | None, selected_value: str) -> str:
    options = ['<option value="">Selecciona un documento</option>']
    documents = []
    if isinstance(contract, dict):
        raw_documents = contract.get("documents", [])
        if isinstance(raw_documents, list):
            documents = raw_documents
    for document in documents:
        document_name = str(document.get("document_name", "")).strip()
        if not document_name:
            continue
        options.append(
            f'<option value="{html.escape(document_name)}"{_selected_value(selected_value, document_name)}>{html.escape(document_name)}</option>'
        )
    return "".join(options)


def _render_calendar_event_rows(items: list[dict[str, object]], current_user: dict[str, object] | None) -> str:
    if not items:
        return '<tr><td colspan="8">Todavia no hay tareas o eventos registrados.</td></tr>'
    rows = []
    current_username = str((current_user or {}).get("username", ""))
    for item in items:
        action = ""
        can_close = (
            str(item.get("assigned_username", "")) == current_username
            or has_permission(current_user, "admin.full")
            or has_permission(current_user, "schedule.view")
        )
        if can_close and str(item.get("status", "")).upper() != "DONE":
            action = (
                "<form class=\"table-action-form\" method=\"post\" action=\"/update-event-status\">"
                f"<input type=\"hidden\" name=\"event_id\" value=\"{html.escape(str(item.get('event_id', '')))}\">"
                "<input type=\"hidden\" name=\"status\" value=\"DONE\">"
                "<button class=\"small-button\" type=\"submit\">Marcar done</button>"
                "</form>"
            )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('event_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('due_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('title', '')))}</td>"
            f"<td>{html.escape(str(item.get('category', '')))}</td>"
            f"<td>{html.escape(str(item.get('assigned_name', '')) or str(item.get('assigned_username', '')))}</td>"
            f"<td>{html.escape(str(item.get('status', '')))}</td>"
            f"<td>{html.escape(str(item.get('related_provider', '')))}</td>"
            f"<td>{action or html.escape(str(item.get('description', '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_note_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="3">Todavia no hay notas de trabajo.</td></tr>'
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('created_at', '')))}</td>"
            f"<td>{html.escape(str(item.get('title', '')))}</td>"
            f"<td>{html.escape(str(item.get('body', '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _aba_context_options_markup(selected_value: str) -> str:
    return "".join(
        f'<option value="{html.escape(value)}"{_selected_value(selected_value, value)}>{html.escape(label)}</option>'
        for value, label in ABA_SERVICE_CONTEXT_LABELS.items()
    )


def _html_date_value(user_value: str) -> str:
    clean = str(user_value or "").strip()
    if not clean:
        return ""
    try:
        parsed = parse_user_date(clean)
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%d")


def _time_parts(value: str) -> tuple[str, str, str]:
    clean = str(value or "").strip().upper()
    if not clean:
        return ("09", "00", "AM")
    try:
        parsed = datetime.strptime(clean, "%H:%M")
    except ValueError:
        try:
            parsed = datetime.strptime(clean, "%I:%M %p")
        except ValueError:
            return ("09", "00", "AM")
    hour_24 = parsed.hour
    minute = parsed.minute
    ampm = "AM" if hour_24 < 12 else "PM"
    hour_12 = hour_24 % 12 or 12
    return (f"{hour_12:02d}", f"{minute:02d}", ampm)


def _time_hour_options_markup(selected_value: str) -> str:
    return "".join(
        f'<option value="{hour:02d}"{_selected_value(selected_value, f"{hour:02d}")}>{hour:02d}</option>'
        for hour in range(1, 13)
    )


def _time_minute_options_markup(selected_value: str) -> str:
    return "".join(
        f'<option value="{minute:02d}"{_selected_value(selected_value, f"{minute:02d}")}>{minute:02d}</option>'
        for minute in range(0, 60, 5)
    )


def _time_ampm_options_markup(selected_value: str) -> str:
    return "".join(
        f'<option value="{value}"{_selected_value(selected_value, value)}>{value}</option>'
        for value in ("AM", "PM")
    )


def _signature_pad_markup(name: str, value: object, label: str) -> str:
    clean_value = str(value or "").strip()
    signature_value = clean_value if clean_value.startswith("data:image/") else ""
    legacy_note = ""
    if clean_value and not clean_value.startswith("data:image/"):
        legacy_note = '<p class="helper-note">Habia una firma antigua en texto. Redibujala para dejarla valida.</p>'
    return (
        '<label class="field signature-field">'
        f"<span>{html.escape(label)}</span>"
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(signature_value)}" data-signature-input="{html.escape(name)}">'
        f'<canvas class="signature-pad-canvas" width="520" height="170" data-signature-pad="{html.escape(name)}"></canvas>'
        '<div class="signature-actions">'
        f'<button class="small-button signature-clear-button" type="button" data-signature-clear="{html.escape(name)}">Limpiar firma</button>'
        "</div>"
        '<p class="helper-note">Dibuja la firma dentro del cuadro.</p>'
        f"{legacy_note}"
        "</label>"
    )


def _date_shell_markup(name: str, raw_value: object, html_value: str, label: str) -> str:
    return (
        '<label class="field">'
        f"<span>{html.escape(label)}</span>"
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(str(raw_value or ""))}">'
        '<div class="date-shell">'
        f'<input type="date" value="{html.escape(html_value)}" data-date-visible="{html.escape(name)}">'
        "</div>"
        "</label>"
    )


def _time_wheel_markup(
    name: str,
    raw_value: object,
    label: str,
    *,
    hour_value: str,
    minute_value: str,
    ampm_value: str,
) -> str:
    return (
        '<label class="field">'
        f"<span>{html.escape(label)}</span>"
        f'<div class="time-wheel" data-time-wheel="{html.escape(name)}">'
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(str(raw_value or ""))}">'
        f'<select data-time-part="hour" aria-label="{html.escape(label)} hora">{_time_hour_options_markup(hour_value)}</select>'
        '<span class="time-wheel-separator">:</span>'
        f'<select data-time-part="minute" aria-label="{html.escape(label)} minutos">{_time_minute_options_markup(minute_value)}</select>'
        f'<select data-time-part="ampm" aria-label="{html.escape(label)} AM PM">{_time_ampm_options_markup(ampm_value)}</select>'
        "</div>"
        "</label>"
    )


def _aba_provider_options_markup(items: list[dict[str, object]], selected_value: str) -> str:
    if not items:
        return '<option value="">Todavia no hay providers ABA disponibles</option>'
    return "".join(
        f'<option value="{html.escape(str(item.get("provider_contract_id", "")))}"'
        f' data-provider-role="{html.escape(str(item.get("provider_role", "")))}"'
        f' data-provider-type="{html.escape(str(item.get("provider_type", "")))}"'
        f' data-client-ids="{html.escape("|".join(str(client_id) for client_id in item.get("client_ids", []) if str(client_id).strip()))}"'
        f'{_selected_value(selected_value, str(item.get("provider_contract_id", "")))}>'
        f'{html.escape(str(item.get("provider_name", "")))} ({html.escape(str(item.get("provider_role", "")))})'
        "</option>"
        for item in items
    )


def _aba_client_options_markup(items: list[dict[str, object]], selected_value: str) -> str:
    if not items:
        return '<option value="">Todavia no hay clientes disponibles</option>'
    return "".join(
        f'<option value="{html.escape(str(item.get("client_id", "")))}"{_selected_value(selected_value, str(item.get("client_id", "")))}>'
        f'{html.escape(str(item.get("client_name", "")))}'
        "</option>"
        for item in items
    )


def _render_aba_appointment_rows(items: list[dict[str, object]], include_financials: bool = True) -> str:
    if not items:
        return f'<tr><td colspan="{9 if include_financials else 8}">Todavia no hay sesiones ABA guardadas.</td></tr>'
    rows = []
    for item in items:
        row = (
            "<tr>"
            f"<td>{html.escape(str(item.get('appointment_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('provider_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('client_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('context_label', '')))}</td>"
            f"<td>{html.escape(str(item.get('billing_code', '')))}</td>"
            f"<td>{html.escape(str(item.get('document_type', '')))}</td>"
            f"<td>{html.escape(str(item.get('start_time_label', '')))} - {html.escape(str(item.get('end_time_label', '')))}</td>"
            f"<td>{int(item.get('units', 0) or 0)}</td>"
        )
        if include_financials:
            row += f"<td>${float(item.get('estimated_total', 0) or 0):,.2f}</td>"
        row += "</tr>"
        rows.append(row)
    return "".join(rows)


def _render_aba_service_log_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="10">Todavia no hay service logs ABA generados.</td></tr>'
    rows = []
    for item in items:
        log_id = quote(str(item.get("log_id", "")))
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('week_start', '')))} - {html.escape(str(item.get('week_end', '')))}</td>"
            f"<td>{html.escape(str(item.get('provider_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('client_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('document_type', '')))}</td>"
            f"<td>{float(item.get('total_hours', 0) or 0):.2f}</td>"
            f"<td>{int(item.get('total_units', 0) or 0)}</td>"
            f"<td>{html.escape(str(item.get('deadline_status', '')))}</td>"
            f"<td>{html.escape(str(item.get('workflow_status', '')))}</td>"
            f"<td>{html.escape(str(item.get('latest_note_due_at', '')) or '-')}</td>"
            f"<td><a class=\"quick-link\" href=\"{_page_href('aba_notes')}?log_id={log_id}#aba-note-preview\">Abrir</a></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_aba_billing_preview(preview: dict[str, object], include_financials: bool = True) -> str:
    message = str(preview.get("message", "")).strip()
    if message:
        return f'<p class="helper-note">{html.escape(message)}</p>'
    payable_label = "Si" if preview.get("payable") else "No"
    required_cpt = str(preview.get("required_cpt", "")).strip()
    rows = [
        f'<div class="mini-row"><strong>Documento</strong><span>{html.escape(str(preview.get("document_type", "")))}</span></div>',
        f'<div class="mini-row"><strong>CPT</strong><span>{html.escape(str(preview.get("billing_code", "")))}</span></div>',
        (
            f'<div class="mini-row"><strong>CPT obligatorio</strong><span>{html.escape(required_cpt)}</span></div>'
            if required_cpt
            else ""
        ),
        f'<div class="mini-row"><strong>Units</strong><span>{int(preview.get("units", 0) or 0)}</span></div>',
        f'<div class="mini-row"><strong>Restante auth</strong><span>{html.escape(str(preview.get("remaining_units", "-")))}</span></div>',
        f'<div class="mini-row"><strong>Payable</strong><span>{payable_label}</span></div>',
        f'<div class="mini-row"><strong>Regla</strong><span>{html.escape(str(preview.get("description", "")))}</span></div>',
    ]
    if include_financials:
        rows.insert(3, f'<div class="mini-row"><strong>Rate</strong><span>${float(preview.get("unit_rate", 0) or 0):,.2f}</span></div>')
        rows.insert(4, f'<div class="mini-row"><strong>Total estimado</strong><span>${float(preview.get("estimated_total", 0) or 0):,.2f}</span></div>')
    else:
        rows.insert(3, '<div class="mini-row"><strong>Billing</strong><span>Las tarifas y totales solo se muestran a usuarios con permiso financiero.</span></div>')
    return (
        '<div class="mini-table">'
        + "".join(row for row in rows if row)
        + "</div>"
    )


def _aba_preview_body_for_viewer(preview_body: object, include_financials: bool) -> str:
    clean_body = str(preview_body or "")
    if include_financials or not clean_body.strip():
        return clean_body
    filtered_lines = []
    for line in clean_body.splitlines():
        upper_line = line.upper()
        if (
            "TOTAL FACTURADO" in upper_line
            or "TOTAL BILLED" in upper_line
            or "ESTIMATED TOTAL" in upper_line
            or "UNIT RATE" in upper_line
        ):
            replacement = "Total Billed: Solo visible para usuarios con permiso financiero."
            if "TOTAL FACTURADO" in upper_line:
                replacement = "TOTAL FACTURADO: Solo visible para usuarios con permiso financiero."
            filtered_lines.append(replacement)
            continue
        filtered_lines.append(line)
    return "\n".join(filtered_lines)


def _render_aba_log_detail(selected_log: dict[str, object] | None, values: dict[str, str], *, can_manage_workflow: bool) -> str:
    if selected_log is None:
        return "<p class=\"helper-note\">Todavia no hay una nota semanal seleccionada. Guarda una sesion o abre un service log de la tabla.</p>"
    provider_signature_value = str(values.get("provider_signature", "")).strip() or str(selected_log.get("provider_signature", "")).strip()
    caregiver_signature_value = str(values.get("caregiver_signature", "")).strip() or str(selected_log.get("caregiver_signature", "")).strip()
    action_markup = (
        "<div class=\"quick-links\">"
        "<button type=\"submit\" name=\"workflow_action\" value=\"review\">Supervisar nota</button>"
        "<button type=\"submit\" name=\"workflow_action\" value=\"close\">Cerrar nota</button>"
        "<button type=\"submit\" name=\"workflow_action\" value=\"reject\">Reject note</button>"
        "<button type=\"submit\" name=\"workflow_action\" value=\"reopen\">Reabrir nota</button>"
        "</div>"
    )
    if not can_manage_workflow:
        action_markup = (
            '<p class="helper-note">Solo oficina, admin o un BCBA/BCaBA pueden cerrar, revisar, rechazar o reabrir esta nota.</p>'
        )
    return (
        '<div class="mini-table">'
        f'<div class="mini-row"><strong>Provider</strong><span>{html.escape(str(selected_log.get("provider_name", "")))}</span></div>'
        f'<div class="mini-row"><strong>Cliente</strong><span>{html.escape(str(selected_log.get("client_name", "")))}</span></div>'
        f'<div class="mini-row"><strong>Documento</strong><span>{html.escape(str(selected_log.get("document_type", "")))}</span></div>'
        f'<div class="mini-row"><strong>Semana</strong><span>{html.escape(str(selected_log.get("week_start", "")))} - {html.escape(str(selected_log.get("week_end", "")))}</span></div>'
        f'<div class="mini-row"><strong>Deadline</strong><span>{html.escape(str(selected_log.get("latest_note_due_at", "")) or "-")} | {html.escape(str(selected_log.get("deadline_status", "")))}</span></div>'
        f'<div class="mini-row"><strong>Workflow</strong><span>{html.escape(str(selected_log.get("workflow_status", "")))}</span></div>'
        f'<div class="mini-row"><strong>Review</strong><span>{html.escape(str(selected_log.get("reviewed_by", "")) or "-")} {html.escape(str(selected_log.get("reviewed_at", "")) or "")}</span></div>'
        f'<div class="mini-row"><strong>Close</strong><span>{html.escape(str(selected_log.get("closed_by", "")) or "-")} {html.escape(str(selected_log.get("closed_at", "")) or "")}</span></div>'
        f'<div class="mini-row"><strong>Reject</strong><span>{html.escape(str(selected_log.get("rejected_reason", "")) or "-")}</span></div>'
        "</div>"
        "<label class=\"field\">"
        "<span>Supervisor</span>"
        f"<input name=\"supervisor_name\" value=\"{html.escape(str(values.get('supervisor_name', '')))}\" placeholder=\"Clinical Supervisor\">"
        "</label>"
        "<div class=\"field-grid\">"
        f"{_signature_pad_markup('provider_signature', provider_signature_value, 'Firma provider')}"
        f"{_signature_pad_markup('caregiver_signature', caregiver_signature_value, 'Firma caregiver')}"
        "</div>"
        "<label class=\"field\">"
        "<span>Motivo si rechazas o reabres</span>"
        f"<textarea name=\"workflow_reason\" placeholder=\"Explain why you are rejecting or reopening the note.\">{html.escape(str(values.get('workflow_reason', '')))}</textarea>"
        "</label>"
        f"<input type=\"hidden\" name=\"log_id\" value=\"{html.escape(str(selected_log.get('log_id', '')))}\">"
        f"{action_markup}"
    )


def _render_session_action_form(selected_session: dict[str, object] | None) -> str:
    if selected_session is None:
        return ""
    session_id = str(selected_session.get("session_id", "")).strip()
    if not session_id:
        return ""
    actual_start = str(selected_session.get("actual_start_time", "")).strip() or str(selected_session.get("scheduled_start_time", "")).strip()
    actual_end = str(selected_session.get("actual_end_time", "")).strip() or str(selected_session.get("scheduled_end_time", "")).strip()
    return (
        '<form class="panel section-card session-inline-form" method="post" action="/aba-session-workflow">'
        f'<input type="hidden" name="appointment_id" value="{html.escape(session_id)}">'
        '<div class="field-grid compact">'
        '<label class="field"><span>Actual start</span>'
        f'<input name="actual_start_time" value="{html.escape(actual_start)}" placeholder="09:00"></label>'
        '<label class="field"><span>Actual end</span>'
        f'<input name="actual_end_time" value="{html.escape(actual_end)}" placeholder="11:00"></label>'
        '<label class="field"><span>Reason if needed</span>'
        '<input name="session_reason" placeholder="Cancel reason, no show note, or reopen reason"></label>'
        "</div>"
        '<div class="quick-links">'
        '<button type="submit" name="session_action" value="confirm">Confirm</button>'
        '<button type="submit" name="session_action" value="start">Start</button>'
        '<button type="submit" name="session_action" value="complete">Complete</button>'
        '<button type="submit" name="session_action" value="cancel">Cancel</button>'
        '<button type="submit" name="session_action" value="no_show">No Show</button>'
        '<button type="submit" name="session_action" value="reopen">Reopen</button>'
        "</div>"
        "</form>"
    )


def _session_status_badge(status: object) -> str:
    clean = str(status or "").strip()
    token = clean.lower()
    if token in {"paid", "ready for billing", "approved", "ready", "locked"}:
        tone = "success"
    elif token in {"billing hold", "denied", "note rejected", "error", "rejected"}:
        tone = "danger"
    elif token in {"pending note", "note submitted", "under clinical review", "submitted to payer", "billed", "partially paid", "warning"}:
        tone = "warn"
    else:
        tone = "neutral"
    return f'<span class="profile-pill {tone}">{html.escape(clean or "Pending")}</span>'


def _validation_pills_markup(results: list[dict[str, object]]) -> str:
    if not results:
        return '<span class="profile-pill neutral">Sin validaciones</span>'
    counts = {"pass": 0, "warning": 0, "fail": 0}
    for item in results:
        status = str(item.get("status", "")).strip().lower()
        if status in counts:
            counts[status] += 1
    pills = []
    if counts["fail"]:
        pills.append(f'<span class="profile-pill danger">{counts["fail"]} fail</span>')
    if counts["warning"]:
        pills.append(f'<span class="profile-pill warn">{counts["warning"]} warning</span>')
    if counts["pass"]:
        pills.append(f'<span class="profile-pill success">{counts["pass"]} pass</span>')
    return "".join(pills) or '<span class="profile-pill neutral">Sin validaciones</span>'


def _render_operational_session_rows(items: list[dict[str, object]], *, claim_links: bool = False) -> str:
    if not items:
        return '<tr><td colspan="11">Todavia no hay sesiones operativas para mostrar.</td></tr>'

    rows = []
    for item in items:
        session_id = quote(str(item.get("session_id", "")))
        log_id = quote(str(item.get("service_log_id", "")))
        client_id = quote(str(item.get("client_id", "")))
        claim_id = str(item.get("claim_id", "")).strip()
        actions = [
            f'<a class="quick-link" href="{_page_href("aba_notes")}?appointment_id={session_id}#session-ops-detail">Abrir</a>',
        ]
        if log_id:
            actions.append(f'<a class="quick-link" href="{_page_href("aba_notes")}?log_id={log_id}#aba-note-preview">Nota</a>')
        if str(item.get("authorization_number", "")).strip():
            actions.append(f'<a class="quick-link" href="{_page_href("clients")}?auth_client_id={client_id}#client-authorizations">Auth</a>')
        if claim_links and claim_id:
            actions.append(f'<a class="quick-link" href="{_page_href("claims")}#claims-batch">Claim</a>')
        elif claim_links:
            actions.append(f'<a class="quick-link" href="{_page_href("claims")}?appointment_id={session_id}#claims837">837</a>')
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('service_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('client_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('provider_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('billing_code', '') or item.get('cpt_code', '')))}</td>"
            f"<td>{int(float(item.get('units', 0) or 0))}</td>"
            f"<td>{_session_status_badge(item.get('note_status', 'Draft'))}</td>"
            f"<td>{_session_status_badge(item.get('session_status', 'Scheduled'))}</td>"
            f"<td>{_session_status_badge(str(item.get('billing_queue_status', '')).replace('_', ' ').title())}</td>"
            f"<td><div class=\"profile-pill-row\">{_validation_pills_markup(item.get('validation_results', []))}</div><small>{html.escape(str(item.get('billing_hold_reason', '')) or '-')}</small></td>"
            f"<td><div class=\"quick-links\">{''.join(actions)}</div></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_claim_follow_up_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="8">Todavia no hay claims en follow-up.</td></tr>'
    rows = []
    for item in items:
        claim_id = html.escape(str(item.get("claim_id", "")))
        created_at = str(item.get("created_at", "")).strip()
        age_days = "-"
        try:
            age_days = str((datetime.now().date() - datetime.fromisoformat(created_at).date()).days)
        except ValueError:
            pass
        status_token = str(item.get("status", "")).strip().lower()
        action_markup = (
            f'<a class="quick-link" href="/cms1500?claim_id={claim_id}">CMS-1500</a> '
            f'<a class="quick-link" href="/claim-edi?claim_id={claim_id}">837</a>'
        )
        if status_token in {"denied", "draft", "pending", "partial"}:
            action_markup += _ai_action_form_markup(
                "explain_claim_denial",
                "Explain Denial",
                return_page="claims",
                active_panel="claim",
                hidden_fields={"claim_id": str(item.get("claim_id", "")).strip()},
            )
        rows.append(
            "<tr>"
            f"<td>{claim_id}</td>"
            f"<td>{html.escape(str(item.get('patient_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>{_session_status_badge(str(item.get('status', '')).upper())}</td>"
            f"<td>{html.escape(str(item.get('tracking_id', '')) or '-')}</td>"
            f"<td>${float(item.get('balance_amount', 0) or 0):,.2f}</td>"
            f"<td>{html.escape(age_days)}</td>"
            f"<td><div class=\"quick-links\">{action_markup}</div></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_claim_batch_rows(items: list[dict[str, object]], *, include_totals: bool = True) -> str:
    if not items:
        return '<tr><td colspan="9">Todavia no hay batches sugeridos desde sesiones listas.</td></tr>'
    rows = []
    for item in items:
        sessions_included = item.get("sessions_included", [])
        first_session_id = quote(str((sessions_included[0] if sessions_included else {}).get("session_id", "")))
        validation_errors = item.get("validation_errors", [])
        validation_copy = "; ".join(str(message) for message in validation_errors[:2]) or "Sin errores de validacion."
        claim_amount_markup = f"${float(item.get('claim_amount', 0) or 0):,.2f}" if include_totals else "Restricted"
        actions = []
        existing_claim_id = str(item.get("existing_claim_id", "")).strip()
        if first_session_id:
            actions.append(f'<a class="quick-link" href="{_page_href("claims")}?appointment_id={first_session_id}#claims837">Precargar claim</a>')
            actions.append(f'<a class="quick-link" href="{_page_href("aba_notes")}?appointment_id={first_session_id}#session-ops-detail">Ver sesiones</a>')
        if existing_claim_id:
            actions.append(f'<a class="quick-link" href="/claim-edi?claim_id={quote(existing_claim_id)}">837</a>')
        else:
            actions.append(
                '<form class="table-action-form" method="post" action="/generate-claim-batch">'
                f'<input type="hidden" name="batch_id" value="{html.escape(str(item.get("batch_id", "")))}">'
                '<button class="small-button" type="submit">Generar 837</button>'
                "</form>"
            )
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('client_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('provider_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('authorization_number', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('period_start', '')) or '-')} - {html.escape(str(item.get('period_end', '')) or '-')}</td>"
            f"<td>{int(float(item.get('session_count', 0) or 0))}</td>"
            f"<td>{html.escape(str(item.get('units_by_cpt_label', '')) or '-')}<br><small>{html.escape(claim_amount_markup)}</small></td>"
            f"<td>{_session_status_badge(str(item.get('status', '')).title())}<br><small>{html.escape(validation_copy)}</small></td>"
            f"<td><div class=\"quick-links\">{''.join(actions) or '-'}</div></td>"
            "</tr>"
        )
    return "".join(rows)


def _render_session_timeline_markup(items: list[dict[str, object]]) -> str:
    if not items:
        return '<p class="helper-note">Todavia no hay timeline disponible para esta sesion.</p>'
    rows = []
    for item in items:
        status = str(item.get("status", "")).strip().lower()
        if status == "done":
            tone = "success"
        elif status == "warning":
            tone = "danger"
        elif status == "current":
            tone = "warn"
        else:
            tone = "neutral"
        rows.append(
            '<div class="session-timeline-item">'
            f'<span class="profile-pill {tone}">{html.escape(str(item.get("step", "")))}</span>'
            '<div class="stack-grid">'
            f'<strong>{html.escape(str(item.get("at", "")) or "-")}</strong>'
            f'<span>{html.escape(str(item.get("owner", "")) or "Sistema")}</span>'
            f'<small>{html.escape(str(item.get("note", "")) or "-")}</small>'
            "</div>"
            "</div>"
        )
    return '<div class="session-timeline-list">' + "".join(rows) + "</div>"


def _render_session_validation_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="3">Todavia no hay validaciones para esta sesion.</td></tr>'
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('validation_type', '')).replace('_', ' ').title())}</td>"
            f"<td>{_session_status_badge(str(item.get('status', '')).upper())}</td>"
            f"<td>{html.escape(str(item.get('message', '')) or '-')}</td>"
            "</tr>"
        )
    return "".join(rows)


def _render_eligibility_history_rows(items: list[dict[str, object]]) -> str:
    if not items:
        return '<tr><td colspan="9">Todavia no hay historial de elegibilidad.</td></tr>'

    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('checked_at', '')))}</td>"
            f"<td>{html.escape(str(item.get('insured_name', '')))}</td>"
            f"<td>{html.escape(str(item.get('payer_name', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('policy_number', '')))}</td>"
            f"<td>{html.escape(str(item.get('benefit', '')))}</td>"
            f"<td>{html.escape(str(item.get('procedure', '')) or '-')}</td>"
            f"<td>{html.escape(str(item.get('status', '')))}</td>"
            f"<td>{html.escape(str(item.get('service_date', '')))}</td>"
            f"<td>{html.escape(str(item.get('actor_name', '')) or str(item.get('actor_username', '')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _uploaded_837_batch_summary(file_name: str, items: list[dict[str, object]]) -> str:
    clean_name = Path(file_name or "archivo_837.edi").name
    lines = [
        f"Archivo procesado: {clean_name}",
        f"Claims detectados: {len(items)}",
        "",
        "Claims guardados en batch:",
    ]
    for item in items:
        total_charge = float(item.get("total_charge_amount", 0) or 0)
        lines.append(
            "- "
            + f"{item.get('claim_id', '')} | {item.get('patient_name', '')} | {item.get('payer_name', '')} | "
            + f"DOS {item.get('service_date', '')} | ${total_charge:,.2f}"
        )
    return "\n".join(lines)


def _render_month_calendar(events: list[dict[str, object]]) -> str:
    today = datetime.now()
    year = today.year
    month = today.month
    cal = month_calendar.Calendar(firstweekday=6)
    events_by_day: dict[int, list[dict[str, object]]] = {}
    for event in events:
        raw_date = str(event.get("event_date", "")).strip()
        if not raw_date:
            continue
        try:
            event_date = datetime.strptime(raw_date, "%m/%d/%Y")
        except ValueError:
            continue
        if event_date.year != year or event_date.month != month:
            continue
        events_by_day.setdefault(event_date.day, []).append(event)

    day_labels = "".join(f"<th>{label}</th>" for label in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))
    week_rows = []
    for week in cal.monthdayscalendar(year, month):
        cells = []
        for day in week:
            if day == 0:
                cells.append('<td class="calendar-empty"></td>')
                continue
            badges = []
            for event in events_by_day.get(day, [])[:3]:
                badges.append(
                    f'<div class="calendar-event">{html.escape(str(event.get("title", "")))}</div>'
                )
            more_count = max(len(events_by_day.get(day, [])) - 3, 0)
            more_markup = f'<div class="calendar-more">+{more_count} mas</div>' if more_count else ""
            cells.append(
                "<td class=\"calendar-day\">"
                f"<strong>{day}</strong>"
                f"{''.join(badges)}"
                f"{more_markup}"
                "</td>"
            )
        week_rows.append(f"<tr>{''.join(cells)}</tr>")
    month_title = today.strftime("%B %Y")
    return (
        f"<div class=\"calendar-shell\"><div class=\"calendar-head\">{html.escape(month_title)}</div>"
        f"<table class=\"calendar-table\"><thead><tr>{day_labels}</tr></thead><tbody>{''.join(week_rows)}</tbody></table></div>"
    )


def _render_login_page(error: str = "") -> str:
    default_admin = ensure_default_admin_user()
    portal_label = str(load_system_configuration().get("portal_label", BRAND_SERVER_LABEL)).strip() or BRAND_SERVER_LABEL
    logo_markup = _platform_logo_markup(inline=True)
    logo_has_wordmark = _platform_logo_is_wordmark()
    error_markup = (
        f'<div class="login-alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(portal_label)} Login</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 20px;
      overflow: hidden;
      background: linear-gradient(135deg, #2f6fe4 0%, #4eb8ea 56%, #82e7c7 100%);
      font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      position: relative;
    }}
    body::before,
    body::after {{
      content: "";
      position: absolute;
      left: -10%;
      width: 120%;
      border-radius: 100% 100% 0 0;
      pointer-events: none;
    }}
    body::before {{
      bottom: -120px;
      height: 320px;
      background: rgba(9, 60, 155, 0.24);
    }}
    body::after {{
      bottom: -70px;
      height: 220px;
      background: rgba(255, 255, 255, 0.12);
    }}
    .login-shell {{
      position: relative;
      z-index: 1;
      width: min(680px, 100%);
      display: grid;
      justify-items: center;
      gap: 22px;
      padding: 28px 20px 24px;
    }}
    .login-hero {{
      display: grid;
      gap: 16px;
      justify-items: center;
      text-align: center;
      width: 100%;
    }}
    .login-card {{
      display: grid;
      gap: 16px;
      width: min(380px, 100%);
      padding: 0;
      background: transparent;
    }}
    .login-logo {{
      width: min(320px, 100%);
      min-height: 84px;
      display: flex;
      justify-content: center;
      align-items: center;
      position: relative;
      overflow: hidden;
      isolation: isolate;
      filter: drop-shadow(0 12px 28px rgba(7, 43, 112, 0.22));
    }}
    .login-logo::after {{
      content: "";
      position: absolute;
      inset: -28% 0;
      z-index: 2;
      pointer-events: none;
      background: linear-gradient(
        180deg,
        rgba(255, 255, 255, 0) 0%,
        rgba(255, 255, 255, 0.10) 28%,
        rgba(255, 255, 255, 0.72) 50%,
        rgba(255, 255, 255, 0.14) 68%,
        rgba(255, 255, 255, 0) 100%
      );
      mix-blend-mode: screen;
      transform: translateY(-145%);
      animation: loginLogoShimmer 4.8s ease-in-out infinite;
    }}
    .login-logo.official-logo {{
      width: min(760px, 100%);
      min-height: 168px;
    }}
    .login-logo img,
    .login-logo svg {{
      position: relative;
      z-index: 1;
      width: 100%;
      height: auto;
      display: block;
    }}
    @keyframes loginLogoShimmer {{
      0%,
      18% {{
        transform: translateY(-145%);
        opacity: 0;
      }}
      30% {{
        opacity: 1;
      }}
      58% {{
        transform: translateY(145%);
        opacity: 1;
      }}
      76%,
      100% {{
        transform: translateY(145%);
        opacity: 0;
      }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .login-logo::after {{
        animation: none;
        opacity: 0;
      }}
    }}
    .login-brand {{ display: grid; gap: 8px; justify-items: center; }}
    .login-brand-label {{
      display: none;
      margin: 0;
      color: rgba(255, 255, 255, 0.86);
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 0;
      color: #ffffff;
      font-size: clamp(24px, 3vw, 32px);
      line-height: 1.04;
      letter-spacing: -0.04em;
      text-shadow: 0 10px 28px rgba(7, 43, 112, 0.22);
    }}
    h2 {{
      margin: 0;
      color: #ffffff;
      font-size: 58px;
      line-height: 0.96;
      letter-spacing: -0.05em;
      text-shadow: 0 10px 28px rgba(7, 43, 112, 0.22);
    }}
    p {{ color: rgba(255, 255, 255, 0.92); line-height: 1.6; margin: 0; }}
    .login-copy {{
      max-width: 50ch;
      font-size: 14px;
      opacity: 0.96;
    }}
    .login-form-head {{
      display: grid;
      gap: 6px;
      justify-items: center;
      text-align: center;
    }}
    .login-form-kicker {{
      color: rgba(255, 255, 255, 0.82);
      font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .login-form-copy {{
      max-width: 40ch;
      font-size: 14px;
      color: rgba(255, 255, 255, 0.92);
    }}
    .field {{ display: grid; gap: 8px; margin-bottom: 12px; }}
    .field span {{
      display: none;
    }}
    input {{
      border: 1px solid rgba(255, 255, 255, 0.48);
      border-radius: 14px;
      padding: 15px 16px;
      font-size: 15px;
      color: #1a2f45;
      background: #ffffff;
      box-shadow: 0 12px 22px rgba(7, 43, 112, 0.18);
    }}
    button {{
      border: 1px solid rgba(11, 79, 189, 0.18);
      border-radius: 14px;
      min-height: 54px;
      padding: 14px 18px;
      color: #ffffff;
      font: 700 18px/1 "Trebuchet MS", Verdana, sans-serif;
      cursor: pointer;
      background: linear-gradient(135deg, #0f5cd8 0%, #0b4fbd 100%);
      box-shadow: 0 16px 30px rgba(7, 43, 112, 0.28);
      transition: background 160ms ease, color 160ms ease, box-shadow 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
      background: linear-gradient(135deg, #0d54cb 0%, #0a46aa 100%);
      border-color: rgba(7, 43, 112, 0.28);
      box-shadow: 0 20px 34px rgba(7, 43, 112, 0.30);
    }}
    .login-alert {{
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.16);
      color: #ffffff;
      border: 1px solid rgba(255, 255, 255, 0.28);
      box-shadow: 0 10px 18px rgba(7, 43, 112, 0.12);
    }}
    .login-form {{
      display: grid;
      gap: 2px;
      width: 100%;
    }}
    .login-footer {{
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 14px;
      align-items: center;
      color: rgba(255, 255, 255, 0.92);
    }}
    .login-divider {{
      color: rgba(255, 255, 255, 0.72);
      font-weight: 700;
    }}
    .login-note {{
      width: min(520px, 100%);
      padding: 14px 18px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.12);
      color: #ffffff;
      border: 1px solid rgba(255, 255, 255, 0.22);
      box-shadow: 0 12px 24px rgba(7, 43, 112, 0.12);
      line-height: 1.7;
      text-align: center;
    }}
    .login-link {{
      display: inline-block;
      color: #ffffff;
      font: 700 14px/1 "Trebuchet MS", Verdana, sans-serif;
      text-decoration: none;
    }}
    code {{
      background: rgba(255, 255, 255, 0.18);
      border-radius: 8px;
      padding: 2px 6px;
    }}
    @media (max-width: 720px) {{
      h2 {{
        font-size: 36px;
      }}
      .login-shell {{
        padding-inline: 10px;
      }}
    }}
  </style>
</head>
<body>
  <main class="login-shell">
    <section class="login-hero">
      <div class="login-logo{' official-logo' if logo_has_wordmark else ''}">{logo_markup}</div>
      <div class="login-brand">
        {f'<h2>{html.escape(BRAND_NAME)}</h2>' if not logo_has_wordmark else ''}
        <p class="login-copy">{html.escape(portal_label)}</p>
      </div>
    </section>
    <section class="login-card">
      {error_markup}
      <form class="login-form" method="post" action="/login">
        <label class="field">
          <span>Username</span>
          <input name="username" autocomplete="username">
        </label>
        <label class="field">
          <span>Password</span>
          <input type="password" name="password" autocomplete="current-password">
        </label>
        <button type="submit">Sign In</button>
      </form>
      <div class="login-footer">
        <a class="login-link" href="/recover-password">Forgot Password?</a>
        <span class="login-divider">|</span>
        <span class="login-form-copy">Secure access only</span>
      </div>
      <div class="login-note">
        Usuario inicial: <code>{html.escape(str(default_admin.get('username', 'admin')))}</code><br>
        Password inicial: <code>TFBilling2026!</code><br>
        Cambialo en cuanto entres y agregues tus propios usuarios.
      </div>
    </section>
  </main>
</body>
</html>
"""


def _render_recovery_page(
    error: str = "",
    result_title: str = "",
    result_body: str = "",
    username: str = "",
) -> str:
    portal_label = str(load_system_configuration().get("portal_label", BRAND_SERVER_LABEL)).strip() or BRAND_SERVER_LABEL
    password_reset_minutes = get_password_reset_minutes()
    error_markup = f'<div class="login-alert">{html.escape(error)}</div>' if error else ""
    result_markup = (
        "<div class=\"login-success\">"
        f"<strong>{html.escape(result_title)}</strong><br>{html.escape(result_body)}"
        "</div>"
        if result_title or result_body
        else ""
    )
    safe_username = html.escape(username)
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(portal_label)} Recuperacion</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 58%, #0c9249 100%);
      font-family: "Palatino Linotype", Georgia, serif;
    }}
    .login-shell {{
      width: min(720px, calc(100vw - 32px));
      background: rgba(255, 255, 255, 0.96);
      border-radius: 26px;
      padding: 28px;
      box-shadow: 0 30px 60px rgba(4, 20, 33, 0.24);
      display: grid;
      gap: 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    h1 {{ margin: 0 0 6px; color: #143d61; }}
    h2 {{ margin: 0 0 6px; color: #143d61; font-size: 22px; }}
    p {{ color: #466179; line-height: 1.55; margin: 0; }}
    .panel {{
      border: 1px solid rgba(20, 61, 97, 0.10);
      border-radius: 18px;
      padding: 18px;
      background: #f9fcff;
    }}
    .field {{ display: grid; gap: 8px; margin-bottom: 14px; }}
    .field span {{ font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif; text-transform: uppercase; letter-spacing: 0.08em; color: #0b4aa0; }}
    input {{
      border: 1px solid rgba(20, 61, 97, 0.14);
      border-radius: 14px;
      padding: 14px;
      font-size: 15px;
    }}
    button {{
      border: 1px solid rgba(13, 81, 184, 0.14);
      border-radius: 999px;
      padding: 14px 18px;
      color: #0d51b8;
      font: 700 14px/1 "Trebuchet MS", Verdana, sans-serif;
      cursor: pointer;
      background: linear-gradient(180deg, #f8fbff 0%, #e3edf8 100%);
      box-shadow: 0 10px 18px rgba(16, 43, 69, 0.06);
      transition: background 160ms ease, color 160ms ease, box-shadow 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
      color: #ffffff;
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 100%);
      border-color: rgba(16, 54, 93, 0.22);
      box-shadow: 0 18px 28px rgba(16, 43, 69, 0.14);
    }}
    .login-alert {{
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(159, 47, 47, 0.10);
      color: #8b1f1f;
      border: 1px solid rgba(159, 47, 47, 0.16);
    }}
    .login-success {{
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(21, 122, 97, 0.10);
      color: #115545;
      border: 1px solid rgba(21, 122, 97, 0.16);
      line-height: 1.55;
    }}
    .login-link {{
      color: #0b4aa0;
      font: 700 14px/1 "Trebuchet MS", Verdana, sans-serif;
    }}
    @media (max-width: 760px) {{
      .grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="login-shell">
    <div>
      <h1>{html.escape(portal_label)}</h1>
      <p>Genera un codigo temporal de recuperacion y luego usalo para definir un password nuevo. En esta version local, el codigo dura {password_reset_minutes} minutos.</p>
    </div>
    {error_markup}
    {result_markup}
    <div class="grid">
      <form class="panel" method="post" action="/recover-password">
        <h2>1. Generar codigo</h2>
        <label class="field">
          <span>Username</span>
          <input name="username" value="{safe_username}" autocomplete="username">
        </label>
        <button type="submit">Generar codigo</button>
      </form>

      <form class="panel" method="post" action="/reset-password">
        <h2>2. Restablecer password</h2>
        <label class="field">
          <span>Username</span>
          <input name="username" value="{safe_username}" autocomplete="username">
        </label>
        <label class="field">
          <span>Codigo de recuperacion</span>
          <input name="recovery_code" inputmode="numeric" maxlength="6">
        </label>
        <label class="field">
          <span>Nuevo password</span>
          <input type="password" name="new_password">
        </label>
        <label class="field">
          <span>Confirmar password</span>
          <input type="password" name="confirm_password">
        </label>
        <button type="submit">Actualizar password</button>
      </form>
    </div>
    <a class="login-link" href="/login">Volver al login</a>
  </main>
</body>
</html>
"""


def _render_mfa_page(username: str, error: str = "") -> str:
    portal_label = str(load_system_configuration().get("portal_label", BRAND_SERVER_LABEL)).strip() or BRAND_SERVER_LABEL
    error_markup = f'<div class="login-alert">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(portal_label)} MFA</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 58%, #0c9249 100%);
      font-family: "Palatino Linotype", Georgia, serif;
    }}
    .login-shell {{
      width: min(520px, calc(100vw - 32px));
      background: rgba(255, 255, 255, 0.96);
      border-radius: 26px;
      padding: 28px;
      box-shadow: 0 30px 60px rgba(4, 20, 33, 0.24);
    }}
    h1 {{ margin: 0 0 10px; color: #143d61; }}
    p {{ color: #466179; line-height: 1.55; }}
    .field {{ display: grid; gap: 8px; margin-bottom: 14px; }}
    .field span {{ font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif; text-transform: uppercase; letter-spacing: 0.08em; color: #0b4aa0; }}
    input {{
      border: 1px solid rgba(20, 61, 97, 0.14);
      border-radius: 14px;
      padding: 14px;
      font-size: 15px;
    }}
    button {{
      border: 1px solid rgba(13, 81, 184, 0.14);
      border-radius: 999px;
      padding: 14px 18px;
      color: #0d51b8;
      font: 700 14px/1 "Trebuchet MS", Verdana, sans-serif;
      cursor: pointer;
      background: linear-gradient(180deg, #f8fbff 0%, #e3edf8 100%);
      box-shadow: 0 10px 18px rgba(16, 43, 69, 0.06);
      transition: background 160ms ease, color 160ms ease, box-shadow 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
      color: #ffffff;
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 100%);
      border-color: rgba(16, 54, 93, 0.22);
      box-shadow: 0 18px 28px rgba(16, 43, 69, 0.14);
    }}
    .login-alert {{
      margin: 0 0 14px;
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(159, 47, 47, 0.10);
      color: #8b1f1f;
      border: 1px solid rgba(159, 47, 47, 0.16);
    }}
  </style>
</head>
<body>
  <main class="login-shell">
    <h1>{html.escape(portal_label)}</h1>
    <p>El usuario <strong>{html.escape(username)}</strong> requiere codigo MFA. Abre tu app de autenticacion y escribe el codigo de 6 digitos.</p>
    {error_markup}
    <form method="post" action="/verify-mfa">
      <label class="field">
        <span>Codigo MFA</span>
        <input name="mfa_code" autocomplete="one-time-code" inputmode="numeric" maxlength="6">
      </label>
      <button type="submit">Verificar codigo</button>
    </form>
  </main>
</body>
</html>
"""


def _render_page(
    result_title: str = "",
    result_body: str = "",
    error: str = "",
    current_page: str = "dashboard",
    current_user: dict[str, str] | None = None,
    claim_form: dict[str, str] | None = None,
    eligibility_form: dict[str, str] | None = None,
    email_form: dict[str, str] | None = None,
    client_form: dict[str, str] | None = None,
    authorization_form: dict[str, str] | None = None,
    payer_config_form: dict[str, str] | None = None,
    payer_enrollment_form: dict[str, str] | None = None,
    agency_form: dict[str, str] | None = None,
    provider_contract_form: dict[str, str] | None = None,
    user_form: dict[str, str] | None = None,
    agenda_form: dict[str, str] | None = None,
    note_form: dict[str, str] | None = None,
    aba_notes_form: dict[str, str] | None = None,
    system_config_form: dict[str, str] | None = None,
    edi837_form: dict[str, str] | None = None,
    era_form: dict[str, str] | None = None,
    roster_form: dict[str, str] | None = None,
    ai_result: dict[str, object] | None = None,
    edi837_payload: str | None = None,
    era_payload: str | None = None,
    operations_selected_session_id: str = "",
    selected_client_id: str = "",
    active_panel: str = "",
) -> str:
    claim_values = _merge_form_values(_claim_form_defaults(), claim_form)
    claim_preview = _claim_service_preview(claim_values)
    claim_line_1_preview = claim_preview["line_1"]
    claim_line_2_preview = claim_preview["line_2"]
    claim_line_3_preview = claim_preview["line_3"]
    eligibility_values = _merge_form_values(_eligibility_form_defaults(), eligibility_form)
    email_values = _merge_form_values(_email_form_defaults(), email_form)
    client_values = _merge_form_values(_client_form_defaults(), client_form)
    authorization_values = _merge_form_values(_authorization_form_defaults(), authorization_form)
    payer_config_values = _merge_form_values(_payer_config_form_defaults(), payer_config_form)
    payer_enrollment_values = _merge_form_values(_payer_enrollment_form_defaults(), payer_enrollment_form)
    agency_values = _merge_form_values(_agency_form_defaults(), agency_form)
    provider_contract_values = _merge_form_values(_provider_contract_form_defaults(), provider_contract_form)
    user_values = _merge_form_values(_user_form_defaults(), user_form)
    agenda_values = _merge_form_values(_agenda_form_defaults(), agenda_form)
    note_values = _merge_form_values(_note_form_defaults(), note_form)
    aba_notes_values = _merge_form_values(_aba_notes_form_defaults(), aba_notes_form)
    system_config = load_system_configuration()
    system_config_values = _merge_form_values(_system_config_form_defaults(system_config), system_config_form)
    edi837_values = _merge_form_values(_edi837_form_defaults(), edi837_form)
    era_values = _merge_form_values(_era_form_defaults(), era_form)
    roster_values = _merge_form_values(_roster_form_defaults(), roster_form)
    edi837_example = html.escape(edi837_payload if edi837_payload is not None else _edi837_default())
    era_example = html.escape(era_payload if era_payload is not None else "")
    current_agency = get_current_agency()
    if current_agency and agency_form is None:
        agency_values.update(
            {
                "agency_id": str(current_agency.get("agency_id", "")),
                "agency_name": str(current_agency.get("agency_name", "")),
                "agency_code": str(current_agency.get("agency_code", "")),
                "notification_email": str(current_agency.get("notification_email", "")),
                "contact_name": str(current_agency.get("contact_name", "")),
                "notes": str(current_agency.get("notes", "")),
            }
        )
    run_provider_document_expiration_checks(str(current_agency.get("agency_id", "")) if current_agency else "")
    security_profile = get_user_security_profile(str((current_user or {}).get("username", ""))) if current_user else {}
    display_user = {**current_user, **security_profile} if current_user else None
    today_date = datetime.now().date()
    logo_markup = _logo_markup(current_agency)
    brand_logo_is_wordmark = _brand_logo_is_wordmark(current_agency)
    avatar_markup = _avatar_markup(display_user)
    status_block = _status_banner(result_title, result_body, error)
    all_claims = list_claims()
    clients = list_clients()
    authorizations = list_authorizations()
    payer_configs = list_payer_configurations()
    payer_enrollments = list_payer_enrollments()
    roster_entries = list_eligibility_roster()
    agencies = list_agencies()
    provider_contracts = list_provider_contracts()
    credential_status_map = _provider_credential_status_map(payer_enrollments)
    provider_contracts = [
        {
            **item,
            "credentialing_status_summary": credential_status_map.get(str(item.get("provider_name", "")).strip().lower(), "Sin credenciales"),
        }
        for item in provider_contracts
    ]
    provider_required_documents = list_provider_required_documents()
    client_required_documents = list_client_required_documents()
    notifications = list_notifications()
    era_archives = list_era_archives()
    users = list_users()
    linked_provider_type = ""
    if display_user:
        linked_provider_name_key = str(display_user.get("linked_provider_name", "")).strip().lower()
        for item in provider_contracts:
            provider_name_key = str(item.get("provider_name", "")).strip().lower()
            if linked_provider_name_key and provider_name_key == linked_provider_name_key:
                linked_provider_type = str(item.get("provider_type", "")).strip()
                break
        display_user = {**display_user, "linked_provider_type": linked_provider_type}
    normalized_user_role = normalized_role_from_user(display_user, provider_contracts)
    clients = filter_clients_for_user(display_user, clients, provider_contracts) if display_user else []
    visible_provider_contracts = filter_provider_contracts_for_user(display_user, provider_contracts, clients) if display_user else []
    authorizations = filter_authorizations_for_user(display_user, authorizations, clients, provider_contracts) if display_user else []
    all_claims = filter_claims_for_user(display_user, all_claims, clients, provider_contracts) if display_user else []
    claim_summary = _claim_summary_from_items(all_claims)
    assignable_users = [
        item
        for item in users
        if item.get("active", True) and not is_provider_role(item, provider_contracts)
    ]
    calendar_events = list_calendar_events()
    eligibility_history = list_eligibility_history(limit=30)
    claim_audit_logs = list_claim_audit_logs(limit=40)
    security_audit_logs = list_system_audit_logs(category="security", limit=30)
    user_audit_logs = list_system_audit_logs(entity_type="user", limit=25)
    client_audit_logs = list_system_audit_logs(entity_type="client", limit=25)
    authorization_audit_logs = list_system_audit_logs(category="authorization", limit=60)
    provider_audit_logs = list_system_audit_logs(entity_type="provider", limit=40)
    enrollment_audit_logs = list_system_audit_logs(entity_type="enrollment", limit=25)
    my_notes = list_user_notes(str((display_user or {}).get("username", ""))) if current_user else []
    my_tasks = [
        item
        for item in calendar_events
        if str(item.get("assigned_username", "")).strip().lower() == str((display_user or {}).get("username", "")).strip().lower()
    ]
    current_agency_name = _display_context_agency_name((current_agency or {}).get("agency_name", ""))
    current_agency_id = str(current_agency.get("agency_id", "")) if current_agency else ""
    user_role = normalized_user_role
    linked_provider_name = str((display_user or {}).get("linked_provider_name", "")).strip()
    aba_access_provider_contracts = (
        provider_contracts
        if has_any_permission(display_user, ("sessions.view", "claims.view", "billing.view"), provider_contracts)
        else visible_provider_contracts
    )
    aba_supported_provider_contracts = [
        item
        for item in aba_access_provider_contracts
        if str(item.get("provider_type", "")).strip().upper().replace(" ", "") in {"BCBA", "BCABA", "RBT"}
    ]
    client_bcba_contracts = [
        item for item in aba_supported_provider_contracts
        if str(item.get("provider_type", "")).strip().upper().replace(" ", "") == "BCBA"
    ]
    client_bcaba_contracts = [
        item for item in aba_supported_provider_contracts
        if str(item.get("provider_type", "")).strip().upper().replace(" ", "") == "BCABA"
    ]
    client_rbt_contracts = [
        item for item in aba_supported_provider_contracts
        if str(item.get("provider_type", "")).strip().upper().replace(" ", "") == "RBT"
    ]
    aba_visible_provider_ids = {
        str(item.get("contract_id", "")).strip()
        for item in aba_supported_provider_contracts
        if str(item.get("contract_id", "")).strip()
    }
    aba_allow_unassigned_fallback = not is_provider_role(display_user, provider_contracts)
    if not aba_notes_values.get("supervisor_name") and display_user:
        aba_notes_values["supervisor_name"] = str((display_user or {}).get("full_name", "") or (display_user or {}).get("username", ""))
    aba_selected_client_id = str(aba_notes_values.get("client_id", "")).strip()
    aba_provider_options = list_aba_provider_options(
        aba_visible_provider_ids,
        aba_selected_client_id,
        allow_unassigned_fallback=False,
    )
    visible_aba_provider_ids = {
        str(item.get("provider_contract_id", "")).strip()
        for item in aba_provider_options
        if str(item.get("provider_contract_id", "")).strip()
    }
    if str(aba_notes_values.get("provider_contract_id", "")).strip() not in visible_aba_provider_ids:
        aba_notes_values["provider_contract_id"] = ""
    if not aba_notes_values.get("provider_contract_id") and aba_provider_options:
        aba_notes_values["provider_contract_id"] = str(aba_provider_options[0].get("provider_contract_id", ""))
    aba_client_options = list_aba_client_options(
        str(aba_notes_values.get("provider_contract_id", "")),
        aba_visible_provider_ids,
        allow_unassigned_fallback=aba_allow_unassigned_fallback,
    )
    visible_aba_client_ids = {
        str(item.get("client_id", "")).strip()
        for item in aba_client_options
        if str(item.get("client_id", "")).strip()
    }
    if aba_client_options and str(aba_notes_values.get("client_id", "")).strip() not in visible_aba_client_ids:
        aba_notes_values["client_id"] = str(aba_client_options[0].get("client_id", ""))
    aba_provider_options = list_aba_provider_options(
        aba_visible_provider_ids,
        str(aba_notes_values.get("client_id", "")),
        allow_unassigned_fallback=False,
    )
    visible_aba_provider_ids = {
        str(item.get("provider_contract_id", "")).strip()
        for item in aba_provider_options
        if str(item.get("provider_contract_id", "")).strip()
    }
    if str(aba_notes_values.get("provider_contract_id", "")).strip() not in visible_aba_provider_ids:
        aba_notes_values["provider_contract_id"] = str(aba_provider_options[0].get("provider_contract_id", "")) if aba_provider_options else ""
    aba_client_options = list_aba_client_options(
        str(aba_notes_values.get("provider_contract_id", "")),
        aba_visible_provider_ids,
        allow_unassigned_fallback=aba_allow_unassigned_fallback,
    )
    visible_aba_client_ids = {
        str(item.get("client_id", "")).strip()
        for item in aba_client_options
        if str(item.get("client_id", "")).strip()
    }
    if aba_client_options and str(aba_notes_values.get("client_id", "")).strip() not in visible_aba_client_ids:
        aba_notes_values["client_id"] = str(aba_client_options[0].get("client_id", ""))
    aba_billing_preview = get_aba_billing_preview(
        str(aba_notes_values.get("provider_contract_id", "")),
        str(aba_notes_values.get("client_id", "")),
        str(aba_notes_values.get("service_context", "")),
        str(aba_notes_values.get("appointment_date", "")),
        str(aba_notes_values.get("start_time", "")),
        str(aba_notes_values.get("end_time", "")),
        aba_visible_provider_ids,
        allow_unassigned_fallback=aba_allow_unassigned_fallback,
    )
    aba_appointment_date_html = _html_date_value(str(aba_notes_values.get("appointment_date", "")))
    aba_start_hour, aba_start_minute, aba_start_ampm = _time_parts(str(aba_notes_values.get("start_time", "")))
    aba_end_hour, aba_end_minute, aba_end_ampm = _time_parts(str(aba_notes_values.get("end_time", "")))
    aba_appointments = filter_sessions_for_user(display_user, list_aba_appointments(aba_visible_provider_ids), clients, provider_contracts)
    aba_service_logs = list_aba_service_logs(aba_visible_provider_ids)
    operational_sessions = filter_sessions_for_user(display_user, list_operational_sessions(aba_visible_provider_ids), clients, provider_contracts)
    operational_dashboards = build_operations_dashboards(operational_sessions)
    ready_claim_batches = build_claim_batches(operational_sessions)
    claim_batches = ready_claim_batches
    claim_batch_source_label = "sesiones ready"
    if not claim_batches and operational_sessions:
        claim_batches = build_claim_batches(operational_sessions, include_non_ready=True)
        claim_batch_source_label = "sesiones con warning/error"
    visible_operational_session_ids = {
        str(item.get("session_id", "")).strip()
        for item in operational_sessions
        if str(item.get("session_id", "")).strip()
    }
    if operations_selected_session_id not in visible_operational_session_ids:
        operations_selected_session_id = ""
    if not operations_selected_session_id and operational_sessions and current_page == "aba_notes":
        operations_selected_session_id = str(operational_sessions[0].get("session_id", ""))
    selected_operational_session = (
        get_operational_session_detail(operations_selected_session_id, aba_visible_provider_ids)
        if operations_selected_session_id
        else None
    )
    billing_queue_ready = [
        item for item in operational_sessions if str(item.get("billing_queue_status", "")).strip().lower() == "ready"
    ]
    billing_queue_hold = [
        item for item in operational_sessions if str(item.get("billing_queue_status", "")).strip().lower() == "hold"
    ]
    claim_batches_preview = claim_batches[:12]
    follow_up_claims = [
        item
        for item in all_claims
        if (
            str(item.get("status", "")).strip().lower() in {"pending", "partial", "denied"}
            or str(item.get("transmission_status", "")).strip().lower() == "transmitted"
        )
    ]
    denied_claims = [item for item in all_claims if str(item.get("status", "")).strip().lower() == "denied"]
    rejected_claims = [
        item
        for item in all_claims
        if (
            str(item.get("status", "")).strip().lower() == "draft"
            and str(item.get("transmission_status", "")).strip().lower() != "transmitted"
        )
    ]
    if current_page == "claims" and claim_form is None and operations_selected_session_id:
        selected_claim_form = build_claim_form_from_session(operations_selected_session_id, aba_visible_provider_ids)
        if selected_claim_form is not None:
            claim_values = _merge_form_values(_claim_form_defaults(), selected_claim_form)
            claim_preview = _claim_service_preview(claim_values)
            claim_line_1_preview = claim_preview["line_1"]
            claim_line_2_preview = claim_preview["line_2"]
            claim_line_3_preview = claim_preview["line_3"]
    selected_aba_log_id = str(aba_notes_values.get("selected_log_id", "")).strip()
    if not selected_aba_log_id and aba_service_logs:
        selected_aba_log_id = str(aba_service_logs[0].get("log_id", ""))
        aba_notes_values["selected_log_id"] = selected_aba_log_id
    selected_aba_log = get_aba_service_log_detail(selected_aba_log_id, aba_visible_provider_ids) if selected_aba_log_id else None
    can_manage_provider_contracts = has_permission(display_user, "providers.create", provider_contracts) or has_permission(display_user, "providers.edit", provider_contracts)
    can_manage_document_templates = has_permission(display_user, "providers.documents.verify", provider_contracts)
    can_manage_system_config = has_permission(display_user, "settings.manage", provider_contracts)
    can_edit_clients = has_permission(display_user, "clients.edit", provider_contracts)
    can_manage_authorizations = has_permission(display_user, "clients.authorizations.edit", provider_contracts)
    can_run_client_eligibility = has_any_permission(display_user, ("clients.view", "clients.assigned.view", "eligibility.view"), provider_contracts)
    can_manage_sessions = has_any_permission(display_user, ("sessions.create", "sessions.edit"), provider_contracts)
    can_manage_users = has_permission(display_user, "users.edit", provider_contracts)
    can_submit_claims = has_permission(display_user, "claims.submit", provider_contracts)
    can_view_supervision_center = has_permission(display_user, "admin.full", provider_contracts) or has_permission(display_user, "hr.pipeline.manage", provider_contracts)
    can_view_billing_rates = can_view_financial_rates(display_user, provider_contracts)
    can_view_claim_totals = can_view_financial_totals(display_user, provider_contracts)
    can_view_claim_paid_amounts = can_view_paid_amounts(display_user, provider_contracts)
    provider_portal_user = is_provider_role(display_user, provider_contracts)
    if not can_view_billing_rates:
        claim_preview["total_charge_amount_text"] = "Restricted"
        claim_preview["total_charge_amount_value"] = ""
        for index in range(1, 4):
            claim_values[f"service_line_{index}_unit_price"] = ""
            line_preview = claim_preview.get(f"line_{index}", {})
            if isinstance(line_preview, dict):
                line_preview["charge_text"] = "Restricted"
                line_preview["charge_amount_value"] = ""
    selected_aba_log_preview_body = _aba_preview_body_for_viewer(
        (selected_aba_log or {}).get("preview_body", ""),
        can_view_billing_rates,
    )
    selected_aba_log_preview_html = (
        render_note_html_document(
            title=str((selected_aba_log or {}).get("preview_title", "")).strip() or "Service Log",
            body=selected_aba_log_preview_body,
            agency_id=str((selected_aba_log or {}).get("agency_id", "")).strip(),
        )
        if selected_aba_log_preview_body.strip()
        else ""
    )
    provider_self_contract = visible_provider_contracts[0] if provider_portal_user and visible_provider_contracts else None
    supervision_contracts = provider_contracts if can_view_supervision_center else []
    supervision_open_contracts = [
        item for item in supervision_contracts if str(item.get("contract_stage", "")).strip().upper() != "ACTIVE"
    ]
    supervision_credential_pending = [
        item
        for item in supervision_contracts
        if (
            str(item.get("credentialing_due_date", "")).strip()
            and str(item.get("credentialing_status_summary", "")).strip().lower() != "enrolled"
        )
        or str(item.get("credentialing_status_summary", "")).strip().lower() in {"pending", "submitted", "follow up"}
    ]
    supervision_related_tasks = [
        item
        for item in calendar_events
        if str(item.get("related_provider", "")).strip()
        or str(item.get("category", "")).strip().lower() in {"follow_up", "credentialing", "deadline"}
    ]
    selected_authorization_client_id = str(authorization_values.get("client_id", "")).strip()
    selected_authorization_client = next(
        (item for item in clients if str(item.get("client_id", "")).strip() == selected_authorization_client_id),
        None,
    )
    selected_authorization_items = authorizations
    if selected_authorization_client is not None:
        selected_member_id = str(selected_authorization_client.get("member_id", "")).strip()
        selected_authorization_items = [
            item
            for item in authorizations
            if str(item.get("client_id", "")).strip() == selected_authorization_client_id
            or (
                selected_member_id
                and str(item.get("patient_member_id", "")).strip() == selected_member_id
            )
        ]
    authorization_return_page = "clients" if current_page == "clients" else "claims"
    if current_page == "clients":
        authorization_section_title = "Autorizaciones y units del cliente"
        authorization_section_copy = (
            "Carga la autorizacion desde la pagina del cliente sin volver a escribir member ID, nombre ni payer. "
            "Usa el boton Autorizacion en la tabla para traer el caso listo."
        )
        if selected_authorization_client is not None:
            selected_client_name = (
                f"{selected_authorization_client.get('first_name', '')} "
                f"{selected_authorization_client.get('last_name', '')}"
            ).strip()
            authorization_client_summary_markup = (
                "<div class=\"mini-table\">"
                f"<div class=\"mini-row\"><strong>Cliente cargado</strong><span>{html.escape(selected_client_name)}</span></div>"
                f"<div class=\"mini-row\"><strong>Member ID</strong><span>{html.escape(str(selected_authorization_client.get('member_id', '')))}</span></div>"
                f"<div class=\"mini-row\"><strong>Payer</strong><span>{html.escape(str(selected_authorization_client.get('payer_name', '')))}</span></div>"
                "</div>"
            )
            authorization_table_title = "Autorizaciones del cliente"
            authorization_table_copy = "Aqui ves las autorizaciones ya guardadas para el cliente que seleccionaste en la tabla."
        else:
            authorization_client_summary_markup = (
                "<div class=\"mini-table\">"
                "<div class=\"mini-row\"><strong>Cliente cargado</strong><span>Selecciona un cliente desde la tabla para precargar la autorizacion.</span></div>"
                "<div class=\"mini-row\"><strong>Member ID</strong><span>Pendiente</span></div>"
                "<div class=\"mini-row\"><strong>Payer</strong><span>Pendiente</span></div>"
                "</div>"
            )
            authorization_table_title = "Autorizaciones activas"
            authorization_table_copy = (
                "Selecciona un cliente desde la tabla para filtrar sus autorizaciones o llena el formulario manualmente si todavia no existe."
            )
    else:
        authorization_section_title = "Autorizaciones y unidades"
        authorization_section_copy = (
            "Registra la autorizacion del cliente con hasta 5 CPTs. El sistema calcula 6 meses automaticamente "
            "desde la fecha inicial, pero puedes editar la fecha final o las unidades restantes si el cliente viene "
            "a mitad de autorizacion de otra agencia."
        )
        authorization_client_summary_markup = ""
        authorization_table_title = "Autorizaciones activas"
        authorization_table_copy = (
            "Las unidades se descuentan cuando el claim facturado coincide con el member ID, CPT y periodo de autorizacion. "
            "Una misma autorizacion puede aparecer en varias lineas, una por cada CPT autorizado."
        )
    displayed_authorization_items = selected_authorization_items if current_page == "clients" else authorizations
    authorization_usage_cards_markup = _render_authorization_usage_cards(displayed_authorization_items)
    selected_client_lookup_id = str(selected_client_id).strip() or str(client_values.get("client_id", "")).strip() or selected_authorization_client_id
    selected_client = next(
        (item for item in clients if str(item.get("client_id", "")).strip() == selected_client_lookup_id),
        None,
    )
    show_client_form_panel = current_page == "clients" and active_panel == "clients"
    if current_page == "clients" and selected_client is not None and selected_authorization_client is None:
        selected_authorization_client = selected_client
        if not str(authorization_values.get("client_id", "")).strip():
            selected_authorization_defaults = _authorization_form_from_client(selected_client)
            authorization_values.update(
                {
                    key: value
                    for key, value in selected_authorization_defaults.items()
                    if not str(authorization_values.get(key, "")).strip()
                }
            )
        selected_authorization_items = _client_authorization_items(selected_client, authorizations)
        selected_client_name = (
            f"{selected_client.get('first_name', '')} "
            f"{selected_client.get('last_name', '')}"
        ).strip()
        authorization_client_summary_markup = (
            "<div class=\"mini-table\">"
            f"<div class=\"mini-row\"><strong>Cliente cargado</strong><span>{html.escape(selected_client_name or 'Pendiente')}</span></div>"
            f"<div class=\"mini-row\"><strong>Member ID</strong><span>{html.escape(str(selected_client.get('member_id', '')) or 'Pendiente')}</span></div>"
            f"<div class=\"mini-row\"><strong>Payer</strong><span>{html.escape(str(selected_client.get('payer_name', '')) or 'Pendiente')}</span></div>"
            "</div>"
        )
        authorization_table_title = "Autorizaciones del cliente"
        authorization_table_copy = "Aqui ves las autorizaciones ya guardadas para el cliente que seleccionaste en el expediente."
        displayed_authorization_items = selected_authorization_items
        authorization_usage_cards_markup = _render_authorization_usage_cards(displayed_authorization_items)
    client_authorization_workspace_markup = ""
    if current_page == "clients" and selected_client is not None:
        selected_authorization_audit_rows = _client_authorization_audit_items(selected_client, authorization_audit_logs)
        editing_authorization_group_id = str(authorization_values.get("authorization_group_id", "")).strip()
        authorization_form_title = "Editar autorizacion del cliente" if editing_authorization_group_id else "Nueva autorizacion del cliente"
        authorization_save_label = "Guardar cambios de autorizacion" if editing_authorization_group_id else "Guardar autorizacion"
        client_authorization_workspace_markup = (
            '<section class="dual-grid"'
            + _section_hidden(current_page, "clients")
            + ">"
            + '<form id="client-authorizations" class="panel section-card" data-skip-auto-collapsible="1" method="post" action="/add-authorization">'
            + f'<input type="hidden" name="return_page" value="{authorization_return_page}">'
            + f'<input type="hidden" name="authorization_group_id" value="{_field_value(authorization_values, "authorization_group_id")}">'
            + f'<input type="hidden" name="client_id" value="{_field_value(authorization_values, "client_id")}">'
            + f"<h2>{authorization_form_title}</h2>"
            + "<p>La autorizacion queda archivada dentro del cliente y conecta scheduler, notes, billing y claims sin duplicar data.</p>"
            + authorization_client_summary_markup
            + '<div class="field-grid">'
            + '<label class="field"><span>Member ID</span><input name="patient_member_id" value="'
            + _field_value(authorization_values, "patient_member_id")
            + '"></label>'
            + '<label class="field"><span>Paciente</span><input name="patient_name" value="'
            + _field_value(authorization_values, "patient_name")
            + '"></label>'
            + '<label class="field"><span>Payer</span><input name="payer_name" value="'
            + _field_value(authorization_values, "payer_name")
            + '"></label>'
            + '<label class="field"><span>Authorization Number</span><input name="authorization_number" value="'
            + _field_value(authorization_values, "authorization_number")
            + '"></label>'
            + '<label class="field"><span>Cuantos CPTs trae</span><select name="authorization_line_count">'
            + _authorization_line_count_options_markup(str(authorization_values.get("authorization_line_count", "5")))
            + "</select></label>"
            + '<label class="field"><span>Fecha inicio</span><input name="start_date" value="'
            + _field_value(authorization_values, "start_date")
            + '" placeholder="MM/DD/YYYY"></label>'
            + '<label class="field"><span>Fecha fin</span><input name="end_date" value="'
            + _field_value(authorization_values, "end_date")
            + '" placeholder="MM/DD/YYYY"></label>'
            + "</div>"
            + '<div class="form-section">'
            + '<div class="section-label">Lineas CPT de la autorizacion</div>'
            + '<p class="module-note">`97155` es supervision/tratamiento del analista. Cuando el `BCBA/BCaBA` y el `RBT` estan concurrentes, el analista puede cobrar `97155` y para el `RBT` usas `97153-XP` como concurrent supervision no reimbursable.</p>'
            + '<div class="table-wrap"><table><thead><tr><th>Linea</th><th>CPT / descripcion</th><th>Total units</th><th>Remaining units</th></tr></thead><tbody>'
            + _authorization_line_rows_markup(authorization_values)
            + "</tbody></table></div>"
            + "</div>"
            + '<label class="field"><span>Notas</span><input name="notes" value="'
            + _field_value(authorization_values, "notes")
            + '"></label>'
            + "<div class=\"directory-card-actions directory-card-actions-left\">"
            + f"<button type=\"submit\">{authorization_save_label}</button>"
            + (
                f'<a class="small-button" href="{html.escape(_client_expediente_href(selected_client, "client-authorizations"))}">Cancelar edicion</a>'
                if editing_authorization_group_id
                else ""
            )
            + "</div>"
            + "</form>"
            + '<div class="stack-grid">'
            + _authorization_session_summary_markup(selected_client, operational_sessions)
            + '<article class="panel section-card" data-skip-auto-collapsible="1">'
            + f"<h2>{authorization_table_title}</h2>"
            + f'<p class="module-note">{authorization_table_copy}</p>'
            + authorization_usage_cards_markup
            + '<div class="table-wrap"><table><thead><tr><th>Auth #</th><th>Linea</th><th>Paciente</th><th>CPT</th><th>Inicio</th><th>Fin</th><th>Total</th><th>Restante</th><th>Estatus</th>'
            + ('<th>Acciones</th>' if can_manage_authorizations else '')
            + '</tr></thead><tbody>'
            + _render_authorization_rows(displayed_authorization_items, client=selected_client, can_manage=can_manage_authorizations)
            + "</tbody></table></div>"
            + "</article>"
            + '<article class="panel section-card" data-skip-auto-collapsible="1">'
            + "<h2>Auditoria de autorizaciones</h2>"
            + "<p>Desde aqui puedes revisar cuando se creo, edito o borro una autorizacion del cliente.</p>"
            + '<div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Registro</th><th>Accion</th><th>Nombre</th><th>Username</th><th>Categoria</th><th>Detalle</th></tr></thead><tbody>'
            + _render_system_audit_rows(selected_authorization_audit_rows, "Todavia no hay auditoria de autorizaciones para este cliente.")
            + "</tbody></table></div>"
            + "</article>"
            + "</div>"
            + "</section>"
        )
    selected_provider_contract_id = str(provider_contract_values.get("contract_id", "")).strip()
    selected_provider_contract = next(
        (item for item in visible_provider_contracts if str(item.get("contract_id", "")).strip() == selected_provider_contract_id),
        None,
    )
    if selected_provider_contract is None and provider_self_contract is not None:
        selected_provider_contract = provider_self_contract
    selected_provider_audit_logs = []
    if selected_provider_contract is not None:
        selected_provider_id = str(selected_provider_contract.get("contract_id", "")).strip()
        selected_provider_audit_logs = [
            item
            for item in provider_audit_logs
            if str(item.get("entity_id", "")).strip() == selected_provider_id
        ][:4]
    selected_provider_contract_values = (
        provider_contract_values
        if selected_provider_contract is not None
        and str(selected_provider_contract.get("contract_id", "")).strip() == selected_provider_contract_id
        else _provider_contract_form_from_record(selected_provider_contract)
        if selected_provider_contract is not None
        else provider_contract_values
    )
    show_new_provider_form = (
        current_page == "providers"
        and active_panel == "provider_contract"
        and selected_provider_contract is None
    )
    ai_result_domain = str((ai_result or {}).get("domain", "")).strip()
    aba_ai_result_markup = _render_ai_result_card(ai_result) if current_page == "aba_notes" and ai_result_domain == "aba_notes" else ""
    claims_ai_result_markup = _render_ai_result_card(ai_result) if current_page == "claims" and ai_result_domain == "claims" else ""
    providers_ai_result_markup = _render_ai_result_card(ai_result) if current_page == "providers" and ai_result_domain == "providers" else ""
    provider_document_warning = _provider_document_warning_markup(visible_provider_contracts, display_user)
    allowed_pages = _pages_for_user(display_user)
    primary_action_label, primary_action_href = _workspace_primary_action(
        current_page,
        allowed_pages,
        can_manage_provider_contracts,
        can_edit_clients=can_edit_clients,
        can_submit_claims=can_submit_claims,
        can_manage_sessions=can_manage_sessions,
        can_manage_users=can_manage_users,
    )
    sidebar_nav_markup = _sidebar_nav_markup(current_page, allowed_pages, display_user, provider_contracts)
    current_user_name = str((display_user or {}).get("full_name", "")) or str((display_user or {}).get("username", ""))
    current_user_first_name = current_user_name.split()[0] if current_user_name else "Equipo"
    greeting_label = f"{_time_of_day_greeting()}, {current_user_first_name}"
    summary_title, summary_text, summary_class = _operation_summary(result_title, error, active_panel, current_user_name)
    password_reset_minutes = int(system_config.get("password_reset_minutes", 30) or 30)
    session_timeout_minutes = int(system_config.get("session_timeout_minutes", 30) or 30)
    mfa_timeout_minutes = int(system_config.get("mfa_timeout_minutes", 10) or 10)
    billing_unit_minutes = int(system_config.get("billing_unit_minutes", 15) or 15)
    eligibility_interval_hours = int(system_config.get("eligibility_check_interval_hours", 6) or 6)
    eligibility_run_days_label = ", ".join(str(day) for day in system_config.get("eligibility_run_days", [1, 15]))
    portal_label = str(system_config.get("portal_label", BRAND_SERVER_LABEL)).strip() or BRAND_SERVER_LABEL
    default_landing_page_label = PERMISSION_PAGE_LABELS.get(str(system_config.get("default_landing_page", "dashboard")), "Dashboard")
    current_user_role_label = role_label(user_role, linked_provider_type=linked_provider_type)
    my_pending_tasks = [item for item in my_tasks if str(item.get("status", "")).upper() != "DONE"]
    raw_contact_email = str(security_profile.get("email", "")).strip()
    raw_contact_phone = str(security_profile.get("phone", "")).strip()
    contact_email = html.escape(raw_contact_email)
    contact_phone = html.escape(raw_contact_phone)
    contact_phone_href = "".join(char for char in raw_contact_phone if char.isdigit() or char == "+")
    current_page_label = PERMISSION_PAGE_LABELS.get(current_page, "Dashboard")
    today_label = datetime.now().strftime("%m/%d/%Y")
    queued_notifications = len(notifications)
    active_clients = len([item for item in clients if item.get("active", True)])
    inactive_clients = len(clients) - active_clients
    active_providers_count = len(
        [item for item in visible_provider_contracts if _provider_directory_status(item) == "active"]
    )
    inactive_providers_count = len(visible_provider_contracts) - active_providers_count
    active_users_count = len([item for item in users if item.get("active", True)])
    inactive_users_count = len(users) - active_users_count
    active_payers_count = len([item for item in payer_configs if item.get("active", True)])
    inactive_payers_count = len(payer_configs) - active_payers_count
    eligibility_used = count_eligibility_history()
    auto_eligibility_clients_count = len([item for item in clients if item.get("auto_eligibility", True)])
    active_authorization_count = len(
        [item for item in authorizations if bool(item.get("active", True)) and float(item.get("remaining_units", 0) or 0) > 0]
    )
    active_roster_entries_count = len([item for item in roster_entries if item.get("active", True)])
    office_staff_count = len([item for item in users if not is_provider_role(item, provider_contracts)])
    linked_provider_users_count = len([item for item in users if str(item.get("linked_provider_name", "")).strip()])
    mfa_enabled_users_count = len([item for item in users if item.get("mfa_enabled", False)])
    notification_sent_count = len([item for item in notifications if str(item.get("email_status", "")).strip().lower() == "sent"])
    notification_draft_count = len([item for item in notifications if str(item.get("email_status", "")).strip().lower() == "drafted"])
    notification_error_count = len(
        [
            item
            for item in notifications
            if str(item.get("email_status", "")).strip().lower() in {"outlook_error", "needs_email_setup"}
        ]
    )
    overdue_tasks_count = 0
    for item in my_pending_tasks:
        due_date_value = parse_user_date(str(item.get("due_date", "")).strip() or str(item.get("event_date", "")).strip())
        if due_date_value is None:
            continue
        due_day = due_date_value.date() if isinstance(due_date_value, datetime) else due_date_value
        if due_day < today_date:
            overdue_tasks_count += 1
    enrollment_pending_count = len(
        [
            item
            for item in payer_enrollments
            if str(item.get("enrollment_status", "")).strip().upper() in {"SUBMITTED", "PENDING", "FOLLOW_UP", "REJECTED"}
        ]
    )
    enrollment_enrolled_count = len(
        [item for item in payer_enrollments if str(item.get("enrollment_status", "")).strip().upper() == "ENROLLED"]
    )
    agencies_with_logo_count = len([item for item in agencies if str(item.get("logo_file_name", "")).strip()])
    payers_with_rates_count = 0
    for item in payer_configs:
        rate_lines = item.get("rate_lines", [])
        if not isinstance(rate_lines, list):
            continue
        has_priced_line = False
        for line in rate_lines:
            try:
                if float(line.get("unit_price", 0) or 0) > 0:
                    has_priced_line = True
                    break
            except (TypeError, ValueError):
                continue
        if has_priced_line:
            payers_with_rates_count += 1
    clients_directory_toolbar_markup = _directory_toolbar_markup(
        directory_name="clients",
        action_href=f"{_page_href('clients')}?new_client=1#clientsdb" if can_edit_clients else "",
        active_count=active_clients,
        inactive_count=inactive_clients,
        agency_name=current_agency_name,
        action_label="Nuevo cliente" if can_edit_clients else "",
    )
    users_directory_toolbar_markup = _directory_toolbar_markup(
        directory_name="users",
        action_href="#users-directory-form" if can_manage_users else "",
        active_count=active_users_count,
        inactive_count=inactive_users_count,
        agency_name=current_agency_name,
        action_label="Nuevo usuario" if can_manage_users else "",
    )
    payers_directory_toolbar_markup = _directory_toolbar_markup(
        directory_name="payers",
        action_href="#payer-config-form",
        active_count=active_payers_count,
        inactive_count=inactive_payers_count,
        agency_name=current_agency_name,
        default_status="all",
    )
    providers_directory_toolbar_markup = _directory_toolbar_markup(
        directory_name="providers",
        action_href="",
        active_count=active_providers_count,
        inactive_count=inactive_providers_count,
        agency_name=current_agency_name,
        default_status="all",
        default_view="table",
        action_label="",
    )
    provider_user_lookup = _user_display_lookup(users)
    clients_directory_markup = _render_clients_directory(clients, authorizations)
    payers_directory_markup = _render_payers_directory(payer_configs)
    providers_directory_markup = _render_providers_directory(
        visible_provider_contracts,
        display_user,
        users,
        can_manage_provider_contracts,
    )
    users_directory_markup = _render_users_directory(users)
    client_workspace_markup = _render_client_workspace_panel(
        selected_client,
        authorizations,
        operational_sessions,
        all_claims,
        include_claim_totals=can_view_claim_totals,
        include_claim_paid=can_view_claim_paid_amounts,
        include_claim_actions=has_permission(display_user, "claims.view", visible_provider_contracts),
        can_edit_client=can_edit_clients,
        can_manage_authorizations=can_manage_authorizations,
        can_run_eligibility=can_run_client_eligibility,
    )
    client_form_title = (
        f"Expediente de {str(selected_client.get('first_name', '')).strip()} {str(selected_client.get('last_name', '')).strip()}".strip()
        if selected_client is not None
        else "Nuevo cliente"
    )
    client_form_copy = (
        "Edita aqui toda la data del cliente seleccionado, su equipo ABA y su expediente documental."
        if selected_client is not None
        else "Completa overview, caregiver, insurance, team y documents para crear el cliente y abrir su workflow."
    )
    client_save_button_label = "Guardar cambios del cliente" if selected_client is not None else "Guardar cliente"
    client_form_markup = (
        _render_client_form_markup(
            values=client_values,
            title=client_form_title,
            copy=client_form_copy,
            save_label=client_save_button_label,
            bcba_contracts=client_bcba_contracts,
            bcaba_contracts=client_bcaba_contracts,
            rbt_contracts=client_rbt_contracts,
        )
        if can_edit_clients
        else ""
    )
    selected_payer_config_id = str(payer_config_values.get("payer_config_id", "")).strip()
    selected_payer_config = next(
        (
            item
            for item in payer_configs
            if str(item.get("payer_config_id", "")).strip() == selected_payer_config_id
        ),
        payer_configs[0] if payer_configs else None,
    )
    payer_workspace_markup = _render_payer_workspace_panel(selected_payer_config)

    providers_docs_pending_count = len(
        [item for item in visible_provider_contracts if int(item.get("progress_percent", 0) or 0) < 100]
    )
    provider_workflow_counts = _provider_workflow_queue_counts(
        visible_provider_contracts,
        display_user,
        provider_user_lookup,
    )
    providers_at_risk_count = len(
        [
            item
            for item in visible_provider_contracts
            if int(item.get("expired_documents", 0) or 0) > 0 or int(item.get("expiring_documents", 0) or 0) > 0
        ]
    )
    credential_due_soon_count = 0
    for item in visible_provider_contracts:
        due_label = str(item.get("credentialing_due_date", "")).strip()
        if not due_label:
            continue
        try:
            days_remaining = int(item.get("credentialing_days_remaining", 0) or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= days_remaining <= 30:
            credential_due_soon_count += 1
    notes_waiting_supervision_count = len(
        [
            item
            for item in aba_service_logs
            if str(item.get("workflow_status", "")).strip().lower() in {"draft", "rejected"}
        ]
    )
    notes_ready_to_close_count = len(
        [item for item in aba_service_logs if str(item.get("workflow_status", "")).strip().lower() == "reviewed"]
    )
    my_provider_assignments_count = len(
        [
            item
            for item in visible_provider_contracts
            if any(
                _is_current_assignment(item.get(field_name, ""), display_user)
                for field_name in (
                    "recruiter_name",
                    "supervisor_name",
                    "credentialing_owner_name",
                    "office_reviewer_name",
                )
            )
        ]
    )
    upcoming_tasks_count = 0
    today_date = datetime.now().date()
    for item in my_pending_tasks:
        due_date_value = parse_user_date(str(item.get("due_date", "")).strip() or str(item.get("event_date", "")).strip())
        if due_date_value is None:
            continue
        due_day = due_date_value.date() if isinstance(due_date_value, datetime) else due_date_value
        delta_days = (due_day - today_date).days
        if 0 <= delta_days <= 7:
            upcoming_tasks_count += 1

    dashboard_today_sessions = []
    for item in operational_sessions:
        session_date_value = parse_user_date(str(item.get("service_date", "")).strip())
        if session_date_value is None:
            continue
        session_day = session_date_value.date() if isinstance(session_date_value, datetime) else session_date_value
        if session_day == today_date:
            dashboard_today_sessions.append(item)
    dashboard_today_sessions.sort(
        key=lambda item: (
            str(item.get("scheduled_start_time", "")).strip(),
            str(item.get("client_name", "")).strip().lower(),
        )
    )
    dashboard_schedule_rows_markup = (
        "".join(
            _dashboard_list_row_markup(
                str(item.get("client_name", "")).strip() or "Cliente",
                " | ".join(
                    value
                    for value in (
                        str(item.get("provider_name", "")).strip(),
                        str(item.get("cpt_code", "")).strip(),
                    )
                    if value
                ) or "Sesion ABA",
                str(item.get("scheduled_start_time", "")).strip() or str(item.get("service_date", "")).strip(),
                tone="neutral",
            )
            for item in dashboard_today_sessions[:3]
        )
        if dashboard_today_sessions
        else '<div class="dashboard-empty-state">No hay sesiones cargadas para hoy.</div>'
    )
    authorization_counts = operational_dashboards.get("authorization", {})
    pending_claims_count = (
        int(claim_summary.get("pending", 0) or 0)
        + int(claim_summary.get("queued", 0) or 0)
        + int(claim_summary.get("partial", 0) or 0)
    )
    denied_claims_count = int(claim_summary.get("denied", 0) or 0)
    total_claims_count = int(claim_summary.get("total", 0) or 0)
    clean_submission_rate = (
        int(round(((total_claims_count - denied_claims_count) / total_claims_count) * 100))
        if total_claims_count
        else 100
    )
    sorted_pending_tasks = sorted(
        my_pending_tasks,
        key=lambda item: parse_user_date(str(item.get("due_date", "")).strip() or str(item.get("event_date", "")).strip()) or datetime.max,
    )
    dashboard_tasks_rows_markup = (
        "".join(
            _dashboard_list_row_markup(
                str(item.get("title", "")).strip() or "Tarea pendiente",
                str(item.get("description", "")).strip() or str(item.get("category", "")).strip() or "Seguimiento operativo",
                str(item.get("due_date", "")).strip() or str(item.get("event_date", "")).strip() or "Pendiente",
                tone="warm",
            )
            for item in sorted_pending_tasks[:3]
        )
        if sorted_pending_tasks
        else '<div class="dashboard-empty-state">No tienes tareas abiertas en este momento.</div>'
    )
    dashboard_shortcut_cards: list[str] = []
    if "agenda" in allowed_pages:
        dashboard_shortcut_cards.append(
            _dashboard_shortcut_card_markup(
                "Agenda",
                "Abre tareas, deadlines y trabajo del dia.",
                f"{len(my_pending_tasks)} pendientes",
                _page_href("agenda"),
            )
        )
    if "providers" in allowed_pages:
        dashboard_shortcut_cards.append(
            _dashboard_shortcut_card_markup(
                "Providers",
                "Entra al roster y sigue expedientes criticos.",
                f"{providers_docs_pending_count} con docs pendientes",
                _page_href("providers"),
            )
        )
    if "claims" in allowed_pages:
        dashboard_shortcut_cards.append(
            _dashboard_shortcut_card_markup(
                "Claims",
                "Revisa cola diaria, transmit y denials.",
                f"{claim_summary.get('queued', 0)} en cola",
                _page_href("claims"),
            )
        )
    if "notifications" in allowed_pages:
        dashboard_shortcut_cards.append(
            _dashboard_shortcut_card_markup(
                "Notificaciones",
                "Controla alertas, emails y avisos internos.",
                f"{queued_notifications} alerta(s)",
                _page_href("notifications"),
            )
        )
    elif "clients" in allowed_pages:
        dashboard_shortcut_cards.append(
            _dashboard_shortcut_card_markup(
                "Clientes",
                "Ve el roster de casos y sus autorizaciones.",
                f"{active_clients} activos",
                _page_href("clients"),
            )
        )
    dashboard_shortcuts_markup = (
        '<div class="dashboard-shortcuts">'
        + "".join(dashboard_shortcut_cards)
        + "</div>"
        if dashboard_shortcut_cards
        else '<div class="dashboard-empty-state">No hay accesos rapidos disponibles para este rol.</div>'
    )
    dashboard_page_intro_markup = (
        '<section class="dashboard-hero-grid dashboard-page-intro"'
        + _section_hidden(current_page, "dashboard")
        + ">"
        '<article class="panel section-card dashboard-hero-card">'
        '<span class="eyebrow">Blue Hope Suite</span>'
        f"<h2>{html.escape(greeting_label)}</h2>"
        "<p>Run clients, sessions, notes, providers, claims, compliance, and daily operations from one cleaner workspace built for ABA teams.</p>"
        '<div class="profile-pill-row">'
        f'<span class="profile-pill neutral">{html.escape(current_agency_name)}</span>'
        f'<span class="profile-pill neutral">{html.escape(current_user_role_label)}</span>'
        f'<span class="profile-pill neutral">{active_clients} active clients</span>'
        "</div>"
        "</article>"
        '<article class="panel section-card dashboard-quick-card">'
        '<div class="dashboard-module-head">'
        "<h3>Quick access</h3>"
        "</div>"
        "<p>Jump into the areas most used by operations, clinical, billing, and office teams.</p>"
        f"{dashboard_shortcuts_markup}"
        "</article>"
        "</section>"
    )
    dashboard_metric_cards = [
        _metric_card_markup(
            "Active Providers",
            active_providers_count,
            "Providers currently ready for service delivery.",
            tone="neutral",
            note=current_agency_name,
            href=_page_href("providers") if "providers" in allowed_pages else "",
        ),
        _metric_card_markup(
            "Pending Notes",
            notes_waiting_supervision_count,
            "Notes still waiting for signature, review or completion.",
            tone="success" if notes_waiting_supervision_count == 0 else "warm",
            note=f"{notes_ready_to_close_count} ready",
            href=_page_href("aba_notes") if "aba_notes" in allowed_pages else "",
        ),
        _metric_card_markup(
            "Expiring Credentials",
            credential_due_soon_count,
            "Providers that will need credential attention soon.",
            tone="warm" if credential_due_soon_count else "success",
            note=f"{providers_at_risk_count} at risk",
            href=_page_href("providers") if "providers" in allowed_pages else "",
        ),
        _metric_card_markup(
            "Upcoming Sessions",
            int(operational_dashboards.get("operations", {}).get("sessions_scheduled_today", 0) or 0),
            "Sessions already scheduled for today.",
            tone="neutral",
            note=today_label,
            href=_page_href("aba_notes") if "aba_notes" in allowed_pages else "",
        ),
    ]
    dashboard_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "dashboard")
        + ">"
        + "".join(dashboard_metric_cards)
        + "</section>"
    )
    dashboard_work_queue_markup = (
        '<section class="dashboard-focus-grid"'
        + _section_hidden(current_page, "dashboard")
        + ">"
        + _dashboard_module_card_markup(
            "Today\'s priorities",
            dashboard_tasks_rows_markup,
            action_label="View all",
            action_href=_page_href("agenda") if "agenda" in allowed_pages else "",
        )
        + _dashboard_module_card_markup(
            "Schedule snapshot",
            dashboard_schedule_rows_markup,
            action_label="Open calendar",
            action_href=_page_href("agenda") if "agenda" in allowed_pages else "",
        )
        + "</section>"
    )
    dashboard_analytics_markup = (
        '<section class="dashboard-analytics-grid"'
        + _section_hidden(current_page, "dashboard")
        + ">"
        + _dashboard_module_card_markup(
            "Weekly Hours",
            _render_dashboard_hours_chart(operational_sessions),
        )
        + (
            '<article class="panel section-card dashboard-performance-card">'
            '<span class="dashboard-performance-kicker">Performance</span>'
            f"<h3>Revenue cycle visibility</h3>"
            f"<p>Track pending claims, denials, unsigned notes, and expiring authorizations from one operational panel. Clean submission rate: {clean_submission_rate}%.</p>"
            '<div class="dashboard-performance-stats">'
            + _dashboard_performance_stat_markup("Pending Claims", pending_claims_count)
            + _dashboard_performance_stat_markup("Denied", denied_claims_count)
            + _dashboard_performance_stat_markup("Unsigned Notes", notes_waiting_supervision_count)
            + _dashboard_performance_stat_markup("Expiring Auths", int(authorization_counts.get("expiring_within_30_days", 0) or 0))
            + "</div>"
            + (
                f'<a class="dashboard-performance-button" href="{html.escape(_page_href("claims"))}">Open financial dashboard</a>'
                if "claims" in allowed_pages
                else ""
            )
            + "</article>"
        )
        + "</section>"
    )

    selected_provider_name = str((selected_provider_contract or {}).get("provider_name", "")).strip() or "Sin perfil abierto"
    selected_provider_workflow = (
        _provider_workflow_summary_values(selected_provider_contract, display_user, provider_user_lookup)
        if selected_provider_contract is not None
        else None
    )
    providers_page_action_cards: list[str] = []
    if can_manage_provider_contracts:
        providers_page_action_cards.append(
            _page_action_card_markup(
                "Alta",
                "Crear un expediente nuevo o retomar onboarding.",
                "Formulario principal",
                _new_provider_href(),
            )
        )
    providers_page_action_cards.append(
        _page_action_card_markup(
            "Directorio",
            "Busca providers por estatus, rol o nombre.",
            f"{len(visible_provider_contracts)} expediente(s)",
            "#providers-directory",
        )
    )
    providers_page_action_cards.append(
        _page_action_card_markup(
            "Perfil activo",
            "Abre el resumen central del expediente seleccionado.",
            selected_provider_name,
            "#provider-detail",
        )
    )
    providers_page_action_cards.append(
        _page_action_card_markup(
            "Workflow",
            "Mira la cola de recruiting, docs, credenciales y activacion.",
            f"{provider_workflow_counts.get('ready_to_activate', 0)} listo(s)",
            "#providers-workflow",
        )
    )
    providers_page_intro_markup = (
        '<section class="page-intro providers-page-intro"'
        + _section_hidden(current_page, "providers")
        + ">"
        '<article class="panel section-card page-title-card page-title-card-inline">'
        '<div class="page-title-stack">'
        '<span class="eyebrow">Provider operations</span>'
        "<h2>Providers</h2>"
        "<p>Manage roster, compliance, credentials and profile actions from one cleaner directory view.</p>"
        "</div>"
        '<div class="page-command-bar">'
        + (
            f'<a class="page-primary-button" href="{_new_provider_href()}">Add Provider</a>'
            if can_manage_provider_contracts
            else ""
        )
        + '<a class="page-secondary-button" href="/exports/provider_contracts.xls">Export</a>'
        "</div>"
        "</article>"
        "</section>"
    )
    providers_metric_cards = [
        _metric_card_markup(
            "Activos",
            active_providers_count,
            "Providers o empleados ya listos para operar.",
            tone="success",
            note=current_agency_name,
            href="#providers-directory",
        ),
        _metric_card_markup(
            "Pipeline",
            inactive_providers_count,
            "Expedientes que siguen en reclutamiento o contratacion.",
            tone="neutral",
            note=f"{my_provider_assignments_count} tuyos",
            href="#providers-directory",
        ),
        _metric_card_markup(
            "Docs en riesgo",
            providers_at_risk_count,
            "Providers con archivos vencidos o proximos a vencer.",
            tone="danger" if providers_at_risk_count else "success",
            note=f"{providers_docs_pending_count} incompletos",
            href="#provider-documents",
        ),
        _metric_card_markup(
            "Credenciales",
            credential_due_soon_count,
            "Expedientes cuya meta de credencializacion cae pronto.",
            tone="warm" if credential_due_soon_count else "success",
            note=f"{len(supervision_credential_pending)} abiertas",
            href="#provider-detail",
        ),
    ]
    providers_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "providers")
        + ">"
        + "".join(providers_metric_cards)
        + "</section>"
    )
    providers_workflow_markup = (
        '<section id="providers-workflow" class="queue-grid"'
        + _section_hidden(current_page, "providers")
        + ">"
        '<article class="panel section-card queue-card">'
        "<h2>Cola del workflow</h2>"
        "<p>Lee el pipeline por la primera etapa que sigue abierta en cada expediente.</p>"
        '<div class="queue-list">'
        + _queue_row_markup(
            "Intake / recruiting",
            provider_workflow_counts.get("intake", 0),
            "Expedientes que aun necesitan recruiter, supervisor o ficha base.",
            "#providers-directory",
        )
        + _queue_row_markup(
            "Checklist documental",
            provider_workflow_counts.get("documents", 0),
            "Expedientes que siguen detenidos por documentos o expiraciones.",
            "#provider-documents",
        )
        + _queue_row_markup(
            "Credencializacion",
            provider_workflow_counts.get("credentialing", 0),
            "Providers que ya pasaron intake/docs y siguen esperando enrollment.",
            _page_href("enrollments") if "enrollments" in allowed_pages else "#providers-directory",
        )
        + _queue_row_markup(
            "Listos para activar",
            provider_workflow_counts.get("ready_to_activate", 0),
            "Expedientes que ya solo necesitan mover la etapa a Active.",
            "#provider-contract-form",
        )
        + _queue_row_markup(
            "Activos sin clientes",
            provider_workflow_counts.get("active_without_clients", 0),
            "Providers activos que todavia no tienen caso ABA vinculado.",
            "#provider-detail",
        )
        + "</div>"
        "</article>"
        '<article class="panel section-card queue-card">'
        "<h2>Foco del expediente abierto</h2>"
        + (
            '<div class="mini-table">'
            f'<div class="mini-row"><strong>Provider</strong><span>{html.escape(selected_provider_name)}</span></div>'
            f'<div class="mini-row"><strong>Estado</strong><span>{html.escape(str((selected_provider_workflow or {}).get("status_label", "")))}</span></div>'
            f'<div class="mini-row"><strong>Siguiente etapa</strong><span>{html.escape(str((selected_provider_workflow or {}).get("next_step_title", "")))}</span></div>'
            f'<div class="mini-row"><strong>Owner sugerido</strong><span>{html.escape(str((selected_provider_workflow or {}).get("next_owner", "")))}</span></div>'
            "</div>"
            f'<p>{html.escape(str((selected_provider_workflow or {}).get("next_action", "")))}</p>'
            if selected_provider_workflow is not None
            else "<p>Abre un provider del directorio para que esta tarjeta te diga que sigue, quien lo debe mover y en que etapa se quedo.</p>"
        )
        + "</article>"
        "</section>"
    )
    hr_page_action_cards: list[str] = []
    if can_manage_provider_contracts:
        hr_page_action_cards.append(
            _page_action_card_markup(
                "Nuevo hire",
                "Crear la contratacion y asignar recruiter o credenciales.",
                "Alta de provider",
                _new_provider_href(),
            )
        )
    hr_page_action_cards.append(
        _page_action_card_markup(
            "Pipeline",
            "Abrir recruiting, onboarding y checklist del provider.",
            f"{inactive_providers_count} expediente(s)",
            f"{_page_href('providers')}#providers-workflow",
        )
    )
    hr_page_action_cards.append(
        _page_action_card_markup(
            "Equipo oficina",
            "Entrar al roster de usuarios administrativos y staff interno.",
            f"{office_staff_count} empleado(s)",
            f"{_page_href('users')}#users-directory",
        )
    )
    hr_page_action_cards.append(
        _page_action_card_markup(
            "Credentialing",
            "Seguir payer enrollment, submissions y fechas meta.",
            f"{enrollment_pending_count} pendiente(s)",
            f"{_page_href('enrollments')}#payer-enrollment-roster",
        )
    )
    hr_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="hr",
        kicker="HR workflow",
        title="Recruiting, Onboarding and Credentialing",
        copy="Este centro junta hiring, office team, checklist documental, quality control y credentialing dentro del mismo portal administrativo.",
        pills=["Recruiting", "Onboarding", "Credentialing", "Office team"],
        primary_label="Open pipeline",
        primary_href=f"{_page_href('providers')}#providers-workflow",
        secondary_label="Office team",
        secondary_href=f"{_page_href('users')}#users-directory",
    )
    hr_metric_cards = [
        _metric_card_markup(
            "Intake abierto",
            provider_workflow_counts.get("intake", 0),
            "Expedientes que aun necesitan recruiter, supervisor o ficha base.",
            tone="neutral",
            note=f"{my_provider_assignments_count} tuyos",
            href=f"{_page_href('providers')}#providers-workflow",
        ),
        _metric_card_markup(
            "Docs pendientes",
            provider_workflow_counts.get("documents", 0),
            "Providers bloqueados por checklist documental o expiraciones.",
            tone="danger" if provider_workflow_counts.get("documents", 0) else "success",
            note=f"{providers_docs_pending_count} incompletos",
            href=f"{_page_href('providers')}#provider-documents",
        ),
        _metric_card_markup(
            "Credentialing",
            provider_workflow_counts.get("credentialing", 0) + enrollment_pending_count,
            "Seguimiento de providers con enrollment abierto o payer pendiente.",
            tone="warm" if (provider_workflow_counts.get("credentialing", 0) + enrollment_pending_count) else "success",
            note=f"{credential_due_soon_count} con meta cerca",
            href=f"{_page_href('enrollments')}#payer-enrollment-roster",
        ),
        _metric_card_markup(
            "Ready QA / Activate",
            provider_workflow_counts.get("ready_to_activate", 0),
            "Expedientes que ya estan listos para quality control o activacion final.",
            tone="success" if provider_workflow_counts.get("ready_to_activate", 0) else "neutral",
            note="Quality control",
            href=f"{_page_href('providers')}#provider-detail",
        ),
        _metric_card_markup(
            "Office team",
            office_staff_count,
            "Usuarios administrativos visibles dentro del mismo workspace.",
            tone="neutral",
            note=f"{mfa_enabled_users_count} MFA",
            href=f"{_page_href('users')}#users-directory",
        ),
        _metric_card_markup(
            "Activos sin caso",
            provider_workflow_counts.get("active_without_clients", 0),
            "Providers activos que aun necesitan cliente o caseload.",
            tone="warm" if provider_workflow_counts.get("active_without_clients", 0) else "success",
            note=f"{linked_provider_users_count} usuario(s) vinculados",
            href=f"{_page_href('providers')}#provider-detail",
        ),
    ]
    hr_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "hr")
        + ">"
        + "".join(hr_metric_cards)
        + "</section>"
    )
    hr_hub_markup = _render_workspace_hub(
        "Centro de Recursos Humanos",
        "Entra por hiring, baja a pipeline y despues abre users, enrollments o elegibilidad solo cuando la operacion lo necesite.",
        [
            {
                "href": _new_provider_href(),
                "icon": "NH",
                "title": "New hire",
                "copy": "Crear la nueva contratacion y asignar recruiter o credentialing owner.",
                "meta": "Formulario principal",
            }
            if can_manage_provider_contracts and "providers" in allowed_pages
            else {},
            {
                "href": f"{_page_href('providers')}#providers-workflow",
                "icon": "PL",
                "title": "Providers pipeline",
                "copy": "Abrir recruiting, onboarding, docs y activacion.",
                "meta": f"{inactive_providers_count} pipeline",
            }
            if "providers" in allowed_pages
            else {},
            {
                "href": f"{_page_href('users')}#users-directory",
                "icon": "OF",
                "title": "Office team",
                "copy": "Directorio visual del staff administrativo.",
                "meta": f"{office_staff_count} staff",
            }
            if "users" in allowed_pages
            else {},
            {
                "href": f"{_page_href('enrollments')}#payer-enrollment-roster",
                "icon": "CR",
                "title": "Credentialing",
                "copy": "Seguir payers, dates, submissions y follow-up.",
                "meta": f"{enrollment_pending_count} abierto(s)",
            }
            if "enrollments" in allowed_pages
            else {},
            {
                "href": _page_href("eligibility"),
                "icon": "EL",
                "title": "Elegibilidad",
                "copy": "Ver roster y validacion de coverage que afecta scheduling.",
                "meta": f"{len(roster_entries)} registro(s)",
            }
            if "eligibility" in allowed_pages
            else {},
            {
                "href": _page_href("notifications"),
                "icon": "NT",
                "title": "Alertas",
                "copy": "Seguir tareas, avisos y notificaciones del equipo.",
                "meta": f"{queued_notifications} alerta(s)",
            }
            if "notifications" in allowed_pages
            else {},
        ],
    )
    hr_focus_markup = (
        '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Provider</strong><span>{html.escape(selected_provider_name)}</span></div>'
        + f'<div class="mini-row"><strong>Estado</strong><span>{html.escape(str((selected_provider_workflow or {}).get("status_label", "")))}</span></div>'
        + f'<div class="mini-row"><strong>Siguiente etapa</strong><span>{html.escape(str((selected_provider_workflow or {}).get("next_step_title", "")))}</span></div>'
        + f'<div class="mini-row"><strong>Owner</strong><span>{html.escape(str((selected_provider_workflow or {}).get("next_owner", "")))}</span></div>'
        + f'<div class="mini-row"><strong>Alertas</strong><span>{providers_at_risk_count} docs en riesgo | {queued_notifications} notificacion(es)</span></div>'
        + "</div>"
        + f'<p>{html.escape(str((selected_provider_workflow or {}).get("next_action", "")))}</p>'
    ) if selected_provider_workflow is not None else (
        "<p>Selecciona un expediente desde las tarjetas de HR para que aqui veas la etapa abierta, el owner sugerido y la siguiente accion operativa.</p>"
    )
    hr_queue_markup = (
        '<section class="queue-grid"'
        + _section_hidden(current_page, "hr")
        + ">"
        + '<article class="panel section-card queue-card">'
        + "<h2>Pipeline de HR</h2>"
        + "<p>Lectura rapida del flujo completo desde intake hasta activacion.</p>"
        + '<div class="queue-list">'
        + _queue_row_markup(
            "Pending contact / intake",
            provider_workflow_counts.get("intake", 0),
            "Expedientes que aun necesitan recruiter, supervisor o ficha base.",
            f"{_page_href('providers')}#providers-workflow",
        )
        + _queue_row_markup(
            "Documents in progress",
            provider_workflow_counts.get("documents", 0),
            "Checklist abierto o documentos vencidos dentro del expediente.",
            f"{_page_href('providers')}#provider-documents",
        )
        + _queue_row_markup(
            "Credentialing open",
            provider_workflow_counts.get("credentialing", 0) + enrollment_pending_count,
            "Providers con credentialing abierto o payers todavia en seguimiento.",
            f"{_page_href('enrollments')}#payer-enrollment-roster",
        )
        + _queue_row_markup(
            "Ready QA / activate",
            provider_workflow_counts.get("ready_to_activate", 0),
            "Expedientes listos para quality control y activacion final.",
            f"{_page_href('providers')}#provider-detail",
        )
        + _queue_row_markup(
            "Active without clients",
            provider_workflow_counts.get("active_without_clients", 0),
            "Providers activos que todavia necesitan caseload o cliente asignado.",
            f"{_page_href('providers')}#provider-detail",
        )
        + "</div>"
        + "</article>"
        + '<article id="hr-focus" class="panel section-card queue-card">'
        + "<h2>Foco actual</h2>"
        + hr_focus_markup
        + "</article>"
        + "</section>"
    )
    hr_pipeline_preview_markup = (
        '<section class="dual-grid"'
        + _section_hidden(current_page, "hr")
        + ">"
        + '<article class="panel section-card">'
        + "<h2>Expedientes prioritarios</h2>"
        + "<p>Vista rapida de las contrataciones y onboarding que mas se deben mover hoy.</p>"
        + _render_hr_candidate_cards(visible_provider_contracts, display_user, provider_user_lookup)
        + "</article>"
        + '<article class="panel section-card">'
        + "<h2>Office team preview</h2>"
        + "<p>Personal administrativo vinculado al control diario de hiring, agenda, compliance y billing.</p>"
        + _render_hr_office_cards(users)
        + "</article>"
        + "</section>"
    )
    hr_client_audit_markup = (
        '<section class="panel section-card" data-skip-auto-collapsible="1"'
        + _section_hidden(current_page, "hr")
        + ">"
        + "<h2>Auditoria de clientes</h2>"
        + "<p>Registro de altas de pacientes, cambios clave del expediente y revisiones de elegibilidad manuales o automaticas.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>Fecha</th><th>Cliente</th><th>Accion</th><th>Nombre</th><th>Username</th><th>Categoria</th><th>Detalle</th></tr></thead><tbody>'
        + _render_system_audit_rows(client_audit_logs, "Todavia no hay auditoria de clientes.")
        + "</tbody></table></div>"
        + "</section>"
    )
    provider_profile_markup = _render_provider_profile_panel(
        selected_provider_contract,
        display_user,
        users,
        can_manage_provider_contracts,
        user_role,
        selected_provider_audit_logs,
        (
            list_shared_calendar_events(
                provider_contract_ids=aba_visible_provider_ids,
                provider_contract_id=str((selected_provider_contract or {}).get("contract_id", "")).strip(),
            )
            if selected_provider_contract is not None
            else []
        ),
    )
    selected_client_name = (
        f"{str((selected_client or {}).get('first_name', '')).strip()} {str((selected_client or {}).get('last_name', '')).strip()}".strip()
        or "Sin cliente abierto"
    )
    clients_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="clients",
        kicker="Client workflow",
        title="Clients Directory",
        copy="Open the client workflow center to move from session to note, validation, claim, payment, and supporting admin work in one place.",
        pills=["Coverage", "ABA team", "Authorizations"],
        primary_label="Open workflow center",
        primary_href="#client-profile",
        secondary_label="New client",
        secondary_href=f"{_page_href('clients')}?new_client=1#clientsdb",
    )
    clients_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "clients")
        + ">"
        + _metric_card_markup("Clientes activos", active_clients, "Casos activos dentro de la agencia.", tone="success", note=current_agency_name, href="#client-directory")
        + _metric_card_markup(
            "Autorizaciones",
            active_authorization_count,
            "Lineas o casos con units todavia disponibles.",
            tone="neutral",
            note=f"{len(authorizations)} registradas",
            href=_client_expediente_href(selected_client, "client-authorizations") if selected_client is not None else "#client-profile",
        )
        + _metric_card_markup("Roster auto", active_roster_entries_count, "Pacientes que entran al scheduler de elegibilidad.", tone="neutral", note=f"{auto_eligibility_clients_count} auto", href=_page_href("eligibility"))
        + _metric_card_markup("Cliente abierto", selected_client_name if selected_client is not None else "Ninguno", "El expediente activo se edita en esta misma pagina.", tone="warm", note="Perfil central", href="#client-profile")
        + "</section>"
    )

    agenda_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="agenda",
        kicker="Agenda workspace",
        title="Agenda y Trabajo del Dia",
        copy="Coordina tareas, deadlines, supervision y notas personales desde una sola vista operativa del equipo.",
        pills=["Tasks", "Calendar", "Follow-up"],
        primary_label="Nueva tarea",
        primary_href="#agenda-form",
        secondary_label="Mi lista",
        secondary_href="#agenda-my-work",
    )
    agenda_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "agenda")
        + ">"
        + _metric_card_markup("Pendientes", len(my_pending_tasks), "Trabajo abierto asignado a tu usuario.", tone="neutral", note=f"{upcoming_tasks_count} esta semana", href="#agenda-my-work")
        + _metric_card_markup("Vencidas", overdue_tasks_count, "Tareas con fecha por debajo de hoy.", tone="danger" if overdue_tasks_count else "success", note="Seguimiento", href="#agenda-my-work")
        + _metric_card_markup("Agenda agencia", len(calendar_events), "Eventos activos del ecosistema.", tone="neutral", note=current_agency_name, href="#agenda-calendar")
        + _metric_card_markup("Notas privadas", len(my_notes), "Seguimiento personal guardado en el portal.", tone="warm", note="Workspace interno", href="#agenda-notes")
        + "</section>"
    )

    aba_notes_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="aba_notes",
        kicker="ABA notes workspace",
        title="Session Notes y Service Logs",
        copy="Todo empieza desde el Session Event: agenda la sesion, documenta, valida billing y empuja el claim sin salir del portal.",
        pills=["Sessions", "Weekly logs", "Workflow"],
        primary_label="Nueva sesion",
        primary_href="#aba-notes-form",
        secondary_label="Service logs",
        secondary_href="#aba-service-logs",
    )
    aba_notes_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "aba_notes")
        + ">"
        + _metric_card_markup("Sesiones", len(aba_appointments), "Appointments ABA guardados en el portal.", tone="neutral", note=current_agency_name, href="#aba-appointments")
        + _metric_card_markup("Logs abiertos", notes_waiting_supervision_count, "Notas en draft o reabiertas que siguen esperando accion.", tone="warm" if notes_waiting_supervision_count else "success", note="Supervision", href="#aba-service-logs")
        + _metric_card_markup("Listas para cerrar", notes_ready_to_close_count, "Logs ya revisados que solo necesitan cierre final.", tone="success", note="Workflow", href="#aba-note-preview")
        + _metric_card_markup("Log activo", str((selected_aba_log or {}).get("client_name", "")).strip() or "Ninguno", "El preview de la derecha siempre sigue este service log.", tone="neutral", note=str((selected_aba_log or {}).get("workflow_status", "")).strip() or "Sin seleccionar", href="#aba-note-preview")
        + "</section>"
    )
    operations_counts = operational_dashboards.get("operations", {})
    scheduler_counts = operational_dashboards.get("scheduler", {})
    billing_counts = operational_dashboards.get("billing", {})
    authorization_counts = operational_dashboards.get("authorization", {})
    operational_sessions_preview = operational_sessions[:18]
    billing_queue_ready_preview = billing_queue_ready[:12]
    billing_queue_hold_preview = billing_queue_hold[:12]
    session_note_preview = str((selected_operational_session or {}).get("session_note", "")).strip()
    if len(session_note_preview) > 260:
        session_note_preview = session_note_preview[:257].rstrip() + "..."
    selected_session_auth_summary = "-"
    selected_session_actions = ""
    selected_session_claim_actions = ""
    selected_session_amount_markup = ""
    selected_claim_bridge_note = "Selecciona una sesion desde la cola para precargar el 837 con cliente, provider y units."
    if selected_operational_session is not None:
        selected_session_auth_summary = (
            f"{selected_operational_session.get('authorization_number', '') or 'Sin auth'}"
            + (
                f" | Restante {int(float(selected_operational_session.get('authorization_remaining_units', 0) or 0))} units"
                if str(selected_operational_session.get("authorization_number", "")).strip()
                else ""
            )
        )
        session_id_href = quote(str(selected_operational_session.get("session_id", "")))
        log_id_href = quote(str(selected_operational_session.get("service_log_id", "")))
        client_id_href = quote(str(selected_operational_session.get("client_id", "")))
        selected_session_actions = (
            f'<a class="quick-link" href="{_page_href("aba_notes")}?appointment_id={session_id_href}#session-ops-detail">Refrescar detalle</a>'
            + (
                f'<a class="quick-link" href="{_page_href("aba_notes")}?log_id={log_id_href}#aba-note-preview">Abrir nota</a>'
                if log_id_href
                else ""
            )
            + (
                f'<a class="quick-link" href="{_page_href("clients")}?auth_client_id={client_id_href}#client-authorizations">Abrir auth</a>'
                if client_id_href
                else ""
            )
            + f'<a class="quick-link" href="{_page_href("claims")}?appointment_id={session_id_href}#claims837">Enviar a claims</a>'
        )
        if str(selected_operational_session.get("claim_id", "")).strip():
            claim_id_href = quote(str(selected_operational_session.get("claim_id", "")))
            selected_session_claim_actions = (
                f'<a class="quick-link" href="/cms1500?claim_id={claim_id_href}">CMS-1500</a>'
                f'<a class="quick-link" href="/claim-edi?claim_id={claim_id_href}">837</a>'
            )
            selected_claim_bridge_note = (
                f"La sesion ya esta enlazada al claim {html.escape(str(selected_operational_session.get('claim_id', '')))}. "
                "Puedes revisar el 837 o volver al batch para darle seguimiento."
            )
        else:
            selected_claim_bridge_note = (
                "Esta sesion puede precargar el claim builder con member ID, provider, payer, CPT y units desde la operacion real."
            )
        if can_view_billing_rates:
            selected_session_amount_markup = (
                f'<div class="mini-row"><strong>Monto estimado</strong><span>${float(selected_operational_session.get("billed_amount", 0) or 0):,.2f}</span></div>'
            )
    aba_operations_markup = (
        '<section class="ops-dashboard-grid"'
        + _section_hidden(current_page, "aba_notes")
        + ">"
        + '<article class="panel section-card ops-dashboard-card">'
        + "<h2>Operations Dashboard</h2>"
        + "<p>Sigue el dia operativo desde la sesion hasta la cola de billing, sin perder notas ni autorizaciones.</p>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Hoy agendadas</strong><span>{int(operations_counts.get("sessions_scheduled_today", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>En progreso</strong><span>{int(operations_counts.get("sessions_in_progress", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Completadas hoy</strong><span>{int(operations_counts.get("sessions_completed_today", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Pending notes</strong><span>{int(operations_counts.get("pending_notes", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Ready for billing</strong><span>{int(operations_counts.get("sessions_ready_for_billing", 0) or 0)}</span></div>'
        + "</div>"
        + '<div class="quick-links">'
        + f'<a class="quick-link" href="#aba-appointments">Scheduler</a>'
        + f'<a class="quick-link" href="#aba-service-logs">Notas</a>'
        + f'<a class="quick-link" href="{_page_href("claims")}#claims-billing-queue">Billing queue</a>'
        + "</div>"
        + "</article>"
        + '<article class="panel section-card ops-dashboard-card">'
        + "<h2>Scheduler Risks</h2>"
        + "<p>Warnings operativos que afectan cobertura, compliance o continuidad de sesiones ABA.</p>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Providers sin clientes</strong><span>{int(scheduler_counts.get("provider_availability_gaps", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Clientes sin asignar</strong><span>{int(scheduler_counts.get("unassigned_clients", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Providers non-compliant</strong><span>{int(scheduler_counts.get("providers_non_compliant", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Auth expiring</strong><span>{int(scheduler_counts.get("expired_auth_warnings", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Coverage urgente</strong><span>{int(scheduler_counts.get("urgent_coverage_needs", 0) or 0)}</span></div>'
        + "</div>"
        + '<div class="quick-links">'
        + f'<a class="quick-link" href="{_page_href("clients")}#client-directory">Clientes</a>'
        + f'<a class="quick-link" href="{_page_href("providers")}#providers-directory">Providers</a>'
        + "</div>"
        + "</article>"
        + '<article class="panel section-card ops-dashboard-card">'
        + "<h2>Billing Pipeline</h2>"
        + "<p>Controla que la sesion tenga note, validaciones limpias y claim follow-up visible antes de cobrar.</p>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Ready</strong><span>{int(billing_counts.get("ready_to_bill_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>On hold</strong><span>{int(billing_counts.get("on_hold_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Denied</strong><span>{int(billing_counts.get("denied_claims_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Rejected</strong><span>{int(billing_counts.get("rejected_claims_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Follow-up</strong><span>{int(billing_counts.get("claims_pending_follow_up", 0) or 0)}</span></div>'
        + "</div>"
        + '<div class="quick-links">'
        + f'<a class="quick-link" href="{_page_href("claims")}#claims-billing-queue">Billing Queue</a>'
        + f'<a class="quick-link" href="{_page_href("claims")}#claims-follow-up">Claims follow-up</a>'
        + "</div>"
        + "</article>"
        + '<article class="panel section-card ops-dashboard-card">'
        + "<h2>Authorization Health</h2>"
        + "<p>Las units y fechas de auth quedan visibles para anticipar hold, expiraciones o agotamiento del caso.</p>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Activas</strong><span>{int(authorization_counts.get("active_authorizations", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Expiran &lt; 30 dias</strong><span>{int(authorization_counts.get("expiring_within_30_days", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Low units</strong><span>{int(authorization_counts.get("low_units_remaining", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Exhausted</strong><span>{int(authorization_counts.get("exhausted_auths", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Usage total</strong><span>{int(authorization_counts.get("usage_percent", 0) or 0)}%</span></div>'
        + "</div>"
        + '<div class="quick-links">'
        + f'<a class="quick-link" href="{_page_href("clients")}#client-directory">Authorization tracker</a>'
        + f'<a class="quick-link" href="{_page_href("claims")}#claims-billing-queue">Billing impact</a>'
        + "</div>"
        + "</article>"
        + "</section>"
    )
    aba_session_queue_markup = (
        '<section id="aba-session-queue" class="panel section-card"'
        + _section_hidden(current_page, "aba_notes")
        + ">"
        + "<h2>Session / Event Queue</h2>"
        + "<p>Este roster convierte el evento del calendario en el centro real del sistema: scheduler, note, auth, validation y billing en una sola fila.</p>"
        + '<div class="table-wrap"><table><thead><tr>'
        + "<th>DOS</th><th>Cliente</th><th>Provider</th><th>Payer</th><th>CPT</th><th>Units</th><th>Note</th><th>Session</th><th>Billing</th><th>Validaciones</th><th>Acciones</th>"
        + "</tr></thead><tbody>"
        + _render_operational_session_rows(operational_sessions_preview, claim_links=True)
        + "</tbody></table></div>"
        + f'<p class="helper-note">Mostrando {len(operational_sessions_preview)} de {len(operational_sessions)} sesiones operativas visibles en esta agencia.</p>'
        + "</section>"
    )
    aba_session_detail_markup = _render_session_workspace_panel(
        selected_operational_session,
        note_preview=session_note_preview,
        auth_summary=selected_session_auth_summary,
        actions_markup=selected_session_actions,
        claim_actions_markup=selected_session_claim_actions,
        amount_markup=selected_session_amount_markup,
    )

    notifications_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="notifications",
        kicker="Notifications workspace",
        title="Alertas y Outlook",
        copy="Redacta emails, revisa la cola y atiende o borra alertas para que desaparezcan de tu lista hasta que el sistema necesite volver a levantarlas.",
        pills=["Email queue", "Outlook", "Alerts"],
        primary_label="Redactar",
        primary_href="#notifications-compose",
        secondary_label="Centro",
        secondary_href="#notifications-center",
    )
    notifications_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "notifications")
        + ">"
        + _metric_card_markup("Registradas", len(notifications), "Total de alertas guardadas dentro de la agencia.", tone="neutral", note=current_agency_name, href="#notifications-center")
        + _metric_card_markup("Enviadas", notification_sent_count, "Notificaciones con salida confirmada.", tone="success", note=f"{notification_draft_count} drafts", href="#notifications-center")
        + _metric_card_markup("Con error", notification_error_count, "Mensajes que necesitan correo configurado o reintento.", tone="danger" if notification_error_count else "success", note="Outlook / setup", href="#notifications-center")
        + _metric_card_markup("Pendientes", queued_notifications, "Cola viva de avisos todavia por atender.", tone="warm" if queued_notifications else "success", note="Follow-up", href="#notifications-center")
        + "</section>"
    )

    users_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="users",
        kicker="Human resources workspace",
        title="Empleados y Accesos",
        copy="Administra perfiles, rangos, vinculos con providers y seguridad de acceso desde un solo centro de recursos humanos.",
        pills=["Users", "Roles", "MFA"],
        primary_label="Nuevo usuario",
        primary_href="#users-directory-form",
        secondary_label="Directorio",
        secondary_href="#users-directory",
    )
    users_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "users")
        + ">"
        + _metric_card_markup("Usuarios activos", active_users_count, "Accesos activos dentro del portal.", tone="success", note=f"{inactive_users_count} inactivos", href="#users-directory")
        + _metric_card_markup("Oficina", office_staff_count, "Personal administrativo y no-provider registrado.", tone="neutral", note="RH / billing / ops", href="#users-directory")
        + _metric_card_markup("Con provider", linked_provider_users_count, "Usuarios amarrados a un expediente de provider.", tone="warm", note="Vinculo interno", href="#users-directory")
        + _metric_card_markup("MFA activo", mfa_enabled_users_count, "Perfiles que ya confirmaron segundo factor.", tone="neutral", note="Seguridad", href=_page_href("security"))
        + "</section>"
    )

    security_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="security",
        kicker="Configuration center",
        title="Configuracion Global",
        copy="Centraliza portales, accesos, tiempos, scheduler, branding y modulos administrativos desde un mismo lugar.",
        pills=["Security", "Portal rules", "Settings"],
        primary_label="Global settings",
        primary_href="#security-global-config",
        secondary_label="Usuarios",
        secondary_href=_page_href("users"),
    )
    security_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "security")
        + ">"
        + _metric_card_markup("Agencias", len(agencies), "Ecosistemas cargados dentro del portal.", tone="neutral", note=f"{agencies_with_logo_count} con logo", href=_page_href("agencies"))
        + _metric_card_markup("Usuarios", active_users_count, "Accesos activos usando la configuracion actual.", tone="success", note=current_agency_name, href=_page_href("users"))
        + _metric_card_markup("MFA", mfa_enabled_users_count, "Perfiles con segundo factor activo.", tone="neutral", note=f"{mfa_timeout_minutes} min", href="#security-access-policy")
        + _metric_card_markup("Scheduler", f"{eligibility_interval_hours}h", "Frecuencia del motor de elegibilidad.", tone="warm", note=f"Dias {eligibility_run_days_label}", href="#security-global-config")
        + "</section>"
    )
    clients_configuration_markup = (
        '<section id="settings-clients" class="panel section-card"'
        + _section_hidden(current_page, "security")
        + ">"
        + '<span class="eyebrow">Clientes</span>'
        + "<h2>Base de clientes y workflow del cliente</h2>"
        + "<p>Desde este boton entras solo a las herramientas del modulo de clientes: base, workflow center, elegibilidad y configuracion documental.</p>"
        + '<div class="tool-grid hub-grid">'
        + f'''
            <a class="tool-tile" href="{_page_href('clients')}#client-directory"{_nav_hidden(allowed_pages, 'clients')}>
              <span class="tool-icon">BD</span>
              <strong>Base de clientes</strong>
              <p>Abre el roster principal para revisar, buscar y abrir expedientes del cliente.</p>
              <span>{active_clients} activo(s)</span>
            </a>
        '''
        + f'''
            <a class="tool-tile" href="{_page_href('clients')}#client-profile"{_nav_hidden(allowed_pages, 'clients')}>
              <span class="tool-icon">WF</span>
              <strong>Client Workflow Center</strong>
              <p>Entra al punto donde empieza el flujo: session, note, validation, claim y payment.</p>
              <span>Session-first</span>
            </a>
        '''
        + f'''
            <a class="tool-tile" href="{_page_href('eligibility')}"{_nav_hidden(allowed_pages, 'eligibility')}>
              <span class="tool-icon">EL</span>
              <strong>Elegibilidad</strong>
              <p>Controla coverage, roster automatico y revisiones que afectan scheduling y billing.</p>
              <span>{len(roster_entries)} registro(s)</span>
            </a>
        '''
        + '''
            <a class="tool-tile" href="#client-document-config">
              <span class="tool-icon">DC</span>
              <strong>Documentos del cliente</strong>
              <p>Administra la lista oficial de documentos requeridos para el expediente del cliente.</p>
              <span>Checklist</span>
            </a>
        '''
        + "</div>"
        + "</section>"
    )

    if current_page == "claims":
        billing_title = "Claims Workspace"
        billing_copy = "Todo nace desde el appointment del cliente: la autorizacion define units, el CPT depende del provider, la nota y firmas cierran la sesion, y despues el sistema arma batches y genera el 837 por payer."
        billing_actions_markup = (
            _page_action_card_markup("837P", "Abrir la preparacion del claim profesional.", "Claim builder", "#claims837")
            + _page_action_card_markup("Read 837", "Leer un 837 en modo oficina.", "Parser", "#read837")
            + _page_action_card_markup("Batch", "Revisar la cola diaria y transmitir.", f"{claim_summary.get('queued', 0)} en cola", "#claims-batch")
            + _page_action_card_markup("Payers", "Ir al catalogo y tarifas por seguro.", f"{active_payers_count} activo(s)", _page_href("payers"))
        )
    elif current_page == "payments":
        billing_title = "Remittances Workspace"
        billing_copy = "Importa ERAs, revisa pagos aplicados y valida diferencias o denials desde la remesa."
        billing_actions_markup = (
            _page_action_card_markup("Subir ERA", "Cargar archivo 835 del payer.", "ERA import", "#era835")
            + _page_action_card_markup("Claims", "Volver a la cola de reclamaciones.", f"{claim_summary.get('total', 0)} claim(s)", _page_href("claims"))
            + _page_action_card_markup("Payers", "Abrir seguros y clearinghouses.", f"{active_payers_count} payer(s)", _page_href("payers"))
            + _page_action_card_markup("Dashboard", "Volver al resumen general de operaciones.", current_agency_name, _page_href("dashboard"))
        )
    else:
        billing_title = "Payers Workspace"
        billing_copy = "Configura payers, clearinghouses y tarifas CPT para que billing tenga reglas claras por seguro."
        billing_actions_markup = (
            _page_action_card_markup("Nuevo payer", "Crear o editar configuracion del seguro.", "Formulario principal", "#payer-config-form")
            + _page_action_card_markup("Directorio", "Abrir roster visual de seguros.", f"{len(payer_configs)} payer(s)", "#payers-directory")
            + _page_action_card_markup("Claims", "Volver al flujo del 837P.", f"{claim_summary.get('total', 0)} claim(s)", _page_href("claims"))
            + _page_action_card_markup("Remesas", "Entrar al area de ERA 835.", f"{len(era_archives)} ERA", _page_href("payments"))
        )
    billing_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key=current_page if current_page in {"claims", "payments", "payers"} else "claims",
        kicker="Billing center",
        title=billing_title,
        copy=billing_copy,
        pills=["Claims", "ERA", "Payers"],
        primary_label="Claims queue" if current_page == "claims" else "Billing center",
        primary_href="#claims-billing-queue" if current_page == "claims" else _page_href("claims"),
        secondary_label="Payers",
        secondary_href=_page_href("payers"),
    )
    billing_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "claims", "payments", "payers")
        + ">"
        + _metric_card_markup("Pendientes", claim_summary.get("pending", 0), "Claims que todavia no cierran su ciclo.", tone="neutral", note=f"{claim_summary.get('queued', 0)} en cola", href=_page_href("claims"))
        + _metric_card_markup("Denegados", claim_summary.get("denied", 0), "Reclamos con pago cero o follow-up requerido.", tone="danger" if claim_summary.get("denied", 0) else "success", note=f"{claim_summary.get('partial', 0)} parciales", href=_page_href("claims"))
        + _metric_card_markup("ERA", len(era_archives), "Archivos de remesa archivados en el sistema.", tone="neutral", note=f"{claim_summary.get('paid', 0)} pagados", href=_page_href("payments"))
        + _metric_card_markup("Payers", active_payers_count, "Seguros activos para facturacion.", tone="warm", note=f"{payers_with_rates_count} con tarifas", href=_page_href("payers"))
        + "</section>"
    )
    if selected_operational_session is None:
        claims_bridge_markup = (
            '<article class="panel section-card">'
            + "<h2>Claim Builder desde sesion</h2>"
            + "<p>Abre cualquier sesion operativa para precargar el 837 con paciente, payer, rendering provider y units.</p>"
            + '<div class="quick-links">'
            + f'<a class="quick-link" href="{_page_href("aba_notes")}#aba-session-queue">Ir a Session Queue</a>'
            + "</div>"
            + "</article>"
        )
    else:
        session_id_href = quote(str(selected_operational_session.get("session_id", "")))
        claims_bridge_markup = (
            '<article class="panel section-card">'
            + "<h2>Claim Builder desde sesion</h2>"
            + f"<p>{selected_claim_bridge_note}</p>"
            + '<div class="mini-table">'
            + f'<div class="mini-row"><strong>Sesion</strong><span>{html.escape(str(selected_operational_session.get("service_date", "")))} | {html.escape(str(selected_operational_session.get("client_name", "")))}</span></div>'
            + f'<div class="mini-row"><strong>Provider</strong><span>{html.escape(str(selected_operational_session.get("provider_name", "")) or "-")}</span></div>'
            + f'<div class="mini-row"><strong>Payer</strong><span>{html.escape(str(selected_operational_session.get("payer_name", "")) or "-")}</span></div>'
            + f'<div class="mini-row"><strong>CPT / Units</strong><span>{html.escape(str(selected_operational_session.get("cpt_code", "")) or "-")} | {int(float(selected_operational_session.get("units", 0) or 0))}</span></div>'
            + f'<div class="mini-row"><strong>Billing status</strong><span>{html.escape(str(selected_operational_session.get("billing_queue_status", "")).replace("_", " ").title() or "-")}</span></div>'
            + "</div>"
            + '<div class="quick-links">'
            + f'<a class="quick-link" href="{_page_href("claims")}?appointment_id={session_id_href}#claims837">Precargar 837</a>'
            + f'<a class="quick-link" href="{_page_href("aba_notes")}?appointment_id={session_id_href}#session-ops-detail">Volver a sesion</a>'
            + "</div>"
            + "</article>"
        )
    claims_dashboard_markup = (
        '<article class="panel section-card">'
        + "<h2>Billing Dashboard</h2>"
        + "<p>Los claims salen del calendario de clientes. Cada appointment cerrado, con nota y firma lista, conserva trazabilidad hasta el batch claim 837, el pago o el denial.</p>"
        + '<div class="mini-table">'
        + f'<div class="mini-row"><strong>Ready to bill</strong><span>{int(billing_counts.get("ready_to_bill_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Billing hold</strong><span>{int(billing_counts.get("on_hold_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Claims denied</strong><span>{int(billing_counts.get("denied_claims_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Rejected</strong><span>{int(billing_counts.get("rejected_claims_count", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Pending follow-up</strong><span>{int(billing_counts.get("claims_pending_follow_up", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Paid today</strong><span>{int(billing_counts.get("paid_today", 0) or 0)}</span></div>'
        + f'<div class="mini-row"><strong>Underpayments</strong><span>{int(billing_counts.get("underpayments", 0) or 0)}</span></div>'
        + "</div>"
        + "</article>"
    )
    claims_billing_queue_markup = (
        '<section id="claims-billing-queue" class="dual-grid"'
        + _section_hidden(current_page, "claims")
        + ">"
        + '<article class="panel section-card">'
        + "<h2>Billing Queue Ready</h2>"
        + "<p>Sesiones con nota, auth y validaciones listas para empujar al claim builder.</p>"
        + '<div class="table-wrap"><table><thead><tr>'
        + "<th>DOS</th><th>Cliente</th><th>Provider</th><th>Payer</th><th>CPT</th><th>Units</th><th>Note</th><th>Session</th><th>Billing</th><th>Validaciones</th><th>Acciones</th>"
        + "</tr></thead><tbody>"
        + _render_operational_session_rows(billing_queue_ready_preview, claim_links=True)
        + "</tbody></table></div>"
        + "</article>"
        + '<article class="panel section-card">'
        + "<h2>Billing Hold Queue</h2>"
        + "<p>Errores previos a billing para corregir nota, auth, payer data, compliance o rendering provider.</p>"
        + '<div class="table-wrap"><table><thead><tr>'
        + "<th>DOS</th><th>Cliente</th><th>Provider</th><th>Payer</th><th>CPT</th><th>Units</th><th>Note</th><th>Session</th><th>Billing</th><th>Validaciones</th><th>Acciones</th>"
        + "</tr></thead><tbody>"
        + _render_operational_session_rows(billing_queue_hold_preview, claim_links=True)
        + "</tbody></table></div>"
        + "</article>"
        + "</section>"
    )
    claims_batch_preview_markup = (
        '<section id="claims-batch-preview" class="panel section-card"'
        + _section_hidden(current_page, "claims")
        + ">"
        + "<h2>Claim Batch Preview</h2>"
        + "<p>Los standalone claims nacen desde appointments listos del calendario y luego se agrupan por cliente, provider, payer, autorizacion y periodo para convertirlos en batch claim 837.</p>"
        + '<div class="table-wrap"><table><thead><tr>'
        + "<th>Cliente</th><th>Provider</th><th>Payer</th><th>Authorization</th><th>Periodo</th><th>Sesiones</th><th>Units by CPT</th><th>Validation</th><th>Acciones</th>"
        + "</tr></thead><tbody>"
        + _render_claim_batch_rows(claim_batches_preview, include_totals=can_view_claim_totals)
        + "</tbody></table></div>"
        + f'<p class="helper-note">Mostrando {len(claim_batches_preview)} batch(es) sugeridos desde {html.escape(claim_batch_source_label)}.</p>'
        + "</section>"
    )
    claims_follow_up_markup = (
        '<section class="dual-grid"'
        + _section_hidden(current_page, "claims")
        + ">"
        + claims_bridge_markup
        + claims_dashboard_markup
        + "</section>"
        + '<section id="claims-follow-up" class="panel section-card"'
        + _section_hidden(current_page, "claims")
        + ">"
        + "<h2>Claims Pending Follow-up</h2>"
        + "<p>Esta cola junta claims transmitidos, parciales o denegados para seguimiento, apelacion o pago restante.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Paciente</th><th>Payer</th><th>Status</th><th>Tracking</th><th>Balance</th><th>Dias abierto</th><th>Acciones</th></tr></thead><tbody>'
        + _render_claim_follow_up_rows(follow_up_claims[:18])
        + "</tbody></table></div>"
        + "</section>"
        + '<section class="dual-grid"'
        + _section_hidden(current_page, "claims")
        + ">"
        + '<article class="panel section-card">'
        + "<h2>Denied Claims Queue</h2>"
        + "<p>Denials reales del payer que requieren reconsideration, follow-up o correccion operativa.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Paciente</th><th>Payer</th><th>Status</th><th>Tracking</th><th>Balance</th><th>Dias abierto</th><th>Acciones</th></tr></thead><tbody>'
        + _render_claim_follow_up_rows(denied_claims[:12])
        + "</tbody></table></div>"
        + "</article>"
        + '<article class="panel section-card">'
        + "<h2>Rejected Claims Queue</h2>"
        + "<p>Claims que aun no salen del batch o se devolvieron antes de una aceptacion formal.</p>"
        + '<div class="table-wrap"><table><thead><tr><th>Claim</th><th>Paciente</th><th>Payer</th><th>Status</th><th>Tracking</th><th>Balance</th><th>Dias abierto</th><th>Acciones</th></tr></thead><tbody>'
        + _render_claim_follow_up_rows(rejected_claims[:12])
        + "</tbody></table></div>"
        + "</article>"
        + "</section>"
    )

    eligibility_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="eligibility",
        kicker="Eligibility workspace",
        title="Consulta y Roster de Elegibilidad",
        copy="Verifica coverage individual, lleva historial y controla el roster automatico que el sistema revisa de forma programada.",
        pills=["271", "Roster", "History"],
        primary_label="Consulta 271",
        primary_href="#eligibility271",
        secondary_label="Roster",
        secondary_href="#eligibility-roster",
    )
    eligibility_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "eligibility")
        + ">"
        + _metric_card_markup("Roster activo", active_roster_entries_count, "Pacientes entrando al scheduler automatico.", tone="success", note=current_agency_name, href="#eligibility-roster")
        + _metric_card_markup("Consultas", eligibility_used, "Ejecuciones de elegibilidad registradas.", tone="neutral", note="History", href="#eligibility-history")
        + _metric_card_markup("Auto clients", auto_eligibility_clients_count, "Clientes con bandera de elegibilidad automatica.", tone="warm", note="Casos", href=_page_href("clients"))
        + _metric_card_markup("Scheduler", f"{eligibility_interval_hours}h", "Frecuencia actual del motor automatico.", tone="neutral", note=f"Dias {eligibility_run_days_label}", href=_page_href("security"))
        + "</section>"
    )

    enrollments_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="enrollments",
        kicker="Credentialing workspace",
        title="Payer Enrollments",
        copy="Controla submission, follow-up y fecha efectiva de cada enrollment con un roster administrativo claro.",
        pills=["Submitted", "Follow-up", "Effective dates"],
        primary_label="Nuevo enrollment",
        primary_href="#payer-enrollment-form",
        secondary_label="Roster",
        secondary_href="#payer-enrollment-roster",
    )
    enrollments_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "enrollments")
        + ">"
        + _metric_card_markup("Pendientes", enrollment_pending_count, "Enrollments aun no cerrados o bajo seguimiento.", tone="warm" if enrollment_pending_count else "success", note="Roster", href="#payer-enrollment-roster")
        + _metric_card_markup("Enrolled", enrollment_enrolled_count, "Payers ya efectivos y cerrados.", tone="success", note=current_agency_name, href="#payer-enrollment-roster")
        + _metric_card_markup("Seguimiento", len(enrollment_audit_logs), "Eventos administrativos registrados.", tone="neutral", note="Audit trail", href="#payer-enrollment-audit")
        + _metric_card_markup("Providers criticos", len(supervision_credential_pending), "Expedientes con credencializacion abierta.", tone="danger" if supervision_credential_pending else "success", note="90 dias", href=_page_href("providers"))
        + "</section>"
    )

    agencies_page_intro_markup = _simple_page_intro_markup(
        current_page=current_page,
        page_key="agencies",
        kicker="Agencies workspace",
        title="Agencias y Ecosistemas",
        copy="Separa operaciones por agencia, branding y contacto para que todo el portal respete el ecosistema correcto.",
        pills=["Branding", "Contact", "Multi-agency"],
        primary_label="Nueva agencia",
        primary_href="#agency-form",
        secondary_label="Listado",
        secondary_href="#agency-list",
    )
    agencies_metrics_markup = (
        '<section class="metric-grid"'
        + _section_hidden(current_page, "agencies")
        + ">"
        + _metric_card_markup("Agencias", len(agencies), "Ecosistemas cargados en el portal.", tone="neutral", note=f"{agencies_with_logo_count} con logo", href="#agency-list")
        + _metric_card_markup("Activa", current_agency_name, "Contexto donde se estan guardando los registros actuales.", tone="success", note=current_agency_id or "Sin ID", href="#agency-current")
        + _metric_card_markup("Alertas", len(notifications), "Notificaciones ligadas a la agencia activa.", tone="neutral", note="Notifications", href=_page_href("notifications"))
        + _metric_card_markup("Payers", active_payers_count, "Seguros activos dentro del ecosistema.", tone="warm", note="Billing setup", href=_page_href("payers"))
        + "</section>"
    )
    providers_hub_markup = _render_workspace_hub(
        "Centro de Providers",
        "Entra por tarjetas al alta, directorio, expediente activo y documentos del provider.",
        [
            {
                "href": _new_provider_href(),
                "icon": "AL",
                "title": "Alta",
                "copy": "Crear o continuar un expediente de provider.",
                "meta": "Formulario principal" if can_manage_provider_contracts else "Solo visible para managers",
            }
            if can_manage_provider_contracts
            else {},
            {
                "href": "#providers-directory",
                "icon": "LS",
                "title": "Directorio",
                "copy": "Vista en tarjetas del roster de providers.",
                "meta": f"{len(visible_provider_contracts)} expediente(s)",
            },
            {
                "href": "#provider-detail",
                "icon": "EX",
                "title": "Expediente",
                "copy": "Abrir el detalle completo del provider seleccionado.",
                "meta": str((selected_provider_contract or {}).get("provider_name", "")).strip() or "Sin expediente abierto",
            },
            {
                "href": "#provider-documents",
                "icon": "DC",
                "title": "Documentos",
                "copy": "Checklist documental y archivos archivados.",
                "meta": f"{len(visible_provider_contracts)} roster(s) en seguimiento",
            },
        ],
    )
    users_hub_markup = _render_workspace_hub(
        "Centro de Recursos Humanos",
        "Entra desde aqui a las cuatro areas principales de recursos humanos sin llenar el menu azul de subopciones.",
        [
            {
                "href": "#users-directory",
                "icon": "OF",
                "title": "Empleados oficina",
                "copy": "Directorio visual y edicion del personal administrativo.",
                "meta": f"{len(users)} empleado(s)",
            },
            {
                "href": f"{_page_href('enrollments')}#payer-enrollment-roster",
                "icon": "CR",
                "title": "Credenciales / Enrollment",
                "copy": "Abrir credencializacion, roster y seguimiento por payer.",
                "meta": f"{len(payer_enrollments)} enrollment(s)",
            },
            {
                "href": _page_href('eligibility'),
                "icon": "EL",
                "title": "Elegibilidad",
                "copy": "Entrar a validacion individual y roster de coverage.",
                "meta": f"{len(roster_entries)} registro(s)",
            },
        ],
    )
    users_operations_hub_markup = _render_workspace_hub(
        "Operaciones del Equipo",
        "Las herramientas administrativas del personal tambien quedan dentro de recursos humanos para que el menu azul se vea mas limpio.",
        [
            {
                "href": _page_href("aba_notes"),
                "icon": "NB",
                "title": "Notas ABA",
                "copy": "Sesiones, service logs y workflow clinico del equipo.",
                "meta": f"{len(aba_service_logs)} log(s)",
            }
            if "aba_notes" in allowed_pages
            else {},
            {
                "href": f"{_page_href('agenda')}#supervision-center",
                "icon": "SP",
                "title": "Supervision",
                "copy": "Barras de contratacion, credenciales y casos abiertos.",
                "meta": f"{len(supervision_open_contracts)} abierto(s)",
            }
            if can_view_supervision_center and "agenda" in allowed_pages
            else {},
            {
                "href": _page_href("agenda"),
                "icon": "AG",
                "title": "Agenda",
                "copy": "Tareas, calendario y pendientes del equipo.",
                "meta": f"{len(my_pending_tasks)} pendiente(s)",
            }
            if "agenda" in allowed_pages
            else {},
            {
                "href": _page_href("notifications"),
                "icon": "NT",
                "title": "Notificaciones",
                "copy": "Cola de email, alertas y avisos internos.",
                "meta": f"{len(notifications)} notificacion(es)",
            }
            if "notifications" in allowed_pages
            else {},
        ],
    )
    enrollments_hub_markup = _render_workspace_hub(
        "Centro de Credencializacion",
        "Todo el flujo de credenciales y payers queda accesible desde estas tarjetas.",
        [
            {
                "href": "#payer-enrollment-form",
                "icon": "NW",
                "title": "Nuevo",
                "copy": "Registrar o actualizar credenciales por payer.",
                "meta": "Formulario de enrollment",
            },
            {
                "href": "#payer-enrollment-roster",
                "icon": "RS",
                "title": "Roster",
                "copy": "Ver estado, fechas y barras del enrollment.",
                "meta": f"{len(payer_enrollments)} linea(s)",
            },
            {
                "href": "#payer-enrollment-audit",
                "icon": "AU",
                "title": "Auditoria",
                "copy": "Bitacora administrativa de follow up y cambios.",
                "meta": f"{len(enrollment_audit_logs)} evento(s)",
            },
            {
                "href": "#provider-contract-form",
                "icon": "PR",
                "title": "Providers",
                "copy": "Saltar a expedientes cuando el enrollment depende del provider.",
                "meta": f"{len(supervision_credential_pending)} pendiente(s) critico(s)",
            },
        ],
    )
    payers_hub_markup = _render_workspace_hub(
        "Centro de Payers",
        "Configura el catalogo visual de seguros, sus clearinghouses y las tarifas CPT que usa billing.",
        [
            {
                "href": "#payer-config-form",
                "icon": "AL",
                "title": "Alta",
                "copy": "Crear o editar un payer con sus datos de clearinghouse.",
                "meta": "Formulario principal",
            },
            {
                "href": "#payers-directory",
                "icon": "LS",
                "title": "Directorio",
                "copy": "Ver los seguros en tarjetas interactivas.",
                "meta": f"{len(payer_configs)} payer(s)",
            },
            {
                "href": "#payer-center",
                "icon": "CH",
                "title": "Clearinghouse",
                "copy": "Revisar payer IDs, receiver IDs y conexion de envio.",
                "meta": str((selected_payer_config or {}).get("clearinghouse_name", "")).strip() or "Sin seleccion",
            },
            {
                "href": "#payer-rates",
                "icon": "CP",
                "title": "Tarifas CPT",
                "copy": "Editar unit prices y billing codes por seguro.",
                "meta": f"{int((selected_payer_config or {}).get('active_rate_count', 0) or 0)} tarifa(s) activa(s)",
            },
        ],
    )
    billing_hub_markup = _render_workspace_hub(
        "Centro de Billing",
        "Usa tarjetas pequenas para entrar a claims, remesas, payers y autorizaciones sin llenar toda la pantalla de botones largos.",
        [
            {
                "href": "#claims837",
                "icon": "83",
                "title": "Claims 837P",
                "copy": "Preparar y guardar el claim profesional.",
                "meta": f"{claim_summary.get('pending', 0)} pendiente(s)",
            },
            {
                "href": _page_href("payments"),
                "icon": "85",
                "title": "Remesas ERA",
                "copy": "Importar y revisar archivos 835.",
                "meta": f"{len(era_archives)} remesa(s)",
            },
            {
                "href": f"{_page_href('payers')}#payer-config-form",
                "icon": "PY",
                "title": "Payers",
                "copy": "Configurar seguros, unit prices y clearinghouse.",
                "meta": f"{len(payer_configs)} payer(s)",
            },
            {
                "href": f"{_page_href('clients')}#client-directory",
                "icon": "AU",
                "title": "Autorizaciones",
                "copy": "Editar CPTs, units y vigencia del cliente.",
                "meta": f"{len(authorizations)} autorizacion(es)",
            },
        ],
    )
    agencies_hub_markup = _render_workspace_hub(
        "Centro de Agencias",
        "Las agencias, logos y configuracion operativa ahora tambien se navegan por tarjetas.",
        [
            {
                "href": "#agency-form",
                "icon": "AL",
                "title": "Registro",
                "copy": "Crear o editar una agencia con logo y datos base.",
                "meta": "Formulario principal",
            },
            {
                "href": "#agency-current",
                "icon": "AC",
                "title": "Activa",
                "copy": "Ver la agencia de trabajo actual y su logo.",
                "meta": current_agency_name,
            },
            {
                "href": "#agency-list",
                "icon": "LS",
                "title": "Listado",
                "copy": "Cambiar de agencia activa y revisar datos guardados.",
                "meta": f"{len(agencies)} agencia(s)",
            },
        ],
    )
    agenda_hub_tiles: list[dict[str, object]] = [
        {
            "href": "#agenda-form",
            "icon": "TK",
            "title": "Tareas",
            "copy": "Crear deadlines, follow ups y eventos del equipo.",
            "meta": "Formulario de agenda",
        },
        {
            "href": "#agenda-my-work",
            "icon": "MY",
            "title": "Mi lista",
            "copy": "Abrir tus pendientes y marcar avances.",
            "meta": f"{len(my_pending_tasks)} pendiente(s)",
        },
        {
            "href": "#agenda-calendar",
            "icon": "CL",
            "title": "Calendario",
            "copy": "Vista mensual y agenda general compartida.",
            "meta": f"{len(calendar_events)} evento(s)",
        },
        {
            "href": "#agenda-notes",
            "icon": "NT",
            "title": "Notas",
            "copy": "Tus notas privadas de trabajo y seguimiento.",
            "meta": f"{len(my_notes)} nota(s)",
        },
    ]
    if can_view_supervision_center:
        agenda_hub_tiles.insert(
            2,
            {
                "href": "#supervision-center",
                "icon": "SP",
                "title": "Supervision",
                "copy": "Control ejecutivo de contratacion, supervisor y credenciales.",
                "meta": f"{len(supervision_open_contracts)} abierto(s)",
            },
        )
    agenda_hub_markup = _render_workspace_hub(
        "Centro de Oficina",
        "La agenda de oficina ahora arranca con tarjetas para entrar al bloque correcto mas rapido.",
        agenda_hub_tiles,
    )
    aba_notes_hub_markup = _render_workspace_hub(
        "Centro de Notas ABA",
        "Abre el modulo por cuadros pequenos para que la sesion, el log semanal y el roster no se vean como bandas largas.",
        [
            {
                "href": "#aba-notes-form",
                "icon": "NS",
                "title": "Nueva sesion",
                "copy": "Crear la sesion y la nota desde el formulario principal.",
                "meta": f"{len(aba_provider_options)} provider(s)",
            },
            {
                "href": "#aba-appointments",
                "icon": "AP",
                "title": "Sesiones",
                "copy": "Ver las sesiones ABA ya guardadas en el sistema.",
                "meta": f"{len(aba_appointments)} sesion(es)",
            },
            {
                "href": "#aba-service-logs",
                "icon": "LG",
                "title": "Service logs",
                "copy": "Revisar workflow y cierre semanal de notas.",
                "meta": f"{len(aba_service_logs)} log(s)",
            },
            {
                "href": "#aba-module-center",
                "icon": "CT",
                "title": "Centro",
                "copy": "Resumen del modulo y relacion con clientes y providers.",
                "meta": f"{len(aba_client_options)} cliente(s)",
            },
        ],
    )
    notifications_hub_markup = _render_workspace_hub(
        "Centro de Comunicacion",
        "Abre redaccion y cola de alertas desde tarjetas interactivas.",
        [
            {
                "href": "#notifications-compose",
                "icon": "ML",
                "title": "Outlook",
                "copy": "Crear draft o enviar email desde Outlook.",
                "meta": "Composer de email",
            },
            {
                "href": "#notifications-center",
                "icon": "AL",
                "title": "Alertas",
                "copy": "Ver notificaciones, colas y reenvios.",
                "meta": f"{len(notifications)} notificacion(es)",
            },
        ],
    )
    try:
        provider_credentialing_due_preview = (
            add_user_date_months(str(provider_contract_values.get("credentialing_start_date", "")).strip(), 3)
            if str(provider_contract_values.get("credentialing_start_date", "")).strip()
            else ""
        )
    except ValueError:
        provider_credentialing_due_preview = ""

    claim_class = _module_card_class(active_panel, "claim", "claim-tone")
    edi837_class = _module_card_class(active_panel, "edi837", "claim-tone")
    eligibility_class = _module_card_class(active_panel, "eligibility", "eligibility-tone")
    era_class = _module_card_class(active_panel, "era", "era-tone")
    editing_provider_contract = bool(str(provider_contract_values.get("contract_id", "")).strip())

    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(portal_label)}</title>
  <style>
    :root {{
      --bg: #edf3fa;
      --surface: rgba(255, 255, 255, 0.98);
      --surface-strong: #ffffff;
      --sidebar: #0f4db4;
      --sidebar-2: #0a3d96;
      --sidebar-3: #18b8c9;
      --ink: #17324c;
      --muted: #6c8096;
      --blue: #0f61d8;
      --blue-strong: #0a49a8;
      --sky: #52a7ff;
      --teal: #18b8c9;
      --green: #17a36b;
      --line: rgba(17, 42, 74, 0.08);
      --shadow: 0 18px 40px rgba(16, 42, 73, 0.09);
      --danger: #cf5b6e;
      --success: #189b67;
      --warning: #e3a742;
    }}
    * {{ box-sizing: border-box; }}
    [hidden] {{ display: none !important; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      background:
        radial-gradient(circle at 16% 14%, rgba(82, 167, 255, 0.10) 0, transparent 24%),
        radial-gradient(circle at 88% 10%, rgba(24, 184, 201, 0.08) 0, transparent 18%),
        linear-gradient(180deg, #f7fafe 0%, #eef4fb 44%, #eaf0f7 100%);
      background-attachment: fixed;
      color: var(--ink);
    }}
    a {{ color: inherit; text-decoration: none; }}
    code {{
      font-family: Consolas, "Courier New", monospace;
      background: rgba(23, 49, 77, 0.06);
      border-radius: 8px;
      padding: 2px 6px;
    }}
    .layout {{
      position: relative;
      isolation: isolate;
      display: grid;
      grid-template-columns: 282px minmax(0, 1fr);
      min-height: 100vh;
      align-items: start;
    }}
    .layout::before {{
      content: "";
      position: fixed;
      inset: 0;
      z-index: 0;
      background:
        radial-gradient(circle at 18% 18%, rgba(82, 167, 255, 0.08) 0, transparent 22%),
        radial-gradient(circle at 84% 84%, rgba(24, 184, 201, 0.08) 0, transparent 24%),
        linear-gradient(180deg, #f8fbff 0%, #eef3fb 46%, #e8eef7 100%);
      pointer-events: none;
    }}
    .sidebar {{
      position: relative;
      z-index: 1;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: hidden;
      padding: 12px 10px;
      background:
        radial-gradient(circle at top right, rgba(255, 255, 255, 0.16) 0, transparent 26%),
        linear-gradient(180deg, #2e73df 0%, #145dca 44%, #0d4fb9 74%, #0a429b 100%);
      border-right: 1px solid rgba(255, 255, 255, 0.08);
      color: #ffffff;
    }}
    .sidebar-inner {{
      display: grid;
      gap: 14px;
      height: 100%;
      min-height: 0;
      align-content: start;
      overflow-y: auto;
      overscroll-behavior: contain;
      scrollbar-gutter: stable;
      padding-right: 2px;
    }}
    .sidebar-inner::-webkit-scrollbar {{
      width: 10px;
    }}
    .sidebar-inner::-webkit-scrollbar-track {{
      background: rgba(255, 255, 255, 0.08);
      border-radius: 999px;
    }}
    .sidebar-inner::-webkit-scrollbar-thumb {{
      background: rgba(255, 255, 255, 0.28);
      border-radius: 999px;
    }}
    .brand-card,
    .sidebar-panel {{
      border-radius: 28px;
      padding: 16px;
      box-shadow: 0 24px 38px rgba(5, 20, 49, 0.18);
    }}
    .brand-card {{
      border: 1px solid rgba(255, 255, 255, 0.78);
      background: rgba(255, 255, 255, 0.98);
      display: grid;
      grid-template-columns: 72px minmax(0, 1fr);
      gap: 14px;
      align-items: center;
    }}
    .brand-card.wordmark-card {{
      grid-template-columns: 1fr;
      justify-items: center;
      padding: 16px 14px;
    }}
    .sidebar-panel {{
      border: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(255, 255, 255, 0.10);
      backdrop-filter: blur(12px);
    }}
    .sidebar-footer {{
      display: grid;
      gap: 10px;
      margin-top: auto;
    }}
    .sidebar-panel-compact {{
      gap: 14px;
    }}
    .brand-logo {{
      padding: 10px;
      border-radius: 20px;
      background: #ffffff;
      box-shadow: 0 14px 28px rgba(15, 32, 52, 0.08);
    }}
    .brand-card.wordmark-card .brand-logo {{
      width: 100%;
      max-width: 270px;
      padding: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }}
    .brand-logo img,
    .brand-logo svg {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .brand-card.wordmark-card .brand-logo img,
    .brand-card.wordmark-card .brand-logo svg {{
      max-height: 112px;
      object-fit: contain;
      margin: 0 auto;
    }}
    .brand-meta {{
      display: grid;
      gap: 6px;
    }}
    .brand-tag,
    .eyebrow,
    .module-badge,
    .result-kicker,
    .summary-card span,
    .stat-card span {{
      font: 800 11px/1.2 "Inter", "Segoe UI", sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .brand-tag {{ color: var(--blue); display: none; }}
    .brand-title {{
      margin: 0;
      max-width: 180px;
      font-size: 28px;
      font-weight: 800;
      line-height: 1.02;
      color: var(--ink);
    }}
    .brand-copy,
    .nav-copy span,
    .sidebar-panel p {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
      margin: 0;
    }}
    .brand-copy {{
      display: none;
    }}
    .nav-group {{ display: grid; gap: 10px; }}
    .nav-stack {{
      display: grid;
      gap: 8px;
    }}
    .nav-link {{
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      min-height: 68px;
      padding: 12px 14px 12px 12px;
      border-radius: 22px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.11) 0%, rgba(255, 255, 255, 0.05) 100%);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.07), 0 14px 24px rgba(8, 39, 92, 0.12);
      transition: background 160ms ease, border-color 160ms ease, box-shadow 160ms ease, opacity 160ms ease;
    }}
    .nav-link:hover,
    .nav-link.active {{
      background: linear-gradient(180deg, rgba(99, 166, 255, 0.28) 0%, rgba(255, 255, 255, 0.10) 100%);
      border-color: rgba(255, 255, 255, 0.16);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.10), 0 16px 28px rgba(8, 39, 92, 0.18);
    }}
    .nav-icon {{
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      border-radius: 12px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.15) 0%, rgba(255, 255, 255, 0.07) 100%);
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: #ffffff;
      font: 800 11px/1 "Inter", "Segoe UI", sans-serif;
      letter-spacing: 0.08em;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
    }}
    .nav-link.active .nav-icon,
    .nav-link:hover .nav-icon {{
      background: linear-gradient(180deg, rgba(113, 177, 255, 0.30) 0%, rgba(255, 255, 255, 0.12) 100%);
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.10);
    }}
    .nav-copy {{
      display: grid;
      gap: 0;
      min-width: 0;
    }}
    .nav-link strong {{
      font: 800 15px/1.15 "Inter", "Segoe UI", sans-serif;
      color: #ffffff;
    }}
    .sidebar-profile-panel {{
      padding: 18px 16px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.14) 0%, rgba(255, 255, 255, 0.09) 100%);
    }}
    .nav-subgroup {{
      display: grid;
      gap: 6px;
      margin: -2px 0 4px 56px;
      padding-left: 14px;
      border-left: 1px solid rgba(255, 255, 255, 0.14);
    }}
    .nav-sublink {{
      display: block;
      padding: 9px 12px;
      border-radius: 14px;
      color: rgba(235, 244, 255, 0.82);
      text-decoration: none;
      font: 700 13px/1.35 "Inter", "Segoe UI", sans-serif;
      background: rgba(255, 255, 255, 0.06);
      transition: background 160ms ease, color 160ms ease;
    }}
    .nav-sublink:hover,
    .nav-sublink.active {{
      background: rgba(255, 255, 255, 0.16);
      color: #ffffff;
    }}
    .sidebar-panel h2 {{
      margin: 0 0 10px;
      font-size: 15px;
      letter-spacing: 0.02em;
      font-family: "Inter", "Segoe UI", sans-serif;
      color: #ffffff;
    }}
    .sidebar-list {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.65;
    }}
    .sidebar-user {{
      display: grid;
      grid-template-columns: 88px 1fr;
      gap: 14px;
      align-items: center;
      margin-bottom: 14px;
    }}
    .avatar-shell,
    .profile-avatar {{
      overflow: hidden;
      display: grid;
      place-items: center;
      background: rgba(21, 88, 182, 0.08);
      border: 1px solid rgba(21, 88, 182, 0.10);
      box-shadow: 0 12px 24px rgba(15, 32, 52, 0.08);
    }}
    .avatar-shell {{
      width: 52px;
      height: 52px;
      border-radius: 18px;
    }}
    .sidebar-profile-panel .avatar-shell {{
      width: 88px;
      height: 88px;
      border-radius: 28px;
      background: rgba(255, 255, 255, 0.96);
      border-color: rgba(255, 255, 255, 0.25);
      box-shadow: 0 18px 30px rgba(7, 29, 71, 0.14);
    }}
    .profile-avatar {{
      width: 92px;
      height: 92px;
      border-radius: 28px;
      background: rgba(13, 81, 184, 0.08);
      border-color: rgba(23, 49, 77, 0.10);
      box-shadow: none;
    }}
    .avatar-shell img,
    .avatar-shell .avatar-fallback,
    .profile-avatar img,
    .profile-avatar .avatar-fallback {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: grid;
      place-items: center;
    }}
    .avatar-fallback {{
      color: #ffffff;
      font: 800 22px/1 "Inter", "Segoe UI", sans-serif;
    }}
    .profile-label {{
      display: block;
      margin-bottom: 4px;
      color: rgba(255, 255, 255, 0.80);
      font: 800 11px/1.2 "Inter", "Segoe UI", sans-serif;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .profile-name {{
      display: block;
      color: #ffffff;
      font-size: 22px;
      font-weight: 800;
      line-height: 1.05;
      margin-bottom: 6px;
    }}
    .profile-copy {{
      color: rgba(235, 244, 255, 0.78);
      font-size: 16px;
      line-height: 1.55;
    }}
    .sidebar-stat-grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .sidebar-stat-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 8px 14px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.14);
      color: #ffffff;
      font: 800 12px/1 "Inter", "Segoe UI", sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .sidebar-actions {{
      display: grid;
      justify-items: start;
      gap: 12px;
      margin-top: 4px;
    }}
    .sidebar-profile-panel .small-button {{
      min-height: 42px;
      padding: 10px 18px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.96);
      color: #0d57c0;
      border-color: rgba(255, 255, 255, 0.55);
      font: 700 14px/1 "Inter", "Segoe UI", sans-serif;
      box-shadow: 0 12px 24px rgba(7, 29, 71, 0.10);
    }}
    .sidebar-profile-panel .table-action-form .small-button {{
      border-radius: 999px;
      padding-left: 22px;
      padding-right: 22px;
    }}
    .sidebar-mini-list {{
      display: grid;
      gap: 10px;
    }}
    .sidebar-mini-row {{
      display: grid;
      gap: 4px;
    }}
    .sidebar-mini-row strong {{
      color: rgba(255, 255, 255, 0.72);
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .sidebar-mini-row span {{
      color: #ffffff;
      font-size: 14px;
      line-height: 1.4;
    }}
    .content {{
      position: relative;
      z-index: 1;
      padding: 22px 26px 34px;
      min-height: auto;
      align-self: start;
      background:
        radial-gradient(circle at 82% 16%, rgba(82, 167, 255, 0.08) 0, transparent 16%),
        radial-gradient(circle at 18% 80%, rgba(24, 184, 201, 0.05) 0, transparent 20%);
    }}
    .content-inner {{
      position: relative;
      z-index: 1;
      width: min(1640px, 100%);
      margin: 0 auto;
      display: grid;
      gap: 22px;
      align-content: start;
    }}
    .topbar-leading {{
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }}
    .topbar-menu {{
      min-width: 44px;
      width: 44px;
      height: 44px;
      padding: 0;
      border-radius: 14px;
      border: 1px solid rgba(17, 42, 74, 0.08);
      background: #f5f8fd;
      color: var(--blue-strong);
      box-shadow: none;
      font-size: 18px;
    }}
    .topbar-menu:hover {{
      background: linear-gradient(135deg, #0d56c0 0%, #0a49a8 100%);
      color: #ffffff;
      border-color: rgba(13, 86, 192, 0.18);
      box-shadow: 0 12px 20px rgba(13, 86, 192, 0.16);
    }}
    .topbar {{
      display: grid;
      grid-template-columns: minmax(220px, auto) minmax(240px, 1fr) auto;
      gap: 16px;
      align-items: center;
      min-height: 80px;
      padding: 18px 22px;
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.98);
      border: 1px solid rgba(17, 42, 74, 0.07);
      box-shadow: 0 16px 30px rgba(16, 42, 73, 0.06);
    }}
    .topbar-title-block {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}
    .topbar-title-block strong {{
      color: var(--ink);
      font: 800 18px/1.1 "Inter", "Segoe UI", sans-serif;
      letter-spacing: -0.03em;
    }}
    .topbar-context {{
      color: var(--muted);
      font: 700 10px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }}
    .topbar-search {{
      display: grid;
      gap: 0;
      max-width: 360px;
    }}
    .topbar-search span,
    .topbar-meta-label {{
      display: none;
    }}
    .topbar-search input {{
      border-radius: 14px;
      border: 1px solid rgba(17, 42, 74, 0.08);
      background: #f6f9fd;
      min-height: 44px;
      padding: 12px 14px;
      font: 14px/1.2 "Inter", "Segoe UI", sans-serif;
      color: var(--ink);
    }}
    .topbar-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
      align-items: center;
    }}
    .topbar-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 0 18px;
      border-radius: 12px;
      background: linear-gradient(135deg, #0d56c0 0%, #0a49a8 100%);
      color: #ffffff;
      font: 700 13px/1 "Inter", "Segoe UI", sans-serif;
      box-shadow: 0 12px 22px rgba(13, 86, 192, 0.18);
    }}
    .topbar-button:hover {{
      transform: translateY(-1px);
      box-shadow: 0 16px 26px rgba(13, 86, 192, 0.22);
    }}
    .topbar-counter {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      min-height: 44px;
      padding: 10px 12px;
      border-radius: 14px;
      background: #f7faff;
      border: 1px solid rgba(17, 42, 74, 0.07);
      color: var(--ink);
      font: 700 13px/1 "Inter", "Segoe UI", sans-serif;
    }}
    .topbar-counter:hover,
    .topbar-profile:hover {{
      border-color: rgba(21, 88, 182, 0.12);
      box-shadow: 0 12px 20px rgba(21, 88, 182, 0.06);
    }}
    .topbar-counter span {{
      color: var(--muted);
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.10em;
      text-transform: uppercase;
    }}
    .topbar-counter strong {{
      color: var(--blue);
      font: 800 14px/1 "Inter", "Segoe UI", sans-serif;
    }}
    .topbar-profile {{
      display: grid;
      grid-template-columns: 42px minmax(0, 1fr);
      gap: 10px;
      align-items: center;
      padding: 6px 8px 6px 8px;
      border-radius: 16px;
      background: #f7faff;
      border: 1px solid rgba(17, 42, 74, 0.07);
      min-width: 200px;
      transition: border-color 160ms ease, box-shadow 160ms ease;
    }}
    .topbar-avatar {{
      width: 42px;
      height: 42px;
      border-radius: 999px;
    }}
    .topbar-profile-copy {{
      display: grid;
      gap: 3px;
      min-width: 0;
    }}
    .topbar-profile-copy span {{
      color: var(--muted);
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
    }}
    .topbar-profile-copy strong {{
      color: var(--ink);
      font: 700 12px/1.25 "Inter", "Segoe UI", sans-serif;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 22px;
      box-shadow: 0 14px 28px rgba(15, 23, 42, 0.06);
      backdrop-filter: blur(8px);
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1.3fr 0.9fr;
      gap: 20px;
      background:
        radial-gradient(circle at right top, rgba(62, 143, 232, 0.12) 0, transparent 34%),
        linear-gradient(135deg, rgba(255, 255, 255, 0.98) 0%, rgba(244, 249, 255, 0.94) 100%);
    }}
    .hero-copy,
    .summary-card,
    .stat-card,
    .module-card,
    .section-card,
    .result-panel {{
      display: grid;
      gap: 12px;
    }}
    .eyebrow {{
      width: fit-content;
      padding: 7px 12px;
      border-radius: 999px;
      background: rgba(30, 127, 206, 0.10);
      color: var(--blue);
    }}
    .hero h2,
    .module-head h2,
    .section-card h2,
    .result-panel h2 {{
      margin: 0;
      font-size: 28px;
      line-height: 1.05;
    }}
    .hero h2 {{
      font-size: clamp(32px, 4.6vw, 54px);
      letter-spacing: -0.04em;
    }}
    .hero p,
    .section-card p,
    .module-head p,
    .module-note,
    .stat-card p,
    .summary-card p,
    .mini-row span {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }}
    .hero-badges {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 180px));
      gap: 10px;
      justify-content: start;
    }}
    .hero-badge {{
      display: grid;
      align-content: center;
      min-height: 72px;
      padding: 12px 14px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.96);
      border: 1px solid rgba(23, 49, 77, 0.08);
      font: 700 13px/1.2 "Trebuchet MS", Verdana, sans-serif;
      text-align: center;
      box-shadow: 0 10px 22px rgba(16, 43, 69, 0.05);
      transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease, border-color 160ms ease;
    }}
    .hero-badge:hover {{
      transform: translateY(-2px);
      color: #ffffff;
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 100%);
      border-color: rgba(16, 54, 93, 0.22);
      box-shadow: 0 18px 28px rgba(16, 43, 69, 0.10);
    }}
    .summary-card {{
      padding: 18px;
      border-radius: 22px;
      background: var(--surface-strong);
    }}
    .summary-card strong,
    .stat-card strong {{
      font-size: 28px;
      line-height: 1.02;
    }}
    .summary-neutral span {{ color: var(--blue); }}
    .summary-success span {{ color: var(--success); }}
    .summary-error span {{ color: var(--danger); }}
    .stats-grid,
    .office-grid,
    .dual-grid,
    .roadmap {{
      display: grid;
      gap: 16px;
    }}
    .stats-grid {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .status-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
    }}
    .office-grid,
    .dual-grid {{
      grid-template-columns: 1fr;
    }}
    .roadmap {{
      grid-template-columns: 1fr;
    }}
    .check-list,
    .sidebar-list {{
      line-height: 1.7;
    }}
    .table-wrap {{
      overflow-x: auto;
      border-radius: 20px;
      border: 1px solid rgba(17, 42, 74, 0.07);
      background: #ffffff;
      box-shadow: 0 10px 24px rgba(16, 42, 73, 0.04);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 720px;
    }}
    th,
    td {{
      padding: 13px 14px;
      border-bottom: 1px solid rgba(17, 42, 74, 0.06);
      text-align: left;
      font-size: 14px;
    }}
    th {{
      font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      color: var(--muted);
      background: #f7f9fc;
    }}
    tbody tr:hover td {{
      background: rgba(242, 247, 255, 0.92);
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .workspace {{
      display: grid;
      gap: 18px;
    }}
    .collapsible-panel {{
      display: grid;
      gap: 0;
      padding: 0;
      overflow: hidden;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(8px);
    }}
    .collapsible-panel[open] {{
      background: var(--surface-strong);
    }}
    .collapsible-summary {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
      padding: 15px 18px;
      cursor: pointer;
      list-style: none;
      user-select: none;
      background: linear-gradient(135deg, rgba(248, 251, 255, 0.94) 0%, rgba(237, 245, 255, 0.88) 100%);
    }}
    .collapsible-summary:hover {{
      background: linear-gradient(135deg, rgba(248, 251, 255, 0.98) 0%, rgba(230, 241, 255, 0.92) 100%);
    }}
    .collapsible-summary::-webkit-details-marker {{
      display: none;
    }}
    .collapsible-summary::marker {{
      content: "";
    }}
    .collapsible-copy {{
      display: grid;
      gap: 3px;
      flex: 1 1 auto;
      min-width: 0;
    }}
    .collapsible-copy strong {{
      color: var(--ink);
      font: 800 15px/1.15 "Inter", "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.01em;
    }}
    .collapsible-copy small {{
      color: var(--muted);
      font: 12px/1.45 "Inter", "Trebuchet MS", Verdana, sans-serif;
    }}
    .collapsible-hint {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 7px 14px;
      border-radius: 999px;
      border: 1px solid rgba(13, 81, 184, 0.12);
      background: linear-gradient(135deg, rgba(248, 251, 255, 0.98) 0%, rgba(225, 238, 255, 0.92) 100%);
      color: var(--blue);
      font: 700 11px/1 "Inter", "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .collapsible-hint::after {{
      content: "+";
      font-size: 16px;
      line-height: 1;
    }}
    .collapsible-panel[open] .collapsible-summary {{
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .collapsible-panel[open] .collapsible-hint::after {{
      content: "-";
    }}
    .collapsible-body {{
      display: grid;
      gap: 16px;
      padding: 0 22px 22px;
    }}
    .collapsible-workspace > .collapsible-body {{
      padding: 22px;
    }}
    .collapsible-form {{
      display: grid;
      gap: 14px;
    }}
    .auto-collapsible-host {{
      gap: 0 !important;
      padding: 0 !important;
      overflow: hidden;
    }}
    .auto-collapsible-host.is-open {{
      background: var(--surface-strong);
    }}
    .auto-collapsible-summary {{
      width: 100%;
      border: 0;
      border-radius: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
      padding: 15px 18px;
      cursor: pointer;
      text-align: left;
      font: inherit;
      box-shadow: none;
      background: linear-gradient(135deg, rgba(248, 251, 255, 0.94) 0%, rgba(237, 245, 255, 0.88) 100%);
      color: inherit;
    }}
    .auto-collapsible-summary:hover {{
      background: linear-gradient(135deg, rgba(248, 251, 255, 0.98) 0%, rgba(230, 241, 255, 0.92) 100%);
    }}
    .auto-collapsible-copy {{
      display: grid;
      gap: 3px;
      flex: 1 1 auto;
      min-width: 0;
    }}
    .auto-collapsible-copy strong {{
      color: var(--ink);
      font: 800 15px/1.15 "Inter", "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.01em;
    }}
    .auto-collapsible-copy small {{
      color: var(--muted);
      font: 12px/1.45 "Inter", "Trebuchet MS", Verdana, sans-serif;
    }}
    .auto-collapsible-hint {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 7px 14px;
      border-radius: 999px;
      border: 1px solid rgba(13, 81, 184, 0.12);
      background: linear-gradient(135deg, rgba(248, 251, 255, 0.98) 0%, rgba(225, 238, 255, 0.92) 100%);
      color: var(--blue);
      font: 700 11px/1 "Inter", "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .auto-collapsible-hint::after {{
      content: "+";
      font-size: 16px;
      line-height: 1;
    }}
    .auto-collapsible-host.is-open .auto-collapsible-summary {{
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .auto-collapsible-host.is-open .auto-collapsible-hint::after {{
      content: "-";
    }}
    .auto-collapsible-body {{
      display: grid;
      gap: 16px;
      padding: 0 22px 22px;
    }}
    .module-card {{
      position: relative;
      transition: transform 170ms ease, box-shadow 170ms ease;
    }}
    .module-card::before {{
      content: "";
      position: absolute;
      inset: 0 0 auto 0;
      height: 7px;
      border-radius: 24px 24px 0 0;
    }}
    .module-card.active,
    .module-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 26px 44px rgba(16, 43, 69, 0.12);
    }}
    .claim-tone::before {{ background: linear-gradient(90deg, #0d51b8 0%, #2f8ded 100%); }}
    .eligibility-tone::before {{ background: linear-gradient(90deg, #0c9249 0%, #25b35b 100%); }}
    .era-tone::before {{ background: linear-gradient(90deg, #10365d 0%, #d91f43 100%); }}
    .module-badge {{
      width: fit-content;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(13, 81, 184, 0.10);
    }}
    .claim-tone .module-badge {{ color: var(--blue); }}
    .eligibility-tone .module-badge {{ color: var(--teal); }}
    .era-tone .module-badge {{ color: #10365d; }}
    .module-note {{
      padding: 13px 14px;
      border-radius: 16px;
      background: rgba(248, 251, 255, 0.88);
      border: 1px dashed rgba(23, 49, 77, 0.14);
      font-size: 14px;
    }}
    .form-section {{
      display: grid;
      gap: 12px;
    }}
    .section-label {{
      font: 700 13px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      color: var(--ink);
    }}
    .field-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .field-grid.compact {{
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .field {{
      display: grid;
      gap: 6px;
    }}
    .field span {{
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      color: var(--muted);
      letter-spacing: 0.03em;
    }}
    .field input,
    .field select,
    .field textarea {{
      width: 100%;
      border-radius: 14px;
      border: 1px solid rgba(23, 49, 77, 0.14);
      background: #fbfdff;
      padding: 12px 13px;
      color: #1b2330;
      font: 14px/1.4 "Trebuchet MS", Verdana, sans-serif;
    }}
    .date-shell {{
      display: flex;
      align-items: center;
      min-height: 48px;
      border-radius: 14px;
      border: 1px solid rgba(23, 49, 77, 0.14);
      background: #fbfdff;
      padding: 0 13px;
    }}
    .date-shell input[type="date"] {{
      border: 0;
      background: transparent;
      padding: 12px 0;
      min-height: 44px;
    }}
    .date-shell input[type="date"]:focus {{
      outline: none;
    }}
    .time-wheel {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) minmax(90px, 0.8fr);
      gap: 8px;
      align-items: center;
    }}
    .time-wheel select {{
      text-align: center;
      font-weight: 700;
    }}
    .time-wheel-separator {{
      color: var(--ink);
      font: 700 20px/1 "Trebuchet MS", Verdana, sans-serif;
      text-align: center;
    }}
    .signature-field {{
      align-self: stretch;
    }}
    .signature-pad-canvas {{
      width: 100%;
      height: 170px;
      border-radius: 18px;
      border: 1px dashed rgba(13, 81, 184, 0.28);
      background: linear-gradient(180deg, #ffffff 0%, #f6faff 100%);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
      touch-action: none;
      cursor: crosshair;
    }}
    .signature-actions {{
      display: flex;
      justify-content: flex-end;
    }}
    .signature-clear-button {{
      width: auto;
      min-width: 0;
    }}
    textarea {{
      width: 100%;
      min-height: 280px;
      resize: vertical;
      border-radius: 18px;
      border: 1px solid rgba(23, 49, 77, 0.14);
      background: #fbfdff;
      padding: 14px;
      font: 14px/1.55 Consolas, "Courier New", monospace;
      color: #1b2330;
    }}
    .service-lines {{
      display: grid;
      gap: 10px;
    }}
    .claim-preview-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }}
    .claim-preview-card {{
      padding: 16px 18px;
      border-radius: 20px;
      border: 1px solid rgba(23, 49, 77, 0.08);
      background: rgba(255, 255, 255, 0.86);
      box-shadow: 0 12px 24px rgba(16, 43, 69, 0.05);
    }}
    .claim-preview-card strong {{
      display: block;
      font-size: 28px;
      line-height: 1.05;
      color: var(--ink);
    }}
    .claim-preview-card span {{
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }}
    .service-lines-shell {{
      border-radius: 22px;
      border: 1px solid rgba(23, 49, 77, 0.10);
      background: rgba(248, 251, 255, 0.62);
      overflow: hidden;
    }}
    .service-lines-toggle {{
      list-style: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px;
      cursor: pointer;
      background: rgba(255, 255, 255, 0.82);
      color: var(--ink);
      font: 700 14px/1.3 "Trebuchet MS", Verdana, sans-serif;
    }}
    .service-lines-toggle::-webkit-details-marker {{
      display: none;
    }}
    .service-lines-toggle strong {{
      color: var(--blue);
      font-size: 16px;
    }}
    .service-lines-body {{
      display: grid;
      gap: 14px;
      padding: 0 16px 16px;
    }}
    .checkbox-inline {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font: 14px/1.4 "Trebuchet MS", Verdana, sans-serif;
      color: var(--ink);
    }}
    .document-checklist {{
      max-height: 620px;
      overflow: auto;
      border-radius: 18px;
      border: 1px solid rgba(23, 49, 77, 0.10);
      background: rgba(248, 251, 255, 0.76);
    }}
    .document-checklist table {{
      min-width: 980px;
    }}
    .client-document-checklist {{
      background: rgba(255, 255, 255, 0.94);
    }}
    .client-document-checklist table {{
      min-width: 1120px;
    }}
    .client-document-checklist th {{
      background: rgba(244, 248, 252, 0.92);
      color: #8794a1;
      letter-spacing: 0.06em;
    }}
    .client-document-checklist td {{
      background: rgba(255, 255, 255, 0.95);
      vertical-align: middle;
    }}
    .document-name-cell {{
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 360px;
      color: var(--ink);
    }}
    .document-caret {{
      color: #94a3b8;
      font-size: 22px;
      line-height: 1;
    }}
    .document-file-icon {{
      width: 18px;
      color: #6f7f93;
      font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.02em;
    }}
    .document-date-input {{
      min-width: 150px;
      text-align: center;
      border-radius: 12px;
      background: #ffffff;
    }}
    .document-expiration-cell {{
      display: grid;
      gap: 8px;
      min-width: 240px;
    }}
    .document-shortcuts {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .document-shortcut {{
      border: 1px solid rgba(23, 49, 77, 0.12);
      background: linear-gradient(180deg, #f8fbff 0%, #e3edf8 100%);
      color: #0d51b8;
      border-radius: 999px;
      padding: 6px 9px;
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.03em;
      cursor: pointer;
      transition: background 160ms ease, color 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    .document-shortcut:hover {{
      transform: translateY(-1px);
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 100%);
      color: #ffffff;
      border-color: rgba(16, 54, 93, 0.22);
    }}
    .document-status-select {{
      min-width: 128px;
      border-radius: 12px;
      font: 700 13px/1 "Trebuchet MS", Verdana, sans-serif;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .document-status-select.delivered {{
      background: rgba(72, 187, 77, 0.16);
      color: #2f7d2f;
      border-color: rgba(72, 187, 77, 0.30);
    }}
    .document-status-select.ignored {{
      background: rgba(148, 163, 184, 0.18);
      color: #556172;
      border-color: rgba(148, 163, 184, 0.30);
    }}
    .document-status-select.pending {{
      background: rgba(59, 130, 246, 0.12);
      color: #195fb4;
      border-color: rgba(59, 130, 246, 0.26);
    }}
    .document-status-select.expired {{
      background: rgba(217, 31, 67, 0.14);
      color: #a21434;
      border-color: rgba(217, 31, 67, 0.28);
    }}
    .document-status-select.pending-approval {{
      background: rgba(245, 158, 11, 0.16);
      color: #8b5a00;
      border-color: rgba(245, 158, 11, 0.28);
    }}
    .document-action-button {{
      position: relative;
      overflow: hidden;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 126px;
      min-height: 38px;
      padding: 10px 14px;
      border-radius: 10px;
      background: linear-gradient(180deg, #f8fbff 0%, #e3edf8 100%);
      color: #0d51b8;
      border: 1px solid rgba(13, 81, 184, 0.14);
      font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.04em;
      cursor: pointer;
      box-shadow: 0 10px 18px rgba(16, 43, 69, 0.06);
      transition: background 160ms ease, color 160ms ease, box-shadow 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    .document-action-button:hover {{
      transform: translateY(-1px);
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 100%);
      color: #ffffff;
      border-color: rgba(16, 54, 93, 0.22);
      box-shadow: 0 18px 28px rgba(16, 43, 69, 0.14);
    }}
    .document-action-button input[type="file"] {{
      position: absolute;
      inset: 0;
      opacity: 0;
      cursor: pointer;
    }}
    .service-row {{
      display: grid;
      grid-template-columns: 1.7fr 1fr 0.7fr 0.8fr;
      gap: 10px;
      padding: 12px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.75);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .service-row-meta {{
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
      justify-content: space-between;
      padding-top: 4px;
    }}
    .service-row-total {{
      display: grid;
      gap: 4px;
    }}
    .service-row-total span {{
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .service-row-total strong {{
      font-size: 16px;
      line-height: 1.1;
      color: var(--ink);
    }}
    button {{
      border: 1px solid rgba(13, 86, 192, 0.12);
      border-radius: 12px;
      padding: 13px 18px;
      color: var(--blue-strong);
      font: 700 14px/1 "Inter", "Segoe UI", sans-serif;
      cursor: pointer;
      width: fit-content;
      background: linear-gradient(180deg, #ffffff 0%, #f1f6ff 100%);
      box-shadow: 0 10px 18px rgba(16, 42, 73, 0.06);
      transition: background 160ms ease, color 160ms ease, box-shadow 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
      color: #ffffff;
      background: linear-gradient(135deg, #0d56c0 0%, #0a49a8 100%);
      border-color: rgba(13, 86, 192, 0.18);
      box-shadow: 0 18px 28px rgba(13, 86, 192, 0.18);
    }}
    .claim-tone button,
    .eligibility-tone button,
    .era-tone button,
    .section-card button,
    .small-button {{
      background: linear-gradient(180deg, #f8fbff 0%, #e3edf8 100%);
      color: var(--blue);
      border: 1px solid rgba(13, 81, 184, 0.14);
    }}
    .table-action-form {{
      margin: 0;
    }}
    .notification-action-stack {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}
    .small-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 9px 12px;
      font-size: 12px;
    }}
    .ai-action-form {{
      display: inline-flex;
      margin: 0;
    }}
    .ai-action-button {{
      white-space: nowrap;
    }}
    .permissions-grid {{
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      align-items: start;
    }}
    .permission-box {{
      padding: 10px 12px;
      border-radius: 16px;
      background: rgba(248, 251, 255, 0.85);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .progress {{
      width: 160px;
      height: 12px;
      border-radius: 999px;
      background: rgba(23, 49, 77, 0.10);
      overflow: hidden;
      margin-bottom: 4px;
    }}
    .progress span {{
      display: block;
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(135deg, #0d51b8 0%, #0c9249 100%);
    }}
    .large-progress {{
      width: 100%;
      height: 16px;
      margin: 4px 0 6px;
    }}
    .authorization-usage-grid {{
      display: grid;
      gap: 16px;
      margin: 18px 0;
    }}
    .authorization-usage-card {{
      display: grid;
      gap: 14px;
      padding: 18px;
      border-radius: 22px;
      background: linear-gradient(180deg, rgba(248, 251, 255, 0.98) 0%, rgba(240, 246, 255, 0.9) 100%);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .authorization-usage-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 14px;
    }}
    .authorization-usage-head strong {{
      color: var(--ink);
      font: 800 20px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .authorization-usage-head span {{
      color: var(--muted);
      font-size: 13px;
    }}
    .authorization-usage-dates {{
      display: grid;
      gap: 4px;
      color: #3f5870;
      font-size: 14px;
    }}
    .authorization-usage-section {{
      display: grid;
      gap: 10px;
    }}
    .authorization-usage-section strong {{
      color: var(--ink);
      font: 800 14px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .authorization-usage-track {{
      position: relative;
      min-height: 44px;
      border-radius: 999px;
      background: rgba(23, 49, 77, 0.10);
      overflow: hidden;
      display: flex;
      align-items: center;
      padding: 0 14px;
    }}
    .authorization-usage-fill {{
      position: absolute;
      inset: 0 auto 0 0;
      border-radius: 999px;
      background: #62bd7b;
    }}
    .authorization-usage-fill.warn {{
      background: #ecaa3a;
    }}
    .authorization-usage-fill.bad {{
      background: #f76767;
    }}
    .authorization-usage-fill.empty {{
      width: 0 !important;
      background: transparent;
    }}
    .authorization-usage-label {{
      position: relative;
      z-index: 1;
      color: #4e6077;
      font: 500 12px/1.35 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.01em;
    }}
    .directory-breadcrumb {{
      margin: 0 0 14px;
      color: #4f6276;
      font: 700 14px/1.4 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.02em;
    }}
    .directory-toolbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 170px 170px auto;
      gap: 14px;
      align-items: end;
      margin-bottom: 20px;
    }}
    .directory-toolbar.no-directory-action {{
      grid-template-columns: minmax(0, 1fr) 170px 170px;
    }}
    .directory-search,
    .directory-filter {{
      display: grid;
      gap: 8px;
    }}
    .directory-search span,
    .directory-filter span {{
      color: var(--muted);
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .directory-search input,
    .directory-filter select {{
      width: 100%;
      min-height: 48px;
      border-radius: 14px;
      border: 1px solid rgba(23, 49, 77, 0.12);
      background: #fbfdff;
      padding: 12px 14px;
      color: #1b2330;
      font: 14px/1.4 "Trebuchet MS", Verdana, sans-serif;
    }}
    .directory-action-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      padding: 0 18px;
      border-radius: 14px;
      background: linear-gradient(180deg, #f8fbff 0%, #e3edf8 100%);
      color: #0d51b8;
      border: 1px solid rgba(13, 81, 184, 0.14);
      font: 700 14px/1 "Trebuchet MS", Verdana, sans-serif;
      text-decoration: none;
      box-shadow: 0 10px 18px rgba(16, 43, 69, 0.06);
      transition: background 160ms ease, color 160ms ease, box-shadow 160ms ease, border-color 160ms ease, transform 160ms ease;
    }}
    .directory-action-button:hover {{
      transform: translateY(-1px);
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 100%);
      color: #ffffff;
      border-color: rgba(16, 54, 93, 0.22);
      box-shadow: 0 18px 28px rgba(16, 43, 69, 0.14);
    }}
    .directory-view {{
      display: block;
    }}
    .directory-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 240px));
      justify-content: start;
      gap: 18px;
    }}
    .directory-card {{
      display: grid;
      justify-items: center;
      gap: 10px;
      min-height: 244px;
      padding: 18px 14px;
      border-radius: 18px;
      border: 1px solid rgba(17, 42, 74, 0.07);
      background: #ffffff;
      box-shadow: 0 12px 24px rgba(16, 42, 73, 0.06);
      text-align: center;
      overflow: hidden;
      transition: box-shadow 180ms ease, border-color 180ms ease;
    }}
    .directory-card:hover,
    .directory-card:focus-within {{
      box-shadow: 0 18px 32px rgba(16, 42, 73, 0.10);
      border-color: rgba(13, 86, 192, 0.14);
    }}
    .directory-card-hero {{
      display: grid;
      justify-items: center;
      gap: 12px;
      width: 100%;
      color: inherit;
      text-decoration: none;
    }}
    .directory-avatar {{
      width: 112px;
      height: 112px;
      display: grid;
      place-items: center;
      border-radius: 28px;
      background: linear-gradient(180deg, rgba(15, 97, 216, 0.08) 0%, rgba(82, 167, 255, 0.16) 100%);
      overflow: hidden;
    }}
    .directory-avatar.profile-avatar {{
      width: 112px;
      height: 112px;
      border-radius: 30px;
      margin: 0;
    }}
    .directory-avatar img,
    .directory-avatar .avatar-fallback,
    .directory-avatar .directory-avatar-fallback {{
      width: 100%;
      height: 100%;
      display: grid;
      place-items: center;
      object-fit: cover;
    }}
    .directory-avatar-fallback {{
      color: #ffffff;
      font: 800 34px/1 "Trebuchet MS", Verdana, sans-serif;
    }}
    .directory-card-title {{
      color: var(--ink);
      font: 800 20px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .directory-card-subtitle {{
      margin: -6px 0 0;
      color: #607488;
      font-size: 13px;
      line-height: 1.4;
    }}
    .directory-chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: center;
    }}
    .directory-chip {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 24px;
      padding: 4px 9px;
      border-radius: 999px;
      background: #eef2f7;
      color: #6c7a89;
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .directory-chip.active {{
      background: rgba(15, 97, 216, 0.10);
      color: #0f61d8;
    }}
    .directory-chip.inactive {{
      background: rgba(239, 68, 68, 0.12);
      color: #d94a4a;
    }}
    .directory-chip.info {{
      background: rgba(77, 155, 240, 0.12);
      color: #3b82f6;
    }}
    .directory-card-meta {{
      display: grid;
      gap: 4px;
      color: #6a7b8d;
      font-size: 12px;
      line-height: 1.4;
    }}
    .directory-card-detail {{
      width: 100%;
      display: grid;
      gap: 8px;
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(244, 248, 252, 0.9);
      text-align: left;
    }}
    .client-auth-summary {{
      display: grid;
      gap: 10px;
      padding: 10px 0 2px;
    }}
    .client-auth-totals {{
      display: grid;
      gap: 8px;
    }}
    .client-auth-totals .mini-row {{
      grid-template-columns: 1fr auto;
      padding: 0 0 8px;
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .client-auth-bars {{
      display: grid;
      gap: 8px;
    }}
    .directory-card-snapshot {{
      width: 100%;
      display: grid;
      gap: 10px;
      padding: 12px 14px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(248, 251, 255, 0.96) 0%, rgba(240, 246, 255, 0.9) 100%);
      border: 1px solid rgba(23, 49, 77, 0.08);
      text-align: left;
    }}
    .provider-card {{
      min-height: 286px;
      align-content: start;
    }}
    .provider-card .directory-card-snapshot {{
      gap: 12px;
    }}
    .provider-card .directory-card-actions {{
      width: 100%;
      margin-top: auto;
    }}
    .provider-card .directory-snapshot-head {{
      align-items: start;
    }}
    .provider-card .directory-snapshot-head span {{
      flex: 1 1 auto;
      text-align: left;
      word-break: break-word;
    }}
    .provider-card:hover,
    .provider-card:focus-within {{
      transform: none;
      box-shadow: 0 16px 28px rgba(16, 42, 73, 0.08);
    }}
    .provider-summary-toggle {{
      width: 100%;
      display: grid;
      gap: 10px;
      padding: 0;
      background: transparent;
      border: 0;
      margin-top: 2px;
    }}
    .provider-summary-toggle-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 0 14px;
      border-radius: 999px;
      background: linear-gradient(180deg, #f8fbff 0%, #e3edf8 100%);
      color: #0d51b8;
      border: 1px solid rgba(13, 81, 184, 0.14);
      font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      cursor: pointer;
      user-select: none;
    }}
    .provider-summary-toggle-button.is-open {{
      background: linear-gradient(135deg, #10365d 0%, #0d51b8 100%);
      color: #ffffff;
      border-color: rgba(16, 54, 93, 0.22);
      box-shadow: 0 14px 24px rgba(16, 43, 69, 0.12);
    }}
    .provider-summary-toggle-body {{
      display: grid;
      gap: 10px;
    }}
    .provider-summary-toggle-body[hidden] {{
      display: none !important;
    }}
    .provider-summary-sections {{
      display: grid;
      gap: 10px;
    }}
    .provider-summary-block {{
      display: grid;
      gap: 8px;
      padding: 12px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.84);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .provider-summary-title {{
      color: #17314d;
      font: 800 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .provider-summary-list {{
      display: grid;
      gap: 6px;
    }}
    .provider-summary-item {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      color: #4f667c;
      font: 13px/1.45 "Trebuchet MS", Verdana, sans-serif;
    }}
    .provider-summary-item strong {{
      color: #18324c;
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}
    .provider-summary-item span {{
      flex: 1 1 auto;
      text-align: right;
      word-break: break-word;
    }}
    .provider-summary-progress {{
      display: grid;
      gap: 8px;
    }}
    .provider-summary-progress-row {{
      display: grid;
      gap: 5px;
    }}
    .provider-summary-progress-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
    }}
    .provider-summary-progress-head strong {{
      color: #18324c;
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}
    .provider-summary-progress-head span {{
      color: #4f667c;
      font: 13px/1.4 "Trebuchet MS", Verdana, sans-serif;
      text-align: right;
    }}
    .provider-summary-note {{
      color: #687b8e;
      font: 12px/1.5 "Trebuchet MS", Verdana, sans-serif;
    }}
    .directory-snapshot-row {{
      display: grid;
      gap: 6px;
    }}
    .directory-snapshot-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px 12px;
    }}
    .directory-snapshot-head strong {{
      color: #18324c;
      font: 800 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .directory-snapshot-head span {{
      color: #435a70;
      font: 13px/1.4 "Trebuchet MS", Verdana, sans-serif;
      text-align: right;
    }}
    .directory-snapshot-note {{
      color: #687b8e;
      font: 12px/1.45 "Trebuchet MS", Verdana, sans-serif;
    }}
    .directory-snapshot-progress {{
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: rgba(23, 49, 77, 0.10);
      overflow: hidden;
    }}
    .directory-snapshot-progress span {{
      display: block;
      height: 100%;
      border-radius: 999px;
    }}
    .directory-detail-toggle {{
      padding: 0;
      overflow: hidden;
    }}
    .directory-detail-summary {{
      list-style: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      cursor: pointer;
      padding: 12px 14px;
    }}
    .directory-detail-summary::-webkit-details-marker {{
      display: none;
    }}
    .directory-detail-summary-copy {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}
    .directory-detail-summary-copy small {{
      color: #6a7b8d;
      font: 12px/1.4 "Trebuchet MS", Verdana, sans-serif;
    }}
    .directory-detail-summary-hint {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 10px;
      border-radius: 999px;
      background: rgba(13, 81, 184, 0.08);
      color: #0d51b8;
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .directory-detail-summary-hint::after {{
      content: "+";
      font-size: 16px;
      line-height: 1;
    }}
    .directory-detail-toggle[open] .directory-detail-summary {{
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .directory-detail-toggle[open] .directory-detail-summary-hint {{
      background: rgba(12, 146, 73, 0.10);
      color: #0c9249;
    }}
    .directory-detail-toggle[open] .directory-detail-summary-hint::after {{
      content: "-";
    }}
    .directory-detail-body {{
      display: grid;
      gap: 8px;
      padding: 12px 14px 14px;
    }}
    .provider-note-body {{
      margin: 0;
      color: #42576c;
      font-size: 13px;
      line-height: 1.6;
      white-space: normal;
      word-break: break-word;
    }}
    .directory-detail-title {{
      color: #30485f;
      font: 800 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .directory-detail-row {{
      display: grid;
      gap: 4px;
    }}
    .directory-detail-row strong {{
      color: #18324c;
      font-size: 12px;
    }}
    .directory-detail-row span {{
      color: #5e7184;
      font-size: 12px;
      line-height: 1.45;
    }}
    .compact-note {{
      margin: 0;
    }}
    .directory-card-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: center;
      margin-top: auto;
    }}
    .client-card {{
      min-height: 220px;
    }}
    .client-card .directory-card-actions {{
      width: 100%;
      display: grid;
      grid-template-columns: 1fr;
    }}
    .client-focus-panel .small-button,
    .client-card .small-button {{
      background: rgba(255, 255, 255, 0.96);
      color: var(--blue);
      border: 1px solid rgba(13, 81, 184, 0.16);
      box-shadow: none;
    }}
    .directory-card-actions-left {{
      justify-content: flex-start;
    }}
    .directory-card-actions .table-action-form {{
      display: inline-flex;
    }}
    .directory-empty {{
      padding: 18px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.9);
    }}
    .provider-doc-card {{
      gap: 10px;
    }}
    .provider-roster {{
      display: grid;
      gap: 12px;
    }}
    .provider-roster-item {{
      padding: 0;
      overflow: hidden;
    }}
    .provider-roster-item summary {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px 12px;
      padding: 18px 20px;
      cursor: pointer;
      list-style: none;
      background: rgba(248, 251, 255, 0.86);
    }}
    .provider-roster-item summary::-webkit-details-marker {{
      display: none;
    }}
    .provider-roster-summary {{
      display: grid;
      gap: 4px;
      min-width: min(320px, 100%);
      flex: 1 1 320px;
    }}
    .provider-roster-name {{
      font: 800 16px/1.2 "Trebuchet MS", Verdana, sans-serif;
      color: var(--ink);
    }}
    .provider-roster-caption {{
      color: var(--muted);
      font-size: 13px;
    }}
    .provider-status-grid {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      margin-top: 4px;
    }}
    .provider-status-track {{
      display: grid;
      gap: 6px;
      padding: 12px 14px;
      border-radius: 18px;
      border: 1px solid rgba(23, 49, 77, 0.08);
      background: rgba(255, 255, 255, 0.84);
    }}
    .provider-status-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }}
    .provider-status-head strong {{
      font: 700 13px/1.3 "Trebuchet MS", Verdana, sans-serif;
      color: var(--ink);
    }}
    .provider-status-head span {{
      font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif;
      color: var(--blue);
      white-space: nowrap;
    }}
    .provider-status-progress {{
      width: 100%;
      height: 10px;
      margin: 0;
    }}
    .contract-track .provider-status-progress span {{
      background: linear-gradient(135deg, #0d51b8 0%, #2f8ded 100%);
    }}
    .credential-track .provider-status-progress span {{
      background: linear-gradient(135deg, #0c9249 0%, #25b35b 100%);
    }}
    .provider-status-track small {{
      color: var(--muted);
      line-height: 1.5;
    }}
    .provider-owner-flag {{
      width: fit-content;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(12, 146, 73, 0.12);
      color: #0c7b43;
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .provider-roster-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      padding: 6px 12px;
      border-radius: 999px;
      border: 1px solid rgba(23, 49, 77, 0.10);
      background: rgba(255, 255, 255, 0.94);
      color: var(--ink);
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .provider-roster-body {{
      display: grid;
      gap: 12px;
      padding: 0 20px 20px;
    }}
    .warning-card {{
      border: 1px solid rgba(217, 31, 67, 0.18);
      background: linear-gradient(135deg, rgba(255, 247, 248, 0.96) 0%, rgba(255, 253, 247, 0.96) 100%);
    }}
    .warning-card h2 {{
      color: #a21434;
    }}
    .warning-card ul {{
      margin: 0;
      padding-left: 20px;
      color: var(--ink);
    }}
    .profile-header {{
      display: grid;
      grid-template-columns: 92px 1fr;
      gap: 16px;
      align-items: center;
    }}
    .quick-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .ai-inline-actions {{
      margin-top: 12px;
    }}
    .quick-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 9px 14px;
      border-radius: 12px;
      border: 1px solid rgba(17, 42, 74, 0.08);
      background: #f6f9fd;
      font: 700 13px/1 "Inter", "Segoe UI", sans-serif;
      color: var(--blue-strong);
    }}
    .ai-result-card {{
      display: grid;
      gap: 18px;
      border: 1px solid rgba(13, 86, 192, 0.10);
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(244, 248, 255, 0.94) 100%);
    }}
    .ai-result-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }}
    .ai-result-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
    }}
    .ai-result-panel {{
      display: grid;
      gap: 10px;
      padding: 16px 18px;
      border-radius: 22px;
      border: 1px solid rgba(23, 49, 77, 0.08);
      background: rgba(248, 251, 255, 0.9);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
    }}
    .ai-result-panel h3 {{
      margin: 0;
      color: var(--ink);
      font: 800 16px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .ai-result-panel ul,
    .ai-result-panel ol {{
      margin: 0;
      padding-left: 18px;
      display: grid;
      gap: 8px;
      color: #50657b;
      font-size: 14px;
      line-height: 1.55;
    }}
    .ai-result-panel pre {{
      margin: 0;
      white-space: pre-wrap;
      color: #23384d;
      font: 500 14px/1.65 "Inter", "Segoe UI", sans-serif;
      background: transparent;
    }}
    .calendar-shell {{
      overflow: hidden;
      border-radius: 18px;
      border: 1px solid rgba(23, 49, 77, 0.10);
      background: #fbfdff;
    }}
    .calendar-head {{
      padding: 14px 16px;
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
      font: 700 15px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--ink);
      background: rgba(23, 49, 77, 0.03);
    }}
    .calendar-table {{
      min-width: 100%;
    }}
    .calendar-table td {{
      vertical-align: top;
      min-width: 120px;
      height: 120px;
    }}
    .calendar-empty {{
      background: rgba(23, 49, 77, 0.03);
    }}
    .calendar-day {{
      background: rgba(248, 251, 255, 0.78);
    }}
    .calendar-day strong {{
      display: block;
      margin-bottom: 8px;
      font: 700 16px/1 "Trebuchet MS", Verdana, sans-serif;
      color: var(--ink);
    }}
    .calendar-event {{
      margin-top: 8px;
      padding: 8px 10px;
      border-radius: 12px;
      background: rgba(13, 81, 184, 0.08);
      color: var(--ink);
      font: 700 12px/1.3 "Trebuchet MS", Verdana, sans-serif;
    }}
    .calendar-more {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .agenda-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: 1.15fr 0.85fr;
    }}
    .tool-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 240px));
      justify-content: start;
    }}
    .tool-grid.hub-grid {{
      grid-template-columns: repeat(auto-fit, minmax(148px, 168px));
    }}
    .tool-tile {{
      display: grid;
      gap: 12px;
      padding: 20px;
      border-radius: 18px;
      border: 1px solid rgba(17, 42, 74, 0.07);
      background: #ffffff;
      box-shadow: 0 12px 26px rgba(16, 42, 73, 0.06);
      transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease, background 160ms ease;
      min-height: 184px;
      align-content: start;
    }}
    .tool-grid.hub-grid .tool-tile {{
      gap: 10px;
      min-height: 148px;
      padding: 16px;
      border-radius: 16px;
    }}
    .tool-grid.hub-grid .tool-icon {{
      width: 46px;
      height: 46px;
      border-radius: 15px;
      font-size: 13px;
    }}
    .tool-grid.hub-grid .tool-tile strong {{
      font-size: 15px;
    }}
    .tool-grid.hub-grid .tool-tile p {{
      font-size: 12px;
      line-height: 1.45;
    }}
    .tool-grid.hub-grid .tool-tile span {{
      font-size: 12px;
      line-height: 1.35;
    }}
    .tool-tile:hover {{
      transform: translateY(-2px);
      background: linear-gradient(180deg, #ffffff 0%, #f2f7ff 100%);
      box-shadow: 0 18px 30px rgba(16, 42, 73, 0.10);
      border-color: rgba(13, 86, 192, 0.14);
    }}
    .tool-icon {{
      width: 54px;
      height: 54px;
      border-radius: 16px;
      display: grid;
      place-items: center;
      font: 700 15px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.08em;
      color: var(--blue);
      background: linear-gradient(180deg, #eef5ff 0%, #dfeeff 100%);
      border: 1px solid rgba(13, 86, 192, 0.08);
    }}
    .tool-tile:hover .tool-icon {{
      color: #ffffff;
      background: linear-gradient(135deg, #0d56c0 0%, #0a49a8 100%);
      border-color: rgba(13, 86, 192, 0.18);
    }}
    .tool-tile strong {{
      font-size: 16px;
      line-height: 1.2;
      color: var(--ink);
    }}
    .tool-tile p,
    .tool-tile span {{
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }}
    .page-intro {{
      display: grid;
      gap: 14px;
      grid-template-columns: minmax(0, 1fr);
      align-items: start;
    }}
    .page-title-card {{
      background: transparent;
      min-height: auto;
      border: none;
      box-shadow: none;
      padding: 0;
    }}
    .page-title-stack {{
      display: grid;
      gap: 12px;
    }}
    .page-title-stack h2 {{
      margin: 0;
      font-size: clamp(28px, 3.8vw, 40px);
      line-height: 1.04;
      letter-spacing: -0.035em;
      color: var(--ink);
    }}
    .page-title-stack p {{
      margin: 0;
      color: #51667b;
      line-height: 1.65;
      max-width: 68ch;
    }}
    .page-title-card-inline {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      flex-wrap: wrap;
    }}
    .page-command-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
    }}
    .page-primary-button,
    .page-secondary-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 44px;
      padding: 0 18px;
      border-radius: 12px;
      text-decoration: none;
      font: 700 14px/1 "Inter", "Segoe UI", sans-serif;
      transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease, color 160ms ease, border-color 160ms ease;
    }}
    .page-primary-button {{
      background: linear-gradient(135deg, #0d56c0 0%, #0a49a8 100%);
      color: #ffffff;
      box-shadow: 0 12px 22px rgba(13, 86, 192, 0.18);
    }}
    .page-primary-button:hover {{
      transform: translateY(-1px);
      box-shadow: 0 16px 26px rgba(13, 86, 192, 0.22);
    }}
    .page-secondary-button {{
      border: 1px solid rgba(17, 42, 74, 0.10);
      background: #ffffff;
      color: var(--ink);
      box-shadow: 0 10px 18px rgba(16, 42, 73, 0.05);
    }}
    .page-secondary-button:hover {{
      transform: translateY(-1px);
      background: #f4f8ff;
      border-color: rgba(13, 86, 192, 0.14);
      box-shadow: 0 14px 22px rgba(16, 42, 73, 0.08);
    }}
    .page-action-stack {{
      display: grid;
      gap: 10px;
      min-height: auto;
      align-content: start;
      background: transparent;
      border: none;
      box-shadow: none;
      padding: 0;
    }}
    .page-actions {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .page-action-card {{
      display: grid;
      gap: 8px;
      min-height: 116px;
      padding: 18px;
      border-radius: 18px;
      border: 1px solid rgba(17, 42, 74, 0.07);
      background: #ffffff;
      box-shadow: 0 10px 22px rgba(16, 42, 73, 0.06);
      text-decoration: none;
      transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease, background 160ms ease;
    }}
    .page-action-card:hover {{
      transform: translateY(-2px);
      background: linear-gradient(180deg, #ffffff 0%, #f2f7ff 100%);
      box-shadow: 0 16px 28px rgba(16, 42, 73, 0.10);
      border-color: rgba(13, 86, 192, 0.14);
    }}
    .page-action-card strong {{
      color: var(--ink);
      font: 800 16px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .page-action-card p {{
      margin: 0;
      color: #5a6f83;
      font-size: 13px;
      line-height: 1.5;
    }}
    .page-action-card span {{
      color: var(--blue);
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.03em;
    }}
    .metric-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      justify-content: stretch;
    }}
    .dashboard-hero-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.85fr);
      align-items: stretch;
    }}
    .dashboard-hero-card {{
      display: grid;
      gap: 14px;
      padding: 28px;
      background:
        radial-gradient(circle at top right, rgba(108, 188, 255, 0.16), transparent 36%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(243, 248, 255, 0.98) 100%);
      border: 1px solid rgba(17, 42, 74, 0.08);
      box-shadow: 0 18px 36px rgba(16, 42, 73, 0.08);
    }}
    .dashboard-hero-card h2 {{
      margin: 0;
      color: var(--ink);
      font: 800 clamp(34px, 4vw, 54px)/1.02 "Inter", "Segoe UI", sans-serif;
      letter-spacing: -0.05em;
    }}
    .dashboard-hero-card p {{
      margin: 0;
      color: #51667b;
      font: 15px/1.8 "Inter", "Segoe UI", sans-serif;
      max-width: 62ch;
    }}
    .dashboard-quick-card {{
      display: grid;
      gap: 16px;
      align-content: start;
      padding: 24px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.98) 0%, rgba(245, 250, 255, 0.98) 100%);
      border: 1px solid rgba(17, 42, 74, 0.08);
      box-shadow: 0 18px 34px rgba(16, 42, 73, 0.07);
    }}
    .dashboard-quick-card > p {{
      margin: -6px 0 0;
      color: #61758a;
      font: 14px/1.7 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-shortcuts {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .dashboard-shortcut-card {{
      display: grid;
      gap: 8px;
      min-height: 124px;
      padding: 16px;
      border-radius: 20px;
      border: 1px solid rgba(13, 86, 192, 0.10);
      background: linear-gradient(180deg, #ffffff 0%, #f5f9ff 100%);
      text-decoration: none;
      box-shadow: 0 12px 24px rgba(16, 42, 73, 0.05);
      transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease, background 160ms ease;
    }}
    .dashboard-shortcut-card:hover {{
      transform: translateY(-2px);
      background: linear-gradient(180deg, #ffffff 0%, #eef5ff 100%);
      border-color: rgba(13, 86, 192, 0.16);
      box-shadow: 0 18px 30px rgba(16, 42, 73, 0.09);
    }}
    .dashboard-shortcut-card strong {{
      color: var(--ink);
      font: 800 16px/1.2 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-shortcut-card p {{
      margin: 0;
      color: #61758a;
      font: 13px/1.55 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-shortcut-card span {{
      color: var(--blue);
      font: 700 12px/1.2 "Inter", "Segoe UI", sans-serif;
    }}
    .metric-card {{
      position: relative;
      display: grid;
      grid-template-columns: 48px minmax(0, 1fr);
      grid-template-areas:
        "icon title"
        "value value"
        "copy copy"
        "note note";
      gap: 10px;
      min-height: 142px;
      padding: 18px 18px 16px;
      border-radius: 18px;
      border: 1px solid rgba(17, 42, 74, 0.07);
      background: #ffffff;
      box-shadow: 0 10px 24px rgba(16, 42, 73, 0.06);
      text-decoration: none;
      align-content: start;
      transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease, background 160ms ease;
    }}
    .metric-card::before {{
      content: "";
      grid-area: icon;
      width: 42px;
      height: 42px;
      border-radius: 999px;
      background: linear-gradient(135deg, rgba(15, 97, 216, 0.12) 0%, rgba(82, 167, 255, 0.18) 100%);
      box-shadow: inset 0 0 0 1px rgba(15, 97, 216, 0.08);
    }}
    .metric-card:hover {{
      transform: translateY(-2px);
      background: linear-gradient(180deg, #ffffff 0%, #f2f7ff 100%);
      box-shadow: 0 18px 30px rgba(16, 42, 73, 0.10);
      border-color: rgba(13, 86, 192, 0.14);
    }}
    .metric-card > span:first-of-type {{
      grid-area: title;
      display: block;
      align-self: start;
      min-width: 0;
      max-width: 100%;
      color: #6d8093;
      font: 800 10.5px/1.35 "Inter", "Segoe UI", sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .metric-card strong {{
      grid-area: value;
      color: var(--ink);
      font-size: 36px;
      line-height: 1.02;
    }}
    .metric-card p {{
      grid-area: copy;
      margin: 0;
      color: #5d7185;
      font-size: 13px;
      line-height: 1.55;
    }}
    .metric-card-note {{
      display: block;
      grid-area: note;
      max-width: 100%;
      color: var(--blue);
      font: 700 12px/1.35 "Inter", "Segoe UI", sans-serif;
      letter-spacing: 0;
      text-transform: none;
      white-space: normal;
      overflow-wrap: anywhere;
    }}
    .dashboard-module-grid,
    .dashboard-analytics-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .dashboard-focus-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: minmax(0, 1.12fr) minmax(0, 0.88fr);
    }}
    .dashboard-module-card {{
      display: grid;
      gap: 18px;
      min-height: 286px;
    }}
    .dashboard-module-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .dashboard-module-head h3 {{
      margin: 0;
      color: var(--ink);
      font: 800 18px/1.15 "Inter", "Segoe UI", sans-serif;
      letter-spacing: -0.02em;
    }}
    .dashboard-card-action {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 0 12px;
      border-radius: 10px;
      border: 1px solid rgba(17, 42, 74, 0.10);
      background: #f7faff;
      color: var(--muted);
      font: 700 12px/1 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-module-body {{
      display: grid;
      gap: 12px;
      min-height: 0;
      align-content: start;
    }}
    .dashboard-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(17, 42, 74, 0.06);
      background: #f9fbfe;
    }}
    .dashboard-row-copy {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}
    .dashboard-row-copy strong {{
      color: var(--ink);
      font: 700 14px/1.25 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-row-copy small {{
      color: #6b7f94;
      font: 13px/1.45 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-row-meta {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 72px;
      min-height: 30px;
      padding: 0 10px;
      border-radius: 999px;
      background: rgba(15, 97, 216, 0.10);
      color: var(--blue);
      font: 700 12px/1 "Inter", "Segoe UI", sans-serif;
      white-space: nowrap;
    }}
    .dashboard-row-meta-warm {{
      background: rgba(243, 181, 74, 0.18);
      color: #aa6a00;
    }}
    .dashboard-empty-state {{
      display: grid;
      place-items: center;
      min-height: 174px;
      padding: 20px;
      border-radius: 18px;
      border: 1px dashed rgba(17, 42, 74, 0.14);
      background: #f9fbfe;
      color: #698096;
      text-align: center;
      font: 14px/1.6 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-chart-shell {{
      display: grid;
      align-items: end;
      min-height: 200px;
      padding: 18px 18px 8px;
      border-radius: 20px;
      background: #f7faff;
      border: 1px solid rgba(17, 42, 74, 0.06);
    }}
    .dashboard-bars {{
      display: grid;
      grid-template-columns: repeat(10, minmax(0, 1fr));
      gap: 14px;
      align-items: end;
      min-height: 170px;
    }}
    .dashboard-bar-item {{
      display: grid;
      gap: 8px;
      align-items: end;
      justify-items: center;
      height: 100%;
    }}
    .dashboard-bar {{
      width: 100%;
      min-height: 10px;
      border-radius: 12px 12px 4px 4px;
      background: linear-gradient(180deg, #2f80ed 0%, #1f6fdd 100%);
      box-shadow: 0 10px 18px rgba(47, 128, 237, 0.18);
    }}
    .dashboard-bar-item span {{
      color: #6d8093;
      font: 700 11px/1.2 "Inter", "Segoe UI", sans-serif;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }}
    .dashboard-donut-layout {{
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr);
      gap: 18px;
      align-items: center;
      min-height: 220px;
      padding: 10px 4px;
    }}
    .dashboard-donut {{
      position: relative;
      width: 180px;
      height: 180px;
      margin: 0 auto;
      border-radius: 999px;
      box-shadow: 0 18px 32px rgba(16, 42, 73, 0.10);
    }}
    .dashboard-donut-core {{
      position: absolute;
      inset: 32px;
      border-radius: 999px;
      background: #ffffff;
      box-shadow: inset 0 0 0 1px rgba(17, 42, 74, 0.06);
    }}
    .dashboard-legend {{
      display: grid;
      gap: 10px;
    }}
    .dashboard-legend-row {{
      display: grid;
      grid-template-columns: 12px auto 1fr;
      gap: 10px;
      align-items: center;
      padding: 12px 14px;
      border-radius: 14px;
      background: #f9fbfe;
      border: 1px solid rgba(17, 42, 74, 0.06);
    }}
    .dashboard-legend-row strong {{
      color: var(--ink);
      font: 800 16px/1 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-legend-row small {{
      color: #6b7f94;
      font: 13px/1.3 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-legend-dot {{
      width: 12px;
      height: 12px;
      border-radius: 999px;
      display: inline-block;
    }}
    .dashboard-legend-dot.approved {{ background: #59c38a; }}
    .dashboard-legend-dot.pending {{ background: #f7a84d; }}
    .dashboard-legend-dot.denied {{ background: #e76a5e; }}
    .dashboard-performance-card {{
      display: grid;
      gap: 18px;
      min-height: 286px;
      padding: 24px;
      color: #ffffff;
      background: linear-gradient(135deg, #0e3a5d 0%, #16678e 48%, #1e88e5 100%);
      border: none;
      box-shadow: 0 22px 40px rgba(14, 58, 93, 0.22);
    }}
    .dashboard-performance-kicker {{
      color: rgba(209, 240, 255, 0.92);
      font: 800 11px/1 "Inter", "Segoe UI", sans-serif;
      letter-spacing: 0.20em;
      text-transform: uppercase;
    }}
    .dashboard-performance-card h3 {{
      margin: 0;
      color: #ffffff;
      font: 800 28px/1.08 "Inter", "Segoe UI", sans-serif;
      letter-spacing: -0.04em;
    }}
    .dashboard-performance-card p {{
      margin: 0;
      color: rgba(239, 247, 255, 0.92);
      font: 14px/1.7 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-performance-stats {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .dashboard-performance-stat {{
      display: grid;
      gap: 8px;
      padding: 14px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.14);
      border: 1px solid rgba(255, 255, 255, 0.16);
      backdrop-filter: blur(8px);
    }}
    .dashboard-performance-stat small {{
      color: rgba(220, 242, 255, 0.92);
      font: 700 12px/1.3 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-performance-stat strong {{
      color: #ffffff;
      font: 800 30px/1 "Inter", "Segoe UI", sans-serif;
    }}
    .dashboard-performance-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 44px;
      width: fit-content;
      padding: 0 18px;
      border-radius: 16px;
      background: #ffffff;
      color: #0e3a5d;
      text-decoration: none;
      font: 800 14px/1 "Inter", "Segoe UI", sans-serif;
      box-shadow: 0 14px 24px rgba(5, 22, 41, 0.18);
    }}
    .dashboard-performance-button:hover {{
      background: #eef6ff;
    }}
    .metric-success strong {{
      color: #0c7b43;
    }}
    .metric-success::before {{
      background: linear-gradient(135deg, rgba(24, 155, 103, 0.12) 0%, rgba(71, 196, 144, 0.18) 100%);
      box-shadow: inset 0 0 0 1px rgba(24, 155, 103, 0.08);
    }}
    .metric-warm strong {{
      color: #b06c00;
    }}
    .metric-warm::before {{
      background: linear-gradient(135deg, rgba(227, 167, 66, 0.16) 0%, rgba(255, 214, 124, 0.22) 100%);
      box-shadow: inset 0 0 0 1px rgba(227, 167, 66, 0.10);
    }}
    .metric-danger strong {{
      color: #a21434;
    }}
    .metric-danger::before {{
      background: linear-gradient(135deg, rgba(207, 91, 110, 0.14) 0%, rgba(255, 160, 175, 0.20) 100%);
      box-shadow: inset 0 0 0 1px rgba(207, 91, 110, 0.08);
    }}
    .queue-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: 1.1fr 0.9fr;
    }}
    .table-profile-cell {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .table-avatar {{
      width: 42px;
      height: 42px;
      border-radius: 14px;
      overflow: hidden;
      flex-shrink: 0;
    }}
    .table-profile-copy {{
      display: grid;
      gap: 4px;
      min-width: 0;
    }}
    .table-profile-copy small {{
      color: #72859a;
      font-size: 12px;
      line-height: 1.35;
    }}
    .table-status-badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 28px;
      padding: 0 12px;
      border-radius: 999px;
      font: 700 12px/1 "Inter", "Segoe UI", sans-serif;
    }}
    .table-status-badge.success {{
      background: rgba(89, 195, 138, 0.18);
      color: #107c4e;
    }}
    .table-status-badge.warn {{
      background: rgba(247, 168, 77, 0.18);
      color: #9f6202;
    }}
    .table-status-badge.danger {{
      background: rgba(231, 106, 94, 0.18);
      color: #b53b33;
    }}
    .table-action-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .queue-card {{
      display: grid;
      gap: 12px;
    }}
    .queue-list {{
      display: grid;
      gap: 12px;
    }}
    .queue-row {{
      display: grid;
      gap: 6px;
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
      text-decoration: none;
      transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease, background 160ms ease;
    }}
    .queue-row:hover {{
      transform: translateY(-1px);
      background: rgba(242, 248, 255, 0.98);
      border-color: rgba(13, 81, 184, 0.14);
      box-shadow: 0 14px 24px rgba(16, 43, 69, 0.07);
    }}
    .queue-row-head {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
    }}
    .queue-row-head strong {{
      color: var(--ink);
      font: 800 14px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .queue-row-head span {{
      color: var(--blue);
      font: 800 15px/1.1 "Trebuchet MS", Verdana, sans-serif;
    }}
    .queue-row p {{
      margin: 0;
      color: #5d7185;
      font-size: 13px;
      line-height: 1.5;
    }}
    .ops-dashboard-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .ops-dashboard-card {{
      display: grid;
      gap: 10px;
      padding: 18px;
      border-radius: 22px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .ops-dashboard-card strong {{
      color: var(--ink);
      font-size: 18px;
      line-height: 1.15;
    }}
    .ops-dashboard-card p,
    .ops-dashboard-card small {{
      margin: 0;
      color: #5d7185;
      line-height: 1.55;
    }}
    .session-progress-track {{
      width: 100%;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(23, 49, 77, 0.08);
    }}
    .session-progress-fill {{
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(135deg, #1558b6 0%, #0f8c4d 100%);
    }}
    .session-timeline-list {{
      display: grid;
      gap: 12px;
    }}
    .session-timeline-item {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .session-timeline-item strong {{
      color: var(--ink);
      font: 800 13px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .session-timeline-item span,
    .session-timeline-item small {{
      color: #5d7185;
      line-height: 1.5;
    }}
    .provider-profile-shell {{
      display: grid;
      gap: 16px;
      grid-template-columns: minmax(0, 1.22fr) minmax(280px, 0.78fr);
      align-items: start;
    }}
    .provider-profile-card,
    .provider-profile-side {{
      display: grid;
      gap: 16px;
    }}
    .provider-profile-copy {{
      display: grid;
      gap: 10px;
    }}
    .provider-profile-copy p {{
      margin: 0;
      color: #51667b;
      line-height: 1.55;
    }}
    .provider-profile-sections {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .provider-profile-section {{
      display: grid;
      gap: 14px;
      padding: 18px;
      border-radius: 22px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .provider-profile-section-full {{
      grid-column: 1 / -1;
    }}
    .provider-profile-section-head {{
      display: grid;
      gap: 4px;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .provider-profile-section-head h3 {{
      margin: 0;
      color: var(--ink);
      font-size: 20px;
      line-height: 1.15;
    }}
    .provider-profile-section-head p {{
      margin: 0;
      color: #62768a;
      line-height: 1.55;
    }}
    .profile-pill-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .profile-pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 28px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(13, 81, 184, 0.08);
      color: var(--blue);
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .profile-pill.neutral {{
      background: #eef2f7;
      color: #5b6d7f;
    }}
    .profile-pill.success {{
      background: rgba(12, 146, 73, 0.12);
      color: #0c7b43;
    }}
    .profile-pill.warn {{
      background: rgba(245, 158, 11, 0.14);
      color: #8b5a00;
    }}
    .profile-pill.danger {{
      background: rgba(217, 31, 67, 0.12);
      color: #a21434;
    }}
    .provider-stat-grid {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .provider-stat-box {{
      display: grid;
      gap: 6px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .provider-stat-box span {{
      color: #6a7b8d;
      font: 700 11px/1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.10em;
      text-transform: uppercase;
    }}
    .provider-stat-box strong {{
      color: var(--ink);
      font-size: 24px;
      line-height: 1.05;
    }}
    .provider-stat-box small {{
      color: #5d7185;
      line-height: 1.45;
    }}
    .provider-workflow-card {{
      display: grid;
      gap: 14px;
      padding: 18px;
      border-radius: 22px;
      background: linear-gradient(180deg, rgba(247, 250, 255, 0.98) 0%, rgba(235, 243, 255, 0.92) 100%);
      border: 1px solid rgba(13, 81, 184, 0.10);
    }}
    .provider-workflow-head {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      justify-content: space-between;
      align-items: flex-start;
    }}
    .provider-workflow-copy {{
      display: grid;
      gap: 6px;
      max-width: 620px;
    }}
    .provider-workflow-copy h3 {{
      margin: 0;
      color: var(--ink);
      font-size: 24px;
      line-height: 1.1;
    }}
    .provider-workflow-copy p {{
      margin: 0;
      color: #51667b;
      line-height: 1.55;
    }}
    .provider-workflow-score {{
      min-width: 140px;
      display: grid;
      gap: 4px;
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.86);
      border: 1px solid rgba(23, 49, 77, 0.08);
      text-align: right;
    }}
    .provider-workflow-score strong {{
      color: var(--blue);
      font-size: 30px;
      line-height: 1;
    }}
    .provider-workflow-score span {{
      color: var(--ink);
      font: 800 13px/1.1 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .provider-workflow-score small {{
      color: #607489;
      line-height: 1.45;
    }}
    .provider-workflow-progress {{
      margin-top: -2px;
    }}
    .provider-workflow-meta {{
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .provider-workflow-grid {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .workflow-step-card {{
      display: grid;
      gap: 10px;
      padding: 14px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.86);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .workflow-step-card.workflow-step-blocked {{
      border-color: rgba(217, 31, 67, 0.18);
      background: rgba(255, 247, 249, 0.94);
    }}
    .workflow-step-card.workflow-step-complete {{
      border-color: rgba(12, 146, 73, 0.18);
      background: rgba(244, 252, 247, 0.96);
    }}
    .workflow-step-card.workflow-step-in_progress {{
      border-color: rgba(245, 158, 11, 0.18);
      background: rgba(255, 250, 240, 0.96);
    }}
    .workflow-step-card.workflow-step-upcoming,
    .workflow-step-card.workflow-step-na {{
      background: rgba(248, 251, 255, 0.94);
    }}
    .workflow-step-head {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: space-between;
      align-items: center;
    }}
    .workflow-step-head strong {{
      color: var(--ink);
      font: 800 15px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .workflow-step-card p {{
      margin: 0;
      color: #51667b;
      line-height: 1.55;
    }}
    .workflow-step-card small {{
      color: #607489;
      line-height: 1.45;
    }}
    .workflow-step-action {{
      padding-top: 6px;
      border-top: 1px solid rgba(23, 49, 77, 0.08);
      color: var(--ink);
      font: 700 12px/1.5 "Trebuchet MS", Verdana, sans-serif;
      letter-spacing: 0.02em;
    }}
    .provider-clients {{
      display: grid;
      gap: 10px;
    }}
    .provider-profile-top-actions {{
      padding-top: 4px;
    }}
    .provider-activity-card {{
      display: grid;
      gap: 10px;
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .provider-activity-row {{
      display: grid;
      gap: 4px;
      padding-bottom: 10px;
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .provider-activity-row:last-child {{
      padding-bottom: 0;
      border-bottom: 0;
    }}
    .provider-activity-row strong {{
      color: var(--ink);
      font: 800 13px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .provider-activity-row span {{
      color: var(--blue);
      font: 700 12px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .provider-activity-row small {{
      color: #607489;
      line-height: 1.45;
    }}
    .hr-flow-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }}
    .hr-flow-card {{
      display: grid;
      gap: 14px;
      align-content: start;
    }}
    .hr-flow-head {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
    }}
    .hr-flow-head h3 {{
      margin: 4px 0 0;
      font-size: 1.08rem;
    }}
    .hr-flow-head p {{
      margin: 6px 0 0;
      color: #607489;
      font-size: 0.9rem;
    }}
    .hr-progress-row {{
      display: grid;
      gap: 8px;
      align-items: center;
    }}
    .hr-progress-row span {{
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #607489;
    }}
    .hr-progress-row small {{
      color: #607489;
      font-size: 0.82rem;
    }}
    .hr-flow-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .attention-list {{
      display: grid;
      gap: 10px;
    }}
    .attention-item {{
      display: grid;
      gap: 4px;
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .attention-item strong {{
      color: var(--ink);
      font: 800 14px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .attention-item span {{
      color: var(--blue);
      font: 800 15px/1.1 "Trebuchet MS", Verdana, sans-serif;
    }}
    .attention-item small {{
      color: #5d7185;
      line-height: 1.5;
    }}
    .profile-shell {{
      display: grid;
      gap: 18px;
      grid-template-columns: 1.1fr 0.9fr;
      align-items: start;
    }}
    .segmented-tabs {{
      display: inline-flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .segment {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 0;
      min-height: 42px;
      padding: 8px 20px;
      border-radius: 999px;
      border: 1px solid rgba(13, 81, 184, 0.14);
      background: linear-gradient(135deg, rgba(248, 251, 255, 0.98) 0%, rgba(234, 242, 255, 0.92) 100%);
      color: var(--blue);
      font: 700 12px/1 "Inter", "Trebuchet MS", Verdana, sans-serif;
      cursor: pointer;
      transition: background 140ms ease, border-color 140ms ease, color 140ms ease;
    }}
    .segment:hover {{
      border-color: rgba(13, 81, 184, 0.20);
      background: linear-gradient(135deg, rgba(240, 247, 255, 0.98) 0%, rgba(226, 239, 255, 0.94) 100%);
      color: var(--blue);
    }}
    .segment.active,
    .segment[aria-pressed="true"] {{
      background: linear-gradient(135deg, rgba(13, 81, 184, 0.12) 0%, rgba(12, 146, 73, 0.12) 100%);
      color: var(--blue);
      border-color: rgba(13, 81, 184, 0.18);
    }}
    .tab-panels {{
      display: grid;
      gap: 18px;
    }}
    .tab-panel[hidden] {{
      display: none;
    }}
    .case-hub-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .case-hub-panels {{
      margin-top: 6px;
    }}
    .case-hub-panel {{
      display: grid;
      gap: 16px;
    }}
    .case-summary-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .case-summary-card {{
      display: grid;
      gap: 10px;
      padding: 18px;
      border-radius: 22px;
      background: rgba(248, 251, 255, 0.92);
      border: 1px solid rgba(23, 49, 77, 0.08);
      box-shadow: 0 12px 24px rgba(15, 23, 42, 0.04);
    }}
    .case-summary-card h3 {{
      margin: 0;
      color: var(--ink);
      font: 800 17px/1.2 "Trebuchet MS", Verdana, sans-serif;
    }}
    .case-hub-split {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }}
    .case-hub-actions {{
      margin: 6px 0 2px;
    }}
    .session-workspace-shell {{
      display: grid;
      gap: 16px;
    }}
    .provider-workflow-grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    }}
    .field[data-workforce-group="OFFICE"][hidden],
    .field[data-workforce-group="PROVIDER"][hidden] {{
      display: none;
    }}
    .document-hub {{
      display: grid;
      gap: 14px;
    }}
    .document-summary {{
      display: grid;
      gap: 10px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.84);
      border: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .stack-grid {{
      display: grid;
      gap: 16px;
    }}
    .helper-note {{
      font-size: 13px;
      color: var(--muted);
      line-height: 1.55;
      margin: 0;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      padding: 7px 11px;
      border-radius: 999px;
      font: 700 12px/1 "Trebuchet MS", Verdana, sans-serif;
    }}
    .success-pill {{
      color: #0b5a47;
      background: rgba(12, 146, 73, 0.14);
      border: 1px solid rgba(12, 146, 73, 0.28);
    }}
    .delivered-pill {{
      color: #0b5a47;
      background: rgba(12, 146, 73, 0.14);
      border: 1px solid rgba(12, 146, 73, 0.28);
    }}
    .pending-pill {{
      color: #195fb4;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.26);
    }}
    .ignored-pill {{
      color: #556172;
      background: rgba(148, 163, 184, 0.18);
      border: 1px solid rgba(148, 163, 184, 0.30);
    }}
    .expired-pill {{
      color: #a21434;
      background: rgba(217, 31, 67, 0.14);
      border: 1px solid rgba(217, 31, 67, 0.28);
    }}
    .pending-approval-pill {{
      color: #8b5a00;
      background: rgba(245, 158, 11, 0.16);
      border: 1px solid rgba(245, 158, 11, 0.28);
    }}
    .success-panel .result-kicker {{ color: var(--success); }}
    .error-panel .result-kicker {{ color: var(--danger); }}
    pre {{
      margin: 0;
      padding: 14px;
      border-radius: 18px;
      background: #fbfdff;
      border: 1px solid rgba(23, 49, 77, 0.10);
      white-space: pre-wrap;
      word-break: break-word;
      font: 14px/1.5 Consolas, "Courier New", monospace;
      color: #1b2330;
    }}
    .result-copy {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(248, 251, 255, 0.82);
      border: 1px solid rgba(23, 49, 77, 0.08);
      color: var(--ink);
      font-size: 15px;
      line-height: 1.7;
    }}
    .mini-table {{
      display: grid;
      gap: 10px;
    }}
    .mini-row {{
      display: grid;
      grid-template-columns: 150px 1fr;
      gap: 12px;
      padding: 12px 0;
      border-bottom: 1px solid rgba(23, 49, 77, 0.08);
    }}
    .mini-row:last-child {{
      border-bottom: 0;
      padding-bottom: 0;
    }}
    .mini-row strong {{
      font: 700 13px/1.2 "Trebuchet MS", Verdana, sans-serif;
      text-transform: uppercase;
      color: var(--ink);
    }}
    .footer-note {{
      text-align: center;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
      margin: 0;
    }}
    @media (max-width: 1180px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .sidebar {{ position: relative; height: auto; overflow: visible; }}
      .sidebar-inner {{ height: auto; overflow: visible; padding-right: 0; }}
      .directory-toolbar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .directory-grid {{
        grid-template-columns: repeat(auto-fit, minmax(190px, 220px));
        justify-content: center;
      }}
    }}
    @media (max-width: 920px) {{
      .topbar,
      .page-intro,
      .dashboard-hero-grid,
      .dashboard-focus-grid,
      .dashboard-module-grid,
      .dashboard-analytics-grid,
      .dashboard-donut-layout,
      .queue-grid,
      .provider-profile-shell,
      .profile-shell,
      .hero,
      .stats-grid,
      .claim-preview-grid,
      .status-grid,
      .office-grid,
      .dual-grid,
      .roadmap,
      .agenda-grid {{ grid-template-columns: 1fr; }}
      .topbar-actions {{
        justify-content: flex-start;
      }}
      .topbar-profile {{
        min-width: 0;
      }}
      .tool-grid {{
        grid-template-columns: repeat(auto-fit, minmax(180px, 220px));
        justify-content: center;
      }}
      .tool-grid.hub-grid {{
        grid-template-columns: repeat(auto-fit, minmax(150px, 190px));
        justify-content: center;
      }}
      .page-actions,
      .dashboard-shortcuts,
      .dashboard-performance-stats,
      .provider-stat-grid,
      .provider-workflow-meta,
      .provider-workflow-grid {{
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      }}
      .provider-profile-sections {{
        grid-template-columns: 1fr;
      }}
      .page-command-bar {{
        width: 100%;
        justify-content: flex-start;
      }}
      .metric-grid {{
        grid-template-columns: repeat(auto-fit, minmax(160px, 190px));
        justify-content: center;
      }}
      .field-grid,
      .field-grid.compact,
      .service-row {{ grid-template-columns: 1fr; }}
      .directory-toolbar,
      .directory-grid {{
        grid-template-columns: repeat(auto-fit, minmax(180px, 220px));
        justify-content: center;
      }}
      .dashboard-bars {{
        grid-template-columns: repeat(5, minmax(0, 1fr));
      }}
      .time-wheel {{ grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) minmax(84px, 0.7fr); }}
    }}
    @media (max-width: 640px) {{
      .content,
      .sidebar {{ padding: 16px; }}
      .panel {{ padding: 18px; border-radius: 20px; }}
      .hero h2,
      .module-head h2,
      .section-card h2,
      .result-panel h2 {{ font-size: 24px; }}
      .provider-workflow-head {{
        flex-direction: column;
      }}
      .provider-workflow-score {{
        width: 100%;
        text-align: left;
      }}
      .topbar {{
        gap: 14px;
      }}
      .topbar-actions {{
        width: 100%;
      }}
      .topbar-button,
      .topbar-counter,
      .topbar-profile {{
        width: 100%;
        justify-content: space-between;
      }}
      .mini-row {{ grid-template-columns: 1fr; gap: 6px; }}
      .directory-card {{ min-height: auto; }}
      .tool-grid,
      .tool-grid.hub-grid {{
        grid-template-columns: repeat(auto-fit, minmax(150px, 180px));
        justify-content: center;
      }}
      .hero-badges {{
        grid-template-columns: repeat(auto-fit, minmax(140px, 170px));
        justify-content: center;
      }}
      .page-actions,
      .dashboard-shortcuts,
      .dashboard-performance-stats,
      .provider-stat-grid,
      .metric-grid {{
        grid-template-columns: 1fr;
      }}
      .page-command-bar {{
        width: 100%;
        justify-content: stretch;
      }}
      .page-primary-button,
      .page-secondary-button {{
        width: 100%;
      }}
      .page-title-card-inline {{
        align-items: start;
      }}
      .dashboard-bars {{
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }}
      .dashboard-donut {{
        width: 150px;
        height: 150px;
      }}
      .dashboard-donut-core {{
        inset: 28px;
      }}
      .directory-grid {{
        grid-template-columns: repeat(auto-fit, minmax(150px, 190px));
        justify-content: center;
      }}
      .time-wheel {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .time-wheel-separator {{ display: none; }}
      .signature-pad-canvas {{ height: 150px; }}
    }}
  </style>
</head>
<body data-page="{html.escape(current_page)}">
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-inner">
        <section class="brand-card{' wordmark-card' if brand_logo_is_wordmark else ''}">
          <div class="brand-logo">{logo_markup}</div>
          {'' if brand_logo_is_wordmark else f'''
          <div class="brand-meta">
            <h1 class="brand-title">{html.escape(BRAND_NAME)}</h1>
            <p class="brand-copy">{html.escape(portal_label)}</p>
          </div>
          '''}
        </section>

        <nav class="nav-group">
          {sidebar_nav_markup}
        </nav>

        <div class="sidebar-footer">
          <section class="sidebar-panel sidebar-panel-compact sidebar-profile-panel">
            <div class="sidebar-user">
              <div class="avatar-shell">{logo_markup}</div>
              <div>
                <span class="profile-label">Perfil activo</span>
                <strong class="profile-name">{html.escape(current_user_name or 'Sin sesion')}</strong>
                <div class="profile-copy">{html.escape(str(security_profile.get('job_title', '')) or current_user_role_label)}</div>
              </div>
            </div>
            <div class="sidebar-stat-grid">
              <span class="sidebar-stat-chip">{html.escape(current_user_role_label)}</span>
              <span class="sidebar-stat-chip">{len(my_pending_tasks)} tareas</span>
              <span class="sidebar-stat-chip">{queued_notifications} alertas</span>
            </div>
            <div class="sidebar-actions">
              <a class="small-button" href="{_page_href('security')}"{_nav_hidden(allowed_pages, 'security')}>Configuracion</a>
              <form class="table-action-form" method="post" action="/logout">
                <button class="small-button" type="submit">Cerrar sesion</button>
              </form>
            </div>
          </section>
        </div>
      </div>
    </aside>

    <main class="content">
      <div class="content-inner">
        <section class="topbar">
          <div class="topbar-leading">
            <button class="topbar-menu" type="button" aria-label="Open navigation">&#9776;</button>
            <div class="topbar-title-block">
              <span class="topbar-context">{html.escape(current_agency_name)}</span>
              <strong>{html.escape(current_page_label)}</strong>
            </div>
          </div>
          <label class="topbar-search">
            <span>Search</span>
            <input type="search" placeholder="Search here">
          </label>
          <div class="topbar-actions">
            {f'<a class="topbar-button" href="{html.escape(primary_action_href)}">{html.escape(primary_action_label)}</a>' if primary_action_label and primary_action_href else ''}
            <a class="topbar-counter" href="{_page_href('agenda')}"{_nav_hidden(allowed_pages, 'agenda')}><span>Tareas</span><strong>{len(my_pending_tasks)}</strong></a>
            <a class="topbar-counter" href="{_page_href('notifications')}"{_nav_hidden(allowed_pages, 'notifications')}><span>Alertas</span><strong>{queued_notifications}</strong></a>
            <div class="topbar-profile">
              <div class="avatar-shell topbar-avatar">{avatar_markup}</div>
              <div class="topbar-profile-copy">
                <span>{html.escape('Welcome, ' + current_user_first_name + '!')}</span>
                <strong>{html.escape(current_user_role_label)}</strong>
              </div>
            </div>
          </div>
        </section>

        {dashboard_page_intro_markup}

        {dashboard_metrics_markup}

        {dashboard_work_queue_markup}

        {dashboard_analytics_markup}

        <section class="panel section-card"{_section_hidden(current_page, 'claims', 'payments')}>
          <h2>Vista general de claims</h2>
          <p>Aqui puedes revisar de forma general los claims guardados, transmitidos, pagados, parcialmente pagados o denegados.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Claim ID</th>
                  <th>Claim # payer</th>
                  <th>Paciente</th>
                  <th>Payer</th>
                  <th>Fecha servicio</th>
                  <th>Cargo</th>
                  <th>Pagado</th>
                  <th>Estatus</th>
                  <th>Transmit</th>
                  <th>Accion</th>
                </tr>
              </thead>
              <tbody>{_render_claim_rows(claim_summary.get('recent', []), include_totals=can_view_claim_totals, include_paid=can_view_claim_paid_amounts)}</tbody>
            </table>
          </div>
        </section>

        {status_block}

        {billing_page_intro_markup}

        {billing_metrics_markup}

        {billing_hub_markup if current_page in ('claims', 'payments', 'payers') else ''}

        {claims_billing_queue_markup if current_page == 'claims' else ''}

        {claims_batch_preview_markup if current_page == 'claims' else ''}

        {claims_follow_up_markup if current_page == 'claims' else ''}

        {claims_ai_result_markup if current_page == 'claims' else ''}

        <details class="section-card collapsible-panel"{_section_hidden(current_page, 'claims')}{_details_open(active_panel, 'claim_upload')}{" hidden" if not can_submit_claims else ""} data-collapse-default="1">
          <summary class="collapsible-summary">
            <span class="collapsible-copy">
              <strong>Subir archivo 837</strong>
              <small>Importa un archivo EDI, guardalo en el batch del dia y revisalo antes de transmitir.</small>
            </span>
            <span class="collapsible-hint">Abrir</span>
          </summary>
          <div class="collapsible-body">
            <form class="collapsible-form" method="post" action="/upload-837" enctype="multipart/form-data">
              <label class="field">
                <span>Archivo 837</span>
                <input type="file" name="edi837_file" accept=".edi,.txt,.837,.dat,.x12,text/plain,application/octet-stream">
              </label>
              <button type="submit">Subir 837 al batch</button>
            </form>
          </div>
        </details>

        <section class="panel section-card"{_section_hidden(current_page, 'payments')}>
          <form method="post" action="/upload-835" enctype="multipart/form-data">
            <h2>Subir archivo ERA 835</h2>
            <p>Sube el ERA recibido del payer para actualizar claims como <strong>paid</strong>, <strong>partial</strong>, <strong>denied</strong> o <strong>pending</strong>.</p>
            <label class="field">
              <span>Archivo 835</span>
              <input type="file" name="edi835_file" accept=".edi,.txt,.835">
            </label>
            <button type="submit">Importar 835</button>
          </form>
        </section>

        <details class="workspace collapsible-panel collapsible-workspace"{_section_hidden(current_page, 'claims')}{_details_open(active_panel, 'claim')}{" hidden" if not can_submit_claims else ""} data-collapse-default="1">
          <summary class="collapsible-summary">
            <span class="collapsible-copy">
              <strong>Preparacion del claim 837P</strong>
              <small>Abre el formulario clinico y administrativo para preparar el reclamo antes de enviarlo.</small>
            </span>
            <span class="collapsible-hint">Abrir</span>
          </summary>
          <div class="collapsible-body">
            <form id="claims837" class="{claim_class}" method="post" action="/submit-claim">
            <div class="claim-preview-grid">
              <article class="claim-preview-card">
                <strong data-claim-preview="lines">{claim_preview['lines']}</strong>
                <span>Lineas activas</span>
              </article>
              <article class="claim-preview-card">
                <strong data-claim-preview="units">{claim_preview['units']}</strong>
                <span>Total units</span>
              </article>
              <article class="claim-preview-card">
                <strong data-claim-preview="minutes">{claim_preview['minutes']}</strong>
                <span>Total minutos</span>
              </article>
              <article class="claim-preview-card">
                <strong data-claim-preview="dollars">{html.escape(str(claim_preview['total_charge_amount_text']))}</strong>
                <span>Total estimado</span>
              </article>
            </div>
            <div class="form-section">
              <div class="section-label">Datos del reclamo</div>
              <div class="field-grid">
                <label class="field">
                  <span>Claim ID</span>
                  <input name="claim_id" value="{_field_value(claim_values, 'claim_id')}" placeholder="10004567">
                </label>
                <label class="field">
                  <span>Fecha de servicio</span>
                  <input name="service_date" value="{_field_value(claim_values, 'service_date')}" placeholder="MM/DD/YYYY">
                </label>
              </div>
            </div>

            <div class="form-section">
              <div class="section-label">Provider</div>
              <div class="field-grid">
                <label class="field">
                  <span>NPI</span>
                  <input name="provider_npi" value="{_field_value(claim_values, 'provider_npi')}" placeholder="1234567893">
                </label>
                <label class="field">
                  <span>Taxonomy Code</span>
                  <input name="provider_taxonomy_code" value="{_field_value(claim_values, 'provider_taxonomy_code')}" placeholder="207Q00000X">
                </label>
                <label class="field">
                  <span>First Name</span>
                  <input name="provider_first_name" value="{_field_value(claim_values, 'provider_first_name')}">
                </label>
                <label class="field">
                  <span>Last Name</span>
                  <input name="provider_last_name" value="{_field_value(claim_values, 'provider_last_name')}">
                </label>
              </div>
              <label class="field">
                <span>Organization Name</span>
                <input name="provider_organization_name" value="{_field_value(claim_values, 'provider_organization_name')}">
              </label>
            </div>

            <div class="form-section">
              <div class="section-label">Paciente</div>
              <div class="field-grid">
                <label class="field">
                  <span>Member ID</span>
                  <input name="patient_member_id" value="{_field_value(claim_values, 'patient_member_id')}">
                </label>
                <label class="field">
                  <span>Fecha de nacimiento</span>
                  <input name="patient_birth_date" value="{_field_value(claim_values, 'patient_birth_date')}" placeholder="MM/DD/YYYY">
                </label>
                <label class="field">
                  <span>First Name</span>
                  <input name="patient_first_name" value="{_field_value(claim_values, 'patient_first_name')}">
                </label>
                <label class="field">
                  <span>Last Name</span>
                  <input name="patient_last_name" value="{_field_value(claim_values, 'patient_last_name')}">
                </label>
                <label class="field">
                  <span>Gender</span>
                  <select name="patient_gender">
                    <option value="M"{_selected(claim_values, 'patient_gender', 'M')}>Masculino</option>
                    <option value="F"{_selected(claim_values, 'patient_gender', 'F')}>Femenino</option>
                    <option value="U"{_selected(claim_values, 'patient_gender', 'U')}>No especificado</option>
                  </select>
                </label>
                <label class="field">
                  <span>Address</span>
                  <input name="patient_address_line1" value="{_field_value(claim_values, 'patient_address_line1')}">
                </label>
                <label class="field">
                  <span>City</span>
                  <input name="patient_city" value="{_field_value(claim_values, 'patient_city')}">
                </label>
                <label class="field">
                  <span>State</span>
                  <input name="patient_state" value="{_field_value(claim_values, 'patient_state')}">
                </label>
                <label class="field">
                  <span>Zip Code</span>
                  <input name="patient_zip_code" value="{_field_value(claim_values, 'patient_zip_code')}">
                </label>
              </div>
            </div>

            <div class="form-section">
              <div class="section-label">Seguro</div>
              <div class="field-grid">
                <label class="field">
                  <span>Payer Name</span>
                  <input name="insurance_payer_name" value="{_field_value(claim_values, 'insurance_payer_name')}">
                </label>
                <label class="field">
                  <span>Payer ID</span>
                  <input name="insurance_payer_id" value="{_field_value(claim_values, 'insurance_payer_id')}">
                </label>
                <label class="field">
                  <span>Policy Number</span>
                  <input name="insurance_policy_number" value="{_field_value(claim_values, 'insurance_policy_number')}">
                </label>
                <label class="field">
                  <span>Plan Name</span>
                  <input name="insurance_plan_name" value="{_field_value(claim_values, 'insurance_plan_name')}">
                </label>
              </div>
            </div>

            <div class="form-section">
              <div class="section-label">Diagnosticos</div>
              <div class="field-grid compact">
                <label class="field">
                  <span>DX 1</span>
                  <input name="diagnosis_code_1" value="{_field_value(claim_values, 'diagnosis_code_1')}" placeholder="J109">
                </label>
                <label class="field">
                  <span>DX 2</span>
                  <input name="diagnosis_code_2" value="{_field_value(claim_values, 'diagnosis_code_2')}" placeholder="R509">
                </label>
                <label class="field">
                  <span>DX 3</span>
                  <input name="diagnosis_code_3" value="{_field_value(claim_values, 'diagnosis_code_3')}" placeholder="Opcional">
                </label>
              </div>
            </div>

            <div class="form-section">
              <div class="section-label">Lineas de servicio</div>
              <input type="hidden" name="total_charge_amount" value="{html.escape(str(claim_preview['total_charge_amount_value']))}" data-claim-total-input>
              <details class="service-lines-shell">
                <summary class="service-lines-toggle">
                  <span>Abrir herramienta de lineas 837P</span>
                  <strong data-claim-preview="toggle-total">{html.escape(str(claim_preview['total_charge_amount_text']))}</strong>
                </summary>
                <div class="service-lines-body">
                  <p class="module-note">Cada unidad equivale a {billing_unit_minutes} minutos. El cargo total de cada linea se calcula como unidades por precio individual. Al elegir el CPT, el sistema sugiere automaticamente la tarifa por unidad segun tu catalogo.</p>
                  <div class="service-lines">
                    <div class="service-row">
                      <label class="field">
                        <span>CPT / HCPCS</span>
                        <select name="service_line_1_procedure_code">{_cpt_options_markup(str(claim_values.get('service_line_1_procedure_code', '')), include_unit_price=can_view_billing_rates, include_price_attr=can_view_billing_rates)}</select>
                      </label>
                      <label class="field">
                        <span>Precio por unidad</span>
                        <input name="service_line_1_unit_price" value="{_field_value(claim_values, 'service_line_1_unit_price')}" placeholder="125.00">
                      </label>
                      <label class="field">
                        <span>Units (15 min c/u)</span>
                        <input name="service_line_1_units" value="{_field_value(claim_values, 'service_line_1_units')}" placeholder="1">
                      </label>
                      <label class="field">
                        <span>DX Ptr</span>
                        <input name="service_line_1_diagnosis_pointer" value="{_field_value(claim_values, 'service_line_1_diagnosis_pointer')}" placeholder="1">
                      </label>
                      <div class="service-row-meta">
                        <div class="service-row-total">
                          <span>Total linea</span>
                          <strong data-claim-line-total="1">{html.escape(str(claim_line_1_preview['charge_text']))}</strong>
                        </div>
                        <div class="service-row-total">
                          <span>Minutos</span>
                          <strong data-claim-line-minutes="1">{html.escape(str(claim_line_1_preview['minutes_text']))}</strong>
                        </div>
                      </div>
                      <input type="hidden" name="service_line_1_charge_amount" value="{html.escape(str(claim_line_1_preview['charge_amount_value']))}" data-claim-line-charge="1">
                    </div>
                    <div class="service-row">
                      <label class="field">
                        <span>CPT / HCPCS</span>
                        <select name="service_line_2_procedure_code">{_cpt_options_markup(str(claim_values.get('service_line_2_procedure_code', '')), include_unit_price=can_view_billing_rates, include_price_attr=can_view_billing_rates)}</select>
                      </label>
                      <label class="field">
                        <span>Precio por unidad</span>
                        <input name="service_line_2_unit_price" value="{_field_value(claim_values, 'service_line_2_unit_price')}" placeholder="45.00">
                      </label>
                      <label class="field">
                        <span>Units (15 min c/u)</span>
                        <input name="service_line_2_units" value="{_field_value(claim_values, 'service_line_2_units')}" placeholder="1">
                      </label>
                      <label class="field">
                        <span>DX Ptr</span>
                        <input name="service_line_2_diagnosis_pointer" value="{_field_value(claim_values, 'service_line_2_diagnosis_pointer')}" placeholder="2">
                      </label>
                      <div class="service-row-meta">
                        <div class="service-row-total">
                          <span>Total linea</span>
                          <strong data-claim-line-total="2">{html.escape(str(claim_line_2_preview['charge_text']))}</strong>
                        </div>
                        <div class="service-row-total">
                          <span>Minutos</span>
                          <strong data-claim-line-minutes="2">{html.escape(str(claim_line_2_preview['minutes_text']))}</strong>
                        </div>
                      </div>
                      <input type="hidden" name="service_line_2_charge_amount" value="{html.escape(str(claim_line_2_preview['charge_amount_value']))}" data-claim-line-charge="2">
                    </div>
                    <div class="service-row">
                      <label class="field">
                        <span>CPT / HCPCS</span>
                        <select name="service_line_3_procedure_code">{_cpt_options_markup(str(claim_values.get('service_line_3_procedure_code', '')), include_unit_price=can_view_billing_rates, include_price_attr=can_view_billing_rates)}</select>
                      </label>
                      <label class="field">
                        <span>Precio por unidad</span>
                        <input name="service_line_3_unit_price" value="{_field_value(claim_values, 'service_line_3_unit_price')}" placeholder="Opcional">
                      </label>
                      <label class="field">
                        <span>Units (15 min c/u)</span>
                        <input name="service_line_3_units" value="{_field_value(claim_values, 'service_line_3_units')}" placeholder="1">
                      </label>
                      <label class="field">
                        <span>DX Ptr</span>
                        <input name="service_line_3_diagnosis_pointer" value="{_field_value(claim_values, 'service_line_3_diagnosis_pointer')}" placeholder="3">
                      </label>
                      <div class="service-row-meta">
                        <div class="service-row-total">
                          <span>Total linea</span>
                          <strong data-claim-line-total="3">{html.escape(str(claim_line_3_preview['charge_text']))}</strong>
                        </div>
                        <div class="service-row-total">
                          <span>Minutos</span>
                          <strong data-claim-line-minutes="3">{html.escape(str(claim_line_3_preview['minutes_text']))}</strong>
                        </div>
                      </div>
                      <input type="hidden" name="service_line_3_charge_amount" value="{html.escape(str(claim_line_3_preview['charge_amount_value']))}" data-claim-line-charge="3">
                    </div>
                  </div>
                </div>
              </details>
            </div>
            <button type="submit">Guardar al batch</button>
            </form>
          </div>
        </details>

        {eligibility_page_intro_markup}

        {eligibility_metrics_markup}

        <section class="workspace"{_section_hidden(current_page, 'eligibility')}>
          <div class="dual-grid">
            <form id="eligibility271" class="{eligibility_class}" method="post" action="/check-eligibility">
              <div class="module-head">
                <span class="module-badge">Cobertura del paciente</span>
                <h2>Consulta de elegibilidad</h2>
                <p>Verifique si la cobertura esta activa antes de facturar la visita.</p>
              </div>
              <p class="module-note">Usuario activo: <strong>{html.escape(current_user_name or 'Sin usuario')}</strong>. Usa solo los datos del paciente y la poliza para correr la verificacion.</p>
              <div class="field-grid">
                <label class="field">
                  <span>Last name</span>
                  <input name="patient_last_name" value="{_field_value(eligibility_values, 'patient_last_name')}">
                </label>
                <label class="field">
                  <span>First name</span>
                  <input name="patient_first_name" value="{_field_value(eligibility_values, 'patient_first_name')}">
                </label>
                <label class="field">
                  <span>Middle</span>
                  <input name="patient_middle_name" value="{_field_value(eligibility_values, 'patient_middle_name')}">
                </label>
                <label class="field">
                  <span>DOB</span>
                  <input name="patient_birth_date" value="{_field_value(eligibility_values, 'patient_birth_date')}" placeholder="MM/DD/YYYY">
                </label>
                <label class="field">
                  <span>Gender</span>
                  <select name="patient_gender">
                    <option value=""{_selected(eligibility_values, 'patient_gender', '')}>Selecciona</option>
                    <option value="M"{_selected(eligibility_values, 'patient_gender', 'M')}>Male</option>
                    <option value="F"{_selected(eligibility_values, 'patient_gender', 'F')}>Female</option>
                    <option value="U"{_selected(eligibility_values, 'patient_gender', 'U')}>Unknown</option>
                  </select>
                </label>
                <label class="field">
                  <span>Policy #</span>
                  <input name="member_id" value="{_field_value(eligibility_values, 'member_id')}">
                </label>
                <label class="field">
                  <span>Service date</span>
                  <input name="service_date" value="{_field_value(eligibility_values, 'service_date')}" placeholder="MM/DD/YYYY">
                </label>
              </div>
              <div class="hero-badges">
                <button type="submit">Consultar elegibilidad</button>
                <button type="submit" formaction="/add-eligibility-to-roster">Agregar al roster automatico</button>
              </div>
            </form>

            <article class="panel section-card">
              <h2>Eligibility Used: {eligibility_used}</h2>
              <p>Historial reciente de consultas de elegibilidad dentro de la agencia activa, parecido al panel operativo que me mostraste.</p>
              <div class="mini-table">
                <div class="mini-row"><strong>Date</strong><span>Fecha y hora de la consulta</span></div>
                <div class="mini-row"><strong>Ins. Name</strong><span>Nombre del paciente o insured</span></div>
                <div class="mini-row"><strong>Status</strong><span>Complete cuando la consulta se procesa correctamente</span></div>
              </div>
            </article>
          </div>

          <section class="panel section-card">
            <h2>Eligibility History</h2>
            <p>My recent queries y validaciones registradas por usuario dentro del portal.</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Ins. Name</th>
                    <th>Payer</th>
                    <th>Policy Number</th>
                    <th>Benefit</th>
                    <th>Procedure</th>
                    <th>Status</th>
                    <th>DOS</th>
                    <th>User</th>
                  </tr>
                </thead>
                <tbody>{_render_eligibility_history_rows(eligibility_history)}</tbody>
              </table>
            </div>
          </section>
        </section>

        <section class="workspace"{_section_hidden(current_page, 'payments')}>
          <form id="era835" class="{era_class}" method="post" action="/parse-835">
            <div class="module-head">
              <span class="module-badge">Pago y remesa</span>
              <h2>Analisis de ERA 835</h2>
              <p>Interprete rapidamente el pago recibido con campos normales, sin tener que trabajar siempre sobre codigo EDI.</p>
            </div>
            <p class="module-note">Llena estas lineas como si fuera un formulario administrativo. El sistema armara el resumen del 835 y actualizara el estatus del claim.</p>
            <div class="field-grid">
              <label class="field">
                <span>Control #</span>
                <input name="transaction_set_control_number" value="{_field_value(era_values, 'transaction_set_control_number')}">
              </label>
              <label class="field">
                <span>Payer</span>
                <input name="payer_name" value="{_field_value(era_values, 'payer_name')}">
              </label>
              <label class="field">
                <span>Payee / Clinic</span>
                <input name="payee_name" value="{_field_value(era_values, 'payee_name')}">
              </label>
              <label class="field">
                <span>Total payment</span>
                <input name="payment_amount" value="{_field_value(era_values, 'payment_amount')}" placeholder="150.00">
              </label>
              <label class="field">
                <span>Claim ID</span>
                <input name="claim_id" value="{_field_value(era_values, 'claim_id')}">
              </label>
              <label class="field">
                <span>Claim # del payer</span>
                <input name="payer_claim_number" value="{_field_value(era_values, 'payer_claim_number')}">
              </label>
              <label class="field">
                <span>Estatus del claim</span>
                <select name="claim_status_code">
                  <option value="1"{_selected(era_values, 'claim_status_code', '1')}>Processed / Paid</option>
                  <option value="2"{_selected(era_values, 'claim_status_code', '2')}>Processed Secondary</option>
                  <option value="4"{_selected(era_values, 'claim_status_code', '4')}>Denied</option>
                  <option value="22"{_selected(era_values, 'claim_status_code', '22')}>Reversed</option>
                  <option value="23"{_selected(era_values, 'claim_status_code', '23')}>Not Our Claim</option>
                </select>
              </label>
              <label class="field">
                <span>Charge amount</span>
                <input name="charge_amount" value="{_field_value(era_values, 'charge_amount')}" placeholder="170.00">
              </label>
              <label class="field">
                <span>Paid amount</span>
                <input name="paid_amount" value="{_field_value(era_values, 'paid_amount')}" placeholder="150.00">
              </label>
            </div>
            <details class="module-note">
              <summary>Modo avanzado: pegar texto EDI 835</summary>
              <p>Solo usa esta opcion si ya tienes el archivo 835 en formato EDI crudo.</p>
              <textarea name="payload" placeholder="Pega aqui un 835 real si quieres leerlo directamente">{era_example}</textarea>
            </details>
            <button type="submit">Procesar remesa 835</button>
          </form>

          <section class="panel section-card">
            <h2>ERA 835 archivados</h2>
            <p>Cuando subes o procesas un 835, el archivo queda guardado para descargarlo despues y revisar cuantas reclamaciones actualizo.</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Importado</th>
                    <th>Archivo</th>
                    <th>Payer</th>
                    <th>Monto</th>
                    <th>Claims</th>
                    <th>Actualizados</th>
                    <th>Descarga</th>
                  </tr>
                </thead>
                <tbody>{_render_era_archive_rows(era_archives)}</tbody>
              </table>
            </div>
          </section>
        </section>

        <details class="workspace collapsible-panel collapsible-workspace"{_section_hidden(current_page, 'claims')}{_details_open(active_panel, 'edi837')}{" hidden" if not can_submit_claims else ""} data-collapse-default="1">
          <summary class="collapsible-summary">
            <span class="collapsible-copy">
              <strong>Lectura de claim 837</strong>
              <small>Despliega la vista para revisar un 837 en formato de oficina y validar sus lineas.</small>
            </span>
            <span class="collapsible-hint">Abrir</span>
          </summary>
          <div class="collapsible-body">
            <form id="read837" class="{edi837_class}" method="post" action="/parse-837">
            <div class="field-grid">
              <label class="field">
                <span>Control #</span>
                <input name="transaction_set_control_number" value="{_field_value(edi837_values, 'transaction_set_control_number')}">
              </label>
              <label class="field">
                <span>Payer</span>
                <input name="payer_name" value="{_field_value(edi837_values, 'payer_name')}">
              </label>
              <label class="field">
                <span>Provider</span>
                <input name="provider_name" value="{_field_value(edi837_values, 'provider_name')}">
              </label>
              <label class="field">
                <span>Provider NPI</span>
                <input name="provider_npi" value="{_field_value(edi837_values, 'provider_npi')}">
              </label>
              <label class="field">
                <span>Paciente</span>
                <input name="patient_name" value="{_field_value(edi837_values, 'patient_name')}">
              </label>
              <label class="field">
                <span>Member ID</span>
                <input name="member_id" value="{_field_value(edi837_values, 'member_id')}">
              </label>
              <label class="field">
                <span>Claim ID</span>
                <input name="claim_id" value="{_field_value(edi837_values, 'claim_id')}">
              </label>
              <label class="field">
                <span>Fecha servicio</span>
                <input name="service_date" value="{_field_value(edi837_values, 'service_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Total charge</span>
                <input name="total_charge_amount" value="{_field_value(edi837_values, 'total_charge_amount')}" placeholder="170.00">
              </label>
            </div>
            <div class="form-section">
              <div class="section-label">Lineas de servicio</div>
              <div class="service-lines">
                <div class="service-row">
                  <label class="field">
                    <span>CPT / HCPCS</span>
                    <select name="service_line_1_procedure_code">{_cpt_options_markup(str(edi837_values.get('service_line_1_procedure_code', '')), include_unit_price=can_view_billing_rates, include_price_attr=can_view_billing_rates)}</select>
                  </label>
                  <label class="field">
                    <span>Charge amount</span>
                    <input name="service_line_1_charge_amount" value="{_field_value(edi837_values, 'service_line_1_charge_amount')}" placeholder="125.00">
                  </label>
                  <label class="field">
                    <span>Units</span>
                    <input name="service_line_1_units" value="{_field_value(edi837_values, 'service_line_1_units')}" placeholder="1">
                  </label>
                  <div></div>
                </div>
                <div class="service-row">
                  <label class="field">
                    <span>CPT / HCPCS</span>
                    <select name="service_line_2_procedure_code">{_cpt_options_markup(str(edi837_values.get('service_line_2_procedure_code', '')), include_unit_price=can_view_billing_rates, include_price_attr=can_view_billing_rates)}</select>
                  </label>
                  <label class="field">
                    <span>Charge amount</span>
                    <input name="service_line_2_charge_amount" value="{_field_value(edi837_values, 'service_line_2_charge_amount')}" placeholder="45.00">
                  </label>
                  <label class="field">
                    <span>Units</span>
                    <input name="service_line_2_units" value="{_field_value(edi837_values, 'service_line_2_units')}" placeholder="1">
                  </label>
                  <div></div>
                </div>
                <div class="service-row">
                  <label class="field">
                    <span>CPT / HCPCS</span>
                    <select name="service_line_3_procedure_code">{_cpt_options_markup(str(edi837_values.get('service_line_3_procedure_code', '')), include_unit_price=can_view_billing_rates, include_price_attr=can_view_billing_rates)}</select>
                  </label>
                  <label class="field">
                    <span>Charge amount</span>
                    <input name="service_line_3_charge_amount" value="{_field_value(edi837_values, 'service_line_3_charge_amount')}" placeholder="Opcional">
                  </label>
                  <label class="field">
                    <span>Units</span>
                    <input name="service_line_3_units" value="{_field_value(edi837_values, 'service_line_3_units')}" placeholder="1">
                  </label>
                  <div></div>
                </div>
              </div>
            </div>
            <details class="module-note">
              <summary>Modo avanzado: pegar texto EDI 837</summary>
              <p>Solo usa esta opcion si ya tienes el 837 crudo en formato EDI.</p>
              <textarea name="payload" placeholder="Pega aqui un 837 real si quieres leerlo directamente">{edi837_example}</textarea>
            </details>
            <button type="submit">Procesar lectura 837</button>
            </form>
          </div>
        </details>

        <details id="claims-batch" class="section-card collapsible-panel"{_section_hidden(current_page, 'claims')}{_details_open(active_panel, 'claim_batch')} data-collapse-default="1">
          <summary class="collapsible-summary">
            <span class="collapsible-copy">
              <strong>Batch de claims del dia</strong>
              <small>Consulta la cola diaria, revisa cada reclamo guardado y transmite cuando el lote este listo.</small>
            </span>
            <span class="collapsible-hint">Abrir</span>
          </summary>
          <div class="collapsible-body">
            <div class="hero-badges">
              <form class="table-action-form" method="post" action="/transmit-batch-today">
                <button type="submit">Transmit batch de hoy</button>
              </form>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fecha batch</th>
                    <th>Claim ID</th>
                    <th>Paciente</th>
                    <th>Payer</th>
                    <th>Fecha servicio</th>
                    <th>Cargo</th>
                    <th>Origen</th>
                    <th>Transmit</th>
                    <th>Tracking ID</th>
                    <th>Transmitido</th>
                    <th>Accion</th>
                  </tr>
                </thead>
                <tbody>{_render_batch_claim_rows(all_claims, include_totals=can_view_claim_totals, can_transmit=can_submit_claims)}</tbody>
              </table>
            </div>
          </div>
        </details>

        <details class="section-card collapsible-panel"{_section_hidden(current_page, 'claims')} data-collapse-default="1">
          <summary class="collapsible-summary">
            <span class="collapsible-copy">
              <strong>Auditoria de claims</strong>
              <small>Abre el historial para ver quien creo, transmitio o modifico cada claim y cuando ocurrio.</small>
            </span>
            <span class="collapsible-hint">Abrir</span>
          </summary>
          <div class="collapsible-body">
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Claim ID</th>
                    <th>Accion</th>
                    <th>Nombre</th>
                    <th>Usuario</th>
                    <th>Detalle</th>
                  </tr>
                </thead>
                <tbody>{_render_claim_audit_rows(claim_audit_logs)}</tbody>
              </table>
            </div>
          </div>
        </details>

        {clients_metrics_markup}

        {client_workspace_markup if selected_client is not None else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'clients')}{" hidden" if not show_client_form_panel else ""}>
          {client_form_markup}

          {'' if selected_client is not None else client_workspace_markup}
        </section>

        {client_authorization_workspace_markup}

        <section id="client-directory" class="panel section-card" data-skip-auto-collapsible="1"{_section_hidden(current_page, 'clients')}>
          <h2>Clientes guardados</h2>
          <p>Esta vista ahora funciona como directorio visual para que veas la lista completa mas rapido, parecido al roster operativo que mostraste.</p>
          {clients_directory_toolbar_markup}
          <div class="directory-view card-view" data-directory-panel="clients" data-view="card">
            {clients_directory_markup}
          </div>
          <div class="directory-view table-view" data-directory-panel="clients" data-view="table" hidden>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Cliente</th>
                    <th>Member ID</th>
                    <th>Payer</th>
                    <th>Provider NPI</th>
                    <th>Lugar</th>
                    <th>County</th>
                    <th>Medicaid ID</th>
                    <th>Fecha servicio</th>
                    <th>Docs</th>
                    <th>Ultimo resultado</th>
                    <th>Ultima revision</th>
                    <th>Auto elig.</th>
                    <th>Accion</th>
                  </tr>
                </thead>
                <tbody>{_render_client_rows(clients, can_edit_client=can_edit_clients, can_manage_authorizations=can_manage_authorizations, can_run_eligibility=can_run_client_eligibility)}</tbody>
              </table>
            </div>
          </div>
        </section>

        {aba_notes_page_intro_markup}

        {aba_notes_metrics_markup}

        {aba_notes_hub_markup if current_page == 'aba_notes' else ''}

        {aba_operations_markup if current_page == 'aba_notes' else ''}

        {aba_session_queue_markup if current_page == 'aba_notes' else ''}

        {aba_session_detail_markup if current_page == 'aba_notes' else ''}

        {aba_ai_result_markup if current_page == 'aba_notes' else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'aba_notes')}>
          <form id="aba-notes-form" class="panel section-card" method="post" action="/add-aba-appointment">
            <h2>Notas ABA y sesiones</h2>
            <p>Programa sesiones ABA, genera el service log semanal y deja la nota lista para supervision y cierre dentro del mismo portal.</p>
            <input type="hidden" name="selected_log_id" value="{_field_value(aba_notes_values, 'selected_log_id')}">
            <div class="field-grid">
              <label class="field">
                <span>Provider</span>
                <select name="provider_contract_id" data-aba-provider-select>{_aba_provider_options_markup(aba_provider_options, str(aba_notes_values.get('provider_contract_id', '')))}</select>
              </label>
              <label class="field">
                <span>Cliente</span>
                <select name="client_id" data-aba-client-select>{_aba_client_options_markup(aba_client_options, str(aba_notes_values.get('client_id', '')))}</select>
              </label>
              <label class="field">
                <span>Tipo de servicio</span>
                <select name="service_context" data-aba-service-context>{_aba_context_options_markup(str(aba_notes_values.get('service_context', 'direct')))}</select>
              </label>
              {_date_shell_markup('appointment_date', aba_notes_values.get('appointment_date', ''), aba_appointment_date_html, 'Fecha')}
              {_time_wheel_markup('start_time', aba_notes_values.get('start_time', ''), 'Hora inicio', hour_value=aba_start_hour, minute_value=aba_start_minute, ampm_value=aba_start_ampm)}
              {_time_wheel_markup('end_time', aba_notes_values.get('end_time', ''), 'Hora fin', hour_value=aba_end_hour, minute_value=aba_end_minute, ampm_value=aba_end_ampm)}
              <label class="field">
                <span>Place of service</span>
                <input name="place_of_service" value="{_field_value(aba_notes_values, 'place_of_service')}" placeholder="Home (12)">
              </label>
              <label class="field">
                <span>Caregiver</span>
                <input name="caregiver_name" value="{_field_value(aba_notes_values, 'caregiver_name')}" placeholder="Nombre caregiver">
              </label>
            </div>
            <p class="helper-note" data-rbt-direct-hint>Selecciona provider, cliente y tipo de servicio para confirmar el CPT obligatorio.</p>
            <div class="field-grid">
              {_signature_pad_markup('caregiver_signature', aba_notes_values.get('caregiver_signature', ''), 'Firma caregiver')}
              {_signature_pad_markup('provider_signature', aba_notes_values.get('provider_signature', ''), 'Firma provider')}
            </div>
            <label class="field">
              <span>Session note</span>
              <textarea name="session_note" placeholder="Resumen clinico, respuesta del cliente, intervenciones y plan para la siguiente sesion.">{_field_value(aba_notes_values, 'session_note')}</textarea>
            </label>
            <div class="form-section">
              <div class="section-label">{"Preview de billing ABA" if can_view_billing_rates else "Preview operativo ABA"}</div>
              <p class="module-note">{"El sistema usa las reglas CPT del modulo nuevo para calcular units, billing code, documento y validacion contra autorizaciones restantes." if can_view_billing_rates else "Aqui solo ves la parte operativa de la sesion. Las tarifas y montos quedan ocultos para usuarios proveedor."}</p>
              {_render_aba_billing_preview(aba_billing_preview, can_view_billing_rates)}
            </div>
            <button type="submit">Guardar sesion ABA</button>
          </form>

          <article id="aba-module-center" class="panel section-card">
            <h2>Centro del modulo</h2>
            <p>Esta primera version ya trabaja dentro del portal web y usa providers, clientes y autorizaciones del sistema principal.</p>
            <div class="mini-table">
              <div class="mini-row"><strong>Providers ABA</strong><span>{len(aba_provider_options)}</span></div>
              <div class="mini-row"><strong>Clientes visibles</strong><span>{len(aba_client_options)}</span></div>
              <div class="mini-row"><strong>Sesiones guardadas</strong><span>{len(aba_appointments)}</span></div>
              <div class="mini-row"><strong>Service logs</strong><span>{len(aba_service_logs)}</span></div>
            </div>
            <p class="helper-note">Si un expediente aun no tiene clientes asignados bien amarrados, el staff interno puede seguir trabajando mientras terminamos de afinar la logica. Los providers solo ven sus clientes realmente asignados.</p>
          </article>
        </section>

        <section id="aba-appointments" class="panel section-card"{_section_hidden(current_page, 'aba_notes')}>
          <h2>Sesiones ABA guardadas</h2>
          <p>Cada sesion genera data reutilizable para notas, service logs semanales y futura conexion con billing por sesiones completadas.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Provider</th>
                  <th>Cliente</th>
                  <th>Servicio</th>
                  <th>CPT</th>
                  <th>Documento</th>
                  <th>Horario</th>
                  <th>Units</th>
                  {"" if not can_view_billing_rates else "<th>Total</th>"}
                </tr>
              </thead>
              <tbody>{_render_aba_appointment_rows(aba_appointments, can_view_billing_rates)}</tbody>
            </table>
          </div>
        </section>

        <section id="aba-service-logs" class="panel section-card"{_section_hidden(current_page, 'aba_notes')}>
          <h2>Service logs semanales</h2>
          <p>Abre cualquier log para revisar el preview de la nota, ver deadline y moverlo entre draft, reviewed, closed o rejected.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Semana</th>
                  <th>Provider</th>
                  <th>Cliente</th>
                  <th>Documento</th>
                  <th>Horas</th>
                  <th>Units</th>
                  <th>Deadline</th>
                  <th>Workflow</th>
                  <th>Note due</th>
                  <th>Accion</th>
                </tr>
              </thead>
              <tbody>{_render_aba_service_log_rows(aba_service_logs)}</tbody>
            </table>
          </div>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'aba_notes')}>
          <article id="aba-note-preview" class="panel section-card">
            <h2>Document preview</h2>
            <p>El service log ya se renderiza como documento clinico dentro del portal, con el mismo layout listo para export y PDF.</p>
            {(
              '<iframe title="Service log preview" class="document-preview-frame" sandbox="" style="width:100%;min-height:1320px;border:0;border-radius:20px;background:#fff;" srcdoc="'
              + html.escape(selected_aba_log_preview_html, quote=True)
              + '"></iframe>'
              if selected_aba_log is not None and selected_aba_log_preview_html
              else (
                "<pre>" + html.escape(selected_aba_log_preview_body) + "</pre>"
                if selected_aba_log is not None
                else "<p class=\"helper-note\">Selecciona un service log para ver aqui la nota completa.</p>"
              )
            )}
          </article>

          <form class="panel section-card" method="post" action="/aba-log-workflow">
            <h2>Workflow de supervision</h2>
            <p>Usa este panel para supervisar, cerrar, rechazar o reabrir la nota semanal sin salir de la misma pagina.</p>
            {_render_aba_log_detail(selected_aba_log, aba_notes_values, can_manage_workflow=_can_close_aba_note(current_user))}
          </form>
        </section>

        {payers_hub_markup if current_page == 'payers' else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'payers')}>
          <form id="payer-config-form" class="panel section-card" method="post" action="/add-payer-config">
            <h2>Catalogo de payers</h2>
            <p>Configura cada seguro con su clearinghouse, sus billing codes por CPT y el unit price que usa tu equipo de facturacion.</p>
            <input type="hidden" name="payer_config_id" value="{_field_value(payer_config_values, 'payer_config_id')}">
            <div class="field-grid">
              <label class="field">
                <span>Payer Name</span>
                <input name="payer_name" value="{_field_value(payer_config_values, 'payer_name')}" placeholder="Sunshine Health">
              </label>
              <label class="field">
                <span>Payer ID</span>
                <input name="payer_id" value="{_field_value(payer_config_values, 'payer_id')}" placeholder="12345">
              </label>
              <label class="field">
                <span>Plan Type</span>
                <select name="plan_type">{_payer_plan_type_options_markup(str(payer_config_values.get('plan_type', 'COMMERCIAL')))}</select>
              </label>
              <label class="field">
                <span>Brand Color</span>
                <input type="color" name="brand_color" value="{_field_value(payer_config_values, 'brand_color')}">
              </label>
              <label class="field">
                <span>Clearinghouse</span>
                <select name="clearinghouse_name">{_clearinghouse_options_markup(str(payer_config_values.get('clearinghouse_name', '')))}</select>
              </label>
              <label class="field">
                <span>Clearinghouse Payer ID</span>
                <input name="clearinghouse_payer_id" value="{_field_value(payer_config_values, 'clearinghouse_payer_id')}" placeholder="Payer ID del clearinghouse">
              </label>
              <label class="field">
                <span>Receiver ID</span>
                <input name="clearinghouse_receiver_id" value="{_field_value(payer_config_values, 'clearinghouse_receiver_id')}" placeholder="Receiver / submitter">
              </label>
              <label class="field">
                <span><input type="checkbox" name="active"{_checked(payer_config_values, 'active')}> Payer activo</span>
              </label>
            </div>
            <label class="field">
              <span>Notas</span>
              <input name="notes" value="{_field_value(payer_config_values, 'notes')}" placeholder="Reglas internas, observaciones o notas de billing">
            </label>
            <div id="payer-rates" class="form-section">
              <div class="section-label">Tarifas CPT por payer</div>
              <p class="module-note">Cada CPT puede guardar el billing code que realmente usas, el HCPCS y el precio por unidad de ese seguro. Si dejas el precio en blanco o 0, el sistema conserva el default general.</p>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>CPT</th>
                      <th>Descripcion</th>
                      <th>Billing code</th>
                      <th>HCPCS</th>
                      <th>Unit price</th>
                    </tr>
                  </thead>
                  <tbody>{_payer_rate_rows_markup(payer_config_values)}</tbody>
                </table>
              </div>
            </div>
            <button type="submit">Guardar payer</button>
          </form>

          <div id="payer-center">
            {payer_workspace_markup}
          </div>
        </section>

        <section id="payers-directory" class="panel section-card" data-skip-auto-collapsible="1"{_section_hidden(current_page, 'payers')}>
          <h2>Directorio de payers</h2>
          <p>Esta lista abre como roster visual en tarjetas para que billing vea todos los seguros, sus clearinghouses y las tarifas activas sin entrar a tablas pesadas desde el inicio.</p>
          {payers_directory_toolbar_markup}
          <div class="directory-view card-view" data-directory-panel="payers" data-view="card">
            {payers_directory_markup}
          </div>
          <div class="directory-view table-view" data-directory-panel="payers" data-view="table" hidden>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Payer</th>
                    <th>Payer ID</th>
                    <th>Plan</th>
                    <th>Clearinghouse</th>
                    <th>CH Payer ID</th>
                    <th>CPTs activos</th>
                    <th>Estatus</th>
                    <th>Accion</th>
                  </tr>
                </thead>
                <tbody>{_render_payer_rows(payer_configs)}</tbody>
              </table>
            </div>
          </div>
        </section>

        {enrollments_page_intro_markup}

        {enrollments_metrics_markup}

        {enrollments_hub_markup if current_page == 'enrollments' else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'enrollments')}>
          <form id="payer-enrollment-form" class="panel section-card" method="post" action="/add-payer-enrollment">
            <h2>Roster de enrolamiento por payer</h2>
            <p>Lleva control estilo Excel de los seguros/payers en los que ya enviaste credenciales y si el provider ya esta enrolado.</p>
            <div class="field-grid">
              <label class="field">
                <span>Contract ID opcional</span>
                <input name="contract_id" value="{_field_value(payer_enrollment_values, 'contract_id')}" placeholder="Vincula este enrollment con la contratacion">
              </label>
              <label class="field">
                <span>Nombre</span>
                <input name="provider_name" value="{_field_value(payer_enrollment_values, 'provider_name')}">
              </label>
              <label class="field">
                <span>SSN</span>
                <input name="ssn" value="{_field_value(payer_enrollment_values, 'ssn')}">
              </label>
              <label class="field">
                <span>NPI</span>
                <input name="npi" value="{_field_value(payer_enrollment_values, 'npi')}">
              </label>
              <label class="field">
                <span>Medicaid ID</span>
                <input name="medicaid_id" value="{_field_value(payer_enrollment_values, 'medicaid_id')}">
              </label>
              <label class="field">
                <span>Payer</span>
                <input name="payer_name" value="{_field_value(payer_enrollment_values, 'payer_name')}">
              </label>
              <label class="field">
                <span>Lugar</span>
                <select name="site_location" data-location-select="enrollment">{_location_options_markup(str(payer_enrollment_values.get('site_location', 'Cape Coral')))}</select>
              </label>
              <label class="field">
                <span>County</span>
                <input name="county_name" data-county-input="enrollment" value="{_field_value(payer_enrollment_values, 'county_name')}" placeholder="Lee">
              </label>
              <label class="field">
                <span>Encargado de credenciales</span>
                <select name="credentialing_owner_name">{_user_select_options_markup(assignable_users, str(payer_enrollment_values.get('credentialing_owner_name', '')))}</select>
              </label>
              <label class="field">
                <span>Supervisor</span>
                <select name="supervisor_name">{_user_select_options_markup(assignable_users, str(payer_enrollment_values.get('supervisor_name', '')))}</select>
              </label>
              <label class="field">
                <span>Estatus enrolamiento</span>
                <select name="enrollment_status">
                  <option value="SUBMITTED"{_selected(payer_enrollment_values, 'enrollment_status', 'SUBMITTED')}>Submitted</option>
                  <option value="PENDING"{_selected(payer_enrollment_values, 'enrollment_status', 'PENDING')}>Pending</option>
                  <option value="ENROLLED"{_selected(payer_enrollment_values, 'enrollment_status', 'ENROLLED')}>Enrolled</option>
                  <option value="FOLLOW_UP"{_selected(payer_enrollment_values, 'enrollment_status', 'FOLLOW_UP')}>Follow Up</option>
                  <option value="REJECTED"{_selected(payer_enrollment_values, 'enrollment_status', 'REJECTED')}>Rejected</option>
                </select>
              </label>
              <label class="field">
                <span>Fecha credenciales</span>
                <input class="document-date-input" name="credentials_submitted_date" value="{_field_value(payer_enrollment_values, 'credentials_submitted_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Fecha efectiva</span>
                <input class="document-date-input" name="effective_date" value="{_field_value(payer_enrollment_values, 'effective_date')}" placeholder="MM/DD/YYYY">
              </label>
            </div>
            <label class="field">
              <span>Notas</span>
              <input name="notes" value="{_field_value(payer_enrollment_values, 'notes')}">
            </label>
            <button type="submit">Guardar enrollment</button>
          </form>

          <article class="panel section-card">
            <h2>Seguimiento de credenciales</h2>
            <p>Este roster te ayuda a revisar rapido si cada payer ya recibio credenciales, desde cuando se sometieron y si el provider ya quedo enrolado.</p>
            <div class="mini-table">
              <div class="mini-row"><strong>Campos</strong><span>Nombre, SSN, NPI, Medicaid ID, payer y fecha de credenciales.</span></div>
              <div class="mini-row"><strong>Operacion</strong><span>Ideal para llevar seguimiento de follow up, efectivos y rechazos.</span></div>
              <div class="mini-row"><strong>Exportacion</strong><span>Tambien queda disponible para descarga en Excel desde el dashboard.</span></div>
            </div>
          </article>
        </section>

        <section id="payer-enrollment-roster" class="panel section-card"{_section_hidden(current_page, 'enrollments')}>
          <h2>Roster de payers y enrolamientos</h2>
          <p>Aqui puedes ver rapidamente si ya estan enrolados y desde que fecha se sometieron las credenciales.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Contract ID</th>
                  <th>Nombre</th>
                  <th>SSN</th>
                  <th>NPI</th>
                  <th>Medicaid ID</th>
                  <th>Payer</th>
                  <th>Lugar</th>
                  <th>County</th>
                  <th>Credenciales</th>
                  <th>Supervisor</th>
                  <th>Estatus</th>
                  <th>Fecha credenciales</th>
                  <th>Fecha efectiva</th>
                  <th>Fecha estimada</th>
                  <th>Dias restantes</th>
                  <th>Barra 90 dias</th>
                </tr>
              </thead>
              <tbody>{_render_payer_enrollment_rows(payer_enrollments)}</tbody>
            </table>
          </div>
        </section>

        <section id="payer-enrollment-audit" class="panel section-card"{_section_hidden(current_page, 'enrollments')}>
          <h2>Auditoria de enrollments</h2>
          <p>Bitacora de altas y cambios administrativos del roster de enrolamiento.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Enrollment</th>
                  <th>Accion</th>
                  <th>Nombre</th>
                  <th>Username</th>
                  <th>Categoria</th>
                  <th>Detalle</th>
                </tr>
              </thead>
              <tbody>{_render_system_audit_rows(enrollment_audit_logs, 'Todavia no hay auditoria de enrollments.')}</tbody>
            </table>
          </div>
        </section>

        {agencies_page_intro_markup}

        {agencies_metrics_markup}

        {agencies_hub_markup if current_page == 'agencies' else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'agencies')}>
          <form id="agency-form" class="panel section-card" method="post" action="/add-agency" enctype="multipart/form-data">
            <h2>Registro de agencias</h2>
            <p>Crea cada agencia o ecosistema donde trabajas. Tambien puedes subir o cambiar el logo para que toda la interfaz use la imagen correcta de esa agencia.</p>
            <input type="hidden" name="agency_id" value="{_field_value(agency_values, 'agency_id')}">
            <div class="field-grid">
              <label class="field">
                <span>Agency Name</span>
                <input name="agency_name" value="{_field_value(agency_values, 'agency_name')}">
              </label>
              <label class="field">
                <span>Agency Code</span>
                <input name="agency_code" value="{_field_value(agency_values, 'agency_code')}">
              </label>
              <label class="field">
                <span>Notification Email</span>
                <input name="notification_email" value="{_field_value(agency_values, 'notification_email')}" placeholder="billing@agencia.com">
              </label>
              <label class="field">
                <span>Contact Name</span>
                <input name="contact_name" value="{_field_value(agency_values, 'contact_name')}">
              </label>
            </div>
            <label class="field">
              <span>Notas</span>
              <input name="notes" value="{_field_value(agency_values, 'notes')}">
            </label>
            <label class="field">
              <span>Logo de la agencia</span>
              <input type="file" name="agency_logo" accept=".png,.jpg,.jpeg,.webp,.svg">
            </label>
            <p class="helper-note">Si subes un logo nuevo, el sistema lo guarda dentro de la agencia y lo usa en el portal de esa cuenta.</p>
            <button type="submit">Guardar agencia</button>
          </form>

          <article id="agency-current" class="panel section-card">
            <h2>Agencia de trabajo actual</h2>
            <p>Escoge la agencia activa para que claims, clientes, enrollments, agenda y notificaciones se guarden dentro de ese ecosistema.</p>
            <div class="profile-header">
              <div class="profile-avatar">{logo_markup}</div>
              <div class="mini-table">
                <div class="mini-row"><strong>Activa</strong><span>{html.escape(current_agency_name)}</span></div>
                <div class="mini-row"><strong>Email</strong><span>{html.escape(str(current_agency.get('notification_email', '')) if current_agency else 'Sin email')}</span></div>
                <div class="mini-row"><strong>Logo</strong><span>{html.escape(str(current_agency.get('logo_file_name', '')) if current_agency and current_agency.get('logo_file_name') else 'Usando logo general')}</span></div>
              </div>
            </div>
            <p class="helper-note">Las nuevas capturas se etiquetan con esta agencia y el logo se refleja en el portal para todos los usuarios de ese ecosistema.</p>
          </article>
        </section>

        <section id="agency-list" class="panel section-card"{_section_hidden(current_page, 'agencies')}>
          <h2>Agencias registradas</h2>
          <p>Aqui puedes cambiar de agencia activa y mantener separada la operacion por ecosistema.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Agency ID</th>
                  <th>Agency</th>
                  <th>Code</th>
                  <th>Notification Email</th>
                  <th>Contact</th>
                  <th>Logo</th>
                  <th>Notes</th>
                  <th>Accion</th>
                </tr>
              </thead>
              <tbody>{_render_agency_rows(agencies, current_agency_id)}</tbody>
            </table>
          </div>
        </section>

        {hr_page_intro_markup}

        {hr_metrics_markup}

        {hr_hub_markup if current_page == 'hr' else ''}

        {hr_queue_markup if current_page == 'hr' else ''}

        {hr_pipeline_preview_markup if current_page == 'hr' else ''}

        {hr_client_audit_markup if current_page == 'hr' else ''}

        <section id="providers-directory" class="panel section-card" data-skip-auto-collapsible="1"{_section_hidden(current_page, 'providers')}{" hidden" if selected_provider_contract is not None or show_new_provider_form else ""}>
          <h2>Providers Directory</h2>
          <p>Filter the roster, open a profile quickly and keep the table clean enough for day-to-day staffing work.</p>
          {providers_directory_toolbar_markup}
          <div class="directory-view card-view" data-directory-panel="providers" data-view="card" hidden>
            {providers_directory_markup}
          </div>
          <div class="directory-view table-view" data-directory-panel="providers" data-view="table">
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Provider</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Credentials</th>
                    <th>Exp. Date</th>
                    <th>Accion</th>
                  </tr>
                </thead>
                <tbody>{_render_provider_contract_rows(visible_provider_contracts, can_manage_provider_contracts)}</tbody>
              </table>
            </div>
          </div>
        </section>

        {provider_profile_markup if current_page == 'providers' and selected_provider_contract is not None else ''}

        {providers_ai_result_markup if current_page == 'providers' and selected_provider_contract is not None else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'providers')}{" hidden" if not can_manage_provider_contracts or selected_provider_contract is None else ""}>
          <form id="provider-upload" class="panel section-card" method="post" action="/provider-admin-upload-document" enctype="multipart/form-data">
            <h2>Subir documento al expediente</h2>
            <p>Selecciona un documento del provider que estas viendo y subelo aqui mismo sin volver al formulario completo.</p>
            <input type="hidden" name="contract_id" value="{html.escape(str((selected_provider_contract or {}).get('contract_id', '')))}">
            <div class="field-grid">
              <label class="field">
                <span>Documento</span>
                <select name="document_name">{_provider_document_options_markup(selected_provider_contract, str(selected_provider_contract_values.get('provider_document_name', '')))}</select>
              </label>
              <label class="field">
                <span>Fecha emitido</span>
                <input class="document-date-input" name="provider_document_issued_date" value="{_field_value(selected_provider_contract_values, 'provider_document_issued_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Fecha expira</span>
                <input class="document-date-input" name="provider_document_expiration_date" value="{_field_value(selected_provider_contract_values, 'provider_document_expiration_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Archivo</span>
                <input type="file" name="provider_admin_document_file" accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx">
              </label>
            </div>
            <button type="submit">Subir al expediente</button>
          </form>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'providers')}{" hidden" if selected_provider_contract is None and not show_new_provider_form else ""}>
          {provider_document_warning}
          <form id="provider-contract-form" class="panel section-card" method="post" action="/add-provider-contract" enctype="multipart/form-data"{" hidden" if not can_manage_provider_contracts else ""}>
            <h2>Contratacion del provider / empleado</h2>
            <p>Registra BCBA, BCaBA, RBT, Mental Health o personal de oficina. Las tabs ahora separan datos generales, responsables del expediente y archivos para que recruiting, supervision y credenciales trabajen juntos.</p>
            {'<p class="helper-note"><strong>Editando expediente:</strong> este formulario cargo una contratacion ya guardada para que puedas continuarla o corregirla.</p>' if editing_provider_contract else ''}
            <div class="segmented-tabs" data-tab-group="provider-contract">
              <button class="segment active" type="button" aria-pressed="true" data-tab-target="general">General</button>
              <button class="segment" type="button" aria-pressed="false" data-tab-target="expediente">Expediente</button>
              <button class="segment" type="button" aria-pressed="false" data-tab-target="files">Files</button>
            </div>
            <div class="tab-panels">
              <section class="tab-panel" data-tab-panel="general">
                <div class="field-grid">
                  <label class="field">
                    <span>Contract ID opcional</span>
                    <input name="contract_id" value="{_field_value(provider_contract_values, 'contract_id')}" placeholder="Si existe, ayuda a actualizar el mismo expediente">
                  </label>
                  <label class="field">
                    <span>Categoria</span>
                    <select name="worker_category" data-worker-category>{_workforce_category_options_markup(str(provider_contract_values.get('worker_category', 'PROVIDER')))}</select>
                  </label>
                  <label class="field">
                    <span>Nombre</span>
                    <input name="provider_name" value="{_field_value(provider_contract_values, 'provider_name')}" placeholder="Nombre completo del provider o empleado">
                  </label>
                  <label class="field" data-workforce-group="PROVIDER"{" hidden" if str(provider_contract_values.get('worker_category', 'PROVIDER')).upper() != 'PROVIDER' else ""}>
                    <span>Provider Type</span>
                    <select name="provider_type">{_provider_type_options_markup(str(provider_contract_values.get('provider_type', 'BCBA')))}</select>
                  </label>
                  <label class="field" data-workforce-group="OFFICE"{" hidden" if str(provider_contract_values.get('worker_category', 'PROVIDER')).upper() != 'OFFICE' else ""}>
                    <span>Departamento de oficina</span>
                    <select name="office_department">{_office_department_options_markup(str(provider_contract_values.get('office_department', '')))}</select>
                  </label>
                  <label class="field">
                    <span>NPI</span>
                    <input name="provider_npi" value="{_field_value(provider_contract_values, 'provider_npi')}" placeholder="Opcional para empleados de oficina">
                  </label>
                  <label class="field">
                    <span>Lugar</span>
                    <select name="site_location" data-location-select="provider">{_location_options_markup(str(provider_contract_values.get('site_location', 'Cape Coral')))}</select>
                  </label>
                  <label class="field">
                    <span>County</span>
                    <input name="county_name" data-county-input="provider" value="{_field_value(provider_contract_values, 'county_name')}" placeholder="Lee">
                  </label>
                  <label class="field">
                    <span>Clientes asignados</span>
                    <input name="assigned_clients" value="{_field_value(provider_contract_values, 'assigned_clients')}" placeholder="Escribe nombres separados por coma">
                  </label>
                  <label class="field">
                    <span>Etapa</span>
                    <select name="contract_stage">{_contract_stage_options_markup(str(provider_contract_values.get('contract_stage', 'NEW')))}</select>
                  </label>
                  <label class="field">
                    <span>Start Date</span>
                    <input class="document-date-input" name="start_date" value="{_field_value(provider_contract_values, 'start_date')}" placeholder="MM/DD/YYYY">
                  </label>
                  <label class="field">
                    <span>Expected Start</span>
                    <input class="document-date-input" name="expected_start_date" value="{_field_value(provider_contract_values, 'expected_start_date')}" placeholder="MM/DD/YYYY">
                  </label>
                </div>
              </section>

              <section class="tab-panel" data-tab-panel="expediente" hidden>
                <div class="field-grid">
                  <label class="field">
                    <span>Recruiter / usuario asignado</span>
                    <select name="recruiter_name">{_user_select_options_markup(assignable_users, str(provider_contract_values.get('recruiter_name', '')))}</select>
                  </label>
                  <label class="field">
                    <span>Supervisor / official</span>
                    <select name="supervisor_name">{_user_select_options_markup(assignable_users, str(provider_contract_values.get('supervisor_name', '')))}</select>
                  </label>
                  <label class="field">
                    <span>Credenciales</span>
                    <select name="credentialing_owner_name">{_user_select_options_markup(assignable_users, str(provider_contract_values.get('credentialing_owner_name', '')))}</select>
                  </label>
                  <label class="field">
                    <span>Office reviewer</span>
                    <select name="office_reviewer_name">{_user_select_options_markup(assignable_users, str(provider_contract_values.get('office_reviewer_name', '')))}</select>
                  </label>
                  <label class="field">
                    <span>Inicio de credenciales</span>
                    <input class="document-date-input" name="credentialing_start_date" data-credentialing-start value="{_field_value(provider_contract_values, 'credentialing_start_date')}" placeholder="MM/DD/YYYY">
                  </label>
                  <label class="field">
                    <span>Meta de 3 meses</span>
                    <input data-credentialing-due value="{html.escape(provider_credentialing_due_preview)}" placeholder="Se calcula automatico" readonly>
                  </label>
                </div>
                <label class="field">
                  <span>Notas</span>
                  <textarea name="notes" placeholder="Seguimiento de reclutamiento, supervision, credenciales o asignacion a clientes.">{_field_value(provider_contract_values, 'notes')}</textarea>
                </label>
                <div class="provider-workflow-grid">
                  <div class="document-summary">
                    <strong>Recruiting</strong>
                    <span>Asigna el recruiter o usuario responsable para que la contratacion aparezca en su lista.</span>
                  </div>
                  <div class="document-summary">
                    <strong>Supervisor</strong>
                    <span>El supervisor u official puede revisar el avance general y recibir alertas del expediente.</span>
                  </div>
                  <div class="document-summary">
                    <strong>Credentialing</strong>
                    <span>El proceso de credenciales cuenta 90 dias y queda visible tambien en el roster de payers.</span>
                  </div>
                  <div class="document-summary">
                    <strong>Office Review</strong>
                    <span>Usalo para vincular quien revisa notas o un grupo de providers desde oficina o quality control.</span>
                  </div>
                </div>
              </section>

              <section class="tab-panel" data-tab-panel="files" hidden>
                <div class="form-section">
                  <span class="section-label">Checklist documental requerido</span>
                  <p class="module-note">Los documentos se guardan dentro del sistema y tambien se copian a OneDrive en una carpeta con el nombre del provider o empleado. Puedes manejar `Pending`, `Delivered` o `Ignored`; cuando el archivo vence, el sistema lo marca como `Expired` y avisa con anticipacion.</p>
                  <div class="document-checklist">
                    <table>
                      <thead>
                        <tr>
                          <th>Documento</th>
                          <th>Fecha emitido</th>
                          <th>Fecha expira</th>
                          <th>Estatus</th>
                          <th>Archivo</th>
                        </tr>
                      </thead>
                      <tbody>{_provider_document_checklist_markup(provider_contract_values)}</tbody>
                    </table>
                  </div>
                </div>
              </section>
            </div>
            <button type="submit">Guardar contratacion</button>
          </form>

          <article class="panel section-card"{" hidden" if not can_manage_provider_contracts else ""}>
            <h2>Document workspace</h2>
            <p>Inspirado en flujos de oficina: un expediente donde recruiting, supervision, credenciales y office review trabajan sobre la misma contratacion y el mismo checklist.</p>
            <div class="document-hub">
              <div class="document-summary">
                <strong>Document status</strong>
                <span>Usa el checklist para marcar `Pending`, `Delivered` o `Ignored`, capturar fecha emitido y fecha expira por cada requisito. Los botones de 6 meses, 1 ano, 2 anos y 5 anos ayudan a calcular vencimientos rapido.</span>
              </div>
              <div class="document-summary">
                <strong>Files</strong>
                <span>Cuando subes un archivo, el portal lo conserva y luego lo puedes abrir desde el expediente documental del provider. Tambien se copia a una carpeta con el nombre del provider.</span>
              </div>
              <div class="document-summary">
                <strong>Comments</strong>
                <span>La caja de notas del contract sirve como comentarios administrativos del onboarding y seguimiento de HR.</span>
              </div>
              <div class="document-summary">
                <strong>Expiraciones</strong>
                <span>Desde 30 dias antes del vencimiento el sistema prepara alertas para la agencia, el usuario asignado y el provider vinculado. Si el documento vence, el estatus pasa a `Expired`.</span>
              </div>
            </div>
          </article>

          <article class="panel section-card"{" hidden" if not provider_portal_user or provider_self_contract else ""}>
            <h2>Mi expediente documental</h2>
            <p>Tu usuario esta marcado como Proveedor, pero todavia no tiene un expediente vinculado. En `Usuarios`, un administrador o Recursos Humanos debe llenar el campo `Provider vinculado` con tu nombre exacto de provider.</p>
          </article>

          <article class="panel section-card"{" hidden" if not provider_portal_user or not provider_self_contract else ""}>
            <h2>Notas de mi expediente</h2>
            <p>Aqui ves las observaciones administrativas y de seguimiento que tu agencia dejo en tu expediente.</p>
            {_provider_notes_summary_markup(provider_self_contract, title='Notas visibles para tu expediente', collapsible=False)}
          </article>

          <form id="provider-self-upload" class="panel section-card" method="post" action="/provider-self-upload-document" enctype="multipart/form-data"{" hidden" if not provider_portal_user or not provider_self_contract else ""}>
            <h2>Subir mi documento</h2>
            <p>Como proveedor puedes subir tus archivos directamente al sistema. Cuando los subas, quedaran pendientes de aprobacion por Recursos Humanos y se guardaran en la carpeta del provider dentro del sistema y OneDrive.</p>
            <input type="hidden" name="contract_id" value="{html.escape(str((provider_self_contract or {}).get('contract_id', '')))}">
            <div class="field-grid">
              <label class="field">
                <span>Documento</span>
                <select name="document_name">{_provider_document_options_markup(provider_self_contract, str(provider_contract_values.get('provider_document_name', '')))}</select>
              </label>
              <label class="field">
                <span>Fecha emitido</span>
                <input class="document-date-input" name="provider_document_issued_date" value="{_field_value(provider_contract_values, 'provider_document_issued_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Fecha expira</span>
                <input class="document-date-input" name="provider_document_expiration_date" value="{_field_value(provider_contract_values, 'provider_document_expiration_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Archivo</span>
                <input type="file" name="provider_self_document_file" accept=".pdf,.png,.jpg,.jpeg,.webp,.doc,.docx">
              </label>
            </div>
            <button type="submit">Enviar documento para aprobacion</button>
          </form>
        </section>

        <section id="provider-documents" class="workspace"{_section_hidden(current_page, 'providers')}{" hidden" if selected_provider_contract is None else ""}>
          {_render_provider_document_cards([selected_provider_contract] if selected_provider_contract is not None else [], display_user, users)}
        </section>

        {agenda_page_intro_markup}

        {agenda_metrics_markup}

        {agenda_hub_markup if current_page == 'agenda' else ''}

        <section class="agenda-grid"{_section_hidden(current_page, 'agenda')}>
          <form id="agenda-form" class="panel section-card" method="post" action="/add-calendar-event">
            <h2>Agenda y asignacion de tareas</h2>
            <p>Crea eventos, deadlines o tareas y asignalos a un usuario especifico para que aparezcan en su lista de trabajo y en notificaciones.</p>
            <div class="field-grid">
              <label class="field">
                <span>Titulo</span>
                <input name="title" value="{_field_value(agenda_values, 'title')}" placeholder="Onboarding de Miguel Narvares">
              </label>
              <label class="field">
                <span>Categoria</span>
                <select name="category">{_event_category_options_markup(str(agenda_values.get('category', 'task')))}</select>
              </label>
              <label class="field">
                <span>Fecha del evento</span>
                <input name="event_date" value="{_field_value(agenda_values, 'event_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Fecha limite</span>
                <input name="due_date" value="{_field_value(agenda_values, 'due_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Asignado a</span>
                <select name="assigned_username">{_user_select_options_markup(users, str(agenda_values.get('assigned_username', '')))}</select>
              </label>
              <label class="field">
                <span>Relacionado con</span>
                <input name="related_provider" value="{_field_value(agenda_values, 'related_provider')}" placeholder="Paciente, provider o enrollment">
              </label>
            </div>
            <label class="field">
              <span>Descripcion</span>
              <textarea name="description" placeholder="Detalle de la tarea, instrucciones o seguimiento requerido.">{_field_value(agenda_values, 'description')}</textarea>
            </label>
            <label class="field">
              <span><input type="checkbox" name="notify_email"{_checked(agenda_values, 'notify_email')}> Generar alerta de email para el usuario asignado</span>
            </label>
            <button type="submit">Guardar tarea</button>
          </form>

          <article id="agenda-my-work" class="panel section-card">
            <h2>Mi lista de trabajo</h2>
            <p>Aqui ves lo que esta asignado a tu usuario. Cada tarea puede marcarse como completada desde esta misma tabla.</p>
            <div class="mini-table">
              <div class="mini-row"><strong>Pendientes</strong><span>{len(my_pending_tasks)}</span></div>
              <div class="mini-row"><strong>Total asignadas</strong><span>{len(my_tasks)}</span></div>
              <div class="mini-row"><strong>Notas personales</strong><span>{len(my_notes)}</span></div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Due</th>
                    <th>Titulo</th>
                    <th>Categoria</th>
                    <th>Asignado</th>
                    <th>Estatus</th>
                    <th>Relacionado</th>
                    <th>Accion</th>
                  </tr>
                </thead>
                <tbody>{_render_calendar_event_rows(my_tasks, display_user)}</tbody>
              </table>
            </div>
          </article>
        </section>

        <section id="supervision-center" class="panel section-card"{_section_hidden(current_page, 'agenda')}{" hidden" if not can_view_supervision_center else ""}>
          <span class="eyebrow">Supervision</span>
          <h2>Centro completo de supervision para Admin</h2>
          <p>Desde aqui puedes revisar de una vez contratacion, supervisor asignado, credencializacion y tareas vinculadas a providers.</p>
          <div class="mini-table">
            <div class="mini-row"><strong>Providers abiertos</strong><span>{len(supervision_open_contracts)}</span></div>
            <div class="mini-row"><strong>Credenciales pendientes</strong><span>{len(supervision_credential_pending)}</span></div>
            <div class="mini-row"><strong>Tareas vinculadas</strong><span>{len(supervision_related_tasks)}</span></div>
            <div class="mini-row"><strong>Expedientes totales</strong><span>{len(supervision_contracts)}</span></div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Etapa</th>
                  <th>Contratacion</th>
                  <th>Supervisor</th>
                  <th>Credencializacion</th>
                  <th>Clientes</th>
                  <th>Lugar</th>
                </tr>
              </thead>
              <tbody>{_render_supervision_rows(supervision_contracts, display_user, users)}</tbody>
            </table>
          </div>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'agenda')}>
          <article id="agenda-calendar" class="panel section-card">
            <h2>Calendario del mes</h2>
            <p>Vista mensual de deadlines, onboarding, meetings o follow ups registrados en la agencia activa.</p>
            {_render_month_calendar(calendar_events)}
          </article>

          <article class="panel section-card">
            <h2>Agenda general de la agencia</h2>
            <p>Todos los eventos activos para que coordinacion, billing y recursos humanos compartan seguimiento.</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Due</th>
                    <th>Titulo</th>
                    <th>Categoria</th>
                    <th>Asignado</th>
                    <th>Estatus</th>
                    <th>Relacionado</th>
                    <th>Accion</th>
                  </tr>
                </thead>
                <tbody>{_render_calendar_event_rows(calendar_events, display_user)}</tbody>
              </table>
            </div>
          </article>
        </section>

        <section id="agenda-notes" class="dual-grid"{_section_hidden(current_page, 'agenda')}>
          <form class="panel section-card" method="post" action="/add-note">
            <h2>Notas de trabajo</h2>
            <p>Guarda notas personales de seguimiento para tu dia a dia dentro del portal.</p>
            <label class="field">
              <span>Titulo</span>
              <input name="title" value="{_field_value(note_values, 'title')}" placeholder="Pendientes de hoy">
            </label>
            <label class="field">
              <span>Nota</span>
              <textarea name="body" placeholder="Ideas, seguimiento, pendientes o informacion de contacto.">{_field_value(note_values, 'body')}</textarea>
            </label>
            <button type="submit">Guardar nota</button>
          </form>

          <article class="panel section-card">
            <h2>Mis notas guardadas</h2>
            <p>Estas notas son privadas para tu usuario dentro de la agencia activa.</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Titulo</th>
                    <th>Contenido</th>
                  </tr>
                </thead>
                <tbody>{_render_note_rows(my_notes)}</tbody>
              </table>
            </div>
          </article>
        </section>

        {notifications_page_intro_markup}

        {notifications_metrics_markup}

        {notifications_hub_markup if current_page == 'notifications' else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'notifications')}>
          <form id="notifications-compose" class="panel section-card" method="post" action="/compose-outlook-email">
            <h2>Enviar email desde Outlook</h2>
            <p>Redacta desde aqui y el sistema intentara usar tu Outlook de Microsoft instalado en esta computadora. Si tu email de perfil coincide con una cuenta de Outlook, lo usa primero; si no, usa la cuenta default del Outlook local.</p>
            <div class="field-grid">
              <label class="field">
                <span>Destinatario</span>
                <input name="recipient_email" value="{_field_value(email_values, 'recipient_email')}" placeholder="destino@agencia.com">
              </label>
              <label class="field">
                <span>Nombre</span>
                <input name="recipient_label" value="{_field_value(email_values, 'recipient_label')}" placeholder="Nombre del destinatario">
              </label>
              <label class="field" style="grid-column: 1 / -1;">
                <span>Asunto</span>
                <input name="subject" value="{_field_value(email_values, 'subject')}" placeholder="Asunto del email">
              </label>
            </div>
            <label class="field">
              <span>Mensaje</span>
              <textarea name="message" placeholder="Escribe aqui el contenido que saldra desde Outlook.">{_field_value(email_values, 'message')}</textarea>
            </label>
            <label class="field">
              <span><input type="checkbox" name="save_to_notifications"{_checked(email_values, 'save_to_notifications')}> Guardar tambien en el centro de notificaciones</span>
            </label>
            <div class="quick-links">
              <button type="submit" name="email_mode" value="draft">Crear draft en Outlook</button>
              <button type="submit" name="email_mode" value="send">Enviar con Outlook</button>
            </div>
          </form>

          <article id="notifications-center" class="panel section-card">
            <h2>Centro de notificaciones</h2>
            <p>Aqui quedan las alertas listas para email cuando cambian elegibilidad, claims, remesas, enrollments, contrataciones, tareas asignadas o bienvenida de usuarios. Ahora tambien puedes sacarlas por Outlook directamente desde esta pantalla.</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fecha</th>
                    <th>Categoria</th>
                    <th>Asunto</th>
                    <th>Enviar a</th>
                    <th>Email status</th>
                    <th>Accion</th>
                    <th>Relacionado</th>
                    <th>Mensaje</th>
                  </tr>
                </thead>
                <tbody>{_render_notification_rows(notifications)}</tbody>
              </table>
            </div>
          </article>
        </section>

        {users_page_intro_markup}

        {users_metrics_markup}

        {users_hub_markup if current_page == 'users' else ''}
        {users_operations_hub_markup if current_page == 'users' else ''}

        <section class="dual-grid"{_section_hidden(current_page, 'users')}>
          <form id="users-directory-form" class="panel section-card" method="post" action="/add-user" enctype="multipart/form-data">
            <h2>Alta de usuarios</h2>
            <p>Crea usuarios adicionales, asignales rango y enviales su primer perfil para que les llegue la bienvenida por email cuando tengas el correo configurado.</p>
            <div class="field-grid">
              <label class="field">
                <span>Nombre completo</span>
                <input name="full_name" value="{_field_value(user_values, 'full_name')}">
              </label>
              <label class="field">
                <span>Username</span>
                <input name="username" value="{_field_value(user_values, 'username')}">
              </label>
              <label class="field">
                <span>Password</span>
                <input type="password" name="password">
              </label>
              <label class="field">
                <span>Rango</span>
                <select name="role">{_role_options_markup(str(user_values.get('role', 'MANAGER')))}</select>
              </label>
            </div>
            <div class="field-grid">
              <label class="field">
                <span>Email</span>
                <input name="email" value="{_field_value(user_values, 'email')}" placeholder="usuario@agencia.com">
              </label>
              <label class="field">
                <span>Telefono</span>
                <input name="phone" value="{_field_value(user_values, 'phone')}" placeholder="(555) 555-1212">
              </label>
              <label class="field">
                <span>Lugar base</span>
                <select name="site_location" data-location-select="user">{_location_options_markup(str(user_values.get('site_location', 'Cape Coral')))}</select>
              </label>
              <label class="field">
                <span>County</span>
                <input name="county_name" data-county-input="user" value="{_field_value(user_values, 'county_name')}" placeholder="Lee">
              </label>
              <label class="field">
                <span>Puesto</span>
                <input name="job_title" value="{_field_value(user_values, 'job_title')}" placeholder="Billing Specialist">
              </label>
              <label class="field">
                <span>Color de perfil</span>
                <input type="color" name="profile_color" value="{_field_value(user_values, 'profile_color') or '#0d51b8'}">
              </label>
            </div>
            <div class="field-grid">
              <label class="field">
                <span>Provider vinculado</span>
                <input name="linked_provider_name" value="{_field_value(user_values, 'linked_provider_name')}" placeholder="Usalo cuando el rango sea BCBA, BCaBA o RBT">
              </label>
            </div>
            <label class="field">
              <span>Bio / notas del perfil</span>
              <textarea name="bio" placeholder="Breve descripcion del usuario, equipo o rol principal.">{_field_value(user_values, 'bio')}</textarea>
            </label>
            <label class="field">
              <span>Foto del perfil</span>
              <input type="file" name="avatar_file" accept=".png,.jpg,.jpeg,.webp">
            </label>
            <div class="field-grid permissions-grid">
              {_module_permissions_markup(user_values)}
            </div>
            <div class="field-grid">
              <label class="field">
                <span><input type="checkbox" name="active"{_checked(user_values, 'active')}> Usuario activo</span>
              </label>
              <label class="field">
                <span><input type="checkbox" name="send_welcome_email"{_checked(user_values, 'send_welcome_email')}> Enviar bienvenida y perfil inicial</span>
              </label>
            </div>
            <button type="submit">Guardar usuario</button>
          </form>

          <article id="users-roles" class="panel section-card">
            <h2>Rangos disponibles</h2>
            <div class="mini-table">
              <div class="mini-row"><strong>Admin</strong><span>Control total del sistema, seguridad, finanzas y configuracion.</span></div>
              <div class="mini-row"><strong>Manager / HR / Recruiter / Credentialing</strong><span>Accesos operativos segun onboarding, documentos y pipeline del provider.</span></div>
              <div class="mini-row"><strong>BCBA / BCaBA / RBT</strong><span>Trabajo clinico, clientes y sesiones asignadas, sin montos por defecto.</span></div>
              <div class="mini-row"><strong>Office / Billing</strong><span>Schedule, billing support, claims y permisos financieros segun el rango.</span></div>
            </div>
          </article>
        </section>

        <section id="users-directory" class="panel section-card" data-skip-auto-collapsible="1"{_section_hidden(current_page, 'users')}>
          <h2>Usuarios registrados</h2>
          <p>El equipo ahora aparece en formato directorio visual para que sea mas facil revisar personal, rol, base y estado activo.</p>
          {users_directory_toolbar_markup}
          <div class="directory-view card-view" data-directory-panel="users" data-view="card">
            {users_directory_markup}
          </div>
          <div class="directory-view table-view" data-directory-panel="users" data-view="table" hidden>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Nombre</th>
                    <th>Username</th>
                    <th>Puesto</th>
                    <th>Email</th>
                    <th>Telefono</th>
                    <th>Lugar</th>
                    <th>County</th>
                    <th>Rango</th>
                    <th>Provider vinculado</th>
                    <th>Estatus</th>
                    <th>MFA</th>
                    <th>Acceso</th>
                  </tr>
                </thead>
                <tbody>{_render_user_rows(users)}</tbody>
              </table>
            </div>
          </div>
        </section>

        <section id="users-audit" class="panel section-card"{_section_hidden(current_page, 'users')}>
          <h2>Auditoria de usuarios</h2>
          <p>Aqui quedan las altas de usuarios y los movimientos principales del acceso.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Usuario</th>
                  <th>Accion</th>
                  <th>Nombre</th>
                  <th>Username</th>
                  <th>Categoria</th>
                  <th>Detalle</th>
                </tr>
              </thead>
              <tbody>{_render_system_audit_rows(user_audit_logs, 'Todavia no hay auditoria de usuarios.')}</tbody>
            </table>
          </div>
        </section>

        {security_page_intro_markup}

        {security_metrics_markup}

        <section class="panel section-card"{_section_hidden(current_page, 'security')}>
          <span class="eyebrow">Centro de Configuracion</span>
          <h2>Configura accesos, tiempos, billing y comportamiento general del portal</h2>
          <p>Esta zona centraliza los ajustes globales del sistema. Desde aqui puedes cambiar reglas operativas y tambien entrar a los modulos donde se administran agencias, usuarios, claims, clientes, providers y notificaciones.</p>
          <div class="tool-grid hub-grid">
            <a class="tool-tile" href="{_page_href('agencies')}"{_nav_hidden(allowed_pages, 'agencies')}>
              <span class="tool-icon">AG</span>
              <strong>Agencias</strong>
              <p>Cambia agencia activa, logos, email de contacto y datos operativos.</p>
              <span>{len(agencies)} agencia(s)</span>
            </a>
            <a class="tool-tile" href="{_page_href('users')}"{_nav_hidden(allowed_pages, 'users')}>
              <span class="tool-icon">US</span>
              <strong>Usuarios</strong>
              <p>Ajusta rangos, permisos por modulo y perfiles del equipo.</p>
              <span>{len(users)} usuario(s)</span>
            </a>
            <a class="tool-tile" href="{_page_href('providers')}"{_nav_hidden(allowed_pages, 'providers')}>
              <span class="tool-icon">PR</span>
              <strong>Providers</strong>
              <p>Configura expedientes, documentos y flujo de contratacion.</p>
              <span>{len(provider_contracts)} expediente(s)</span>
            </a>
            {""
            if not can_view_supervision_center
            else f'''
            <a class="tool-tile" href="{_page_href('agenda')}#supervision-center"{_nav_hidden(allowed_pages, 'agenda')}>
              <span class="tool-icon">SP</span>
              <strong>Supervision</strong>
              <p>Abre el control ejecutivo de supervisors, credenciales y agenda de providers.</p>
              <span>{len(supervision_related_tasks)} tarea(s) vinculada(s)</span>
            </a>
            '''}
            <a class="tool-tile" href="#settings-clients">
              <span class="tool-icon">PT</span>
              <strong>Clientes</strong>
              <p>Entra a base de clientes, workflow center, elegibilidad y documentos del cliente.</p>
              <span>{active_clients} activo(s)</span>
            </a>
            <a class="tool-tile" href="{_page_href('claims')}"{_nav_hidden(allowed_pages, 'claims')}>
              <span class="tool-icon">83</span>
              <strong>Claims</strong>
              <p>Ajusta batch diario, unidad ABA y flujo operativo del claim.</p>
              <span>{claim_summary.get('total', 0)} claim(s)</span>
            </a>
            <a class="tool-tile" href="{_page_href('dashboard')}#integrations"{_nav_hidden(allowed_pages, 'dashboard')}>
              <span class="tool-icon">IN</span>
              <strong>Integraciones</strong>
              <p>Abre clearinghouses, conectores y bloques tecnicos del portal.</p>
              <span>Dashboard tecnico</span>
            </a>
            <a class="tool-tile" href="{_page_href('notifications')}"{_nav_hidden(allowed_pages, 'notifications')}>
              <span class="tool-icon">NT</span>
              <strong>Alertas</strong>
              <p>Revisa colas de email y mensajes generados por el sistema.</p>
              <span>{len(notifications)} notificacion(es)</span>
            </a>
          </div>
        </section>

        {clients_configuration_markup}

        <section class="profile-shell"{_section_hidden(current_page, 'security')}>
          <article class="panel section-card">
            <span class="eyebrow">Configuracion personal</span>
            <div class="profile-header">
              <div class="profile-avatar">{avatar_markup}</div>
              <div class="stack-grid">
                <div>
                  <h2>{html.escape(current_user_name or 'Perfil de usuario')}</h2>
                  <p>Resumen de tu perfil, tu rol y tus accesos dentro del centro de configuracion del portal.</p>
                </div>
                <div class="segmented-tabs">
                  <span class="segment active">Personal</span>
                  <span class="segment">Professional</span>
                  <span class="segment">Resources</span>
                </div>
              </div>
            </div>
            <div class="mini-table">
              <div class="mini-row"><strong>Email</strong><span>{contact_email or 'Pendiente'}</span></div>
              <div class="mini-row"><strong>Telefono</strong><span>{contact_phone or 'Pendiente'}</span></div>
              <div class="mini-row"><strong>Lugar</strong><span>{html.escape(str(security_profile.get('site_location', '')) or 'Pendiente')}</span></div>
              <div class="mini-row"><strong>County</strong><span>{html.escape(str(security_profile.get('county_name', '')) or 'Pendiente')}</span></div>
              <div class="mini-row"><strong>Puesto</strong><span>{html.escape(str(security_profile.get('job_title', '')) or 'Pendiente')}</span></div>
              <div class="mini-row"><strong>Role</strong><span>{html.escape(current_user_role_label)}</span></div>
            </div>
          </article>

          <article class="tool-grid">
            <a class="tool-tile" href="{_page_href('agenda')}"{_nav_hidden(allowed_pages, 'agenda')}>
              <span class="tool-icon">CA</span>
              <strong>Calendar</strong>
              <p>Revisa tu lista de trabajo y deadlines asignados.</p>
              <span>{len(my_pending_tasks)} pendiente(s)</span>
            </a>
            <a class="tool-tile" href="{_page_href('providers')}"{_nav_hidden(allowed_pages, 'providers')}>
              <span class="tool-icon">DC</span>
              <strong>Documents</strong>
              <p>Abre expedientes de providers y documentos archivados.</p>
              <span>{len(provider_contracts)} expediente(s)</span>
            </a>
            <a class="tool-tile" href="#settings-clients">
              <span class="tool-icon">PT</span>
              <strong>Clients</strong>
              <p>Base, workflow y herramientas administrativas del modulo de clientes.</p>
              <span>{active_clients} activo(s)</span>
            </a>
            <a class="tool-tile" href="{_page_href('aba_notes')}"{_nav_hidden(allowed_pages, 'aba_notes')}>
              <span class="tool-icon">NB</span>
              <strong>Notas ABA</strong>
              <p>Scheduler ABA, notas semanales y workflow de supervision.</p>
              <span>{len(aba_appointments)} sesion(es)</span>
            </a>
            <a class="tool-tile" href="{_page_href('claims')}#claims837"{_nav_hidden(allowed_pages, 'claims')}>
              <span class="tool-icon">83</span>
              <strong>837P Prep</strong>
              <p>Abre el preparador del claim y revisa totales antes de transmitir.</p>
              <span>{claim_summary.get('queued', 0)} en batch</span>
            </a>
            <a class="tool-tile" href="{_page_href('notifications')}"{_nav_hidden(allowed_pages, 'notifications')}>
              <span class="tool-icon">NT</span>
              <strong>Notes & Alerts</strong>
              <p>Notas personales y alertas listas para email.</p>
              <span>{len(my_notes)} nota(s)</span>
            </a>
          </article>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'security')}>
          <form class="panel section-card" method="post" action="/update-profile" enctype="multipart/form-data">
            <h2>Mi perfil</h2>
            <p>Personaliza tu perfil, sube tu foto y deja listos los datos de contacto para trabajo diario, alertas y futuras integraciones.</p>
            <div class="profile-header">
              <div class="profile-avatar">{avatar_markup}</div>
              <div class="mini-table">
                <div class="mini-row"><strong>Usuario</strong><span>{html.escape(str(security_profile.get('username', '')))}</span></div>
                <div class="mini-row"><strong>Rango</strong><span>{html.escape(current_user_role_label)}</span></div>
                <div class="mini-row"><strong>Puesto</strong><span>{html.escape(str(security_profile.get('job_title', '')) or 'Sin puesto')}</span></div>
              </div>
            </div>
            <div class="segmented-tabs">
              <span class="segment active">Personal</span>
              <span class="segment">Professional</span>
            </div>
            <div class="field-grid">
              <label class="field">
                <span>Nombre completo</span>
                <input name="full_name" value="{html.escape(str(security_profile.get('full_name', '')))}">
              </label>
              <label class="field">
                <span>Email</span>
                <input name="email" value="{contact_email}" placeholder="usuario@agencia.com">
              </label>
              <label class="field">
                <span>Telefono</span>
                <input name="phone" value="{contact_phone}" placeholder="(555) 555-1212">
              </label>
              <label class="field">
                <span>Lugar base</span>
                <select name="site_location" data-location-select="profile">{_location_options_markup(str(security_profile.get('site_location', '')))}</select>
              </label>
              <label class="field">
                <span>County</span>
                <input name="county_name" data-county-input="profile" value="{html.escape(str(security_profile.get('county_name', '')))}" placeholder="Lee">
              </label>
              <label class="field">
                <span>Puesto</span>
                <input name="job_title" value="{html.escape(str(security_profile.get('job_title', '')))}">
              </label>
              <label class="field">
                <span>Color del perfil</span>
                <input type="color" name="profile_color" value="{html.escape(str(security_profile.get('profile_color', '#0d51b8')))}">
              </label>
              <label class="field">
                <span>Foto de perfil</span>
                <input type="file" name="avatar_file" accept=".png,.jpg,.jpeg,.webp">
              </label>
            </div>
            <label class="field">
              <span>Bio</span>
              <textarea name="bio" placeholder="Descripcion corta, especialidad o notas internas del perfil.">{html.escape(str(security_profile.get('bio', '')))}</textarea>
            </label>
            <div class="quick-links">
              <a class="quick-link" href="mailto:{html.escape(raw_contact_email or 'billing@example.com')}">Email</a>
              <a class="quick-link" href="tel:{html.escape(contact_phone_href or '5555551212')}">Llamar</a>
            </div>
            <p class="helper-note">El boton de llamada abre la aplicacion configurada en tu equipo. Para llamadas entrantes reales dentro del sistema luego conectamos Twilio o Microsoft Teams Phone.</p>
            <button type="submit">Guardar perfil</button>
          </form>

          <form class="panel section-card" method="post" action="/change-password">
            <h2>Cambiar password</h2>
            <p>Usa esta seccion para actualizar tu password y fortalecer el acceso a tu cuenta.</p>
            <label class="field">
              <span>Password actual</span>
              <input type="password" name="current_password">
            </label>
            <label class="field">
              <span>Nuevo password</span>
              <input type="password" name="new_password">
            </label>
            <label class="field">
              <span>Confirmar password</span>
              <input type="password" name="confirm_password">
            </label>
            <button type="submit">Cambiar password</button>
          </form>

          <article class="panel section-card">
            <h2>Estado de seguridad</h2>
            <div class="mini-table">
              <div class="mini-row"><strong>MFA</strong><span>{'Activo' if security_profile.get('mfa_enabled') else 'No configurado'}</span></div>
              <div class="mini-row"><strong>Ultimo login</strong><span>{html.escape(str(security_profile.get('last_login_at', '')) or 'Sin registro')}</span></div>
              <div class="mini-row"><strong>Intentos</strong><span>{html.escape(str(security_profile.get('failed_attempts', 0)))}</span></div>
              <div class="mini-row"><strong>Bloqueo</strong><span>{html.escape(str(security_profile.get('locked_until', '')) or 'No')}</span></div>
              <div class="mini-row"><strong>Sesion</strong><span>{session_timeout_minutes} min sin actividad.</span></div>
            </div>
          </article>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'security')}>
          <form class="panel section-card" method="post" action="/start-mfa-setup">
            <h2>Activar MFA / 2FA</h2>
            <p>Primero valida tu password actual para generar la llave del autenticador.</p>
            <label class="field">
              <span>Password actual</span>
              <input type="password" name="current_password">
            </label>
            <button type="submit">Generar llave MFA</button>
          </form>

          <form class="panel section-card" method="post" action="/confirm-mfa-setup">
            <h2>Confirmar MFA</h2>
            <p>Si ya generaste la llave, copiala en tu app de autenticacion y escribe el codigo de 6 digitos.</p>
            <label class="field">
              <span>Llave secreta</span>
              <input value="{_field_value({'secret': str(security_profile.get('mfa_pending_secret', ''))}, 'secret')}" readonly>
            </label>
            <label class="field">
              <span>URI</span>
              <textarea readonly>{html.escape(str(security_profile.get('mfa_setup_uri', '')))}</textarea>
            </label>
            <label class="field">
              <span>Codigo de 6 digitos</span>
              <input name="mfa_code" maxlength="6" inputmode="numeric">
            </label>
            <button type="submit">Activar MFA</button>
          </form>
        </section>

        <section class="panel section-card"{_section_hidden(current_page, 'security')}>
          <h2>Desactivar MFA</h2>
          <p>Solo usa esta opcion si vas a cambiar de app autenticadora o si perdiste acceso al segundo factor.</p>
          <form class="table-action-form" method="post" action="/disable-mfa">
            <label class="field">
              <span>Password actual</span>
              <input type="password" name="current_password">
            </label>
            <button type="submit">Desactivar MFA</button>
          </form>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'security')}>
          <article class="panel section-card">
            <h2>Recuperacion de password</h2>
            <p>Desde la pantalla de login, cada usuario puede generar un codigo temporal para restablecer su password.</p>
            <div class="mini-table">
              <div class="mini-row"><strong>Ruta</strong><span><a href="/recover-password">/recover-password</a></span></div>
              <div class="mini-row"><strong>Vigencia</strong><span>{password_reset_minutes} minutos por codigo.</span></div>
              <div class="mini-row"><strong>Demo</strong><span>Mientras conectamos email real, el sistema tambien deja rastro en notificaciones.</span></div>
            </div>
          </article>

          <article class="panel section-card">
            <h2>Expiracion de sesion</h2>
            <div class="mini-table">
              <div class="mini-row"><strong>Sesion principal</strong><span>Se vence a los {session_timeout_minutes} minutos de inactividad.</span></div>
              <div class="mini-row"><strong>Confirmacion MFA</strong><span>Se vence a los {mfa_timeout_minutes} minutos.</span></div>
              <div class="mini-row"><strong>Resultado</strong><span>El usuario debera volver a entrar si la sesion expira.</span></div>
              <div class="mini-row"><strong>Alertas</strong><span>Las tareas asignadas y la bienvenida de usuarios se encolan en Notificaciones para email real mas adelante.</span></div>
            </div>
          </article>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'security')}{" hidden" if not can_manage_system_config else ""}>
          <form id="security-global-config" class="panel section-card" method="post" action="/save-system-config">
            <h2>Configuracion global del portal</h2>
            <p>Controla el comportamiento general del sistema sin tocar el codigo: pagina inicial, tiempos de sesion, unidad ABA y calendario de elegibilidad.</p>
            <div class="field-grid">
              <label class="field">
                <span>Nombre del portal</span>
                <input name="portal_label" value="{_field_value(system_config_values, 'portal_label')}" placeholder="Blue Hope Suite">
              </label>
              <label class="field">
                <span>Pagina inicial despues del login</span>
                <select name="default_landing_page">{_page_options_markup(str(system_config_values.get('default_landing_page', 'dashboard')))}</select>
              </label>
              <label class="field">
                <span>Minutos por unidad ABA</span>
                <input name="billing_unit_minutes" value="{_field_value(system_config_values, 'billing_unit_minutes')}" placeholder="15">
              </label>
              <label class="field">
                <span>Dias de corrida de elegibilidad</span>
                <input name="eligibility_run_days" value="{_field_value(system_config_values, 'eligibility_run_days')}" placeholder="1, 15">
              </label>
              <label class="field">
                <span>Frecuencia del scheduler</span>
                <input name="eligibility_check_interval_hours" value="{_field_value(system_config_values, 'eligibility_check_interval_hours')}" placeholder="6">
              </label>
            </div>
            <button type="submit">Guardar configuracion global</button>
          </form>

          <form id="security-access-policy" class="panel section-card" method="post" action="/save-system-config">
            <h2>Politicas de acceso y seguridad</h2>
            <p>Ajusta expiracion de sesion, ventana MFA, recuperacion de password e intentos antes del bloqueo.</p>
            <div class="field-grid">
              <label class="field">
                <span>Sesion principal (min)</span>
                <input name="session_timeout_minutes" value="{_field_value(system_config_values, 'session_timeout_minutes')}" placeholder="30">
              </label>
              <label class="field">
                <span>Sesion MFA (min)</span>
                <input name="mfa_timeout_minutes" value="{_field_value(system_config_values, 'mfa_timeout_minutes')}" placeholder="10">
              </label>
              <label class="field">
                <span>Recuperacion password (min)</span>
                <input name="password_reset_minutes" value="{_field_value(system_config_values, 'password_reset_minutes')}" placeholder="30">
              </label>
              <label class="field">
                <span>Intentos antes de bloqueo</span>
                <input name="lockout_attempts" value="{_field_value(system_config_values, 'lockout_attempts')}" placeholder="5">
              </label>
              <label class="field">
                <span>Duracion del bloqueo (min)</span>
                <input name="lockout_minutes" value="{_field_value(system_config_values, 'lockout_minutes')}" placeholder="15">
              </label>
            </div>
            <button type="submit">Guardar politicas</button>
          </form>
        </section>

        <section class="panel section-card"{_section_hidden(current_page, 'security')}>
          <h2>Resumen actual de configuracion</h2>
          <div class="mini-table">
            <div class="mini-row"><strong>Portal</strong><span>{html.escape(portal_label)}</span></div>
            <div class="mini-row"><strong>Pagina inicial</strong><span>{html.escape(default_landing_page_label)}</span></div>
            <div class="mini-row"><strong>Sesion principal</strong><span>{session_timeout_minutes} min</span></div>
            <div class="mini-row"><strong>Sesion MFA</strong><span>{mfa_timeout_minutes} min</span></div>
            <div class="mini-row"><strong>Recuperacion password</strong><span>{password_reset_minutes} min</span></div>
            <div class="mini-row"><strong>Bloqueo</strong><span>{system_config.get('lockout_attempts', 5)} intento(s) / {system_config.get('lockout_minutes', 15)} min</span></div>
            <div class="mini-row"><strong>Unidad ABA</strong><span>{billing_unit_minutes} min</span></div>
            <div class="mini-row"><strong>Elegibilidad</strong><span>Dias {html.escape(eligibility_run_days_label)} | scheduler cada {eligibility_interval_hours} hora(s)</span></div>
          </div>
          <p class="helper-note"{" hidden" if can_manage_system_config else ""}>Tu perfil puede revisar la configuracion, pero solo Admin o General pueden cambiar estos ajustes globales.</p>
        </section>

        <section class="panel section-card"{_section_hidden(current_page, 'security')}>
          <h2>Auditoria de seguridad</h2>
          <p>Bitacora de cambios de password, MFA, recuperacion, bloqueos e inicios/cierres de sesion.</p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Usuario</th>
                  <th>Accion</th>
                  <th>Nombre</th>
                  <th>Username</th>
                  <th>Categoria</th>
                  <th>Detalle</th>
                </tr>
              </thead>
              <tbody>{_render_system_audit_rows(security_audit_logs, 'Todavia no hay auditoria de seguridad.')}</tbody>
            </table>
          </div>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'security')}{" hidden" if not can_manage_document_templates else ""}>
          <form id="client-document-config" class="panel section-card" method="post" action="/save-document-config">
            <h2>Configuracion de documentos de clientes</h2>
            <p>Aqui puedes anadir, quitar o reordenar los documentos requeridos del expediente del cliente para la agencia activa. Usa una linea por documento. Los cambios aplican al roster actual y a los nuevos clientes de esta agencia.</p>
            <input type="hidden" name="document_type" value="client">
            <label class="field">
              <span>Lista de documentos</span>
              <textarea name="document_names" placeholder="Un documento por linea.">{html.escape(chr(10).join(client_required_documents))}</textarea>
            </label>
            <button type="submit">Guardar lista de clientes</button>
          </form>

          <form class="panel section-card" method="post" action="/save-document-config">
            <h2>Configuracion de documentos de providers</h2>
            <p>Administra desde aqui la lista oficial de documentos de contratacion del provider para la agencia activa. Usa una linea por documento. Desde esta configuracion puedes agregar o quitar requisitos cuando cambie tu proceso de Recursos Humanos o cuando una agencia pida algo distinto.</p>
            <input type="hidden" name="document_type" value="provider">
            <label class="field">
              <span>Lista de documentos</span>
              <textarea name="document_names" placeholder="Un documento por linea.">{html.escape(chr(10).join(provider_required_documents))}</textarea>
            </label>
            <button type="submit">Guardar lista de providers</button>
          </form>
        </section>

        <section class="dual-grid"{_section_hidden(current_page, 'eligibility')}>
          <form id="eligibility-roster" class="panel section-card" method="post" action="/add-roster-entry">
            <h2>Lista automatica de elegibilidad</h2>
            <p>Los pacientes que esten en esta lista se revisan automaticamente los dias 1 y 15 de cada mes mientras permanezcan activos en el roster.</p>
            <div class="field-grid">
              <label class="field">
                <span>Payer ID</span>
                <input name="payer_id" value="{_field_value(roster_values, 'payer_id')}">
              </label>
              <label class="field">
                <span>Provider NPI</span>
                <input name="provider_npi" value="{_field_value(roster_values, 'provider_npi')}">
              </label>
              <label class="field">
                <span>Member ID</span>
                <input name="member_id" value="{_field_value(roster_values, 'member_id')}">
              </label>
              <label class="field">
                <span>First Name</span>
                <input name="patient_first_name" value="{_field_value(roster_values, 'patient_first_name')}">
              </label>
              <label class="field">
                <span>Last Name</span>
                <input name="patient_last_name" value="{_field_value(roster_values, 'patient_last_name')}">
              </label>
              <label class="field">
                <span>Fecha nacimiento</span>
                <input name="patient_birth_date" value="{_field_value(roster_values, 'patient_birth_date')}" placeholder="MM/DD/YYYY">
              </label>
              <label class="field">
                <span>Fecha servicio</span>
                <input name="service_date" value="{_field_value(roster_values, 'service_date')}" placeholder="MM/DD/YYYY">
              </label>
            </div>
            <button type="submit">Agregar a lista automatica</button>
          </form>
        </section>

        <section class="roadmap"{_section_hidden(current_page, 'eligibility')}>
          <article id="eligibility-history" class="panel section-card">
            <h2>Roster de elegibilidad</h2>
            <p>Este panel muestra la lista que se revalida automaticamente en las fechas programadas.</p>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Paciente</th>
                    <th>Member ID</th>
                    <th>Payer ID</th>
                    <th>Ultimo resultado</th>
                    <th>Ultima revision</th>
                    <th>Proxima corrida</th>
                    <th>Estatus</th>
                  </tr>
                </thead>
                <tbody>{_render_roster_rows(roster_entries)}</tbody>
              </table>
            </div>
          </article>
        </section>

        <section class="panel roadmap" id="integrations"{_section_hidden(current_page, 'dashboard')}>
          <article class="section-card">
            <h2>Proximos modulos para produccion</h2>
            <p>Este dashboard ya deja lista la base visual y operativa para crecer a una aplicacion de facturacion completa.</p>
            <div class="mini-table">
              <div class="mini-row"><strong>Clearinghouse</strong><span>Integracion real con Availity, Office Ally, Waystar u otro canal de envio.</span></div>
              <div class="mini-row"><strong>Pacientes</strong><span>Expediente administrativo con datos de cobertura, plan, autorizaciones y seguimiento.</span></div>
              <div class="mini-row"><strong>Pagos</strong><span>Control de saldo, diferencias, ERA, cobro secundario y reportes administrativos.</span></div>
            </div>
          </article>
          <article class="section-card">
            <h2>Identidad de marca</h2>
            <p>La interfaz ya usa la identidad de Blue Hope y acepta el logo original automaticamente si el archivo se guarda en la carpeta de assets.</p>
            <div class="mini-table">
              <div class="mini-row"><strong>Ruta esperada</strong><span><code>assets/blue-hope-logo.png</code></span></div>
              <div class="mini-row"><strong>Alternativas</strong><span><code>logo.png</code>, <code>.jpg</code>, <code>.jpeg</code>, <code>.webp</code> o <code>.svg</code></span></div>
              <div class="mini-row"><strong>Resultado</strong><span>Cuando exista el archivo, el portal lo mostrara en lugar del logo vectorial de respaldo.</span></div>
            </div>
          </article>
        </section>

        <p class="footer-note">{html.escape(BRAND_NAME)} corre localmente en tu computadora. El siguiente paso para produccion es conectar pagadores reales, autenticacion y base de datos.</p>
      </div>
    </main>
  </div>
  <script>
    (function() {{
      function parseUserDate(value) {{
        var clean = String(value || "").trim();
        var match = clean.match(/^([0-9]{{2}})[/]([0-9]{{2}})[/]([0-9]{{4}})$/);
        if (!match) {{
          return null;
        }}
        var month = Number(match[1]);
        var day = Number(match[2]);
        var year = Number(match[3]);
        var parsed = new Date(year, month - 1, day);
        if (
          parsed.getFullYear() !== year ||
          parsed.getMonth() !== month - 1 ||
          parsed.getDate() !== day
        ) {{
          return null;
        }}
        return parsed;
      }}

      function formatUserDate(dateValue) {{
        var month = String(dateValue.getMonth() + 1).padStart(2, "0");
        var day = String(dateValue.getDate()).padStart(2, "0");
        var year = String(dateValue.getFullYear());
        return month + "/" + day + "/" + year;
      }}

      function addMonths(baseDate, amount) {{
        var originalDay = baseDate.getDate();
        var result = new Date(baseDate.getFullYear(), baseDate.getMonth(), 1);
        result.setMonth(result.getMonth() + amount);
        var lastDay = new Date(result.getFullYear(), result.getMonth() + 1, 0).getDate();
        result.setDate(Math.min(originalDay, lastDay));
        return result;
      }}

      function addYears(baseDate, amount) {{
        return addMonths(baseDate, amount * 12);
      }}

      function openDatePicker(targetInput) {{
        if (!targetInput) {{
          return;
        }}
        var picker = document.createElement("input");
        picker.type = "date";
        picker.style.position = "fixed";
        picker.style.opacity = "0";
        picker.style.pointerEvents = "none";
        picker.style.left = "-9999px";
        picker.style.top = "-9999px";
        var baseDate = parseUserDate(targetInput.value) || new Date();
        picker.value = [
          String(baseDate.getFullYear()).padStart(4, "0"),
          String(baseDate.getMonth() + 1).padStart(2, "0"),
          String(baseDate.getDate()).padStart(2, "0"),
        ].join("-");
        picker.addEventListener("change", function() {{
          if (picker.value) {{
            var parts = picker.value.split("-");
            targetInput.value = parts[1] + "/" + parts[2] + "/" + parts[0];
          }}
          picker.remove();
        }});
        picker.addEventListener("blur", function() {{
          window.setTimeout(function() {{
            if (picker.isConnected) {{
              picker.remove();
            }}
          }}, 150);
        }});
        document.body.appendChild(picker);
        if (typeof picker.showPicker === "function") {{
          picker.showPicker();
        }} else {{
          picker.focus();
          picker.click();
        }}
      }}

      function shouldAttachCalendar(input) {{
        if (!input || input.tagName !== "INPUT") {{
          return false;
        }}
        var placeholder = String(input.getAttribute("placeholder") || "").trim().toUpperCase();
        var name = String(input.getAttribute("name") || "").trim().toLowerCase();
        return (
          placeholder === "MM/DD/YYYY" ||
          input.classList.contains("document-date-input") ||
          name.indexOf("date") !== -1 ||
          name.indexOf("dob") !== -1 ||
          name.indexOf("birth") !== -1 ||
          name.indexOf("issued") !== -1 ||
          name.indexOf("expiration") !== -1 ||
          name.indexOf("effective") !== -1
        );
      }}

      function syncVisibleDateInput(input) {{
        if (!input) {{
          return;
        }}
        var hiddenName = String(input.dataset.dateVisible || "").trim();
        if (!hiddenName) {{
          return;
        }}
        var scope = input.closest("form") || document;
        var hiddenInput = scope.querySelector('input[type="hidden"][name="' + hiddenName + '"]');
        if (!hiddenInput) {{
          return;
        }}
        if (!String(input.value || "").trim()) {{
          hiddenInput.value = "";
          return;
        }}
        var parts = String(input.value || "").split("-");
        if (parts.length !== 3) {{
          return;
        }}
        hiddenInput.value = parts[1] + "/" + parts[2] + "/" + parts[0];
      }}

      function composeTimeValue(hourValue, minuteValue, ampmValue) {{
        var hour = parseInt(String(hourValue || "09"), 10);
        var minute = parseInt(String(minuteValue || "00"), 10);
        var normalizedAmpm = String(ampmValue || "AM").trim().toUpperCase() === "PM" ? "PM" : "AM";
        if (!Number.isFinite(hour) || hour < 1 || hour > 12) {{
          hour = 9;
        }}
        if (!Number.isFinite(minute) || minute < 0 || minute > 59) {{
          minute = 0;
        }}
        var hour24 = hour % 12;
        if (normalizedAmpm === "PM") {{
          hour24 += 12;
        }}
        return String(hour24).padStart(2, "0") + ":" + String(minute).padStart(2, "0");
      }}

      function syncTimeWheel(container) {{
        if (!container) {{
          return;
        }}
        var hiddenInput = container.querySelector('input[type="hidden"][name]');
        var hourSelect = container.querySelector('select[data-time-part="hour"]');
        var minuteSelect = container.querySelector('select[data-time-part="minute"]');
        var ampmSelect = container.querySelector('select[data-time-part="ampm"]');
        if (!hiddenInput || !hourSelect || !minuteSelect || !ampmSelect) {{
          return;
        }}
        hiddenInput.value = composeTimeValue(hourSelect.value, minuteSelect.value, ampmSelect.value);
      }}

      function syncAbaRequiredCptHint(form) {{
        if (!form) {{
          return;
        }}
        var hint = form.querySelector("[data-rbt-direct-hint]");
        if (!hint) {{
          return;
        }}
        var providerSelect = form.querySelector('select[name="provider_contract_id"]');
        var contextSelect = form.querySelector('select[name="service_context"]');
        var selectedOption = providerSelect ? providerSelect.options[providerSelect.selectedIndex] : null;
        var providerRole = String(selectedOption ? selectedOption.dataset.providerRole || "" : "").trim().toUpperCase();
        var serviceContext = String(contextSelect ? contextSelect.value || "" : "").trim().toLowerCase();
        if (providerRole === "RBT" && serviceContext === "direct") {{
          hint.textContent = "RBT directo requiere CPT 97153.";
          return;
        }}
        hint.textContent = "Selecciona provider, cliente y tipo de servicio para confirmar el CPT obligatorio.";
      }}

      function syncAbaProviderOptions(form) {{
        if (!form) {{
          return;
        }}
        var clientSelect = form.querySelector('select[name="client_id"]');
        var providerSelect = form.querySelector('select[name="provider_contract_id"]');
        if (!clientSelect || !providerSelect) {{
          syncAbaRequiredCptHint(form);
          return;
        }}
        var selectedClientId = String(clientSelect.value || "").trim();
        var firstVisibleValue = "";
        var currentVisible = false;
        Array.prototype.forEach.call(providerSelect.options, function(option) {{
          var optionValue = String(option.value || "").trim();
          if (!optionValue) {{
            option.hidden = false;
            option.disabled = false;
            return;
          }}
          var clientIds = String(option.dataset.clientIds || "")
            .split("|")
            .map(function(value) {{ return String(value || "").trim(); }})
            .filter(Boolean);
          var isVisible = !selectedClientId || clientIds.indexOf(selectedClientId) !== -1;
          option.hidden = !isVisible;
          option.disabled = !isVisible;
          if (isVisible && !firstVisibleValue) {{
            firstVisibleValue = optionValue;
          }}
          if (isVisible && optionValue === String(providerSelect.value || "").trim()) {{
            currentVisible = true;
          }}
        }});
        if (!currentVisible) {{
          providerSelect.value = firstVisibleValue;
        }}
        syncAbaRequiredCptHint(form);
      }}

      function clearSignatureCanvas(canvas) {{
        if (!canvas) {{
          return;
        }}
        var context = canvas.getContext("2d");
        if (!context) {{
          return;
        }}
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.lineCap = "round";
        context.lineJoin = "round";
        context.lineWidth = 2.5;
        context.strokeStyle = "#173156";
        canvas.dataset.hasInk = "0";
      }}

      function drawSignatureImage(canvas, imageSource) {{
        if (!canvas || !imageSource) {{
          return;
        }}
        var image = new Image();
        image.onload = function() {{
          clearSignatureCanvas(canvas);
          var context = canvas.getContext("2d");
          if (!context) {{
            return;
          }}
          context.drawImage(image, 0, 0, canvas.width, canvas.height);
          canvas.dataset.hasInk = "1";
        }};
        image.src = imageSource;
      }}

      function initSignatureField(field) {{
        if (!field || field.dataset.signatureReady === "1") {{
          return;
        }}
        var hiddenInput = field.querySelector("[data-signature-input]");
        var canvas = field.querySelector("[data-signature-pad]");
        var clearButton = field.querySelector("[data-signature-clear]");
        if (!hiddenInput || !canvas) {{
          return;
        }}
        field.dataset.signatureReady = "1";
        clearSignatureCanvas(canvas);
        if (String(hiddenInput.value || "").trim().indexOf("data:image/") === 0) {{
          drawSignatureImage(canvas, String(hiddenInput.value || "").trim());
        }}
        var drawing = false;

        function pointFromEvent(event) {{
          var rect = canvas.getBoundingClientRect();
          if (!rect.width || !rect.height) {{
            return {{ x: 0, y: 0 }};
          }}
          return {{
            x: (event.clientX - rect.left) * (canvas.width / rect.width),
            y: (event.clientY - rect.top) * (canvas.height / rect.height),
          }};
        }}

        function storeSignature() {{
          if (canvas.dataset.hasInk === "1") {{
            hiddenInput.value = canvas.toDataURL("image/png");
          }}
        }}

        canvas.addEventListener("pointerdown", function(event) {{
          event.preventDefault();
          drawing = true;
          var point = pointFromEvent(event);
          var context = canvas.getContext("2d");
          if (!context) {{
            return;
          }}
          context.beginPath();
          context.moveTo(point.x, point.y);
        }});

        canvas.addEventListener("pointermove", function(event) {{
          if (!drawing) {{
            return;
          }}
          event.preventDefault();
          var point = pointFromEvent(event);
          var context = canvas.getContext("2d");
          if (!context) {{
            return;
          }}
          context.lineTo(point.x, point.y);
          context.stroke();
          canvas.dataset.hasInk = "1";
        }});

        function endDrawing() {{
          if (!drawing) {{
            return;
          }}
          drawing = false;
          var context = canvas.getContext("2d");
          if (context) {{
            context.beginPath();
          }}
          storeSignature();
        }}

        canvas.addEventListener("pointerup", endDrawing);
        canvas.addEventListener("pointerleave", endDrawing);
        canvas.addEventListener("pointercancel", endDrawing);

        if (clearButton) {{
          clearButton.addEventListener("click", function() {{
            hiddenInput.value = "";
            clearSignatureCanvas(canvas);
          }});
        }}
      }}

      function syncProcedurePrice(select) {{
        if (!select) {{
          return;
        }}
        var selectedOption = select.options[select.selectedIndex];
        if (!selectedOption) {{
          return;
        }}
        var unitPrice = String(selectedOption.dataset.unitPrice || "").trim();
        if (!unitPrice) {{
          return;
        }}
        var priceFieldName = String(select.name || "").replace("_procedure_code", "_unit_price");
        if (!priceFieldName || priceFieldName === String(select.name || "")) {{
          return;
        }}
        var priceInput = document.querySelector('input[name="' + priceFieldName + '"]');
        if (!priceInput) {{
          return;
        }}
        priceInput.value = unitPrice;
      }}

      function parseClaimNumber(value) {{
        var normalized = String(value || "").replace(/[^0-9.-]/g, "").trim();
        var numberValue = Number(normalized);
        return Number.isFinite(numberValue) ? numberValue : 0;
      }}

      function updateClaimPreparationTotals() {{
        var form = document.getElementById("claims837");
        if (!form) {{
          return;
        }}

        var totalLines = 0;
        var totalUnits = 0;
        var totalMinutes = 0;
        var totalCharge = 0;

        for (var index = 1; index <= 3; index += 1) {{
          var codeSelect = form.querySelector('[name="service_line_' + index + '_procedure_code"]');
          var unitPriceInput = form.querySelector('[name="service_line_' + index + '_unit_price"]');
          var unitsInput = form.querySelector('[name="service_line_' + index + '_units"]');
          var chargeInput = form.querySelector('[data-claim-line-charge="' + index + '"]');
          var lineTotalLabel = form.querySelector('[data-claim-line-total="' + index + '"]');
          var lineMinutesLabel = form.querySelector('[data-claim-line-minutes="' + index + '"]');

          var codeValue = String(codeSelect ? codeSelect.value || "" : "").trim();
          var unitPriceValue = String(unitPriceInput ? unitPriceInput.value || "" : "").trim();
          var unitsValue = String(unitsInput ? unitsInput.value || "" : "").trim();
          var hasLine = Boolean(codeValue || unitPriceValue || unitsValue);

          var units = parseInt(unitsValue, 10);
          if (!Number.isFinite(units) || units < 0) {{
            units = 0;
          }}

          var unitPrice = parseClaimNumber(unitPriceValue);
          var minutes = units * 15;
          var lineCharge = Math.round(unitPrice * units * 100) / 100;

          if (hasLine) {{
            totalLines += 1;
            totalUnits += units;
            totalMinutes += minutes;
            totalCharge += lineCharge;
          }}

          if (chargeInput) {{
            chargeInput.value = hasLine ? lineCharge.toFixed(2) : "";
          }}
          if (lineTotalLabel) {{
            lineTotalLabel.textContent = hasLine ? "$" + lineCharge.toFixed(2) : "--";
          }}
          if (lineMinutesLabel) {{
            lineMinutesLabel.textContent = hasLine ? String(minutes) + " min" : "--";
          }}
        }}

        var totalChargeText = "$" + totalCharge.toFixed(2);
        form.querySelectorAll('[data-claim-preview="lines"]').forEach(function(node) {{
          node.textContent = String(totalLines);
        }});
        form.querySelectorAll('[data-claim-preview="units"]').forEach(function(node) {{
          node.textContent = String(totalUnits);
        }});
        form.querySelectorAll('[data-claim-preview="minutes"]').forEach(function(node) {{
          node.textContent = String(totalMinutes);
        }});
        form.querySelectorAll('[data-claim-preview="dollars"], [data-claim-preview="toggle-total"]').forEach(function(node) {{
          node.textContent = totalChargeText;
        }});

        var totalChargeInput = form.querySelector("[data-claim-total-input]");
        if (totalChargeInput) {{
          totalChargeInput.value = totalLines ? totalCharge.toFixed(2) : "";
        }}
      }}

      function syncAuthorizationLineVisibility() {{
        var countSelect = document.querySelector('select[name="authorization_line_count"]');
        if (!countSelect) {{
          return;
        }}
        var visibleCount = Number(countSelect.value || "{MAX_AUTHORIZATION_LINES}");
        document.querySelectorAll(".authorization-line-row").forEach(function(row) {{
          var lineIndex = Number(row.dataset.lineIndex || "0");
          row.hidden = lineIndex > visibleCount;
        }});
      }}

      function syncAuthorizationRemainingUnits(totalInput) {{
        if (!totalInput) {{
          return;
        }}
        var remainingName = String(totalInput.name || "").replace("_total_units", "_remaining_units");
        if (!remainingName || remainingName === String(totalInput.name || "")) {{
          return;
        }}
        var remainingInput = document.querySelector('input[name="' + remainingName + '"]');
        if (!remainingInput) {{
          return;
        }}
        if (!String(remainingInput.value || "").trim()) {{
          remainingInput.value = String(totalInput.value || "").trim();
        }}
      }}

      function syncAuthorizationEndDate(force) {{
        var startInput = document.querySelector('input[name="start_date"]');
        var endInput = document.querySelector('input[name="end_date"]');
        if (!startInput || !endInput) {{
          return;
        }}
        var startDate = parseUserDate(startInput.value);
        if (!startDate) {{
          return;
        }}
        if (!force && endInput.dataset.manualOverride === "1" && String(endInput.value || "").trim()) {{
          return;
        }}
        endInput.value = formatUserDate(addMonths(startDate, 6));
        endInput.dataset.autoCalculated = "1";
      }}

      function syncLocationCounty(select) {{
        if (!select) {{
          return;
        }}
        var key = String(select.dataset.locationSelect || "").trim();
        if (!key) {{
          return;
        }}
        var countyInput = document.querySelector('[data-county-input="' + key + '"]');
        if (!countyInput) {{
          return;
        }}
        var selectedOption = select.options[select.selectedIndex];
        if (!selectedOption) {{
          return;
        }}
        var countyValue = String(selectedOption.dataset.county || "").trim();
        if (countyValue) {{
          countyInput.value = countyValue;
        }}
      }}

      function syncWorkforceFields() {{
        var categorySelect = document.querySelector('[data-worker-category]');
        if (!categorySelect) {{
          return;
        }}
        var category = String(categorySelect.value || "PROVIDER").toUpperCase();
        document.querySelectorAll('[data-workforce-group]').forEach(function(field) {{
          field.hidden = String(field.dataset.workforceGroup || "").toUpperCase() !== category;
        }});
      }}

      function syncCredentialingDueDate() {{
        var startInput = document.querySelector('[data-credentialing-start]');
        var dueInput = document.querySelector('[data-credentialing-due]');
        if (!startInput || !dueInput) {{
          return;
        }}
        var startDate = parseUserDate(startInput.value);
        dueInput.value = startDate ? formatUserDate(addMonths(startDate, 3)) : "";
      }}

      function initSegmentedTabs() {{
        document.querySelectorAll('.segmented-tabs[data-tab-group]').forEach(function(tabBar) {{
          var groupName = String(tabBar.dataset.tabGroup || "").trim();
          var panelScope = tabBar.closest("form, section, article") || document;
          if (!groupName) {{
            return;
          }}
          var buttons = tabBar.querySelectorAll('[data-tab-target]');
          var panels = panelScope.querySelectorAll('[data-tab-panel]');
          function activateTab(targetName) {{
            buttons.forEach(function(button) {{
              var active = String(button.dataset.tabTarget || "") === targetName;
              button.classList.toggle("active", active);
              button.setAttribute("aria-pressed", active ? "true" : "false");
            }});
            panels.forEach(function(panel) {{
              panel.hidden = String(panel.dataset.tabPanel || "") !== targetName;
            }});
          }}
          buttons.forEach(function(button) {{
            button.addEventListener("click", function() {{
              activateTab(String(button.dataset.tabTarget || ""));
            }});
          }});
          var defaultButton = tabBar.querySelector('[data-tab-target][aria-pressed="true"]') || buttons[0];
          if (defaultButton) {{
            activateTab(String(defaultButton.dataset.tabTarget || ""));
          }}
        }});
      }}

      function setAutoCollapsibleState(card, expanded) {{
        if (!card) {{
          return;
        }}
        var body = card.querySelector(".auto-collapsible-body");
        var summary = card.querySelector(".auto-collapsible-summary");
        var hint = card.querySelector(".auto-collapsible-hint");
        if (!body || !summary) {{
          return;
        }}
        body.hidden = !expanded;
        card.classList.toggle("is-open", expanded);
        summary.setAttribute("aria-expanded", expanded ? "true" : "false");
        if (hint) {{
          hint.textContent = expanded ? "Cerrar" : "Abrir";
        }}
      }}

      function initAutoCollapsibleCards() {{
        var page = String(document.body.dataset.page || "").trim().toLowerCase();
        if (!page || page === "dashboard") {{
          return;
        }}

        document.querySelectorAll("main .content-inner .panel.section-card, main .content-inner .panel.module-card").forEach(function(card) {{
          if (card.dataset.autoCollapsible === "1") {{
            return;
          }}
          if (card.dataset.skipAutoCollapsible === "1") {{
            return;
          }}
          if (card.hidden || card.closest("[hidden]") || card.closest("details")) {{
            return;
          }}

          var title = card.querySelector("h2");
          if (!title) {{
            return;
          }}

          var summaryTitle = String(title.textContent || "").trim();
          if (!summaryTitle) {{
            return;
          }}

          var moduleHead = title.closest(".module-head");
          var summaryCopy = "Haz clic para abrir este modulo.";
          var copyNode = null;

          if (moduleHead) {{
            copyNode = moduleHead.querySelector("p");
          }}
          if (!copyNode) {{
            copyNode = Array.from(card.querySelectorAll("p")).find(function(candidate) {{
              if (!String(candidate.textContent || "").trim()) {{
                return false;
              }}
              return !candidate.closest(".helper-note, .mini-table, .table-wrap, details");
            }}) || null;
          }}
          if (copyNode) {{
            summaryCopy = String(copyNode.textContent || "").trim();
          }}

          var omittedNodes = [];
          if (moduleHead && moduleHead.parentElement === card) {{
            omittedNodes.push(moduleHead);
          }} else if (title.parentElement === card) {{
            omittedNodes.push(title);
          }}
          if (copyNode && copyNode.parentElement === card && omittedNodes.indexOf(copyNode) === -1) {{
            omittedNodes.push(copyNode);
          }}

          var originalChildren = Array.from(card.childNodes);
          var hasVisibleContent = originalChildren.some(function(node) {{
            if (omittedNodes.indexOf(node) !== -1) {{
              return false;
            }}
            return node.nodeType !== 3 || String(node.textContent || "").trim();
          }});
          if (!hasVisibleContent) {{
            return;
          }}

          var summary = document.createElement("button");
          summary.type = "button";
          summary.className = "auto-collapsible-summary";

          var summaryCopyBox = document.createElement("span");
          summaryCopyBox.className = "auto-collapsible-copy";

          var summaryTitleNode = document.createElement("strong");
          summaryTitleNode.textContent = summaryTitle;
          summaryCopyBox.appendChild(summaryTitleNode);

          if (summaryCopy) {{
            var summaryTextNode = document.createElement("small");
            summaryTextNode.textContent = summaryCopy;
            summaryCopyBox.appendChild(summaryTextNode);
          }}

          var summaryHint = document.createElement("span");
          summaryHint.className = "auto-collapsible-hint";
          summaryHint.textContent = "Abrir";

          summary.appendChild(summaryCopyBox);
          summary.appendChild(summaryHint);

          var body = document.createElement("div");
          body.className = "auto-collapsible-body";

          while (card.firstChild) {{
            card.removeChild(card.firstChild);
          }}
          card.appendChild(summary);
          card.appendChild(body);

          originalChildren.forEach(function(node) {{
            if (omittedNodes.indexOf(node) !== -1) {{
              return;
            }}
            body.appendChild(node);
          }});

          card.classList.add("auto-collapsible-host");
          card.dataset.autoCollapsible = "1";

          var shouldExpand = card.classList.contains("active");
          setAutoCollapsibleState(card, shouldExpand);

          summary.addEventListener("click", function() {{
            setAutoCollapsibleState(card, body.hidden);
          }});
        }});
      }}

      function openHashTargetPanel() {{
        var hash = String(window.location.hash || "").trim();
        if (!hash || hash === "#") {{
          return;
        }}
        var target = document.getElementById(hash.slice(1));
        if (!target) {{
          return;
        }}
        var parentDetails = target.closest("details");
        while (parentDetails) {{
          parentDetails.open = true;
          parentDetails = parentDetails.parentElement ? parentDetails.parentElement.closest("details") : null;
        }}
        var autoCard = target.closest(".auto-collapsible-host");
        if (autoCard) {{
          setAutoCollapsibleState(autoCard, true);
        }}
        window.setTimeout(function() {{
          target.scrollIntoView({{ block: "start" }});
        }}, 0);
      }}

      function collapseDefaultClaimsPanels() {{
        var page = String(document.body.dataset.page || "").trim();
        var hash = String(window.location.hash || "").trim();
        var hasHash = Boolean(hash && hash !== "#");
        var hasError = Boolean(document.querySelector(".error-panel"));
        if (page !== "claims" || hasHash || hasError) {{
          return;
        }}
        document.querySelectorAll('details.collapsible-panel[data-collapse-default="1"]').forEach(function(panel) {{
          panel.open = false;
        }});
      }}

      function initDirectoryFilters() {{
        document.querySelectorAll("[data-directory-toolbar]").forEach(function(toolbar) {{
          var directoryName = String(toolbar.dataset.directoryToolbar || "").trim();
          if (!directoryName) {{
            return;
          }}

          var searchInput = toolbar.querySelector('[data-directory-search="' + directoryName + '"]');
          var statusSelect = toolbar.querySelector('[data-directory-status="' + directoryName + '"]');
          var viewSelect = toolbar.querySelector('[data-directory-view="' + directoryName + '"]');
          var cardPanel = document.querySelector('[data-directory-panel="' + directoryName + '"][data-view="card"]');
          var tablePanel = document.querySelector('[data-directory-panel="' + directoryName + '"][data-view="table"]');

          function applyFilters() {{
            var query = String(searchInput ? searchInput.value || "" : "").trim().toLowerCase();
            var status = String(statusSelect ? statusSelect.value || "active" : "active").trim().toLowerCase();
            var view = String(viewSelect ? viewSelect.value || "card" : "card").trim().toLowerCase();

            if (cardPanel) {{
              cardPanel.hidden = view !== "card";
            }}
            if (tablePanel) {{
              tablePanel.hidden = view !== "table";
            }}

            document.querySelectorAll('[data-directory-card="' + directoryName + '"]').forEach(function(card) {{
              var cardStatus = String(card.dataset.status || "").trim().toLowerCase();
              var haystack = String(card.dataset.search || "").trim().toLowerCase();
              var matchesStatus = status === "all" || cardStatus === status;
              var matchesQuery = !query || haystack.indexOf(query) !== -1;
              card.hidden = !(matchesStatus && matchesQuery);
            }});

            document.querySelectorAll('[data-directory-row="' + directoryName + '"]').forEach(function(row) {{
              var rowStatus = String(row.dataset.status || "").trim().toLowerCase();
              var haystack = String(row.dataset.search || "").trim().toLowerCase();
              var matchesStatus = status === "all" || rowStatus === status;
              var matchesQuery = !query || haystack.indexOf(query) !== -1;
              row.hidden = !(matchesStatus && matchesQuery);
            }});
          }}

          if (searchInput) {{
            searchInput.addEventListener("input", applyFilters);
          }}
          if (statusSelect) {{
            statusSelect.addEventListener("change", applyFilters);
          }}
          if (viewSelect) {{
            viewSelect.addEventListener("change", applyFilters);
          }}
          applyFilters();
        }});
      }}

      function initProviderSummaryToggles() {{
        document.querySelectorAll("[data-provider-summary-toggle]").forEach(function(button) {{
          if (button.dataset.providerSummaryReady === "1") {{
            return;
          }}
          var targetId = String(button.getAttribute("aria-controls") || "").trim();
          if (!targetId) {{
            return;
          }}
          var target = document.getElementById(targetId);
          if (!target) {{
            return;
          }}

          function setExpanded(expanded) {{
            target.hidden = !expanded;
            button.classList.toggle("is-open", expanded);
            button.setAttribute("aria-expanded", expanded ? "true" : "false");
            button.textContent = expanded ? "Cerrar perfil rapido" : "Abrir perfil rapido";
          }}

          function closeSiblingToggles() {{
            document.querySelectorAll("[data-provider-summary-toggle]").forEach(function(otherButton) {{
              if (otherButton === button) {{
                return;
              }}
              var otherTargetId = String(otherButton.getAttribute("aria-controls") || "").trim();
              if (!otherTargetId) {{
                return;
              }}
              var otherTarget = document.getElementById(otherTargetId);
              if (!otherTarget) {{
                return;
              }}
              otherTarget.hidden = true;
              otherButton.classList.remove("is-open");
              otherButton.setAttribute("aria-expanded", "false");
              otherButton.textContent = "Abrir perfil rapido";
            }});
          }}

          setExpanded(false);
          button.addEventListener("click", function() {{
            var shouldOpen = target.hidden;
            if (shouldOpen) {{
              closeSiblingToggles();
            }}
            setExpanded(shouldOpen);
          }});
          button.dataset.providerSummaryReady = "1";
        }});
      }}

      document.querySelectorAll(".document-shortcut").forEach(function(button) {{
        button.addEventListener("click", function() {{
          var issuedInput = document.getElementById(button.dataset.issuedInput || "");
          var expirationInput = document.getElementById(button.dataset.expirationInput || "");
          if (!issuedInput || !expirationInput) {{
            return;
          }}

          var baseDate = parseUserDate(issuedInput.value);
          if (!baseDate) {{
            baseDate = new Date();
            issuedInput.value = formatUserDate(baseDate);
          }}

          var amount = Number(button.dataset.amount || "0");
          var unit = String(button.dataset.unit || "years");
          var expirationDate = unit === "months" ? addMonths(baseDate, amount) : addYears(baseDate, amount);
          expirationInput.value = formatUserDate(expirationDate);
        }});
      }});

      document.querySelectorAll("input").forEach(function(input) {{
        if (!shouldAttachCalendar(input)) {{
          return;
        }}
        if (!input.getAttribute("title")) {{
          input.setAttribute("title", "Doble click para abrir el calendario");
        }}
        input.addEventListener("dblclick", function() {{
          openDatePicker(input);
        }});
      }});

      document.querySelectorAll('input[type="date"][data-date-visible]').forEach(function(input) {{
        syncVisibleDateInput(input);
        input.addEventListener("input", function() {{
          syncVisibleDateInput(input);
        }});
        input.addEventListener("change", function() {{
          syncVisibleDateInput(input);
        }});
      }});

      document.querySelectorAll("[data-time-wheel]").forEach(function(container) {{
        syncTimeWheel(container);
        container.querySelectorAll("select").forEach(function(select) {{
          select.addEventListener("change", function() {{
            syncTimeWheel(container);
          }});
        }});
      }});

      document.querySelectorAll(".signature-field").forEach(function(field) {{
        initSignatureField(field);
      }});

      document.querySelectorAll('form[action="/add-aba-appointment"]').forEach(function(form) {{
        syncAbaProviderOptions(form);
        var clientSelect = form.querySelector('select[name="client_id"]');
        var providerSelect = form.querySelector('select[name="provider_contract_id"]');
        var contextSelect = form.querySelector('select[name="service_context"]');
        if (clientSelect) {{
          clientSelect.addEventListener("change", function() {{
            syncAbaProviderOptions(form);
          }});
        }}
        if (providerSelect) {{
          providerSelect.addEventListener("change", function() {{
            syncAbaRequiredCptHint(form);
          }});
        }}
        if (contextSelect) {{
          contextSelect.addEventListener("change", function() {{
            syncAbaRequiredCptHint(form);
          }});
        }}
      }});

      document.querySelectorAll('select[name^="service_line_"][name$="_procedure_code"]').forEach(function(select) {{
        syncProcedurePrice(select);
        updateClaimPreparationTotals();
        select.addEventListener("change", function() {{
          syncProcedurePrice(select);
          updateClaimPreparationTotals();
        }});
      }});

      document.querySelectorAll('#claims837 input[name^="service_line_"][name$="_unit_price"], #claims837 input[name^="service_line_"][name$="_units"]').forEach(function(input) {{
        input.addEventListener("input", function() {{
          updateClaimPreparationTotals();
        }});
        input.addEventListener("change", function() {{
          updateClaimPreparationTotals();
        }});
      }});

      updateClaimPreparationTotals();

      document.querySelectorAll("[data-location-select]").forEach(function(select) {{
        syncLocationCounty(select);
        select.addEventListener("change", function() {{
          syncLocationCounty(select);
        }});
      }});

      var workforceCategorySelect = document.querySelector("[data-worker-category]");
      if (workforceCategorySelect) {{
        syncWorkforceFields();
        workforceCategorySelect.addEventListener("change", function() {{
          syncWorkforceFields();
        }});
      }}

      var credentialingStartInput = document.querySelector("[data-credentialing-start]");
      if (credentialingStartInput) {{
        syncCredentialingDueDate();
        credentialingStartInput.addEventListener("input", function() {{
          syncCredentialingDueDate();
        }});
        credentialingStartInput.addEventListener("change", function() {{
          syncCredentialingDueDate();
        }});
      }}

      initSegmentedTabs();
      initDirectoryFilters();
      initProviderSummaryToggles();
      initAutoCollapsibleCards();
      collapseDefaultClaimsPanels();
      openHashTargetPanel();
      window.addEventListener("hashchange", openHashTargetPanel);

      var authorizationCountSelect = document.querySelector('select[name="authorization_line_count"]');
      if (authorizationCountSelect) {{
        syncAuthorizationLineVisibility();
        authorizationCountSelect.addEventListener("change", function() {{
          syncAuthorizationLineVisibility();
        }});
      }}

      document.querySelectorAll('input[name^="authorization_line_"][name$="_total_units"]').forEach(function(input) {{
        input.addEventListener("change", function() {{
          syncAuthorizationRemainingUnits(input);
        }});
      }});

      var authorizationStartInput = document.querySelector('input[name="start_date"]');
      var authorizationEndInput = document.querySelector('input[name="end_date"]');
      if (authorizationStartInput && authorizationEndInput) {{
        syncAuthorizationEndDate(false);
        authorizationStartInput.addEventListener("change", function() {{
          syncAuthorizationEndDate(false);
        }});
        authorizationEndInput.addEventListener("input", function() {{
          authorizationEndInput.dataset.manualOverride = "1";
        }});
        authorizationEndInput.addEventListener("change", function() {{
          authorizationEndInput.dataset.manualOverride = "1";
        }});
      }}
    }})();
  </script>
</body>
</html>
"""


def _form_state_args(active_panel: str, form_data: dict[str, str]) -> dict[str, object]:
    if active_panel == "claim":
        return {"claim_form": form_data}
    if active_panel == "eligibility":
        return {"eligibility_form": form_data}
    if active_panel == "notification":
        return {
            "email_form": {
                **form_data,
                "save_to_notifications": form_data.get("save_to_notifications", ""),
            }
        }
    if active_panel == "clients":
        return {
            "client_form": {
                **form_data,
                "active": form_data.get("active", ""),
                "auto_eligibility": form_data.get("auto_eligibility", ""),
            }
        }
    if active_panel == "authorization":
        return {"authorization_form": form_data}
    if active_panel == "payer_config":
        return {
            "payer_config_form": {
                **form_data,
                "active": form_data.get("active", ""),
            }
        }
    if active_panel == "payer_roster":
        return {"payer_enrollment_form": form_data}
    if active_panel == "agency":
        return {"agency_form": form_data}
    if active_panel == "provider_contract":
        return {"provider_contract_form": form_data}
    if active_panel == "user":
        permission_state = {f"perm_{key}": form_data.get(f"perm_{key}", "") for key in PERMISSION_PAGE_LABELS}
        return {
            "user_form": {
                **form_data,
                **permission_state,
                "active": form_data.get("active", ""),
            }
        }
    if active_panel == "agenda":
        return {
            "agenda_form": {
                **form_data,
                "notify_email": form_data.get("notify_email", ""),
            }
        }
    if active_panel == "note":
        return {"note_form": form_data}
    if active_panel == "aba_notes":
        return {"aba_notes_form": form_data}
    if active_panel == "system_config":
        return {"system_config_form": form_data}
    if active_panel == "roster":
        return {"roster_form": form_data}
    if active_panel == "edi837":
        return {
            "edi837_form": form_data,
            "edi837_payload": form_data.get("payload", ""),
        }
    if active_panel == "era":
        return {
            "era_form": form_data,
            "era_payload": form_data.get("payload", ""),
        }
    return {}


class BillingWebHandler(BaseHTTPRequestHandler):
    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(content.encode("utf-8"), "text/html; charset=utf-8", status)

    def _parse_header_params(self, header_value: str) -> dict[str, str]:
        params: dict[str, str] = {}
        for part in header_value.split(";"):
            clean = part.strip()
            if "=" not in clean:
                continue
            key, value = clean.split("=", 1)
            params[key.strip().lower()] = value.strip().strip('"')
        return params

    def _parse_multipart_form_data(self, content_type: str, raw_body: bytes) -> tuple[dict[str, str], dict[str, UploadedFile]]:
        params = self._parse_header_params(content_type)
        boundary = params.get("boundary", "")
        if not boundary:
            return ({}, {})

        delimiter = f"--{boundary}".encode("utf-8")
        form_data: dict[str, str] = {}
        files: dict[str, UploadedFile] = {}

        for part in raw_body.split(delimiter):
            if not part or part in {b"--", b"--\r\n"}:
                continue
            section = part.strip(b"\r\n")
            if section == b"--":
                continue
            header_block, separator, body = section.partition(b"\r\n\r\n")
            if not separator:
                continue

            headers: dict[str, str] = {}
            for raw_header in header_block.decode("utf-8", errors="replace").split("\r\n"):
                if ":" not in raw_header:
                    continue
                header_name, header_value = raw_header.split(":", 1)
                headers[header_name.strip().lower()] = header_value.strip()

            disposition = headers.get("content-disposition", "")
            disposition_params = self._parse_header_params(disposition)
            field_name = disposition_params.get("name", "")
            if not field_name:
                continue

            clean_body = body[:-2] if body.endswith(b"\r\n") else body
            file_name = Path(disposition_params.get("filename", "")).name
            if file_name:
                files[field_name] = UploadedFile(
                    filename=file_name,
                    content_type=headers.get("content-type", "application/octet-stream"),
                    content=clean_body,
                )
            else:
                form_data[field_name] = clean_body.decode("utf-8", errors="replace")

        return (form_data, files)

    def _read_form_data(self) -> tuple[dict[str, str], dict[str, UploadedFile]]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type:
            return self._parse_multipart_form_data(content_type, raw_body)
        parsed = parse_qs(raw_body.decode("utf-8"))
        return ({key: values[0] if values else "" for key, values in parsed.items()}, {})

    def _cookie_map(self) -> dict[str, str]:
        cookie_header = self.headers.get("Cookie", "")
        cookies: dict[str, str] = {}
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            cookies[key] = value
        return cookies

    def _current_user(self) -> dict[str, object] | None:
        token = self._cookie_map().get(SESSION_COOKIE_NAME, "")
        if not token:
            return None
        session = SESSIONS.get(token)
        if not _session_active(session, get_session_timeout_seconds()):
            if session is not None:
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": session.get("user_id", ""),
                        "entity_name": session.get("username", ""),
                        "action": "USER_SESSION_EXPIRED",
                        "actor_username": session.get("username", ""),
                        "actor_name": session.get("full_name", ""),
                        "details": "La sesion expiro por inactividad.",
                    }
                )
            SESSIONS.pop(token, None)
            return None
        if session is not None and not session.get("linked_provider_name"):
            refreshed_user = get_user_public_profile(str(session.get("username", "")))
            session = {
                **session,
                **refreshed_user,
            }
        session["last_seen_at"] = time.time()
        SESSIONS[token] = session
        return session

    def _pending_mfa(self) -> dict[str, object] | None:
        token = self._cookie_map().get(MFA_COOKIE_NAME, "")
        if not token:
            return None
        session = MFA_SESSIONS.get(token)
        if not _session_active(session, get_mfa_session_timeout_seconds()):
            if session is not None:
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": session.get("user_id", ""),
                        "entity_name": session.get("username", ""),
                        "action": "USER_MFA_SESSION_EXPIRED",
                        "actor_username": session.get("username", ""),
                        "actor_name": session.get("full_name", ""),
                        "details": "La ventana de confirmacion MFA expiro.",
                    }
                )
            MFA_SESSIONS.pop(token, None)
            return None
        session["last_seen_at"] = time.time()
        MFA_SESSIONS[token] = session
        return session

    def _start_session(self, user: dict[str, object]) -> str:
        token = secrets.token_urlsafe(24)
        now = time.time()
        SESSIONS[token] = {
            "user_id": str(user.get("user_id", "")),
            "full_name": str(user.get("full_name", "")),
            "username": str(user.get("username", "")),
            "email": str(user.get("email", "")),
            "phone": str(user.get("phone", "")),
            "job_title": str(user.get("job_title", "")),
            "bio": str(user.get("bio", "")),
            "site_location": str(user.get("site_location", "")),
            "county_name": str(user.get("county_name", "")),
            "profile_color": str(user.get("profile_color", "#0d51b8")),
            "avatar_file_name": str(user.get("avatar_file_name", "")),
            "avatar_file_path": str(user.get("avatar_file_path", "")),
            "linked_provider_name": str(user.get("linked_provider_name", "")),
            "role": str(user.get("role", "")),
            "module_permissions": dict(user.get("module_permissions", {})) if isinstance(user.get("module_permissions"), dict) else {},
            "permission_overrides": dict(user.get("permission_overrides", {})) if isinstance(user.get("permission_overrides"), dict) else {},
            "created_at": now,
            "last_seen_at": now,
        }
        return token

    def _start_pending_mfa(self, user: dict[str, object]) -> str:
        token = secrets.token_urlsafe(24)
        now = time.time()
        MFA_SESSIONS[token] = {
            "username": str(user.get("username", "")),
            "user_id": str(user.get("user_id", "")),
            "full_name": str(user.get("full_name", "")),
            "created_at": now,
            "last_seen_at": now,
        }
        return token

    def _end_session(self) -> None:
        token = self._cookie_map().get(SESSION_COOKIE_NAME, "")
        if token:
            SESSIONS.pop(token, None)
        pending_token = self._cookie_map().get(MFA_COOKIE_NAME, "")
        if pending_token:
            MFA_SESSIONS.pop(pending_token, None)

    def _send_redirect(self, location: str, set_cookie: str | list[str] | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if isinstance(set_cookie, list):
            for item in set_cookie:
                self.send_header("Set-Cookie", item)
        elif set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()

    def _page_allowed(self, user: dict[str, str] | None, page: str) -> bool:
        return rbac_can_access_page(user, page, list_provider_contracts())

    def _serve_asset(self, asset_name: str) -> None:
        safe_name = Path(unquote(asset_name)).name
        candidate = ASSETS_DIR / safe_name

        if not candidate.is_file():
            self._send_html(_render_page(error="Asset no encontrado."), status=HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_bytes(candidate.read_bytes(), content_type, HTTPStatus.OK)

    def _serve_agency_logo(self, agency_id: str) -> None:
        try:
            body, filename = get_agency_logo_bytes(agency_id)
        except Exception as exc:
            self._send_html(_render_page(error=str(exc)), status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._send_bytes(body, content_type, HTTPStatus.OK)

    def _serve_user_avatar(self, username: str) -> None:
        try:
            body, filename = get_user_avatar_bytes(username)
        except Exception as exc:
            self._send_html(_render_page(error=str(exc)), status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._send_bytes(body, content_type, HTTPStatus.OK)

    def _serve_provider_document(self, contract_id: str, document_name: str, current_user: dict[str, object] | None) -> None:
        contract = next((item for item in list_provider_contracts() if str(item.get("contract_id", "")) == contract_id), None)
        if contract is None:
            self._send_html(_render_page(error="No encontre ese expediente de provider."), status=HTTPStatus.NOT_FOUND)
            return
        visible_contracts = filter_provider_contracts_for_user(current_user, list_provider_contracts(), list_clients())
        visible_contract_ids = {str(item.get("contract_id", "")).strip() for item in visible_contracts if str(item.get("contract_id", "")).strip()}
        if str(contract.get("contract_id", "")).strip() not in visible_contract_ids:
            self._send_html(_render_page(error="Ese documento no pertenece a tu rango de acceso."), status=HTTPStatus.FORBIDDEN)
            return
        documents = contract.get("documents", [])
        if not isinstance(documents, list):
            documents = []
        document = next((item for item in documents if str(item.get("document_name", "")) == document_name), None)
        if document is None or not document.get("file_path"):
            self._send_html(_render_page(error="No encontre ese documento."), status=HTTPStatus.NOT_FOUND)
            return
        try:
            body, filename = get_upload_bytes(str(document.get("file_path", "")))
        except Exception as exc:
            self._send_html(_render_page(error=str(exc)), status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._send_bytes(
            body,
            content_type,
            HTTPStatus.OK,
            {"Content-Disposition": f'inline; filename="{filename}"'},
        )

    def _serve_client_document(self, client_id: str, document_name: str, current_user: dict[str, object] | None) -> None:
        visible_clients = filter_clients_for_user(current_user, list_clients(), list_provider_contracts())
        client = next((item for item in visible_clients if str(item.get("client_id", "")) == client_id), None)
        if client is None:
            self._send_html(_render_page(error="No encontre ese expediente del cliente."), status=HTTPStatus.NOT_FOUND)
            return
        documents = client.get("documents", [])
        if not isinstance(documents, list):
            documents = []
        document = next((item for item in documents if str(item.get("document_name", "")) == document_name), None)
        if document is None or not document.get("file_path"):
            self._send_html(_render_page(error="No encontre ese documento del cliente."), status=HTTPStatus.NOT_FOUND)
            return
        try:
            body, filename = get_upload_bytes(str(document.get("file_path", "")))
        except Exception as exc:
            self._send_html(_render_page(error=str(exc)), status=HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self._send_bytes(
            body,
            content_type,
            HTTPStatus.OK,
            {"Content-Disposition": f'inline; filename="{filename}"'},
        )

    def _serve_cms1500(self, claim_id: str, actor: dict[str, object] | None = None) -> None:
        record = get_claim_by_id(claim_id)
        if record is None:
            self._send_html(_render_page(error="No encontre ese claim para CMS-1500."), status=HTTPStatus.NOT_FOUND)
            return
        if actor is not None:
            visible_clients = filter_clients_for_user(actor, list_clients(), list_provider_contracts())
            visible_claims = filter_claims_for_user(actor, [record], visible_clients, list_provider_contracts())
            if not visible_claims:
                self._send_html(_render_page(error="Tu rango no puede abrir ese CMS-1500."), status=HTTPStatus.FORBIDDEN)
                return
        if actor is not None:
            add_claim_audit_log(
                {
                    "agency_id": record.get("agency_id", ""),
                    "agency_name": record.get("agency_name", ""),
                    "claim_id": claim_id,
                    "action": "CLAIM_OPEN_CMS1500",
                    "actor_username": actor.get("username", ""),
                    "actor_name": actor.get("full_name", ""),
                    "details": "Se abrio la vista CMS-1500 del claim.",
                }
            )
        self._send_html(render_cms1500_html(record), HTTPStatus.OK)

    def _serve_export(self, export_name: str, actor: dict[str, object] | None = None) -> None:
        provider_contracts = list_provider_contracts()
        clients = filter_clients_for_user(actor, list_clients(), provider_contracts) if actor else list_clients()
        claims = filter_claims_for_user(actor, list_claims(), clients, provider_contracts) if actor else list_claims()
        authorizations = filter_authorizations_for_user(actor, list_authorizations(), clients, provider_contracts) if actor else list_authorizations()
        visible_provider_contracts = filter_provider_contracts_for_user(actor, provider_contracts, clients) if actor else provider_contracts
        if export_name == "claims.xls":
            body, filename = claims_export_bytes(claims)
        elif export_name == "authorizations.xls":
            body, filename = authorizations_export_bytes(authorizations)
        elif export_name == "clients.xls":
            body, filename = clients_export_bytes(clients)
        elif export_name == "eligibility_roster.xls":
            body, filename = roster_export_bytes(list_eligibility_roster())
        elif export_name == "payer_enrollments.xls":
            body, filename = payer_enrollments_export_bytes(list_payer_enrollments())
        elif export_name == "agencies.xls":
            body, filename = agencies_export_bytes(list_agencies())
        elif export_name == "provider_contracts.xls":
            body, filename = provider_contracts_export_bytes(visible_provider_contracts)
        elif export_name == "notifications.xls":
            body, filename = notifications_export_bytes(list_notifications())
        elif export_name == "era_archives.xls":
            body, filename = era_archives_export_bytes(list_era_archives())
        else:
            self._send_html(_render_page(error="Export no encontrado."), status=HTTPStatus.NOT_FOUND)
            return

        self._send_bytes(
            body,
            "application/vnd.ms-excel; charset=utf-8",
            HTTPStatus.OK,
            {"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _serve_era_download(self, archive_id: str) -> None:
        try:
            body, filename = get_era_archive_bytes(archive_id)
        except Exception as exc:
            self._send_html(_render_page(error=str(exc)), status=HTTPStatus.NOT_FOUND)
            return

        self._send_bytes(
            body,
            "text/plain; charset=utf-8",
            HTTPStatus.OK,
            {"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _serve_claim_edi(self, claim_id: str, actor: dict[str, object] | None = None) -> None:
        try:
            if actor is not None:
                claim_record = get_claim_by_id(claim_id)
                visible_clients = filter_clients_for_user(actor, list_clients(), list_provider_contracts())
                visible_claims = filter_claims_for_user(actor, [claim_record] if claim_record is not None else [], visible_clients, list_provider_contracts())
                if claim_record is not None and not visible_claims:
                    self._send_html(_render_page(error="Tu rango no puede descargar ese 837."), status=HTTPStatus.FORBIDDEN)
                    return
            body, filename = get_claim_edi_bytes(claim_id)
        except Exception as exc:
            self._send_html(_render_page(error=str(exc)), status=HTTPStatus.NOT_FOUND)
            return
        if actor is not None:
            add_claim_audit_log(
                {
                    "claim_id": claim_id,
                    "action": "CLAIM_OPEN_837",
                    "actor_username": actor.get("username", ""),
                    "actor_name": actor.get("full_name", ""),
                    "details": f"Se descargo o abrio el archivo {filename}.",
                }
            )

        self._send_bytes(
            body,
            "text/plain; charset=utf-8",
            HTTPStatus.OK,
            {"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def do_GET(self) -> None:
        ensure_default_admin_user()
        run_due_eligibility_checks(MockClearinghouseConnector())
        url = urlsplit(self.path)
        path = url.path
        query = parse_qs(url.query)

        if path.startswith("/assets/"):
            self._serve_asset(path.removeprefix("/assets/"))
            return

        if path == "/agency-logo":
            current_user = self._current_user()
            if current_user is None:
                self._send_bytes(b"", "text/plain; charset=utf-8", HTTPStatus.UNAUTHORIZED)
                return
            agency_id = (query.get("agency_id") or [""])[0]
            self._serve_agency_logo(agency_id)
            return

        if path == "/user-avatar":
            current_user = self._current_user()
            if current_user is None:
                self._send_bytes(b"", "text/plain; charset=utf-8", HTTPStatus.UNAUTHORIZED)
                return
            username = (query.get("username") or [""])[0]
            self._serve_user_avatar(username)
            return

        if path == "/provider-document":
            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            if not self._page_allowed(current_user, "providers"):
                self._send_html(
                    _render_page(error="Tu rango no puede abrir documentos de providers.", current_page="dashboard", current_user=current_user),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            contract_id = (query.get("contract_id") or [""])[0]
            document_name = (query.get("document_name") or [""])[0]
            self._serve_provider_document(contract_id, document_name, current_user)
            return

        if path == "/client-document":
            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            if not self._page_allowed(current_user, "clients"):
                self._send_html(
                    _render_page(
                        error="Tu rango no puede abrir documentos de clientes.",
                        current_page="dashboard",
                        current_user=current_user,
                    ),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            client_id = (query.get("client_id") or [""])[0]
            document_name = (query.get("document_name") or [""])[0]
            self._serve_client_document(client_id, document_name, current_user)
            return

        if path == "/login":
            if self._current_user() is not None:
                current_user = self._current_user()
                self._send_redirect(_page_href(_default_page_for_user(current_user)))
                return
            self._send_html(_render_login_page())
            return

        if path == "/recover-password":
            self._send_html(_render_recovery_page())
            return

        if path == "/mfa":
            pending = self._pending_mfa()
            if pending is None:
                self._send_redirect("/login")
                return
            self._send_html(_render_mfa_page(str(pending.get("username", ""))))
            return

        if path.startswith("/exports/"):
            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            export_name = path.removeprefix("/exports/")
            export_page = {
                "claims.xls": "claims",
                "authorizations.xls": "claims",
                "eligibility_roster.xls": "eligibility",
                "clients.xls": "clients",
                "payer_enrollments.xls": "enrollments",
                "agencies.xls": "agencies",
                "provider_contracts.xls": "providers",
                "notifications.xls": "notifications",
                "era_archives.xls": "payments",
            }.get(export_name, "dashboard")
            if not self._page_allowed(current_user, export_page):
                self._send_html(
                    _render_page(
                        error="Tu rango no puede descargar ese reporte.",
                        current_page=export_page,
                        current_user=current_user,
                    ),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            self._serve_export(export_name, current_user)
            return

        if path == "/cms1500":
            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            if not self._page_allowed(current_user, "claims"):
                self._send_html(
                    _render_page(error="Tu rango no puede abrir CMS-1500.", current_page="dashboard", current_user=current_user),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            claim_id = (query.get("claim_id") or [""])[0]
            self._serve_cms1500(claim_id, current_user)
            return

        if path == "/era-download":
            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            if not self._page_allowed(current_user, "payments"):
                self._send_html(
                    _render_page(error="Tu rango no puede descargar ERA.", current_page="dashboard", current_user=current_user),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            archive_id = (query.get("archive_id") or [""])[0]
            self._serve_era_download(archive_id)
            return

        if path == "/claim-edi":
            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            if not self._page_allowed(current_user, "claims"):
                self._send_html(
                    _render_page(error="Tu rango no puede descargar 837.", current_page="dashboard", current_user=current_user),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            claim_id = (query.get("claim_id") or [""])[0]
            self._serve_claim_edi(claim_id, current_user)
            return

        if path in {"/", "/dashboard", "/hr", "/claims", "/eligibility", "/clients", "/aba_notes", "/payments", "/payers", "/enrollments", "/agencies", "/providers", "/agenda", "/notifications", "/users", "/security"}:
            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            current_page = {
                "/": "dashboard",
                "/dashboard": "dashboard",
                "/hr": "hr",
                "/claims": "claims",
                "/eligibility": "eligibility",
                "/clients": "clients",
                "/aba_notes": "aba_notes",
                "/payments": "payments",
                "/payers": "payers",
                "/enrollments": "enrollments",
                "/agencies": "agencies",
                "/providers": "providers",
                "/agenda": "agenda",
                "/notifications": "notifications",
                "/users": "users",
                "/security": "security",
            }[path]
            if not self._page_allowed(current_user, current_page):
                fallback_page = _default_page_for_user(current_user)
                self._send_html(
                    _render_page(
                        error="Tu rango no tiene permiso para abrir esta pagina.",
                        current_page=fallback_page,
                        current_user=current_user,
                    ),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            render_kwargs: dict[str, object] = {
                "current_page": current_page,
                "current_user": current_user,
            }
            if current_page in {"providers", "hr"}:
                edit_contract_id = str((query.get("edit_contract_id") or [""])[0]).strip()
                new_provider = str((query.get("new_provider") or [""])[0]).strip()
                if current_page == "providers" and new_provider:
                    render_kwargs["provider_contract_form"] = _provider_contract_form_defaults()
                    render_kwargs["active_panel"] = "provider_contract"
                if edit_contract_id:
                    contract = get_provider_contract_by_id(edit_contract_id)
                    if contract is not None:
                        render_kwargs["provider_contract_form"] = _provider_contract_form_from_record(contract)
                        render_kwargs["active_panel"] = "provider_contract"
            if current_page == "payers":
                edit_payer_id = str((query.get("edit_payer_id") or [""])[0]).strip()
                if edit_payer_id:
                    payer_config = get_payer_configuration_by_id(edit_payer_id)
                    if payer_config is not None:
                        render_kwargs["payer_config_form"] = _payer_config_form_from_record(payer_config)
                        render_kwargs["active_panel"] = "payer_config"
            if current_page == "clients":
                open_client_id = str((query.get("open_client_id") or [""])[0]).strip()
                edit_client_id = str((query.get("edit_client_id") or [""])[0]).strip()
                auth_client_id = str((query.get("auth_client_id") or [""])[0]).strip()
                edit_authorization_group_id = str((query.get("edit_authorization_group_id") or [""])[0]).strip()
                new_client = str((query.get("new_client") or [""])[0]).strip()
                selected_client_id = open_client_id or edit_client_id or auth_client_id
                if new_client:
                    render_kwargs["active_panel"] = "clients"
                if selected_client_id:
                    render_kwargs["selected_client_id"] = selected_client_id
                if selected_client_id:
                    client = get_client_by_id(selected_client_id)
                    if client is not None:
                        if edit_client_id:
                            render_kwargs["authorization_form"] = _authorization_form_from_client(client)
                            render_kwargs["client_form"] = _client_form_from_record(client)
                            render_kwargs["active_panel"] = "clients"
                        elif auth_client_id or edit_authorization_group_id:
                            render_kwargs["authorization_form"] = _authorization_form_from_client(client)
                            render_kwargs["active_panel"] = "authorization"
                            if edit_authorization_group_id:
                                group_items = get_authorization_group_records(edit_authorization_group_id)
                                if group_items:
                                    render_kwargs["authorization_form"] = _authorization_form_from_group(client, group_items)
            if current_page == "aba_notes":
                selected_log_id = str((query.get("log_id") or [""])[0]).strip()
                selected_session_id = str((query.get("appointment_id") or [""])[0]).strip()
                if selected_log_id:
                    render_kwargs["aba_notes_form"] = {"selected_log_id": selected_log_id}
                    render_kwargs["active_panel"] = "aba_notes"
                if selected_session_id:
                    render_kwargs["operations_selected_session_id"] = selected_session_id
            if current_page == "claims":
                selected_session_id = str((query.get("appointment_id") or [""])[0]).strip()
                if selected_session_id:
                    render_kwargs["operations_selected_session_id"] = selected_session_id
                    render_kwargs["active_panel"] = "claim"
            self._send_html(_render_page(**render_kwargs))
            return

        self._send_html(_render_page(error="Ruta no encontrada."), status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        ensure_default_admin_user()
        form_data, files = self._read_form_data()
        run_due_eligibility_checks(MockClearinghouseConnector())
        active_panel = {
            "/login": "",
            "/recover-password": "",
            "/reset-password": "",
            "/verify-mfa": "",
            "/logout": "",
            "/submit-claim": "claim",
            "/upload-837": "claim_upload",
            "/check-eligibility": "eligibility",
            "/compose-outlook-email": "notification",
            "/send-notification-outlook": "notification",
            "/resolve-notification": "notification",
            "/delete-notification": "notification",
            "/add-eligibility-to-roster": "eligibility",
            "/add-client": "clients",
            "/check-client-eligibility": "clients",
            "/check-all-clients-eligibility": "clients",
            "/add-aba-appointment": "aba_notes",
            "/aba-session-workflow": "aba_notes",
            "/aba-log-workflow": "aba_notes",
            "/ai-assistant-action": "",
            "/parse-837": "edi837",
            "/add-authorization": "authorization",
            "/delete-authorization-group": "authorization",
            "/add-payer-config": "payer_config",
            "/add-payer-enrollment": "payer_roster",
            "/add-agency": "agency",
            "/set-current-agency": "agency",
            "/add-provider-contract": "provider_contract",
            "/provider-admin-upload-document": "provider_contract",
            "/provider-self-upload-document": "provider_contract",
            "/approve-provider-document": "provider_contract",
            "/add-user": "user",
            "/add-calendar-event": "agenda",
            "/update-event-status": "agenda",
            "/add-note": "note",
            "/change-password": "",
            "/update-profile": "",
            "/start-mfa-setup": "",
            "/confirm-mfa-setup": "",
            "/disable-mfa": "",
            "/save-system-config": "system_config",
            "/save-document-config": "",
            "/add-roster-entry": "roster",
            "/parse-835": "era",
            "/upload-835": "era",
            "/generate-claim-batch": "claim_batch",
            "/transmit-claim": "claim_batch",
            "/transmit-batch-today": "claim_batch",
        }.get(self.path, "")
        current_page = {
            "/login": "dashboard",
            "/recover-password": "dashboard",
            "/reset-password": "dashboard",
            "/verify-mfa": "dashboard",
            "/logout": "dashboard",
            "/submit-claim": "claims",
            "/upload-837": "claims",
            "/check-eligibility": "eligibility",
            "/compose-outlook-email": "notifications",
            "/send-notification-outlook": "notifications",
            "/resolve-notification": "notifications",
            "/delete-notification": "notifications",
            "/add-eligibility-to-roster": "eligibility",
            "/add-client": "clients",
            "/check-client-eligibility": "clients",
            "/check-all-clients-eligibility": "clients",
            "/add-aba-appointment": "aba_notes",
            "/aba-session-workflow": "aba_notes",
            "/aba-log-workflow": "aba_notes",
            "/ai-assistant-action": "dashboard",
            "/parse-837": "claims",
            "/add-authorization": "clients",
            "/delete-authorization-group": "clients",
            "/add-payer-config": "payers",
            "/add-payer-enrollment": "enrollments",
            "/add-agency": "agencies",
            "/set-current-agency": "agencies",
            "/add-provider-contract": "providers",
            "/provider-admin-upload-document": "providers",
            "/provider-self-upload-document": "providers",
            "/approve-provider-document": "providers",
            "/add-user": "users",
            "/add-calendar-event": "agenda",
            "/update-event-status": "agenda",
            "/add-note": "agenda",
            "/change-password": "security",
            "/update-profile": "security",
            "/start-mfa-setup": "security",
            "/confirm-mfa-setup": "security",
            "/disable-mfa": "security",
            "/save-system-config": "security",
            "/save-document-config": "security",
            "/add-roster-entry": "eligibility",
            "/parse-835": "payments",
            "/upload-835": "payments",
            "/generate-claim-batch": "claims",
            "/transmit-claim": "claims",
            "/transmit-batch-today": "claims",
        }.get(self.path, "dashboard")
        if self.path in {"/add-authorization", "/delete-authorization-group"}:
            return_page = str(form_data.get("return_page", "")).strip().lower()
            if return_page in {"clients"}:
                current_page = return_page
            else:
                current_page = "clients"
        if self.path == "/ai-assistant-action":
            requested_page = str(form_data.get("return_page", "")).strip().lower()
            if requested_page in {"aba_notes", "claims", "providers"}:
                current_page = requested_page
            requested_panel = str(form_data.get("return_panel", "")).strip()
            if requested_panel:
                active_panel = requested_panel
        try:
            if self.path == "/login":
                attempted_username = form_data.get("username", "").strip().lower()
                auth_result = authenticate_user(form_data.get("username", ""), form_data.get("password", ""))
                if not auth_result.get("ok"):
                    add_system_audit_log(
                        {
                            "category": "security",
                            "entity_type": "user",
                            "entity_name": attempted_username,
                            "action": "USER_LOCKED_OUT" if auth_result.get("status") == "locked" else "USER_LOGIN_FAILED",
                            "actor_username": attempted_username,
                            "actor_name": attempted_username,
                            "details": str(auth_result.get("message", "No pude iniciar sesion.")),
                        }
                    )
                    self._send_html(_render_login_page(str(auth_result.get("message", "No pude iniciar sesion."))), status=HTTPStatus.UNAUTHORIZED)
                    return
                user = dict(auth_result.get("user", {}))
                if auth_result.get("requires_mfa"):
                    token = self._start_pending_mfa(user)
                    self._send_redirect("/mfa", _cookie_header(MFA_COOKIE_NAME, token, get_mfa_session_timeout_seconds()))
                    return
                user = complete_user_login(str(user.get("username", "")))
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": user.get("user_id", ""),
                        "entity_name": user.get("username", ""),
                        "action": "USER_LOGIN",
                        "actor_username": user.get("username", ""),
                        "actor_name": user.get("full_name", ""),
                        "details": "Inicio de sesion correcto.",
                    }
                )
                token = self._start_session(user)
                target = _default_page_for_user(user)
                self._send_redirect(_page_href(target), _cookie_header(SESSION_COOKIE_NAME, token, get_session_timeout_seconds()))
                return

            if self.path == "/recover-password":
                recovery = create_password_reset_token(form_data.get("username", ""))
                add_notification(
                    {
                        "category": "security",
                        "subject": f"Codigo de recuperacion para {recovery.get('username', '')}",
                        "message": (
                            f"Codigo temporal: {recovery.get('recovery_code', '')}. "
                            f"Vence el {recovery.get('expires_at', '')}."
                        ),
                        "related_id": recovery.get("user_id", ""),
                    }
                )
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": recovery.get("user_id", ""),
                        "entity_name": recovery.get("username", ""),
                        "action": "USER_PASSWORD_RECOVERY_REQUEST",
                        "actor_username": recovery.get("username", ""),
                        "actor_name": recovery.get("full_name", ""),
                        "details": f"Se genero un codigo temporal con vigencia hasta {recovery.get('expires_at', '')}.",
                    }
                )
                self._send_html(
                    _render_recovery_page(
                        result_title="Codigo generado",
                        result_body=(
                            f"Codigo temporal: {recovery.get('recovery_code', '')}. "
                            f"Vence el {recovery.get('expires_at', '')}."
                        ),
                        username=str(recovery.get("username", "")),
                    )
                )
                return

            if self.path == "/reset-password":
                username = form_data.get("username", "")
                new_password = form_data.get("new_password", "")
                if new_password != form_data.get("confirm_password", ""):
                    raise ValueError("La confirmacion del nuevo password no coincide.")
                if len(new_password.strip()) < 8:
                    raise ValueError("El nuevo password debe tener al menos 8 caracteres.")
                user = reset_password_with_recovery_code(
                    username,
                    form_data.get("recovery_code", ""),
                    new_password,
                )
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": user.get("user_id", ""),
                        "entity_name": user.get("username", ""),
                        "action": "USER_PASSWORD_RESET",
                        "actor_username": user.get("username", ""),
                        "actor_name": user.get("full_name", ""),
                        "details": "Password actualizado mediante codigo de recuperacion.",
                    }
                )
                self._send_html(
                    _render_recovery_page(
                        result_title="Password actualizado",
                        result_body="Ya puedes volver al login y entrar con tu password nuevo.",
                        username=username,
                    )
                )
                return

            if self.path == "/verify-mfa":
                pending = self._pending_mfa()
                if pending is None:
                    self._send_redirect("/login")
                    return
                username = str(pending.get("username", ""))
                try:
                    verify_user_mfa(username, form_data.get("mfa_code", ""))
                    user = complete_user_login(username)
                except Exception as exc:
                    add_system_audit_log(
                        {
                            "category": "security",
                            "entity_type": "user",
                            "entity_id": pending.get("user_id", ""),
                            "entity_name": username,
                            "action": "USER_MFA_FAILED",
                            "actor_username": username,
                            "actor_name": pending.get("full_name", username),
                            "details": str(exc),
                        }
                    )
                    self._send_html(_render_mfa_page(username, str(exc)), status=HTTPStatus.UNAUTHORIZED)
                    return
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": user.get("user_id", ""),
                        "entity_name": user.get("username", ""),
                        "action": "USER_MFA_VERIFIED",
                        "actor_username": user.get("username", ""),
                        "actor_name": user.get("full_name", ""),
                        "details": "Segundo factor validado correctamente.",
                    }
                )
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": user.get("user_id", ""),
                        "entity_name": user.get("username", ""),
                        "action": "USER_LOGIN",
                        "actor_username": user.get("username", ""),
                        "actor_name": user.get("full_name", ""),
                        "details": "Inicio de sesion completado despues de MFA.",
                    }
                )
                pending_token = self._cookie_map().get(MFA_COOKIE_NAME, "")
                if pending_token:
                    MFA_SESSIONS.pop(pending_token, None)
                token = self._start_session(user)
                self._send_redirect(
                    _page_href(_default_page_for_user(user)),
                    [
                        _cookie_header(SESSION_COOKIE_NAME, token, get_session_timeout_seconds()),
                        _expired_cookie_header(MFA_COOKIE_NAME),
                    ],
                )
                return

            if self.path == "/logout":
                if (current_user := self._current_user()) is not None:
                    add_system_audit_log(
                        {
                            "category": "security",
                            "entity_type": "user",
                            "entity_id": current_user.get("user_id", ""),
                            "entity_name": current_user.get("username", ""),
                            "action": "USER_LOGOUT",
                            "actor_username": current_user.get("username", ""),
                            "actor_name": current_user.get("full_name", ""),
                            "details": "Cierre de sesion manual.",
                        }
                    )
                self._end_session()
                self._send_redirect(
                    "/login",
                    [
                        _expired_cookie_header(SESSION_COOKIE_NAME),
                        _expired_cookie_header(MFA_COOKIE_NAME),
                    ],
                )
                return

            current_user = self._current_user()
            if current_user is None:
                self._send_redirect("/login")
                return
            if not self._page_allowed(current_user, current_page):
                self._send_html(
                    _render_page(
                        error="Tu rango no tiene permiso para esta accion.",
                        current_page=current_page,
                        current_user=current_user,
                    ),
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            post_permission_requirements: dict[str, tuple[str, ...]] = {
                "/add-provider-contract": ("providers.create", "providers.edit"),
                "/provider-admin-upload-document": ("providers.documents.verify",),
                "/provider-self-upload-document": ("providers.documents.view",),
                "/approve-provider-document": ("providers.documents.verify",),
                "/save-document-config": ("providers.documents.verify",),
                "/save-system-config": ("settings.manage",),
                "/add-user": ("users.edit",),
                "/add-client": ("clients.edit",),
                "/check-client-eligibility": ("clients.view", "clients.assigned.view"),
                "/check-all-clients-eligibility": ("eligibility.view",),
                "/add-authorization": ("clients.authorizations.edit",),
                "/delete-authorization-group": ("clients.authorizations.edit",),
                "/add-aba-appointment": ("sessions.create", "sessions.edit"),
                "/aba-session-workflow": ("sessions.edit",),
                "/aba-log-workflow": ("notes.write", "notes.review", "notes.close"),
                "/submit-claim": ("claims.submit",),
                "/upload-837": ("claims.submit",),
                "/generate-claim-batch": ("claims.submit",),
                "/transmit-claim": ("claims.submit",),
                "/transmit-batch-today": ("claims.submit",),
                "/parse-837": ("claims.view",),
                "/parse-835": ("billing.view",),
                "/upload-835": ("billing.view",),
                "/add-agency": ("settings.manage",),
                "/set-current-agency": ("settings.manage",),
                "/add-payer-config": ("billing.view",),
                "/add-payer-enrollment": ("providers.credentials.edit",),
                "/add-calendar-event": ("schedule.view",),
                "/update-event-status": ("schedule.view",),
                "/add-note": ("notes.write",),
            }
            required_permissions = post_permission_requirements.get(self.path, ())
            if required_permissions and not has_any_permission(current_user, required_permissions, list_provider_contracts()):
                self._send_html(
                    _render_page(
                        error="Tu usuario no tiene permiso para completar esta accion.",
                        current_page=current_page,
                        current_user=current_user,
                    ),
                    status=HTTPStatus.FORBIDDEN,
                )
                return

            if self.path == "/ai-assistant-action":
                action_name = str(form_data.get("action_name", "")).strip()
                action_catalog = available_ai_actions()
                action_meta = action_catalog.get(action_name)
                if action_meta is None:
                    raise ValueError("La accion de IA solicitada no existe.")
                expected_domain = str(action_meta.get("domain", "")).strip()
                if expected_domain and expected_domain != current_page:
                    raise ValueError("Esa accion de IA no corresponde al modulo actual.")

                render_kwargs: dict[str, object] = {
                    "current_page": current_page,
                    "current_user": current_user,
                    "active_panel": active_panel,
                }
                audit_payload = {
                    "category": "ai",
                    "actor_username": current_user.get("username", ""),
                    "actor_name": current_user.get("full_name", ""),
                }

                if action_name == "improve_session_note":
                    session_id = str(form_data.get("session_id", "")).strip()
                    allowed_aba_provider_ids = _allowed_aba_provider_ids_for_user(current_user)
                    ai_result_payload = run_ai_assistant_action(
                        action_name,
                        session_id=session_id,
                        provider_contract_ids=allowed_aba_provider_ids,
                    )
                    render_kwargs["operations_selected_session_id"] = session_id
                    selected_log_id = str(form_data.get("selected_log_id", "")).strip()
                    if selected_log_id:
                        render_kwargs["aba_notes_form"] = {"selected_log_id": selected_log_id}
                    audit_payload.update(
                        {
                            "entity_type": "aba_note",
                            "entity_id": session_id,
                            "entity_name": str(ai_result_payload.get("action_label", "")),
                            "action": "AI_IMPROVE_SESSION_NOTE",
                            "details": "Asistente IA ejecuto mejora de nota con payload estructurado minimo.",
                        }
                    )
                elif action_name == "explain_claim_denial":
                    claim_id = str(form_data.get("claim_id", "")).strip()
                    ai_result_payload = run_ai_assistant_action(action_name, claim_id=claim_id)
                    render_kwargs.update(_form_state_args(active_panel, form_data))
                    audit_payload.update(
                        {
                            "entity_type": "claim",
                            "entity_id": claim_id,
                            "entity_name": claim_id,
                            "action": "AI_EXPLAIN_CLAIM_DENIAL",
                            "details": "Asistente IA explico denial/rejection con datos estructurados del claim y auditoria reciente.",
                        }
                    )
                elif action_name == "check_missing_provider_documents":
                    contract_id = str(form_data.get("contract_id", "")).strip()
                    ai_result_payload = run_ai_assistant_action(action_name, contract_id=contract_id)
                    provider_record = get_provider_contract_by_id(contract_id)
                    if provider_record is not None:
                        render_kwargs["provider_contract_form"] = _provider_contract_form_from_record(provider_record)
                    audit_payload.update(
                        {
                            "entity_type": "provider",
                            "entity_id": contract_id,
                            "entity_name": str((provider_record or {}).get("provider_name", "")) if 'provider_record' in locals() else contract_id,
                            "action": "AI_CHECK_PROVIDER_DOCUMENTS",
                            "details": "Asistente IA reviso checklist documental usando solo resumen estructurado del expediente.",
                        }
                    )
                else:
                    raise ValueError("La accion de IA solicitada no existe.")

                add_system_audit_log(audit_payload)
                render_kwargs["ai_result"] = ai_result_payload
                self._send_html(_render_page(**render_kwargs))
                return

            if self.path == "/compose-outlook-email":
                recipient_email = form_data.get("recipient_email", "")
                recipient_label = form_data.get("recipient_label", "")
                subject = form_data.get("subject", "")
                message = form_data.get("message", "")
                email_mode = "send" if form_data.get("email_mode", "") == "send" else "draft"
                save_to_notifications = bool(form_data.get("save_to_notifications"))
                sender_profile = get_user_security_profile(str(current_user.get("username", "")))
                preferred_sender_email = str(sender_profile.get("email", "")).strip()
                if not preferred_sender_email:
                    preferred_sender_email = str((get_current_agency() or {}).get("notification_email", "")).strip()

                notification_record = None
                if save_to_notifications:
                    notification_record = add_notification(
                        {
                            "category": "manual_email",
                            "subject": subject,
                            "message": message,
                            "recipient_label": recipient_label,
                            "recipient_email": recipient_email,
                            "related_id": str(current_user.get("username", "")),
                        }
                    )
                try:
                    outlook_result = _send_via_outlook(
                        recipient_email=recipient_email,
                        subject=subject,
                        message=message,
                        preferred_sender_email=preferred_sender_email,
                        mode=email_mode,
                    )
                except Exception as exc:
                    if notification_record is not None:
                        update_notification_email_status(
                            str(notification_record.get("notification_id", "")),
                            "outlook_error",
                            str(exc),
                        )
                    raise

                if notification_record is not None:
                    update_notification_email_status(
                        str(notification_record.get("notification_id", "")),
                        str(outlook_result.get("status", "")),
                    )
                add_system_audit_log(
                    {
                        "category": "notification",
                        "entity_type": "notification",
                        "entity_id": str((notification_record or {}).get("notification_id", "")),
                        "entity_name": subject,
                        "action": "OUTLOOK_EMAIL_SENT" if email_mode == "send" else "OUTLOOK_EMAIL_DRAFTED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Correo para {recipient_email} por Outlook en modo {email_mode}. "
                            f"Cuenta usada: {str(outlook_result.get('account', '')).strip() or 'default'}."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Email preparado en Outlook" if email_mode == "draft" else "Email enviado con Outlook",
                        (
                            f"Destinatario: {recipient_email}\n"
                            f"Asunto: {subject}\n"
                            f"Modo: {'Draft en Outlook' if email_mode == 'draft' else 'Enviado con Outlook'}\n"
                            f"Cuenta usada: {str(outlook_result.get('account', '')).strip() or 'Cuenta default de Outlook'}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/send-notification-outlook":
                notification_id = form_data.get("notification_id", "")
                notification = get_notification_by_id(notification_id)
                if notification is None:
                    raise ValueError("No encontre la notificacion seleccionada.")
                recipient_email = str(notification.get("recipient_email", "")).strip()
                if not recipient_email:
                    raise ValueError("Esta notificacion no tiene un email destinatario configurado.")
                email_mode = "send" if form_data.get("email_mode", "") == "send" else "draft"
                sender_profile = get_user_security_profile(str(current_user.get("username", "")))
                preferred_sender_email = str(sender_profile.get("email", "")).strip()
                if not preferred_sender_email:
                    preferred_sender_email = str((get_current_agency() or {}).get("notification_email", "")).strip()
                try:
                    outlook_result = _send_via_outlook(
                        recipient_email=recipient_email,
                        subject=str(notification.get("subject", "")),
                        message=str(notification.get("message", "")),
                        preferred_sender_email=preferred_sender_email,
                        mode=email_mode,
                    )
                except Exception as exc:
                    update_notification_email_status(notification_id, "outlook_error", str(exc))
                    raise
                updated_notification = update_notification_email_status(
                    notification_id,
                    str(outlook_result.get("status", "")),
                )
                add_system_audit_log(
                    {
                        "category": "notification",
                        "entity_type": "notification",
                        "entity_id": str(updated_notification.get("notification_id", "")),
                        "entity_name": str(updated_notification.get("subject", "")),
                        "action": "OUTLOOK_NOTIFICATION_SENT" if email_mode == "send" else "OUTLOOK_NOTIFICATION_DRAFTED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Notificacion a {recipient_email} por Outlook en modo {email_mode}. "
                            f"Cuenta usada: {str(outlook_result.get('account', '')).strip() or 'default'}."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Notificacion preparada" if email_mode == "draft" else "Notificacion enviada",
                        (
                            f"Asunto: {updated_notification.get('subject', '')}\n"
                            f"Destinatario: {recipient_email}\n"
                            f"Estado: {updated_notification.get('email_status', '')}\n"
                            f"Cuenta Outlook: {str(outlook_result.get('account', '')).strip() or 'Cuenta default de Outlook'}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path in {"/resolve-notification", "/delete-notification"}:
                notification_id = str(form_data.get("notification_id", "")).strip()
                notification = get_notification_by_id(notification_id)
                if notification is None:
                    raise ValueError("No encontre la notificacion seleccionada.")
                new_state = "handled" if self.path == "/resolve-notification" else "deleted"
                updated_notification = update_notification_state(
                    notification_id,
                    new_state,
                    acted_by=str(current_user.get("username", "")),
                )
                add_system_audit_log(
                    {
                        "category": "notification",
                        "entity_type": "notification",
                        "entity_id": str(updated_notification.get("notification_id", "")),
                        "entity_name": str(updated_notification.get("subject", "")),
                        "action": "NOTIFICATION_HANDLED" if new_state == "handled" else "NOTIFICATION_DELETED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Notificacion marcada como {'atendida' if new_state == 'handled' else 'borrada'} "
                            f"desde el centro de notificaciones. Categoria: {updated_notification.get('category', '')}."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Notificacion atendida" if new_state == "handled" else "Notificacion borrada",
                        (
                            f"Asunto: {updated_notification.get('subject', '')}\n"
                            f"Categoria: {updated_notification.get('category', '')}\n"
                            f"Estado nuevo: {'atendida' if new_state == 'handled' else 'borrada'}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/submit-claim":
                if form_data.get("payload", "").strip():
                    claim = _build_claim(json.loads(form_data["payload"]))
                else:
                    claim = _build_claim(_claim_payload_from_form(form_data))
                edi_payload = Claim837Builder().build_professional_claim(claim)
                stored = add_claim_record(claim)
                source_session_id = str(form_data.get("session_source_id", "")).strip()
                source_session = get_aba_appointment_detail(source_session_id, _allowed_aba_provider_ids_for_user(current_user)) if source_session_id else None
                if source_session is not None and str(source_session.get("authorization_consumed_at", "")).strip():
                    authorization_updates = [
                        {
                            "authorization_number": str(source_session.get("authorization_number", "")),
                            "cpt_code": str(source_session.get("service_code", "")),
                            "used_units": int(float(source_session.get("authorization_consumed_units", 0) or 0)),
                            "remaining_units": float(source_session.get("authorization_remaining_after", 0) or 0),
                            "status": "already_consumed_by_session_workflow",
                        }
                    ]
                else:
                    authorization_updates = consume_authorization_units(claim)
                if source_session_id:
                    attach_claim_to_aba_sessions(session_ids=[source_session_id], claim_id=str(stored.get("claim_id", "")))
                body = _pretty_json(
                    {
                        "batch_record": stored,
                        "authorization_updates": authorization_updates,
                        "x12_837": edi_payload,
                    }
                )
                add_claim_audit_log(
                    {
                        "agency_id": stored.get("agency_id", ""),
                        "agency_name": stored.get("agency_name", ""),
                        "claim_id": stored.get("claim_id", ""),
                        "action": "CLAIM_STAGED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": "Claim guardado manualmente en el batch.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Claim guardado en batch",
                        body,
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/upload-837":
                upload = files.get("edi837_file")
                if upload is None or not upload.content.strip():
                    raise ValueError("Selecciona un archivo 837 antes de subirlo.")
                upload_text = upload.content.decode("utf-8", errors="replace")
                parsed_claims = Claim837Parser().parse_many(upload_text)
                if not parsed_claims:
                    raise ValueError("No pude encontrar ningun claim dentro de ese archivo 837.")
                stored_records = add_uploaded_claim_records(parsed_claims, upload.filename)
                for stored in stored_records:
                    add_claim_audit_log(
                        {
                            "agency_id": stored.get("agency_id", ""),
                            "agency_name": stored.get("agency_name", ""),
                            "claim_id": stored.get("claim_id", ""),
                            "action": "CLAIM_UPLOAD_837",
                            "actor_username": current_user.get("username", ""),
                            "actor_name": current_user.get("full_name", ""),
                            "details": f"Archivo 837 subido: {stored.get('source_file_name', '')}.",
                        }
                    )
                self._send_html(
                    _render_page(
                        "Archivo 837 guardado en batch",
                        _uploaded_837_batch_summary(upload.filename, stored_records),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/check-eligibility":
                if form_data.get("payload", "").strip():
                    request = _build_eligibility_request(json.loads(form_data["payload"]))
                    matched_client = find_client_for_eligibility(
                        request.member_id,
                        request.patient_first_name,
                        request.patient_last_name,
                    )
                else:
                    matched_client = find_client_for_eligibility(
                        form_data.get("member_id", ""),
                        form_data.get("patient_first_name", ""),
                        form_data.get("patient_last_name", ""),
                    )
                    payload = _eligibility_payload_from_form(form_data)
                    if matched_client is not None:
                        payload["payer_id"] = str(matched_client.get("payer_id", ""))
                        payload["provider_npi"] = str(matched_client.get("provider_npi", ""))
                    request = _build_eligibility_request(payload)
                response = EligibilityService(MockClearinghouseConnector()).check(request)
                payer_name = str((matched_client or {}).get("payer_name", "")).strip()
                history = add_eligibility_history_entry(
                    {
                        "agency_id": (matched_client or {}).get("agency_id", ""),
                        "agency_name": (matched_client or {}).get("agency_name", ""),
                        "insured_name": (
                            f"{request.patient_last_name}, " if request.patient_last_name else ""
                        )
                        + " ".join(
                            part
                            for part in [
                                request.patient_first_name,
                                getattr(request, "patient_middle_name", ""),
                            ]
                            if str(part).strip()
                        ),
                        "payer_name": payer_name,
                        "policy_number": request.member_id,
                        "benefit": "30",
                        "procedure": "",
                        "status": "complete",
                        "service_date": request.service_date,
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                    }
                )
                add_notification(
                    {
                        "category": "eligibility",
                        "subject": f"Elegibilidad {response.coverage_status} para {request.patient_first_name} {request.patient_last_name}",
                        "message": (
                            f"La consulta directa de elegibilidad devolvio {response.coverage_status} "
                            f"para {request.patient_first_name} {request.patient_last_name}."
                        ),
                        "related_id": request.member_id,
                    }
                )
                body = "\n".join(
                    [
                        f"Paciente: {request.patient_first_name} {request.patient_last_name}".strip(),
                        f"Policy #: {request.member_id}",
                        f"Payer: {payer_name or 'No encontrado en la base local'}",
                        f"Status: {response.coverage_status}",
                        f"Plan: {response.plan_name or 'Sin plan'}",
                        f"DOS: {request.service_date}",
                        f"User: {current_user.get('full_name', '')}",
                        f"History ID: {history.get('history_id', '')}",
                    ]
                )
                self._send_html(
                    _render_page(
                        "Resultado de elegibilidad",
                        body,
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/add-eligibility-to-roster":
                matched_client = find_client_for_eligibility(
                    form_data.get("member_id", ""),
                    form_data.get("patient_first_name", ""),
                    form_data.get("patient_last_name", ""),
                )
                record = add_roster_entry(
                    {
                        "payer_id": form_data.get("payer_id", "") or str((matched_client or {}).get("payer_id", "")),
                        "provider_npi": form_data.get("provider_npi", "") or str((matched_client or {}).get("provider_npi", "")),
                        "member_id": form_data.get("member_id", ""),
                        "patient_first_name": form_data.get("patient_first_name", ""),
                        "patient_last_name": form_data.get("patient_last_name", ""),
                        "patient_birth_date": form_data.get("patient_birth_date", ""),
                        "service_date": form_data.get("service_date", ""),
                        "active": True,
                    }
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "client",
                        "entity_type": "client",
                        "entity_id": record.get("roster_id", ""),
                        "entity_name": f"{record.get('patient_first_name', '')} {record.get('patient_last_name', '')}".strip(),
                        "action": "CLIENT_ADDED_TO_ROSTER",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Paciente agregado al roster automatico con proxima corrida {record.get('next_run_date', '')}."
                        ),
                    }
                )
                roster_summary = "\n".join(
                    [
                        f"Paciente: {record.get('patient_first_name', '')} {record.get('patient_last_name', '')}".strip(),
                        f"Policy #: {record.get('member_id', '')}",
                        f"Payer ID: {record.get('payer_id', '') or 'Tomado automaticamente si existe en clientes'}",
                        f"Proxima corrida: {record.get('next_run_date', '')}",
                        "El paciente ya quedo en el roster automatico del 1 y 15.",
                    ]
                )
                self._send_html(
                    _render_page(
                        "Paciente agregado al roster automatico",
                        roster_summary,
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/add-client":
                documents = []
                for index, document_name in enumerate(list_client_required_documents()):
                    upload = files.get(f"client_document_{index}_file")
                    documents.append(
                        {
                            "document_name": document_name,
                            "issued_date": form_data.get(f"client_document_{index}_issued_date", ""),
                            "expiration_date": form_data.get(f"client_document_{index}_expiration_date", ""),
                            "status": form_data.get(f"client_document_{index}_status", "Pending"),
                            "file_name": upload.filename if upload is not None else "",
                            "file_content": upload.content if upload is not None else b"",
                        }
                    )
                record = add_client(
                    {
                        "client_id": form_data.get("client_id", ""),
                        "first_name": form_data.get("first_name", ""),
                        "last_name": form_data.get("last_name", ""),
                        "preferred_language": form_data.get("preferred_language", ""),
                        "diagnosis": form_data.get("diagnosis", ""),
                        "member_id": form_data.get("member_id", ""),
                        "birth_date": form_data.get("birth_date", ""),
                        "service_date": form_data.get("service_date", ""),
                        "payer_name": form_data.get("payer_name", ""),
                        "payer_id": form_data.get("payer_id", ""),
                        "insurance_effective_date": form_data.get("insurance_effective_date", ""),
                        "subscriber_name": form_data.get("subscriber_name", ""),
                        "subscriber_id": form_data.get("subscriber_id", ""),
                        "provider_npi": form_data.get("provider_npi", ""),
                        "site_location": form_data.get("site_location", ""),
                        "county_name": form_data.get("county_name", ""),
                        "gender": form_data.get("gender", ""),
                        "medicaid_id": form_data.get("medicaid_id", ""),
                        "address_line1": form_data.get("address_line1", ""),
                        "address_city": form_data.get("address_city", ""),
                        "address_state": form_data.get("address_state", ""),
                        "address_zip_code": form_data.get("address_zip_code", ""),
                        "caregiver_name": form_data.get("caregiver_name", ""),
                        "caregiver_relationship": form_data.get("caregiver_relationship", ""),
                        "caregiver_phone": form_data.get("caregiver_phone", ""),
                        "caregiver_email": form_data.get("caregiver_email", ""),
                        "physician_name": form_data.get("physician_name", ""),
                        "physician_npi": form_data.get("physician_npi", ""),
                        "physician_phone": form_data.get("physician_phone", ""),
                        "physician_address": form_data.get("physician_address", ""),
                        "bcba_contract_id": form_data.get("bcba_contract_id", ""),
                        "bcaba_contract_id": form_data.get("bcaba_contract_id", ""),
                        "rbt_contract_id": form_data.get("rbt_contract_id", ""),
                        "notes": form_data.get("notes", ""),
                        "active": bool(form_data.get("active")),
                        "auto_eligibility": bool(form_data.get("auto_eligibility")),
                        "documents": documents,
                    }
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "client",
                        "entity_type": "client",
                        "entity_id": record.get("client_id", ""),
                        "entity_name": f"{record.get('first_name', '')} {record.get('last_name', '')}".strip(),
                        "action": "CLIENT_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Cliente guardado con payer {record.get('payer_name', '')} y expediente "
                            f"{record.get('delivered_documents', 0)}/{record.get('total_documents', 0)}."
                        ),
                    }
                )
                client_summary = "\n".join(
                    [
                        f"Cliente: {record.get('first_name', '')} {record.get('last_name', '')}".strip(),
                        f"Policy #: {record.get('member_id', '')}",
                        f"Payer: {record.get('payer_name', '') or 'Pendiente'}",
                        f"Documentos entregados: {record.get('delivered_documents', 0)}/{record.get('total_documents', 0)}",
                        f"Auto roster: {'Si' if record.get('auto_eligibility', True) else 'No'}",
                    ]
                )
                self._send_html(
                    _render_page(
                        "Cliente guardado en la base local",
                        client_summary,
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        client_form=_client_form_from_record(record),
                        authorization_form=_authorization_form_from_client(record),
                    )
                )
                return

            if self.path == "/check-client-eligibility":
                updates = run_client_eligibility_checks(
                    MockClearinghouseConnector(),
                    [form_data.get("client_id", "")],
                    actor_username=str(current_user.get("username", "")),
                    actor_name=str(current_user.get("full_name", "")),
                )
                self._send_html(
                    _render_page(
                        "Elegibilidad del cliente procesada",
                        _pretty_json({"results": updates}),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/check-all-clients-eligibility":
                updates = run_client_eligibility_checks(
                    MockClearinghouseConnector(),
                    actor_username=str(current_user.get("username", "")),
                    actor_name=str(current_user.get("full_name", "")),
                )
                self._send_html(
                    _render_page(
                        "Elegibilidad por lote completada",
                        _pretty_json({"results": updates, "count": len(updates)}),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/parse-837":
                if form_data.get("payload", "").strip():
                    parsed = Claim837Parser().parse(form_data.get("payload", ""))
                else:
                    parsed = _build_parsed_837_from_form(form_data)
                self._send_html(
                    _render_page(
                        "Resultado de lectura 837",
                        _pretty_json(asdict(parsed)),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/parse-835":
                if form_data.get("payload", "").strip():
                    parsed = Era835Parser().parse(form_data.get("payload", ""))
                    raw_era_content = form_data.get("payload", "")
                else:
                    parsed = _build_parsed_835_from_form(form_data)
                    raw_era_content = _build_835_payload(parsed)
                claim_updates = apply_era_to_claims(parsed)
                archive = add_era_archive(parsed, raw_era_content, f"era_{parsed.transaction_set_control_number or 'manual'}.txt", claim_updates)
                for update in claim_updates:
                    add_claim_audit_log(
                        {
                            "claim_id": update.get("claim_id", ""),
                            "action": "CLAIM_ERA_APPLIED",
                            "actor_username": current_user.get("username", ""),
                            "actor_name": current_user.get("full_name", ""),
                            "details": f"ERA aplico estatus {update.get('status', '')} y pagado {update.get('paid_amount', 0)}.",
                        }
                    )
                body = _pretty_json(asdict(parsed))
                if claim_updates:
                    body = _pretty_json({"era": asdict(parsed), "claim_updates": claim_updates, "archive": archive})
                self._send_html(
                    _render_page(
                        "Resultado de remesa ERA 835",
                        body,
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/upload-835":
                upload = files.get("edi835_file")
                if upload is None or not upload.content.strip():
                    raise ValueError("Selecciona un archivo 835 antes de importarlo.")
                raw_era_content = upload.content.decode("utf-8", errors="replace")
                parsed = Era835Parser().parse(raw_era_content)
                claim_updates = apply_era_to_claims(parsed)
                archive = add_era_archive(parsed, raw_era_content, upload.filename, claim_updates)
                for update in claim_updates:
                    add_claim_audit_log(
                        {
                            "claim_id": update.get("claim_id", ""),
                            "action": "CLAIM_ERA_UPLOAD",
                            "actor_username": current_user.get("username", ""),
                            "actor_name": current_user.get("full_name", ""),
                            "details": f"Archivo 835 importado con resultado {update.get('status', '')}.",
                        }
                    )
                self._send_html(
                    _render_page(
                        "Archivo ERA 835 importado",
                        _pretty_json({"era": asdict(parsed), "claim_updates": claim_updates, "archive": archive}),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/add-authorization":
                authorization_lines = []
                for index in range(1, MAX_AUTHORIZATION_LINES + 1):
                    cpt_code = _normalize_cpt_code(form_data.get(f"authorization_line_{index}_cpt_code", ""))
                    total_units = form_data.get(f"authorization_line_{index}_total_units", "").strip()
                    remaining_units = form_data.get(f"authorization_line_{index}_remaining_units", "").strip()
                    if not any([cpt_code, total_units, remaining_units]):
                        continue
                    if not cpt_code:
                        raise ValueError(f"Falta el CPT en la linea {index} de la autorizacion.")
                    if not total_units:
                        raise ValueError(f"Falta el total de units en la linea {index} de la autorizacion.")
                    authorization_lines.append(
                        {
                            "cpt_code": cpt_code,
                            "total_units": total_units,
                            "remaining_units": remaining_units or total_units,
                        }
                    )
                if not authorization_lines:
                    raise ValueError("Agrega por lo menos un CPT con units en la autorizacion.")
                authorization_payload = {
                    "authorization_group_id": form_data.get("authorization_group_id", ""),
                    "client_id": form_data.get("client_id", ""),
                    "patient_member_id": form_data.get("patient_member_id", ""),
                    "patient_name": form_data.get("patient_name", ""),
                    "payer_name": form_data.get("payer_name", ""),
                    "authorization_number": form_data.get("authorization_number", ""),
                    "start_date": form_data.get("start_date", ""),
                    "end_date": form_data.get("end_date", ""),
                    "lines": authorization_lines,
                    "notes": form_data.get("notes", ""),
                }
                editing_group_id = str(form_data.get("authorization_group_id", "")).strip()
                record = (
                    update_authorization_group(authorization_payload)
                    if editing_group_id
                    else add_authorization(authorization_payload)
                )
                selected_client = get_client_by_id(str(form_data.get("client_id", "")).strip())
                add_system_audit_log(
                    {
                        "agency_id": str((get_current_agency() or {}).get("agency_id", "")),
                        "agency_name": str((get_current_agency() or {}).get("agency_name", "")),
                        "category": "authorization",
                        "entity_type": "client",
                        "entity_id": str(form_data.get("client_id", "")).strip(),
                        "entity_name": str(record.get("patient_name", "")),
                        "action": "AUTHORIZATION_UPDATED" if editing_group_id else "AUTHORIZATION_CREATED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Autorizacion {record.get('authorization_number', '')} "
                            f"con {record.get('line_count', 0)} linea(s), payer {record.get('payer_name', '')}, "
                            f"vigencia {record.get('start_date', '')} a {record.get('end_date', '')}, "
                            f"member ID {record.get('patient_member_id', '')}."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Autorizacion actualizada" if editing_group_id else "Autorizacion guardada",
                        (
                            f"Se guardaron {record.get('line_count', 0)} lineas bajo la autorizacion "
                            f"{record.get('authorization_number', '')} para {record.get('patient_name', '')}. "
                            f"Vigencia: {record.get('start_date', '')} a {record.get('end_date', '')}."
                        ),
                        current_page="clients",
                        current_user=current_user,
                        active_panel="authorization",
                        selected_client_id=str(form_data.get("client_id", "")).strip(),
                        authorization_form=_authorization_form_from_client(selected_client) if selected_client is not None else _authorization_form_defaults(),
                    )
                )
                return

            if self.path == "/delete-authorization-group":
                client_id = str(form_data.get("client_id", "")).strip()
                group_id = str(form_data.get("authorization_group_id", "")).strip()
                removed = delete_authorization_group(group_id)
                selected_client = get_client_by_id(client_id)
                add_system_audit_log(
                    {
                        "agency_id": str((get_current_agency() or {}).get("agency_id", "")),
                        "agency_name": str((get_current_agency() or {}).get("agency_name", "")),
                        "category": "authorization",
                        "entity_type": "client",
                        "entity_id": client_id,
                        "entity_name": str(removed.get("patient_name", "")),
                        "action": "AUTHORIZATION_DELETED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Se borro la autorizacion {removed.get('authorization_number', '')} "
                            f"con {removed.get('line_count', 0)} linea(s)."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Autorizacion borrada",
                        (
                            f"Se elimino la autorizacion {removed.get('authorization_number', '')} "
                            f"del cliente {removed.get('patient_name', '')}."
                        ),
                        current_page="clients",
                        current_user=current_user,
                        active_panel="authorization",
                        selected_client_id=client_id,
                        authorization_form=_authorization_form_from_client(selected_client) if selected_client is not None else _authorization_form_defaults(),
                    )
                )
                return

            if self.path == "/add-agency":
                agency_logo = files.get("agency_logo")
                record = add_agency(
                    {
                        "agency_id": form_data.get("agency_id", ""),
                        "agency_name": form_data.get("agency_name", ""),
                        "agency_code": form_data.get("agency_code", ""),
                        "notification_email": form_data.get("notification_email", ""),
                        "contact_name": form_data.get("contact_name", ""),
                        "notes": form_data.get("notes", ""),
                        "logo_file_name": agency_logo.filename if agency_logo is not None else "",
                        "logo_file_content": agency_logo.content if agency_logo is not None else b"",
                    }
                )
                agency_summary = "\n".join(
                    [
                        f"Agencia: {record.get('agency_name', '')}",
                        f"Codigo: {record.get('agency_code', '') or 'Sin codigo'}",
                        f"Email de notificaciones: {record.get('notification_email', '') or 'Pendiente'}",
                        f"Contacto: {record.get('contact_name', '') or 'Pendiente'}",
                        (
                            f"Logo: cargado correctamente ({record.get('logo_file_name', '')})"
                            if record.get("logo_file_name")
                            else "Logo: todavia no hay logo cargado"
                        ),
                    ]
                )
                self._send_html(
                    _render_page(
                        "Agencia guardada",
                        agency_summary,
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/set-current-agency":
                record = set_current_agency(form_data.get("agency_id", ""))
                self._send_html(
                    _render_page(
                        "Agencia activa actualizada",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/add-payer-config":
                rate_lines = []
                for index, cpt_code in enumerate(SUPPORTED_CPT_CODES, start=1):
                    rate_lines.append(
                        {
                            "cpt_code": form_data.get(f"payer_rate_{index}_cpt_code", cpt_code),
                            "billing_code": form_data.get(f"payer_rate_{index}_billing_code", cpt_code),
                            "hcpcs_code": form_data.get(f"payer_rate_{index}_hcpcs_code", ""),
                            "unit_price": form_data.get(f"payer_rate_{index}_unit_price", ""),
                        }
                    )
                record = save_payer_configuration(
                    {
                        "payer_config_id": form_data.get("payer_config_id", ""),
                        "payer_name": form_data.get("payer_name", ""),
                        "payer_id": form_data.get("payer_id", ""),
                        "plan_type": form_data.get("plan_type", "COMMERCIAL"),
                        "brand_color": form_data.get("brand_color", "#0d51b8"),
                        "clearinghouse_name": form_data.get("clearinghouse_name", ""),
                        "clearinghouse_payer_id": form_data.get("clearinghouse_payer_id", ""),
                        "clearinghouse_receiver_id": form_data.get("clearinghouse_receiver_id", ""),
                        "notes": form_data.get("notes", ""),
                        "active": bool(form_data.get("active")),
                        "rate_lines": rate_lines,
                    }
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "payer",
                        "entity_type": "payer",
                        "entity_id": record.get("payer_config_id", ""),
                        "entity_name": record.get("payer_name", ""),
                        "action": "PAYER_CONFIG_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Clearinghouse {record.get('clearinghouse_name', '') or 'sin definir'} | "
                            f"CPTs activos {record.get('active_rate_count', 0)}."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Payer guardado",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        payer_config_form=_payer_config_form_from_record(record),
                    )
                )
                return

            if self.path == "/add-payer-enrollment":
                record = add_payer_enrollment(
                    {
                        "contract_id": form_data.get("contract_id", ""),
                        "provider_name": form_data.get("provider_name", ""),
                        "ssn": form_data.get("ssn", ""),
                        "npi": form_data.get("npi", ""),
                        "medicaid_id": form_data.get("medicaid_id", ""),
                        "payer_name": form_data.get("payer_name", ""),
                        "site_location": form_data.get("site_location", ""),
                        "county_name": form_data.get("county_name", ""),
                        "credentialing_owner_name": form_data.get("credentialing_owner_name", ""),
                        "supervisor_name": form_data.get("supervisor_name", ""),
                        "enrollment_status": form_data.get("enrollment_status", "SUBMITTED"),
                        "credentials_submitted_date": form_data.get("credentials_submitted_date", ""),
                        "effective_date": form_data.get("effective_date", ""),
                        "notes": form_data.get("notes", ""),
                    }
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "enrollment",
                        "entity_type": "enrollment",
                        "entity_id": record.get("enrollment_id", ""),
                        "entity_name": f"{record.get('provider_name', '')} | {record.get('payer_name', '')}",
                        "action": "ENROLLMENT_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Estatus {record.get('enrollment_status', '')}.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Enrollment guardado",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/add-provider-contract":
                if not (
                    has_permission(current_user, "providers.create", list_provider_contracts())
                    or has_permission(current_user, "providers.edit", list_provider_contracts())
                ):
                    raise ValueError("Solo Administracion o Recursos Humanos pueden gestionar contrataciones de providers.")
                documents = []
                for index, document_name in enumerate(list_provider_required_documents()):
                    upload = files.get(f"provider_document_{index}_file")
                    has_upload = upload is not None and bool(upload.content)
                    documents.append(
                        {
                            "document_name": document_name,
                            "issued_date": form_data.get(f"provider_document_{index}_issued_date", ""),
                            "expiration_date": form_data.get(f"provider_document_{index}_expiration_date", ""),
                            "status": form_data.get(f"provider_document_{index}_status", "Pending"),
                            "file_name": upload.filename if has_upload else "",
                            "file_content": upload.content if has_upload else b"",
                            "actor_role": str(current_user.get("role", "")) if has_upload else "",
                            "actor_username": str(current_user.get("username", "")) if has_upload else "",
                            "actor_name": str(current_user.get("full_name", "")) if has_upload else "",
                            "submitted_at": datetime.now().strftime("%m/%d/%Y %H:%M") if has_upload else "",
                        }
                    )
                record = add_provider_contract(
                    {
                        "contract_id": form_data.get("contract_id", ""),
                        "provider_name": form_data.get("provider_name", ""),
                        "worker_category": form_data.get("worker_category", "PROVIDER"),
                        "provider_type": form_data.get("provider_type", "BCBA"),
                        "office_department": form_data.get("office_department", ""),
                        "provider_npi": form_data.get("provider_npi", ""),
                        "contract_stage": form_data.get("contract_stage", "NEW"),
                        "start_date": form_data.get("start_date", ""),
                        "expected_start_date": form_data.get("expected_start_date", ""),
                        "site_location": form_data.get("site_location", ""),
                        "county_name": form_data.get("county_name", ""),
                        "recruiter_name": form_data.get("recruiter_name", ""),
                        "supervisor_name": form_data.get("supervisor_name", ""),
                        "credentialing_owner_name": form_data.get("credentialing_owner_name", ""),
                        "office_reviewer_name": form_data.get("office_reviewer_name", ""),
                        "assigned_clients": form_data.get("assigned_clients", ""),
                        "credentialing_start_date": form_data.get("credentialing_start_date", ""),
                        "notes": form_data.get("notes", ""),
                        "documents": documents,
                    }
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "provider",
                        "entity_type": "provider",
                        "entity_id": record.get("contract_id", ""),
                        "entity_name": record.get("provider_name", ""),
                        "action": "PROVIDER_CONTRACT_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Expediente documental {record.get('completed_documents', record.get('delivered_documents', 0))}/"
                            f"{record.get('total_documents', 0)} con avance {record.get('progress_percent', 0)}%."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Contratacion guardada",
                        (
                            f"Provider: {record.get('provider_name', '')}\n"
                            f"Categoria: {record.get('worker_category', '')}\n"
                            f"Tipo: {record.get('provider_type', '')}\n"
                            f"Lugar: {record.get('site_location', '') or 'Pendiente'} / {record.get('county_name', '') or 'Pendiente'}\n"
                            f"Etapa: {record.get('contract_stage', '')}\n"
                            f"Checklist completado: {record.get('completed_documents', 0)}/{record.get('total_documents', 0)}\n"
                            f"Credenciales: {record.get('credentialing_owner_name', '') or 'Sin asignar'}\n"
                            f"Expirados: {record.get('expired_documents', 0)}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        provider_contract_form=_provider_contract_form_from_record(record),
                    )
                )
                return

            if self.path == "/provider-admin-upload-document":
                if not has_permission(current_user, "providers.documents.verify", list_provider_contracts()):
                    raise ValueError("Solo Administracion o Recursos Humanos pueden subir documentos a este expediente.")
                upload = files.get("provider_admin_document_file")
                if upload is None or not upload.content.strip():
                    raise ValueError("Selecciona un archivo antes de subirlo.")
                record = submit_provider_document(
                    contract_id=form_data.get("contract_id", ""),
                    document_name=form_data.get("document_name", ""),
                    issued_date=form_data.get("provider_document_issued_date", ""),
                    expiration_date=form_data.get("provider_document_expiration_date", ""),
                    file_name=upload.filename,
                    file_content=upload.content,
                    actor_username=str(current_user.get("username", "")),
                    actor_name=str(current_user.get("full_name", "")),
                    actor_role=str(current_user.get("role", "")),
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "provider",
                        "entity_type": "provider",
                        "entity_id": record.get("contract_id", ""),
                        "entity_name": record.get("provider_name", ""),
                        "action": "PROVIDER_DOCUMENT_UPLOADED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Documento cargado: {form_data.get('document_name', '')}.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Documento guardado",
                        (
                            f"Provider: {record.get('provider_name', '')}\n"
                            f"Documento: {form_data.get('document_name', '')}\n"
                            "El archivo quedo guardado en el expediente y pendiente de aprobacion si aplica."
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        provider_contract_form=_provider_contract_form_from_record(record),
                    )
                )
                return

            if self.path == "/provider-self-upload-document":
                if not is_provider_role(current_user, list_provider_contracts()):
                    raise ValueError("Solo un usuario proveedor puede usar este formulario.")
                linked_provider_name = str(current_user.get("linked_provider_name", "")).strip().lower()
                if not linked_provider_name:
                    raise ValueError("Tu usuario proveedor todavia no tiene un provider vinculado.")
                contract = get_provider_contract_by_id(form_data.get("contract_id", ""))
                if contract is None:
                    raise ValueError("No encontre el expediente del provider.")
                if str(contract.get("provider_name", "")).strip().lower() != linked_provider_name:
                    raise ValueError("Solo puedes subir documentos para tu propio expediente.")
                upload = files.get("provider_self_document_file")
                if upload is None or not upload.content.strip():
                    raise ValueError("Selecciona un archivo antes de enviarlo.")
                record = submit_provider_document(
                    contract_id=form_data.get("contract_id", ""),
                    document_name=form_data.get("document_name", ""),
                    issued_date=form_data.get("provider_document_issued_date", ""),
                    expiration_date=form_data.get("provider_document_expiration_date", ""),
                    file_name=upload.filename,
                    file_content=upload.content,
                    actor_username=str(current_user.get("username", "")),
                    actor_name=str(current_user.get("full_name", "")),
                    actor_role=str(current_user.get("role", "")),
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "provider",
                        "entity_type": "provider",
                        "entity_id": record.get("contract_id", ""),
                        "entity_name": record.get("provider_name", ""),
                        "action": "PROVIDER_DOCUMENT_SUBMITTED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Documento enviado para aprobacion: {form_data.get('document_name', '')}.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Documento enviado",
                        f"El documento {form_data.get('document_name', '')} quedo pendiente de aprobacion por Recursos Humanos.",
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/approve-provider-document":
                if not has_permission(current_user, "providers.documents.verify", list_provider_contracts()):
                    raise ValueError("Solo Recursos Humanos o Administracion pueden aprobar documentos.")
                record = approve_provider_document(
                    contract_id=form_data.get("contract_id", ""),
                    document_name=form_data.get("document_name", ""),
                    approver_username=str(current_user.get("username", "")),
                    approver_name=str(current_user.get("full_name", "")),
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "provider",
                        "entity_type": "provider",
                        "entity_id": record.get("contract_id", ""),
                        "entity_name": record.get("provider_name", ""),
                        "action": "PROVIDER_DOCUMENT_APPROVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Documento aprobado: {form_data.get('document_name', '')}.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Documento aprobado",
                        f"Recursos Humanos aprobo {form_data.get('document_name', '')} para {record.get('provider_name', '')}.",
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        provider_contract_form=_provider_contract_form_from_record(record),
                    )
                )
                return

            if self.path == "/save-document-config":
                if not has_permission(current_user, "providers.documents.verify", list_provider_contracts()):
                    raise ValueError("Solo Administracion o Recursos Humanos pueden cambiar esta configuracion.")
                document_names = [line.strip() for line in form_data.get("document_names", "").splitlines() if line.strip()]
                saved = save_required_documents(form_data.get("document_type", ""), document_names)
                current_agency = get_current_agency()
                self._send_html(
                    _render_page(
                        "Configuracion actualizada",
                        (
                            f"Se guardaron {len(saved)} documentos para {form_data.get('document_type', '')}.\n"
                            f"Agencia activa: {str((current_agency or {}).get('agency_name', 'Sin agencia seleccionada'))}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel="security",
                    )
                )
                return

            if self.path == "/save-system-config":
                if not has_permission(current_user, "settings.manage", list_provider_contracts()):
                    raise ValueError("Solo Admin o General pueden cambiar la configuracion global del sistema.")
                saved_config = save_system_configuration(form_data)
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "system",
                        "entity_id": "system_config",
                        "entity_name": "Configuracion global",
                        "action": "SYSTEM_CONFIG_UPDATED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Landing {saved_config.get('default_landing_page', 'dashboard')}, "
                            f"sesion {saved_config.get('session_timeout_minutes', 30)} min, "
                            f"MFA {saved_config.get('mfa_timeout_minutes', 10)} min, "
                            f"unidad ABA {saved_config.get('billing_unit_minutes', 15)} min."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Configuracion global actualizada",
                        (
                            f"Landing: {saved_config.get('default_landing_page', 'dashboard')}\n"
                            f"Sesion: {saved_config.get('session_timeout_minutes', 30)} min\n"
                            f"MFA: {saved_config.get('mfa_timeout_minutes', 10)} min\n"
                            f"Password reset: {saved_config.get('password_reset_minutes', 30)} min\n"
                            f"Unidad ABA: {saved_config.get('billing_unit_minutes', 15)} min\n"
                            f"Elegibilidad: {', '.join(str(day) for day in saved_config.get('eligibility_run_days', []))}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel="system_config",
                    )
                )
                return

            if self.path == "/add-user":
                module_permissions = {
                    key.removeprefix("perm_"): bool(value)
                    for key, value in form_data.items()
                    if key.startswith("perm_")
                }
                avatar_file = files.get("avatar_file")
                record = add_user(
                    {
                        "full_name": form_data.get("full_name", ""),
                        "username": form_data.get("username", ""),
                        "password": form_data.get("password", ""),
                        "email": form_data.get("email", ""),
                        "phone": form_data.get("phone", ""),
                        "site_location": form_data.get("site_location", ""),
                        "county_name": form_data.get("county_name", ""),
                        "job_title": form_data.get("job_title", ""),
                        "bio": form_data.get("bio", ""),
                        "linked_provider_name": form_data.get("linked_provider_name", ""),
                        "profile_color": form_data.get("profile_color", "#0d51b8"),
                        "role": form_data.get("role", "MANAGER"),
                        "active": bool(form_data.get("active")),
                        "module_permissions": module_permissions,
                        "avatar_file_name": avatar_file.filename if avatar_file is not None else "",
                        "avatar_file_content": avatar_file.content if avatar_file is not None else b"",
                    }
                )
                if bool(form_data.get("send_welcome_email")):
                    add_notification(
                        {
                            "category": "user",
                            "subject": f"Bienvenida de acceso para {record.get('full_name', '')}",
                            "message": (
                                f"Se creo el perfil {record.get('username', '')} con rango {record.get('role', '')}. "
                                "Cuando conectes email real, este mensaje puede salir como bienvenida inicial para que el usuario complete su perfil."
                            ),
                            "related_id": record.get("user_id", ""),
                            "recipient_email": record.get("email", ""),
                        }
                    )
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": record.get("user_id", ""),
                        "entity_name": record.get("username", ""),
                        "action": "USER_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Usuario guardado con rango {record.get('role', '')}.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Usuario guardado",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/add-aba-appointment":
                allowed_aba_provider_ids = _allowed_aba_provider_ids_for_user(current_user)
                allow_unassigned_fallback = not is_provider_role(current_user, list_provider_contracts())
                record = add_aba_appointment(
                    {
                        "provider_contract_id": form_data.get("provider_contract_id", ""),
                        "client_id": form_data.get("client_id", ""),
                        "service_context": form_data.get("service_context", "direct"),
                        "appointment_date": form_data.get("appointment_date", ""),
                        "start_time": form_data.get("start_time", ""),
                        "end_time": form_data.get("end_time", ""),
                        "place_of_service": form_data.get("place_of_service", ""),
                        "caregiver_name": form_data.get("caregiver_name", ""),
                        "caregiver_signature": form_data.get("caregiver_signature", ""),
                        "provider_signature": form_data.get("provider_signature", ""),
                        "session_note": form_data.get("session_note", ""),
                        "created_by_username": current_user.get("username", ""),
                        "created_by_name": current_user.get("full_name", ""),
                    },
                    allowed_aba_provider_ids,
                    allow_unassigned_fallback=allow_unassigned_fallback,
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "aba_notes",
                        "entity_type": "aba_appointment",
                        "entity_id": record.get("appointment_id", ""),
                        "entity_name": record.get("client_name", ""),
                        "action": "ABA_APPOINTMENT_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"{record.get('provider_name', '')} con {record.get('client_name', '')} "
                            f"{record.get('billing_code', '')} {record.get('units', 0)} unit(s)."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Sesion ABA guardada",
                        (
                            f"Provider: {record.get('provider_name', '')}\n"
                            f"Cliente: {record.get('client_name', '')}\n"
                            f"CPT: {record.get('billing_code', '')}\n"
                            f"Documento: {record.get('document_type', '')}\n"
                            f"Units: {record.get('units', 0)}\n"
                            f"Horario: {record.get('start_at', '')} -> {record.get('end_at', '')}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel="aba_notes",
                        aba_notes_form={**form_data, "selected_log_id": str(record.get("service_log_id", ""))},
                    )
                )
                return

            if self.path == "/aba-session-workflow":
                allowed_aba_provider_ids = _allowed_aba_provider_ids_for_user(current_user)
                record = update_aba_session_event(
                    action=form_data.get("session_action", ""),
                    appointment_id=form_data.get("appointment_id", ""),
                    actor_username=str(current_user.get("username", "")),
                    actor_name=str(current_user.get("full_name", "")),
                    actual_start_time=form_data.get("actual_start_time", ""),
                    actual_end_time=form_data.get("actual_end_time", ""),
                    reason=form_data.get("session_reason", ""),
                    provider_contract_ids=allowed_aba_provider_ids,
                )
                add_system_audit_log(
                    {
                        "category": "aba_notes",
                        "entity_type": "aba_session",
                        "entity_id": record.get("appointment_id", ""),
                        "entity_name": record.get("client_name", ""),
                        "action": f"ABA_SESSION_{str(form_data.get('session_action', '')).strip().upper()}",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"{record.get('provider_name', '')} con {record.get('client_name', '')} "
                            f"estatus {record.get('session_status', '')}."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Sesion ABA actualizada",
                        (
                            f"Cliente: {record.get('client_name', '')}\n"
                            f"Provider: {record.get('provider_name', '')}\n"
                            f"Estatus: {record.get('session_status', '')}\n"
                            f"Actual: {record.get('actual_start_time_label', '-') or '-'} -> {record.get('actual_end_time_label', '-') or '-'}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel="aba_notes",
                        operations_selected_session_id=str(record.get("appointment_id", "")),
                    )
                )
                return

            if self.path == "/aba-log-workflow":
                if str(form_data.get("workflow_action", "")).strip().lower() in {"review", "close", "reject", "reopen"} and not _can_close_aba_note(current_user):
                    raise ValueError("Solo oficina, admin o un BCBA/BCaBA pueden cerrar o supervisar esta nota.")
                allowed_aba_provider_ids = _allowed_aba_provider_ids_for_user(current_user)
                record = update_aba_service_log_workflow(
                    action=form_data.get("workflow_action", ""),
                    log_id=form_data.get("log_id", ""),
                    supervisor_name=form_data.get("supervisor_name", ""),
                    reason=form_data.get("workflow_reason", ""),
                    caregiver_signature=form_data.get("caregiver_signature", ""),
                    provider_signature=form_data.get("provider_signature", ""),
                    provider_contract_ids=allowed_aba_provider_ids,
                )
                add_system_audit_log(
                    {
                        "category": "aba_notes",
                        "entity_type": "aba_log",
                        "entity_id": record.get("log_id", ""),
                        "entity_name": record.get("client_name", ""),
                        "action": f"ABA_LOG_{str(form_data.get('workflow_action', '')).strip().upper()}",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"{record.get('document_type', '')} {record.get('workflow_status', '')} "
                            f"para {record.get('client_name', '')}."
                        ),
                    }
                )
                self._send_html(
                    _render_page(
                        "Workflow ABA actualizado",
                        (
                            f"Cliente: {record.get('client_name', '')}\n"
                            f"Documento: {record.get('document_type', '')}\n"
                            f"Workflow: {record.get('workflow_status', '')}\n"
                            f"Deadline: {record.get('latest_note_due_at', '') or '-'}"
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel="aba_notes",
                        aba_notes_form={**form_data, "selected_log_id": str(record.get("log_id", ""))},
                    )
                )
                return

            if self.path == "/generate-claim-batch":
                allowed_aba_provider_ids = _allowed_aba_provider_ids_for_user(current_user)
                batch_result = create_claim_from_batch(form_data.get("batch_id", ""), allowed_aba_provider_ids)
                session_ids = [str(item).strip() for item in batch_result.get("session_ids", []) if str(item).strip()]
                attach_claim_to_aba_sessions(
                    session_ids=session_ids,
                    claim_id=str(batch_result.get("claim_id", "")),
                    batch_id=str(batch_result.get("batch_id", "")),
                )
                add_claim_audit_log(
                    {
                        "claim_id": str(batch_result.get("claim_id", "")),
                        "action": "CLAIM_BATCH_CREATED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Batch {batch_result.get('batch_id', '')} generado desde {len(session_ids)} sesion(es).",
                    }
                )
                self._send_html(
                    _render_page(
                        "Claim batch generado",
                        (
                            f"Batch: {batch_result.get('batch_id', '')}\n"
                            f"Claim ID: {batch_result.get('claim_id', '')}\n"
                            f"Sesiones: {len(session_ids)}\n"
                            "El 837 ya quedo archivado dentro del claim generado."
                        ),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel="claim_batch",
                    )
                )
                return

            if self.path == "/add-calendar-event":
                record = add_calendar_event(
                    {
                        "title": form_data.get("title", ""),
                        "category": form_data.get("category", "task"),
                        "event_date": form_data.get("event_date", ""),
                        "due_date": form_data.get("due_date", ""),
                        "assigned_username": form_data.get("assigned_username", ""),
                        "related_provider": form_data.get("related_provider", ""),
                        "description": form_data.get("description", ""),
                        "notify_email": bool(form_data.get("notify_email")),
                        "created_by_username": current_user.get("username", ""),
                        "created_by_name": current_user.get("full_name", ""),
                    }
                )
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "agenda",
                        "entity_type": "event",
                        "entity_id": record.get("event_id", ""),
                        "entity_name": record.get("title", ""),
                        "action": "CALENDAR_EVENT_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": (
                            f"Tarea {record.get('category', '')} para {record.get('assigned_name', '') or record.get('assigned_username', '')} "
                            f"con vencimiento {record.get('due_date', '')}."
                        ),
                    }
                )
                assigned_label = str(record.get("assigned_name", "") or record.get("assigned_username", "")).strip() or "Sin asignar"
                event_summary = "\n".join(
                    [
                        f"Tarea: {record.get('title', '')}",
                        f"Categoria: {str(record.get('category', '')).replace('_', ' ').title()}",
                        f"Asignado a: {assigned_label}",
                        f"Fecha del evento: {record.get('event_date', '')}",
                        f"Fecha limite: {record.get('due_date', '')}",
                        f"Alerta email: {'Si' if record.get('notify_email') else 'No'}",
                    ]
                )
                self._send_html(
                    _render_page(
                        "Tarea guardada",
                        event_summary,
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        agenda_form=_agenda_form_defaults(),
                    )
                )
                return

            if self.path == "/update-event-status":
                record = update_calendar_event_status(form_data.get("event_id", ""), form_data.get("status", "DONE"))
                add_system_audit_log(
                    {
                        "agency_id": record.get("agency_id", ""),
                        "agency_name": record.get("agency_name", ""),
                        "category": "agenda",
                        "entity_type": "event",
                        "entity_id": record.get("event_id", ""),
                        "entity_name": record.get("title", ""),
                        "action": "CALENDAR_EVENT_STATUS_UPDATED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Estatus actualizado a {record.get('status', '')}.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Estatus de tarea actualizado",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/add-note":
                record = add_user_note(
                    {
                        "username": current_user.get("username", ""),
                        "full_name": current_user.get("full_name", ""),
                        "title": form_data.get("title", ""),
                        "body": form_data.get("body", ""),
                    }
                )
                add_system_audit_log(
                    {
                        "category": "agenda",
                        "entity_type": "note",
                        "entity_id": record.get("note_id", ""),
                        "entity_name": record.get("title", ""),
                        "action": "USER_NOTE_SAVED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": "Nota personal guardada en agenda.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Nota guardada",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/change-password":
                new_password = form_data.get("new_password", "")
                if new_password != form_data.get("confirm_password", ""):
                    raise ValueError("La confirmacion del nuevo password no coincide.")
                if len(new_password.strip()) < 8:
                    raise ValueError("El nuevo password debe tener al menos 8 caracteres.")
                record = change_password(
                    str(current_user.get("username", "")),
                    form_data.get("current_password", ""),
                    new_password,
                )
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": record.get("user_id", ""),
                        "entity_name": record.get("username", ""),
                        "action": "USER_PASSWORD_CHANGED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": "Password actualizado desde el modulo de seguridad.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Password actualizado",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                    )
                )
                return

            if self.path == "/update-profile":
                avatar_file = files.get("avatar_file")
                record = update_user_profile(
                    str(current_user.get("username", "")),
                    {
                        "full_name": form_data.get("full_name", ""),
                        "email": form_data.get("email", ""),
                        "phone": form_data.get("phone", ""),
                        "site_location": form_data.get("site_location", ""),
                        "county_name": form_data.get("county_name", ""),
                        "job_title": form_data.get("job_title", ""),
                        "bio": form_data.get("bio", ""),
                        "profile_color": form_data.get("profile_color", "#0d51b8"),
                        "avatar_file_name": avatar_file.filename if avatar_file is not None else "",
                        "avatar_file_content": avatar_file.content if avatar_file is not None else b"",
                    },
                )
                current_user = {**current_user, **record}
                session_token = self._cookie_map().get(SESSION_COOKIE_NAME, "")
                if session_token:
                    SESSIONS[session_token] = {
                        **SESSIONS.get(session_token, {}),
                        **record,
                        "last_seen_at": time.time(),
                    }
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": record.get("user_id", ""),
                        "entity_name": record.get("username", ""),
                        "action": "USER_PROFILE_UPDATED",
                        "actor_username": record.get("username", ""),
                        "actor_name": record.get("full_name", ""),
                        "details": "Perfil personal actualizado desde seguridad.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Perfil actualizado",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                    )
                )
                return

            if self.path == "/start-mfa-setup":
                profile = initiate_mfa_setup(
                    str(current_user.get("username", "")),
                    form_data.get("current_password", ""),
                )
                refreshed_user = get_user_public_profile(str(current_user.get("username", "")))
                current_user = {**current_user, **refreshed_user}
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": current_user.get("user_id", ""),
                        "entity_name": current_user.get("username", ""),
                        "action": "USER_MFA_SETUP_STARTED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": "Se genero una nueva llave MFA pendiente de confirmacion.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Llave MFA generada",
                        _pretty_json(profile),
                        current_page=current_page,
                        current_user=current_user,
                    )
                )
                return

            if self.path == "/confirm-mfa-setup":
                profile = confirm_mfa_setup(
                    str(current_user.get("username", "")),
                    form_data.get("mfa_code", ""),
                )
                refreshed_user = get_user_public_profile(str(current_user.get("username", "")))
                current_user = {**current_user, **refreshed_user}
                session_token = self._cookie_map().get(SESSION_COOKIE_NAME, "")
                if session_token:
                    SESSIONS[session_token] = {
                        **SESSIONS.get(session_token, {}),
                        **dict(current_user),
                        "last_seen_at": time.time(),
                    }
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": current_user.get("user_id", ""),
                        "entity_name": current_user.get("username", ""),
                        "action": "USER_MFA_ENABLED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": "MFA activado correctamente.",
                    }
                )
                self._send_html(
                    _render_page(
                        "MFA activado",
                        _pretty_json(profile),
                        current_page=current_page,
                        current_user=current_user,
                    )
                )
                return

            if self.path == "/disable-mfa":
                profile = disable_mfa(
                    str(current_user.get("username", "")),
                    form_data.get("current_password", ""),
                )
                refreshed_user = get_user_public_profile(str(current_user.get("username", "")))
                current_user = {**current_user, **refreshed_user}
                session_token = self._cookie_map().get(SESSION_COOKIE_NAME, "")
                if session_token:
                    SESSIONS[session_token] = {
                        **SESSIONS.get(session_token, {}),
                        **dict(current_user),
                        "last_seen_at": time.time(),
                    }
                add_system_audit_log(
                    {
                        "category": "security",
                        "entity_type": "user",
                        "entity_id": current_user.get("user_id", ""),
                        "entity_name": current_user.get("username", ""),
                        "action": "USER_MFA_DISABLED",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": "MFA fue desactivado desde el modulo de seguridad.",
                    }
                )
                self._send_html(
                    _render_page(
                        "MFA desactivado",
                        _pretty_json(profile),
                        current_page=current_page,
                        current_user=current_user,
                    )
                )
                return

            if self.path == "/add-roster-entry":
                record = add_roster_entry(
                    {
                        "payer_id": form_data.get("payer_id", ""),
                        "provider_npi": form_data.get("provider_npi", ""),
                        "member_id": form_data.get("member_id", ""),
                        "patient_first_name": form_data.get("patient_first_name", ""),
                        "patient_last_name": form_data.get("patient_last_name", ""),
                        "patient_birth_date": form_data.get("patient_birth_date", ""),
                        "service_date": form_data.get("service_date", ""),
                    }
                )
                self._send_html(
                    _render_page(
                        "Paciente agregado al roster automatico",
                        _pretty_json(record),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                        **_form_state_args(active_panel, form_data),
                    )
                )
                return

            if self.path == "/transmit-claim":
                update = transmit_claim_record(form_data.get("claim_id", ""), MockClearinghouseConnector())
                add_claim_audit_log(
                    {
                        "claim_id": form_data.get("claim_id", ""),
                        "action": "CLAIM_TRANSMIT",
                        "actor_username": current_user.get("username", ""),
                        "actor_name": current_user.get("full_name", ""),
                        "details": f"Claim transmitido con tracking {update.get('tracking_id', '')}.",
                    }
                )
                self._send_html(
                    _render_page(
                        "Claim transmitido",
                        _pretty_json(update),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            if self.path == "/transmit-batch-today":
                updates = transmit_daily_batch(MockClearinghouseConnector())
                for update in updates:
                    add_claim_audit_log(
                        {
                            "claim_id": update.get("claim_id", ""),
                            "action": "CLAIM_TRANSMIT_BATCH",
                            "actor_username": current_user.get("username", ""),
                            "actor_name": current_user.get("full_name", ""),
                            "details": f"Transmitido desde batch del {update.get('batch_date', '')}.",
                        }
                    )
                self._send_html(
                    _render_page(
                        "Batch transmitido",
                        _pretty_json({"batch_date": today_user_date(), "results": updates, "count": len(updates)}),
                        current_page=current_page,
                        current_user=current_user,
                        active_panel=active_panel,
                    )
                )
                return

            self._send_html(
                _render_page(error="Accion no encontrada.", current_page=current_page, current_user=current_user, active_panel=active_panel),
                status=HTTPStatus.NOT_FOUND,
            )
        except json.JSONDecodeError as exc:
            if self.path in {"/recover-password", "/reset-password"}:
                self._send_html(
                    _render_recovery_page(
                        error=f"JSON invalido: {exc}",
                        username=form_data.get("username", ""),
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_html(
                _render_page(
                    error=f"JSON invalido: {exc}",
                    current_page=current_page,
                    current_user=self._current_user(),
                    active_panel=active_panel,
                    **_form_state_args(active_panel, form_data),
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:  # pragma: no cover - safety net for local manual use
            if self.path in {"/recover-password", "/reset-password"}:
                self._send_html(
                    _render_recovery_page(
                        error=str(exc),
                        username=form_data.get("username", ""),
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_html(
                _render_page(
                    error=str(exc),
                    current_page=current_page,
                    current_user=self._current_user(),
                    active_panel=active_panel,
                    **_form_state_args(active_panel, form_data),
                ),
                status=HTTPStatus.BAD_REQUEST,
            )


def _start_eligibility_scheduler() -> None:
    def _runner() -> None:
        connector = MockClearinghouseConnector()
        while True:
            run_due_eligibility_checks(connector)
            time.sleep(60 * 60 * get_eligibility_check_interval_hours())

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Run the local {BRAND_NAME} web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    args = parser.parse_args()

    _start_eligibility_scheduler()
    server = ThreadingHTTPServer((args.host, args.port), BillingWebHandler)
    print(f"{BRAND_NAME} web disponible en http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
