from __future__ import annotations

import base64
import calendar
import json
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from billing_app.models import (
    Address,
    Claim,
    EligibilityRequest,
    EligibilityResponse,
    InsurancePolicy,
    Parsed835,
    Parsed837,
    Patient,
    Provider,
    ServiceLine,
)
from billing_app.services.claim_builder import Claim837Builder
from billing_app.services.date_utils import add_user_date_months, format_user_date, parse_user_date, today_user_date
from billing_app.services.rbac import default_module_permissions_for_role, normalize_role


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
CLAIMS_FILE = DATA_DIR / "claims.json"
AUTHORIZATIONS_FILE = DATA_DIR / "authorizations.json"
ELIGIBILITY_ROSTER_FILE = DATA_DIR / "eligibility_roster.json"
CLIENTS_FILE = DATA_DIR / "clients.json"
PAYER_ENROLLMENTS_FILE = DATA_DIR / "payer_enrollments.json"
AGENCIES_FILE = DATA_DIR / "agencies.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
PROVIDER_CONTRACTS_FILE = DATA_DIR / "provider_contracts.json"
NOTIFICATIONS_FILE = DATA_DIR / "notifications.json"
ERA_ARCHIVES_FILE = DATA_DIR / "era_archives.json"
USERS_FILE = DATA_DIR / "users.json"
CLAIM_AUDIT_LOGS_FILE = DATA_DIR / "claim_audit_logs.json"
SYSTEM_AUDIT_LOGS_FILE = DATA_DIR / "system_audit_logs.json"
PASSWORD_RESET_TOKENS_FILE = DATA_DIR / "password_reset_tokens.json"
CALENDAR_EVENTS_FILE = DATA_DIR / "calendar_events.json"
USER_NOTES_FILE = DATA_DIR / "user_notes.json"
ELIGIBILITY_HISTORY_FILE = DATA_DIR / "eligibility_history.json"
UPLOADS_DIR = DATA_DIR / "uploads"
ONEDRIVE_APP_DIR = "Blue Hope ABA Solutions"
LOCKOUT_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
PASSWORD_RESET_MINUTES = 30
SESSION_TIMEOUT_MINUTES = 30
MFA_SESSION_TIMEOUT_MINUTES = 10
ELIGIBILITY_RUN_DAYS = (1, 15)
ELIGIBILITY_CHECK_INTERVAL_HOURS = 6
BILLING_UNIT_MINUTES = 15
PERMISSION_KEYS = (
    "dashboard",
    "hr",
    "claims",
    "eligibility",
    "clients",
    "aba_notes",
    "payments",
    "enrollments",
    "payers",
    "agencies",
    "providers",
    "agenda",
    "notifications",
    "users",
    "security",
)
PROVIDER_REQUIRED_DOCUMENTS = (
    "Car Insurance",
    "CPR/First Aid",
    "Certificate Letter of RBT/BCaBA/BCBA",
    "HIV/Aids",
    "HIPAA 2.0",
    "HIPAA",
    "Civil Rights Training",
    "OSHA",
    "Car Registration",
    "Certificate of CMS/Medicare Fraud, Waste and Abuse Prevention",
    "Security Awareness",
    "Company Liability Insurance",
    "Local Police Record",
    "Physical Exam",
    "Domestic Violence",
    "Workers Comp Exemption",
    "AHCA Level II BGS",
    "DCF Fingerprints",
    "Driver License",
    "Zero Tolerance",
    "Requirements For All Waiver Providers",
    "Affidavit of Good Moral Character",
    "Passport, Resident Card or Other Residence Proof",
    "E-Verify",
    "Welcome Letter of MD",
    "Copy of NPI",
    "Diploma / Equivalente",
    "I-9",
    "Social Security",
    "Contract",
    "Company IRS Letter",
    "W-9/W-2",
    "Handbook",
    "Resume",
    "Void Check of Business Account",
    "2 Reference Letters",
    "Direct Care Core Competencies",
    "Fotos para ID",
)
CLIENT_REQUIRED_DOCUMENTS = (
    "BIP",
    "Approval Letter",
    "Medical Referral",
    "Medical Necessity Letter",
    "HIPAA, Services, and Treatment Consent",
    "Driver License Caregiver",
    "Caregiver Authorization",
    "CDE",
    "Diaper Consent / Consent for Toilet Training",
    "IEP",
    "Change of Provider Form",
    "Insurance Card",
    "Bus Transportation",
    "Discharge Letter",
)
DOCUMENT_TEMPLATE_KEYS = {
    "provider": "provider_required_documents",
    "client": "client_required_documents",
}
SYSTEM_CONFIG_KEY = "system_config"
PAYER_CONFIGS_KEY = "payer_configs_by_agency"
DEFAULT_PORTAL_LABEL = "Blue Hope Suite"
LEGACY_PORTAL_LABELS = {
    "",
    "Blue Hope Billing Server",
    "Blue Hope ABA Solutions",
    "BHAS Blue Hope Aba Solution",
    "Blue Hope Suite",
}


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


def _normalize_portal_label(value: Any) -> str:
    clean = str(value or "").strip()
    if clean in LEGACY_PORTAL_LABELS:
        return DEFAULT_PORTAL_LABEL
    return clean


def _ensure_store(path: Path) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if path.name == SETTINGS_FILE.name:
            path.write_text("{}", encoding="utf-8")
        else:
            path.write_text("[]", encoding="utf-8")


def _load_list(path: Path) -> list[dict[str, Any]]:
    _ensure_store(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_list(path: Path, items: list[dict[str, Any]]) -> None:
    _ensure_store(path)
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _load_dict(path: Path) -> dict[str, Any]:
    _ensure_store(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_dict(path: Path, payload: dict[str, Any]) -> None:
    _ensure_store(path)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_upload_file(folder_name: str, base_name: str, content: bytes) -> str:
    target_dir = UPLOADS_DIR / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(base_name or "upload.txt").name
    target_path = target_dir / safe_name
    if target_path.exists():
        target_path = target_dir / f"{target_path.stem}_{uuid.uuid4().hex[:6]}{target_path.suffix}"
    target_path.write_bytes(content)
    return str(target_path.relative_to(DATA_DIR))


def _safe_folder_name(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "_" for char in str(value or "")).strip()
    return clean.rstrip(". ") or "General"


def _onedrive_root() -> Path | None:
    candidates = [
        os.environ.get("OneDrive", ""),
        os.environ.get("OneDriveCommercial", ""),
        os.environ.get("OneDriveConsumer", ""),
        str(Path(os.environ.get("USERPROFILE", str(Path.home()))) / "OneDrive"),
    ]
    seen: set[str] = set()
    for candidate in candidates:
        clean = str(candidate or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        candidate_path = Path(clean)
        if candidate_path.is_dir():
            return candidate_path
    return None


def _write_onedrive_file(folder_parts: list[str], base_name: str, content: bytes) -> str:
    onedrive_root = _onedrive_root()
    if onedrive_root is None:
        return ""

    clean_parts = [_safe_folder_name(part) for part in folder_parts if str(part or "").strip()]
    target_dir = onedrive_root.joinpath(*clean_parts)
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(base_name or "upload.txt").name
    target_path = target_dir / safe_name
    if target_path.exists():
        target_path = target_dir / f"{target_path.stem}_{uuid.uuid4().hex[:6]}{target_path.suffix}"
    target_path.write_bytes(content)
    return str(target_path)


def _read_upload_file(relative_path: str) -> bytes:
    target_path = DATA_DIR / relative_path
    if not target_path.is_file():
        raise FileNotFoundError("No encontre el archivo archivado.")
    return target_path.read_bytes()


def get_upload_bytes(relative_path: str) -> tuple[bytes, str]:
    clean_relative = str(relative_path or "").strip()
    if not clean_relative:
        raise FileNotFoundError("No encontre el archivo solicitado.")
    return (_read_upload_file(clean_relative), Path(clean_relative).name)


def _filter_current_agency(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current_agency_id = get_current_agency_id()
    if not current_agency_id:
        return items
    return [item for item in items if not item.get("agency_id") or item.get("agency_id") == current_agency_id]


def load_claims() -> list[dict[str, Any]]:
    return _load_list(CLAIMS_FILE)


def save_claims(items: list[dict[str, Any]]) -> None:
    _save_list(CLAIMS_FILE, items)


def get_claim_by_id(claim_id: str) -> dict[str, Any] | None:
    return next((item for item in load_claims() if item.get("claim_id") == claim_id), None)


def load_authorizations() -> list[dict[str, Any]]:
    return _load_list(AUTHORIZATIONS_FILE)


def save_authorizations(items: list[dict[str, Any]]) -> None:
    _save_list(AUTHORIZATIONS_FILE, items)


def load_eligibility_roster() -> list[dict[str, Any]]:
    return _load_list(ELIGIBILITY_ROSTER_FILE)


def save_eligibility_roster(items: list[dict[str, Any]]) -> None:
    _save_list(ELIGIBILITY_ROSTER_FILE, items)


def load_clients() -> list[dict[str, Any]]:
    return _load_list(CLIENTS_FILE)


def save_clients(items: list[dict[str, Any]]) -> None:
    _save_list(CLIENTS_FILE, items)


def get_client_by_id(client_id: str) -> dict[str, Any] | None:
    return next((item for item in load_clients() if item.get("client_id") == client_id), None)


def list_clients() -> list[dict[str, Any]]:
    items = [_enrich_client(item) for item in _filter_current_agency(load_clients())]
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)


def find_client_for_eligibility(member_id: str, first_name: str = "", last_name: str = "") -> dict[str, Any] | None:
    clean_member_id = str(member_id or "").strip().lower()
    clean_first_name = str(first_name or "").strip().lower()
    clean_last_name = str(last_name or "").strip().lower()
    if not clean_member_id:
        return None
    for item in list_clients():
        if str(item.get("member_id", "")).strip().lower() != clean_member_id:
            continue
        if clean_first_name and str(item.get("first_name", "")).strip().lower() != clean_first_name:
            continue
        if clean_last_name and str(item.get("last_name", "")).strip().lower() != clean_last_name:
            continue
        return item
    return None


def load_payer_enrollments() -> list[dict[str, Any]]:
    return _load_list(PAYER_ENROLLMENTS_FILE)


def save_payer_enrollments(items: list[dict[str, Any]]) -> None:
    _save_list(PAYER_ENROLLMENTS_FILE, items)


def list_payer_enrollments() -> list[dict[str, Any]]:
    visible_items = []
    for item in _filter_current_agency(load_payer_enrollments()):
        enriched = item.copy()
        if enriched.get("credentials_submitted_date"):
            expected_completion_date, days_remaining = _countdown_fields(enriched["credentials_submitted_date"])
            enriched["expected_completion_date"] = expected_completion_date
            enriched["days_remaining"] = days_remaining
            elapsed_days = max(90 - days_remaining, 0)
            enriched["credentialing_progress_percent"] = min(int(round((elapsed_days / 90) * 100)), 100)
        visible_items.append(enriched)
    return sorted(visible_items, key=lambda item: item.get("updated_at", ""), reverse=True)


def load_agencies() -> list[dict[str, Any]]:
    return _load_list(AGENCIES_FILE)


def save_agencies(items: list[dict[str, Any]]) -> None:
    _save_list(AGENCIES_FILE, items)


def list_agencies() -> list[dict[str, Any]]:
    return sorted(load_agencies(), key=lambda item: item.get("updated_at", ""), reverse=True)


def load_settings() -> dict[str, Any]:
    return _load_dict(SETTINGS_FILE)


def save_settings(payload: dict[str, Any]) -> None:
    _save_dict(SETTINGS_FILE, payload)


def _normalize_int_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        clean_value = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        clean_value = default
    return max(minimum, min(clean_value, maximum))


def _normalize_eligibility_run_days(value: Any) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = str(value or "").replace(";", ",").split(",")
    normalized: set[int] = set()
    for item in raw_items:
        try:
            day_value = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= day_value <= 31:
            normalized.add(day_value)
    if not normalized:
        normalized = set(ELIGIBILITY_RUN_DAYS)
    return sorted(normalized)


def load_system_configuration() -> dict[str, Any]:
    settings = load_settings()
    raw = settings.get(SYSTEM_CONFIG_KEY, {})
    if not isinstance(raw, dict):
        raw = {}
    landing_page = str(raw.get("default_landing_page", "dashboard")).strip().lower() or "dashboard"
    if landing_page not in PERMISSION_KEYS:
        landing_page = "dashboard"
    portal_label = _normalize_portal_label(raw.get("portal_label", DEFAULT_PORTAL_LABEL)) or DEFAULT_PORTAL_LABEL
    return {
        "portal_label": portal_label,
        "default_landing_page": landing_page,
        "session_timeout_minutes": _normalize_int_setting(
            raw.get("session_timeout_minutes", SESSION_TIMEOUT_MINUTES),
            SESSION_TIMEOUT_MINUTES,
            5,
            240,
        ),
        "mfa_timeout_minutes": _normalize_int_setting(
            raw.get("mfa_timeout_minutes", MFA_SESSION_TIMEOUT_MINUTES),
            MFA_SESSION_TIMEOUT_MINUTES,
            3,
            60,
        ),
        "password_reset_minutes": _normalize_int_setting(
            raw.get("password_reset_minutes", PASSWORD_RESET_MINUTES),
            PASSWORD_RESET_MINUTES,
            5,
            180,
        ),
        "lockout_attempts": _normalize_int_setting(
            raw.get("lockout_attempts", LOCKOUT_ATTEMPTS),
            LOCKOUT_ATTEMPTS,
            3,
            10,
        ),
        "lockout_minutes": _normalize_int_setting(
            raw.get("lockout_minutes", LOCKOUT_MINUTES),
            LOCKOUT_MINUTES,
            1,
            180,
        ),
        "billing_unit_minutes": _normalize_int_setting(
            raw.get("billing_unit_minutes", BILLING_UNIT_MINUTES),
            BILLING_UNIT_MINUTES,
            5,
            120,
        ),
        "eligibility_run_days": _normalize_eligibility_run_days(raw.get("eligibility_run_days", ELIGIBILITY_RUN_DAYS)),
        "eligibility_check_interval_hours": _normalize_int_setting(
            raw.get("eligibility_check_interval_hours", ELIGIBILITY_CHECK_INTERVAL_HOURS),
            ELIGIBILITY_CHECK_INTERVAL_HOURS,
            1,
            24,
        ),
    }


def save_system_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_system_configuration()
    updated = {
        "portal_label": _normalize_portal_label(payload.get("portal_label", current.get("portal_label", ""))) or str(current.get("portal_label", DEFAULT_PORTAL_LABEL)),
        "default_landing_page": str(payload.get("default_landing_page", current.get("default_landing_page", "dashboard"))).strip().lower() or "dashboard",
        "session_timeout_minutes": _normalize_int_setting(
            payload.get("session_timeout_minutes", current.get("session_timeout_minutes", SESSION_TIMEOUT_MINUTES)),
            int(current.get("session_timeout_minutes", SESSION_TIMEOUT_MINUTES)),
            5,
            240,
        ),
        "mfa_timeout_minutes": _normalize_int_setting(
            payload.get("mfa_timeout_minutes", current.get("mfa_timeout_minutes", MFA_SESSION_TIMEOUT_MINUTES)),
            int(current.get("mfa_timeout_minutes", MFA_SESSION_TIMEOUT_MINUTES)),
            3,
            60,
        ),
        "password_reset_minutes": _normalize_int_setting(
            payload.get("password_reset_minutes", current.get("password_reset_minutes", PASSWORD_RESET_MINUTES)),
            int(current.get("password_reset_minutes", PASSWORD_RESET_MINUTES)),
            5,
            180,
        ),
        "lockout_attempts": _normalize_int_setting(
            payload.get("lockout_attempts", current.get("lockout_attempts", LOCKOUT_ATTEMPTS)),
            int(current.get("lockout_attempts", LOCKOUT_ATTEMPTS)),
            3,
            10,
        ),
        "lockout_minutes": _normalize_int_setting(
            payload.get("lockout_minutes", current.get("lockout_minutes", LOCKOUT_MINUTES)),
            int(current.get("lockout_minutes", LOCKOUT_MINUTES)),
            1,
            180,
        ),
        "billing_unit_minutes": _normalize_int_setting(
            payload.get("billing_unit_minutes", current.get("billing_unit_minutes", BILLING_UNIT_MINUTES)),
            int(current.get("billing_unit_minutes", BILLING_UNIT_MINUTES)),
            5,
            120,
        ),
        "eligibility_run_days": _normalize_eligibility_run_days(
            payload.get("eligibility_run_days", current.get("eligibility_run_days", ELIGIBILITY_RUN_DAYS))
        ),
        "eligibility_check_interval_hours": _normalize_int_setting(
            payload.get("eligibility_check_interval_hours", current.get("eligibility_check_interval_hours", ELIGIBILITY_CHECK_INTERVAL_HOURS)),
            int(current.get("eligibility_check_interval_hours", ELIGIBILITY_CHECK_INTERVAL_HOURS)),
            1,
            24,
        ),
    }
    if updated["default_landing_page"] not in PERMISSION_KEYS:
        updated["default_landing_page"] = "dashboard"
    settings = load_settings()
    settings[SYSTEM_CONFIG_KEY] = updated
    save_settings(settings)
    return updated


def _payer_configs_by_agency(settings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    scoped = settings.get(PAYER_CONFIGS_KEY, {})
    if not isinstance(scoped, dict):
        return {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for agency_id, items in scoped.items():
        if isinstance(items, list):
            normalized[str(agency_id)] = [dict(item) for item in items if isinstance(item, dict)]
    return normalized


def _normalize_payer_rate_lines(lines: Any) -> list[dict[str, Any]]:
    if not isinstance(lines, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for line in lines:
        if not isinstance(line, dict):
            continue
        cpt_code = _normalize_cpt_code(str(line.get("cpt_code", "")))
        if not cpt_code or cpt_code in seen_codes:
            continue
        seen_codes.add(cpt_code)
        billing_code = str(line.get("billing_code", "")).strip() or cpt_code
        hcpcs_code = str(line.get("hcpcs_code", "")).strip()
        try:
            unit_price = float(str(line.get("unit_price", "")).strip() or 0)
        except (TypeError, ValueError):
            unit_price = 0.0
        normalized.append(
            {
                "cpt_code": cpt_code,
                "billing_code": billing_code,
                "hcpcs_code": hcpcs_code,
                "unit_price": round(unit_price, 2),
            }
        )
    return normalized


def list_payer_configurations(agency_id: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    scoped = _payer_configs_by_agency(settings)
    clean_agency_id = str(agency_id or "").strip() or get_current_agency_id()
    items = scoped.get(clean_agency_id, []) if clean_agency_id else []
    enriched: list[dict[str, Any]] = []
    for item in items:
        rates = _normalize_payer_rate_lines(item.get("rate_lines", []))
        record = dict(item)
        record["rate_lines"] = rates
        record["active_rate_count"] = len([line for line in rates if float(line.get("unit_price", 0) or 0) > 0])
        record["clearinghouse_label"] = " | ".join(
            part
            for part in (
                str(record.get("clearinghouse_name", "")).strip(),
                str(record.get("clearinghouse_payer_id", "")).strip(),
            )
            if part
        ) or "Sin clearinghouse"
        enriched.append(record)
    return sorted(enriched, key=lambda item: item.get("updated_at", ""), reverse=True)


def get_payer_configuration_by_id(payer_config_id: str, agency_id: str = "") -> dict[str, Any] | None:
    clean_id = str(payer_config_id or "").strip()
    if not clean_id:
        return None
    return next(
        (item for item in list_payer_configurations(agency_id) if str(item.get("payer_config_id", "")).strip() == clean_id),
        None,
    )


def find_payer_configuration(
    payer_name: str = "",
    payer_id: str = "",
    agency_id: str = "",
) -> dict[str, Any] | None:
    clean_name = str(payer_name or "").strip().lower()
    clean_payer_id = str(payer_id or "").strip().lower()
    if not clean_name and not clean_payer_id:
        return None
    for item in list_payer_configurations(agency_id):
        item_name = str(item.get("payer_name", "")).strip().lower()
        item_payer_id = str(item.get("payer_id", "")).strip().lower()
        if clean_payer_id and item_payer_id and item_payer_id == clean_payer_id:
            return item
        if clean_name and item_name == clean_name:
            return item
    return None


def get_payer_configured_unit_price(
    cpt_code: str,
    payer_name: str = "",
    payer_id: str = "",
    agency_id: str = "",
) -> float | None:
    normalized_code = _normalize_cpt_code(cpt_code)
    if not normalized_code:
        return None
    payer = find_payer_configuration(payer_name, payer_id, agency_id)
    if payer is None:
        return None
    for line in payer.get("rate_lines", []):
        if _normalize_cpt_code(str(line.get("cpt_code", ""))) != normalized_code:
            continue
        try:
            return float(line.get("unit_price", 0) or 0)
        except (TypeError, ValueError):
            return None
    return None


def save_payer_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    current_agency = get_current_agency() or {}
    clean_agency_id = str(payload.get("agency_id", "")).strip() or str(current_agency.get("agency_id", "")).strip()
    clean_agency_name = str(payload.get("agency_name", "")).strip() or str(current_agency.get("agency_name", "")).strip()
    if not clean_agency_id:
        raise ValueError("Selecciona una agencia antes de guardar payers.")

    payer_name = str(payload.get("payer_name", "")).strip()
    if not payer_name:
        raise ValueError("Escribe el nombre del payer.")

    settings = load_settings()
    scoped = _payer_configs_by_agency(settings)
    items = scoped.get(clean_agency_id, [])
    payer_config_id = str(payload.get("payer_config_id", "")).strip() or f"PYR-{uuid.uuid4().hex[:8].upper()}"
    clean_payer_id = str(payload.get("payer_id", "")).strip()
    match_index = next(
        (
            index
            for index, item in enumerate(items)
            if str(item.get("payer_config_id", "")).strip() == payer_config_id
            or (
                str(item.get("payer_name", "")).strip().lower() == payer_name.lower()
                and str(item.get("payer_id", "")).strip().lower() == clean_payer_id.lower()
            )
        ),
        None,
    )
    previous = items[match_index] if match_index is not None else {}
    record = {
        "payer_config_id": str(previous.get("payer_config_id", payer_config_id)).strip(),
        "agency_id": clean_agency_id,
        "agency_name": clean_agency_name,
        "payer_name": payer_name,
        "payer_id": clean_payer_id,
        "plan_type": str(payload.get("plan_type", previous.get("plan_type", "COMMERCIAL"))).strip().upper() or "COMMERCIAL",
        "brand_color": str(payload.get("brand_color", previous.get("brand_color", "#0d51b8"))).strip() or "#0d51b8",
        "clearinghouse_name": str(payload.get("clearinghouse_name", previous.get("clearinghouse_name", ""))).strip(),
        "clearinghouse_payer_id": str(payload.get("clearinghouse_payer_id", previous.get("clearinghouse_payer_id", ""))).strip(),
        "clearinghouse_receiver_id": str(payload.get("clearinghouse_receiver_id", previous.get("clearinghouse_receiver_id", ""))).strip(),
        "notes": str(payload.get("notes", previous.get("notes", ""))).strip(),
        "active": bool(payload.get("active", True)),
        "rate_lines": _normalize_payer_rate_lines(payload.get("rate_lines", previous.get("rate_lines", []))),
        "created_at": previous.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
    }
    if match_index is None:
        items.insert(0, record)
    else:
        items[match_index] = record
    scoped[clean_agency_id] = items
    settings[PAYER_CONFIGS_KEY] = scoped
    save_settings(settings)
    return next(
        (item for item in list_payer_configurations(clean_agency_id) if str(item.get("payer_config_id", "")).strip() == str(record.get("payer_config_id", "")).strip()),
        record,
    )


def get_session_timeout_seconds() -> int:
    return int(load_system_configuration().get("session_timeout_minutes", SESSION_TIMEOUT_MINUTES)) * 60


def get_mfa_session_timeout_seconds() -> int:
    return int(load_system_configuration().get("mfa_timeout_minutes", MFA_SESSION_TIMEOUT_MINUTES)) * 60


def get_password_reset_minutes() -> int:
    return int(load_system_configuration().get("password_reset_minutes", PASSWORD_RESET_MINUTES))


def get_lockout_attempts() -> int:
    return int(load_system_configuration().get("lockout_attempts", LOCKOUT_ATTEMPTS))


def get_lockout_minutes() -> int:
    return int(load_system_configuration().get("lockout_minutes", LOCKOUT_MINUTES))


def get_billing_unit_minutes() -> int:
    return int(load_system_configuration().get("billing_unit_minutes", BILLING_UNIT_MINUTES))


def get_eligibility_run_days() -> list[int]:
    return list(load_system_configuration().get("eligibility_run_days", list(ELIGIBILITY_RUN_DAYS)))


def get_eligibility_check_interval_hours() -> int:
    return int(load_system_configuration().get("eligibility_check_interval_hours", ELIGIBILITY_CHECK_INTERVAL_HOURS))


def get_default_landing_page() -> str:
    return str(load_system_configuration().get("default_landing_page", "dashboard"))


def _normalize_document_names(names: list[Any], fallback: tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in names:
        clean = str(item or "").strip()
        if not clean:
            continue
        dedupe_key = clean.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(clean)
    return normalized or list(fallback)


def _agency_scoped_document_templates(settings: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    scoped = settings.get("document_templates_by_agency", {})
    return scoped if isinstance(scoped, dict) else {}


def _list_required_documents(document_type: str, fallback: tuple[str, ...], agency_id: str = "") -> list[str]:
    settings = load_settings()
    document_key = DOCUMENT_TEMPLATE_KEYS[document_type]
    clean_agency_id = str(agency_id or "").strip() or get_current_agency_id()
    if clean_agency_id:
        agency_templates = _agency_scoped_document_templates(settings).get(clean_agency_id, {})
        if isinstance(agency_templates, dict):
            scoped = agency_templates.get(document_key, [])
            if isinstance(scoped, list):
                return _normalize_document_names(scoped, fallback)
    configured = settings.get(document_key, [])
    if isinstance(configured, list):
        return _normalize_document_names(configured, fallback)
    return list(fallback)


def list_provider_required_documents(agency_id: str = "") -> list[str]:
    return _list_required_documents("provider", PROVIDER_REQUIRED_DOCUMENTS, agency_id)


def list_client_required_documents(agency_id: str = "") -> list[str]:
    return _list_required_documents("client", CLIENT_REQUIRED_DOCUMENTS, agency_id)


def save_required_documents(document_type: str, names: list[str], agency_id: str = "") -> list[str]:
    document_key = DOCUMENT_TEMPLATE_KEYS.get(str(document_type or "").strip().lower())
    if not document_key:
        raise ValueError("Tipo de documento no valido.")
    fallback = PROVIDER_REQUIRED_DOCUMENTS if document_type.lower() == "provider" else CLIENT_REQUIRED_DOCUMENTS
    settings = load_settings()
    normalized = _normalize_document_names(names, fallback)
    clean_agency_id = str(agency_id or "").strip() or get_current_agency_id()
    if clean_agency_id:
        scoped_templates = _agency_scoped_document_templates(settings)
        agency_templates = scoped_templates.get(clean_agency_id, {})
        if not isinstance(agency_templates, dict):
            agency_templates = {}
        agency_templates[document_key] = normalized
        scoped_templates[clean_agency_id] = agency_templates
        settings["document_templates_by_agency"] = scoped_templates
    else:
        settings[document_key] = normalized
    save_settings(settings)
    return normalized


def get_current_agency_id() -> str:
    return str(load_settings().get("current_agency_id", ""))


def get_current_agency() -> dict[str, Any] | None:
    current_agency_id = get_current_agency_id()
    if not current_agency_id:
        return None
    return next((item for item in load_agencies() if item.get("agency_id") == current_agency_id), None)


def set_current_agency(agency_id: str) -> dict[str, Any] | None:
    agency = next((item for item in load_agencies() if item.get("agency_id") == agency_id), None)
    if agency is None:
        raise ValueError("No encontre esa agencia.")
    settings = load_settings()
    settings["current_agency_id"] = agency_id
    save_settings(settings)
    return agency


def add_agency(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_agencies()
    agency_id = payload.get("agency_id") or f"AGY-{uuid.uuid4().hex[:8].upper()}"
    match_index = next(
        (
            index
            for index, item in enumerate(items)
            if item.get("agency_id") == payload.get("agency_id")
            or item.get("agency_name") == payload.get("agency_name")
        ),
        None,
    )
    previous = items[match_index] if match_index is not None else {}
    record = {
        "agency_id": previous.get("agency_id", agency_id),
        "agency_name": payload["agency_name"],
        "agency_code": payload.get("agency_code", ""),
        "notification_email": payload.get("notification_email", ""),
        "contact_name": payload.get("contact_name", ""),
        "notes": payload.get("notes", ""),
        "logo_file_name": str(previous.get("logo_file_name", "")),
        "logo_file_path": str(previous.get("logo_file_path", "")),
        "created_at": previous.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
    }
    logo_content = payload.get("logo_file_content", b"") or b""
    logo_name = str(payload.get("logo_file_name", "")).strip()
    if logo_content:
        upload_name = _agency_logo_filename(record["agency_name"], logo_name)
        record["logo_file_path"] = _write_upload_file("agency_logos", upload_name, logo_content)
        record["logo_file_name"] = upload_name
    if match_index is None:
        items.insert(0, record)
    else:
        items[match_index] = record
    save_agencies(items)

    settings = load_settings()
    if not settings.get("current_agency_id"):
        settings["current_agency_id"] = record["agency_id"]
        save_settings(settings)
    return record


def get_agency_logo_bytes(agency_id: str) -> tuple[bytes, str]:
    agency = next((item for item in load_agencies() if item.get("agency_id") == agency_id), None)
    if agency is None:
        raise ValueError("No encontre esa agencia.")
    return (
        _read_upload_file(str(agency.get("logo_file_path", ""))),
        str(agency.get("logo_file_name", f"{agency_id}.png")),
    )


def load_provider_contracts() -> list[dict[str, Any]]:
    return _load_list(PROVIDER_CONTRACTS_FILE)


def save_provider_contracts(items: list[dict[str, Any]]) -> None:
    _save_list(PROVIDER_CONTRACTS_FILE, items)


def load_notifications() -> list[dict[str, Any]]:
    return _load_list(NOTIFICATIONS_FILE)


def save_notifications(items: list[dict[str, Any]]) -> None:
    _save_list(NOTIFICATIONS_FILE, items)


def get_notification_by_id(notification_id: str) -> dict[str, Any] | None:
    notification_id = str(notification_id).strip()
    if not notification_id:
        return None
    return next((item for item in load_notifications() if str(item.get("notification_id", "")).strip() == notification_id), None)


def _notification_state_value(item: dict[str, Any]) -> str:
    clean = str(item.get("notification_state", "active")).strip().lower()
    return clean or "active"


def _notification_dedupe_key(
    payload: dict[str, Any],
    recipient_email: str,
    recipient_label: str,
) -> str:
    explicit = str(payload.get("dedupe_key", "")).strip()
    if explicit:
        return explicit
    parts = [
        str(payload.get("agency_id", "")).strip(),
        str(payload.get("category", "general")).strip().lower(),
        str(payload.get("related_id", "")).strip(),
        str(payload.get("subject", "")).strip(),
        recipient_email.strip().lower(),
        recipient_label.strip().lower(),
        str(payload.get("message", "")).strip(),
    ]
    return "||".join(parts)


def load_era_archives() -> list[dict[str, Any]]:
    return _load_list(ERA_ARCHIVES_FILE)


def save_era_archives(items: list[dict[str, Any]]) -> None:
    _save_list(ERA_ARCHIVES_FILE, items)


def load_users() -> list[dict[str, Any]]:
    return _load_list(USERS_FILE)


def save_users(items: list[dict[str, Any]]) -> None:
    _save_list(USERS_FILE, items)


def load_claim_audit_logs() -> list[dict[str, Any]]:
    return _load_list(CLAIM_AUDIT_LOGS_FILE)


def save_claim_audit_logs(items: list[dict[str, Any]]) -> None:
    _save_list(CLAIM_AUDIT_LOGS_FILE, items)


def load_system_audit_logs() -> list[dict[str, Any]]:
    return _load_list(SYSTEM_AUDIT_LOGS_FILE)


def save_system_audit_logs(items: list[dict[str, Any]]) -> None:
    _save_list(SYSTEM_AUDIT_LOGS_FILE, items)


def load_password_reset_tokens() -> list[dict[str, Any]]:
    return _load_list(PASSWORD_RESET_TOKENS_FILE)


def save_password_reset_tokens(items: list[dict[str, Any]]) -> None:
    _save_list(PASSWORD_RESET_TOKENS_FILE, items)


def load_calendar_events() -> list[dict[str, Any]]:
    return _load_list(CALENDAR_EVENTS_FILE)


def save_calendar_events(items: list[dict[str, Any]]) -> None:
    _save_list(CALENDAR_EVENTS_FILE, items)


def load_user_notes() -> list[dict[str, Any]]:
    return _load_list(USER_NOTES_FILE)


def save_user_notes(items: list[dict[str, Any]]) -> None:
    _save_list(USER_NOTES_FILE, items)


def load_eligibility_history() -> list[dict[str, Any]]:
    return _load_list(ELIGIBILITY_HISTORY_FILE)


def save_eligibility_history(items: list[dict[str, Any]]) -> None:
    _save_list(ELIGIBILITY_HISTORY_FILE, items)


def _validate_password_text(password: str) -> str:
    clean_password = str(password or "")
    if len(clean_password.strip()) < 8:
        raise ValueError("La contrasena debe tener al menos 8 caracteres.")
    return clean_password


def _clear_password_reset_tokens_for_username(username: str) -> None:
    clean_username = str(username or "").strip().lower()
    if not clean_username:
        return
    items = [
        item
        for item in load_password_reset_tokens()
        if str(item.get("username", "")).strip().lower() != clean_username
    ]
    save_password_reset_tokens(items)


def _password_hash(password: str, salt: str | None = None) -> str:
    clean_salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(clean_salt),
        200_000,
    ).hex()
    return f"{clean_salt}${digest}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest = stored_hash.split("$", 1)
    except ValueError:
        return False
    candidate = _password_hash(password, salt).split("$", 1)[1]
    return hmac.compare_digest(candidate, digest)


def _default_permissions_for_role(role: str) -> dict[str, bool]:
    defaults = default_module_permissions_for_role(role)
    normalized = {key: bool(defaults.get(key, False)) for key in PERMISSION_KEYS}
    normalized["security"] = True
    if "dashboard" not in normalized:
        normalized["dashboard"] = True
    return normalized


def _normalize_permissions(payload: dict[str, Any] | None, role: str) -> dict[str, bool]:
    defaults = _default_permissions_for_role(role)
    if not payload:
        return defaults
    normalized: dict[str, bool] = {}
    for key in PERMISSION_KEYS:
        value = payload.get(key, False)
        normalized[key] = bool(value)
    if not normalized.get("security"):
        normalized["security"] = True
    return normalized


def _sanitize_user(item: dict[str, Any]) -> dict[str, Any]:
    clean = item.copy()
    clean["module_permissions"] = _normalize_permissions(clean.get("module_permissions"), str(clean.get("role", "MANAGER")))
    clean["permission_overrides"] = {
        str(key): bool(value)
        for key, value in (clean.get("permission_overrides", {}) if isinstance(clean.get("permission_overrides", {}), dict) else {}).items()
    }
    clean.pop("password_hash", None)
    clean.pop("mfa_secret", None)
    clean.pop("mfa_pending_secret", None)
    return clean


def _profile_asset_filename(username: str, original_name: str) -> str:
    safe_username = "".join(char.lower() if char.isalnum() else "_" for char in username).strip("_") or "user"
    original = Path(original_name or "profile.png").name
    return f"{safe_username}_{original}"


def _totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("utf-8").rstrip("=")


def _totp_code(secret: str, counter: int) -> str:
    padded_secret = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded_secret, casefold=True)
    counter_bytes = counter.to_bytes(8, byteorder="big")
    digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )
    return f"{binary % 1_000_000:06d}"


def _verify_totp(secret: str, code: str, window: int = 1) -> bool:
    clean_code = code.strip()
    if len(clean_code) != 6 or not clean_code.isdigit():
        return False
    current_counter = int(datetime.now().timestamp() // 30)
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_totp_code(secret, current_counter + offset), clean_code):
            return True
    return False


def _lockout_until() -> str:
    return (datetime.now() + timedelta(minutes=get_lockout_minutes())).isoformat()


def _format_lockout_message(locked_until: str) -> str:
    try:
        lock_time = datetime.fromisoformat(locked_until)
        return f"Cuenta bloqueada hasta {lock_time.strftime('%m/%d/%Y %H:%M')}."
    except ValueError:
        return "Cuenta bloqueada temporalmente."


def summarize_claims() -> dict[str, Any]:
    claims = _filter_current_agency(load_claims())
    summary = {
        "pending": 0,
        "paid": 0,
        "partial": 0,
        "denied": 0,
        "queued": 0,
        "transmitted": 0,
        "total": len(claims),
    }
    for claim in claims:
        status = claim.get("status", "pending")
        summary[status] = summary.get(status, 0) + 1
        transmission_status = claim.get("transmission_status", "queued")
        summary[transmission_status] = summary.get(transmission_status, 0) + 1
    recent = sorted(claims, key=lambda item: item.get("updated_at", ""), reverse=True)[:10]
    summary["recent"] = recent
    return summary


def _with_current_agency(payload: dict[str, Any]) -> dict[str, Any]:
    current_agency = get_current_agency()
    enriched = payload.copy()
    if current_agency is not None:
        enriched.setdefault("agency_id", current_agency.get("agency_id", ""))
        enriched.setdefault("agency_name", current_agency.get("agency_name", ""))
    else:
        enriched.setdefault("agency_id", "")
        enriched.setdefault("agency_name", "")
    return enriched


def _stage_progress(stage: str) -> int:
    stages = {
        "NEW": 10,
        "COLLECTING_DOCS": 25,
        "INTERVIEW": 45,
        "SUBMITTED": 45,
        "OFFER_SENT": 65,
        "IN_REVIEW": 70,
        "ONBOARDING": 85,
        "APPROVED": 90,
        "ACTIVE": 100,
    }
    return stages.get(stage, 0)


def _countdown_fields(submitted_date: str) -> tuple[str, int]:
    start = parse_user_date(submitted_date)
    target_date = start + timedelta(days=90)
    days_remaining = max((target_date - datetime.now().date()).days, 0)
    return format_user_date(target_date), days_remaining


def _notification_recipient(agency_id: str = "") -> str:
    agencies = load_agencies()
    agency = next((item for item in agencies if item.get("agency_id") == agency_id), None)
    if agency is not None:
        return str(agency.get("notification_email", ""))
    current_agency = get_current_agency()
    return str(current_agency.get("notification_email", "")) if current_agency else ""


def _clean_user_date(value: Any) -> str:
    clean_value = str(value or "").strip()
    if not clean_value or clean_value == "-":
        return ""
    return format_user_date(clean_value)


def _normalize_provider_requested_status(value: Any) -> str:
    status = str(value or "").strip().title()
    if status in {"Pending", "Delivered", "Ignored"}:
        return status
    return ""


def _provider_document_status_snapshot(
    requested_status: str,
    approval_status: str,
    expiration_date: str,
    has_document: bool,
    today: date | None = None,
) -> dict[str, Any]:
    current_day = today or datetime.now().date()
    normalized_requested = _normalize_provider_requested_status(requested_status) or "Pending"
    normalized_approval = str(approval_status or "").strip().lower()
    ignored = normalized_requested == "Ignored"
    days_until_expiration: int | None = None
    if expiration_date:
        try:
            days_until_expiration = (parse_user_date(expiration_date) - current_day).days
        except ValueError:
            days_until_expiration = None
    is_expired = bool(has_document and not ignored and days_until_expiration is not None and days_until_expiration < 0)
    expiring_soon = bool(has_document and not ignored and days_until_expiration is not None and 0 <= days_until_expiration <= 30)

    if ignored:
        display_status = "Ignored"
    elif is_expired:
        display_status = "Expired"
    elif normalized_approval == "pending":
        display_status = "Pending Approval"
    elif normalized_requested == "Delivered":
        display_status = "Delivered"
    else:
        display_status = "Pending"

    expiration_state = "ignored" if ignored else "expired" if is_expired else "expiring_soon" if expiring_soon else "ok"
    return {
        "requested_status": normalized_requested,
        "display_status": display_status,
        "ignored": ignored,
        "delivered": display_status == "Delivered",
        "completed": display_status in {"Delivered", "Ignored"},
        "is_expired": is_expired,
        "expiring_soon": expiring_soon,
        "days_until_expiration": days_until_expiration,
        "expiration_state": expiration_state,
    }


def _provider_document_filename(provider_name: str, document_name: str, original_name: str) -> str:
    clean_provider = "".join(char.lower() if char.isalnum() else "_" for char in provider_name).strip("_") or "provider"
    clean_document = "".join(char.lower() if char.isalnum() else "_" for char in document_name).strip("_") or "document"
    original = Path(original_name or "document.pdf").name
    return f"{clean_provider}_{clean_document}_{original}"


def _client_document_filename(client_name: str, document_name: str, original_name: str) -> str:
    clean_client = "".join(char.lower() if char.isalnum() else "_" for char in client_name).strip("_") or "client"
    clean_document = "".join(char.lower() if char.isalnum() else "_" for char in document_name).strip("_") or "document"
    original = Path(original_name or "document.pdf").name
    return f"{clean_client}_{clean_document}_{original}"


def _agency_logo_filename(agency_name: str, original_name: str) -> str:
    clean_agency = "".join(char.lower() if char.isalnum() else "_" for char in agency_name).strip("_") or "agency"
    original = Path(original_name or "logo.png").name
    return f"{clean_agency}_{original}"


def _build_provider_documents(
    provider_name: str,
    payload_documents: list[dict[str, Any]] | None,
    previous_documents: list[dict[str, Any]] | None = None,
    agency_id: str = "",
) -> tuple[list[dict[str, Any]], int, int, int]:
    previous_map = {
        str(item.get("document_name", "")): item
        for item in (previous_documents or [])
    }
    payload_map = {
        str(item.get("document_name", "")): item
        for item in (payload_documents or [])
        if item.get("document_name")
    }

    documents: list[dict[str, Any]] = []
    completed_count = 0
    admin_roles = {"ADMIN", "MANAGER", "HR", "OFFICE"}

    provider_folder = f"provider_contracts/{_safe_folder_name(provider_name)}"
    for document_name in list_provider_required_documents(agency_id):
        previous = previous_map.get(document_name, {})
        payload = payload_map.get(document_name, {})
        file_content = payload.get("file_content", b"") or b""
        file_name = str(payload.get("file_name", "") or previous.get("file_name", ""))
        file_path = str(previous.get("file_path", ""))
        onedrive_file_path = str(previous.get("onedrive_file_path", ""))
        actor_username = str(payload.get("actor_username", "") or previous.get("submitted_by_username", "")).strip()
        actor_role = normalize_role(str(payload.get("actor_role", "") or previous.get("submitted_by_role", "")).strip().upper())
        actor_name = str(payload.get("actor_name", "") or previous.get("submitted_by_name", "")).strip()
        if file_content:
            upload_name = _provider_document_filename(provider_name, document_name, str(payload.get("file_name", "")))
            file_path = _write_upload_file(provider_folder, upload_name, file_content)
            file_name = Path(upload_name).name
            onedrive_file_path = _write_onedrive_file(
                [ONEDRIVE_APP_DIR, "Providers", provider_name],
                upload_name,
                file_content,
            )
        elif not onedrive_file_path and file_path:
            try:
                onedrive_file_path = _write_onedrive_file(
                    [ONEDRIVE_APP_DIR, "Providers", provider_name],
                    file_name or Path(file_path).name,
                    _read_upload_file(file_path),
                )
            except FileNotFoundError:
                onedrive_file_path = ""

        previous_requested_status = _normalize_provider_requested_status(
            previous.get("requested_status", "") or previous.get("status", "")
        )
        requested_status = _normalize_provider_requested_status(payload.get("status"))
        approval_status = str(previous.get("approval_status", "missing") or "missing").strip().lower()
        direct_admin_delivery = bool(file_content) and actor_role in admin_roles
        if file_content and actor_role in {"BCBA", "BCABA", "RBT"}:
            approval_status = "pending"
            requested_status = "Pending"
        elif requested_status == "Ignored":
            approval_status = "ignored"
        elif requested_status == "Delivered":
            approval_status = "approved" if (file_path or file_content or bool(previous.get("file_path"))) else approval_status
        elif direct_admin_delivery and requested_status != "Pending":
            requested_status = "Delivered"
            approval_status = "approved"
        elif requested_status == "Pending":
            approval_status = "missing"
        elif approval_status == "pending":
            requested_status = previous_requested_status or "Pending"
        else:
            requested_status = previous_requested_status or ("Delivered" if approval_status == "approved" else "Pending")

        issued_date = _clean_user_date(payload.get("issued_date")) or str(previous.get("issued_date", ""))
        expiration_date = _clean_user_date(payload.get("expiration_date")) or str(previous.get("expiration_date", ""))
        approved_by_username = str(previous.get("approved_by_username", ""))
        approved_by_name = str(previous.get("approved_by_name", ""))
        approved_at = str(previous.get("approved_at", ""))
        if file_content and actor_role in {"BCBA", "BCABA", "RBT"}:
            approved_by_username = ""
            approved_by_name = ""
            approved_at = ""
        elif requested_status == "Delivered" or direct_admin_delivery:
            approved_by_username = actor_username or approved_by_username
            approved_by_name = actor_name or approved_by_name
            approved_at = str(payload.get("submitted_at", "")) or approved_at
        elif requested_status in {"Pending", "Ignored"}:
            approved_by_username = approved_by_username if requested_status != "Pending" else ""
            approved_by_name = approved_by_name if requested_status != "Pending" else ""
            approved_at = approved_at if requested_status != "Pending" else ""

        status_snapshot = _provider_document_status_snapshot(
            requested_status=requested_status,
            approval_status=approval_status,
            expiration_date=expiration_date,
            has_document=bool(file_path or file_name or requested_status in {"Delivered", "Ignored"}),
        )
        if status_snapshot["completed"]:
            completed_count += 1

        documents.append(
            {
                "document_name": document_name,
                "issued_date": issued_date,
                "expiration_date": expiration_date,
                "requested_status": status_snapshot["requested_status"],
                "status": status_snapshot["display_status"],
                "delivered": status_snapshot["delivered"],
                "ignored": status_snapshot["ignored"],
                "is_expired": status_snapshot["is_expired"],
                "expiring_soon": status_snapshot["expiring_soon"],
                "days_until_expiration": status_snapshot["days_until_expiration"],
                "expiration_state": status_snapshot["expiration_state"],
                "file_name": file_name,
                "file_path": file_path,
                "onedrive_file_path": onedrive_file_path,
                "approval_status": approval_status,
                "submitted_by_username": actor_username,
                "submitted_by_name": actor_name,
                "submitted_by_role": actor_role or str(previous.get("submitted_by_role", "")),
                "submitted_at": str(payload.get("submitted_at", previous.get("submitted_at", ""))),
                "approved_by_username": approved_by_username,
                "approved_by_name": approved_by_name,
                "approved_at": approved_at,
                "expiry_notice_30_sent_at": str(previous.get("expiry_notice_30_sent_at", "")),
                "expiry_notice_expired_sent_at": str(previous.get("expiry_notice_expired_sent_at", "")),
            }
        )

    total_documents = len(list_provider_required_documents(agency_id))
    progress_percent = int(round((completed_count / total_documents) * 100)) if total_documents else 0
    return (documents, completed_count, total_documents, progress_percent)


def _enrich_provider_contract(item: dict[str, Any]) -> dict[str, Any]:
    enriched = item.copy()
    documents, completed_count, total_documents, progress_percent = _build_provider_documents(
        str(enriched.get("provider_name", "")),
        [],
        enriched.get("documents", []),
        str(enriched.get("agency_id", "")),
    )
    delivered_count = len([document for document in documents if document.get("status") == "Delivered"])
    ignored_count = len([document for document in documents if document.get("status") == "Ignored"])
    expired_documents = [document for document in documents if document.get("is_expired")]
    expiring_documents = [document for document in documents if document.get("expiring_soon")]
    enriched["documents"] = documents
    enriched["delivered_documents"] = delivered_count
    enriched["completed_documents"] = completed_count
    enriched["ignored_documents"] = ignored_count
    enriched["total_documents"] = total_documents
    enriched["progress_percent"] = progress_percent
    enriched["expired_documents"] = len(expired_documents)
    enriched["expired_document_names"] = [str(document.get("document_name", "")) for document in expired_documents]
    enriched["expiring_documents"] = len(expiring_documents)
    enriched["expiring_document_names"] = [str(document.get("document_name", "")) for document in expiring_documents]
    enriched["documents_complete"] = completed_count == total_documents and total_documents > 0
    return enriched


def _build_client_documents(
    client_name: str,
    payload_documents: list[dict[str, Any]] | None,
    previous_documents: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], int, int, int]:
    previous_map = {
        str(item.get("document_name", "")): item
        for item in (previous_documents or [])
    }
    payload_map = {
        str(item.get("document_name", "")): item
        for item in (payload_documents or [])
        if item.get("document_name")
    }

    documents: list[dict[str, Any]] = []
    delivered_count = 0

    client_folder = f"client_documents/{_safe_folder_name(client_name)}"
    for document_name in list_client_required_documents():
        previous = previous_map.get(document_name, {})
        payload = payload_map.get(document_name, {})
        file_content = payload.get("file_content", b"") or b""
        file_name = str(payload.get("file_name", "") or previous.get("file_name", ""))
        file_path = str(previous.get("file_path", ""))
        onedrive_file_path = str(previous.get("onedrive_file_path", ""))
        if file_content:
            upload_name = _client_document_filename(client_name, document_name, str(payload.get("file_name", "")))
            file_path = _write_upload_file(client_folder, upload_name, file_content)
            file_name = Path(upload_name).name
            onedrive_file_path = _write_onedrive_file(
                [ONEDRIVE_APP_DIR, "Clients", client_name],
                upload_name,
                file_content,
            )
        elif not onedrive_file_path and file_path:
            try:
                onedrive_file_path = _write_onedrive_file(
                    [ONEDRIVE_APP_DIR, "Clients", client_name],
                    file_name or Path(file_path).name,
                    _read_upload_file(file_path),
                )
            except FileNotFoundError:
                onedrive_file_path = ""

        previous_status = str(previous.get("status", "") or "").strip().title()
        requested_status = str(payload.get("status", "") or "").strip().title()
        issued_date = _clean_user_date(payload.get("issued_date")) or str(previous.get("issued_date", ""))
        expiration_date = _clean_user_date(payload.get("expiration_date")) or str(previous.get("expiration_date", ""))

        if requested_status not in {"Delivered", "Ignored", "Pending"}:
            requested_status = ""

        if requested_status == "Ignored":
            status = "Ignored"
        elif requested_status == "Delivered":
            status = "Delivered"
        elif file_content:
            status = "Delivered"
        elif not requested_status and previous_status in {"Delivered", "Ignored", "Pending"}:
            status = previous_status
        elif requested_status == "Pending" and not any([file_content, issued_date, expiration_date]) and previous_status in {"Delivered", "Ignored"}:
            status = previous_status
        else:
            status = requested_status or ("Delivered" if file_path else "Pending")

        delivered = status == "Delivered"
        if delivered:
            delivered_count += 1

        documents.append(
            {
                "document_name": document_name,
                "issued_date": issued_date,
                "expiration_date": expiration_date,
                "status": status,
                "delivered": delivered,
                "ignored": status == "Ignored",
                "file_name": file_name,
                "file_path": file_path,
                "onedrive_file_path": onedrive_file_path,
            }
        )

    total_documents = len(list_client_required_documents())
    progress_percent = int(round((delivered_count / total_documents) * 100)) if total_documents else 0
    return (documents, delivered_count, total_documents, progress_percent)


def _enrich_client(item: dict[str, Any]) -> dict[str, Any]:
    enriched = item.copy()
    documents, delivered_count, total_documents, progress_percent = _build_client_documents(
        f"{enriched.get('first_name', '')} {enriched.get('last_name', '')}".strip(),
        [],
        enriched.get("documents", []),
    )
    provider_contract_lookup = {
        str(contract.get("contract_id", "")).strip(): contract
        for contract in _filter_current_agency(load_provider_contracts())
        if str(contract.get("contract_id", "")).strip()
    }
    for role_key in ("bcba", "bcaba", "rbt"):
        contract_id = str(enriched.get(f"{role_key}_contract_id", "")).strip()
        contract = provider_contract_lookup.get(contract_id, {})
        enriched[f"{role_key}_provider_name"] = str(contract.get("provider_name", "")).strip()
        enriched[f"{role_key}_provider_npi"] = str(contract.get("provider_npi", "")).strip()
    enriched["documents"] = documents
    enriched["delivered_documents"] = delivered_count
    enriched["total_documents"] = total_documents
    enriched["progress_percent"] = progress_percent
    enriched["care_team_names"] = [
        str(enriched.get(f"{role_key}_provider_name", "")).strip()
        for role_key in ("bcba", "bcaba", "rbt")
        if str(enriched.get(f"{role_key}_provider_name", "")).strip()
    ]
    return enriched


def add_notification(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_notifications()
    category = str(payload.get("category", "general")).strip() or "general"
    recipient_email = payload.get("recipient_email", "") or _notification_recipient(payload.get("agency_id", ""))
    recipient_label = str(payload.get("recipient_label", "")).strip() or recipient_email or "Sin destinatario"
    dedupe_key = _notification_dedupe_key(payload, str(recipient_email), recipient_label)
    allow_duplicate = bool(payload.get("allow_duplicate")) or category == "manual_email"

    if not allow_duplicate:
        for index, item in enumerate(items):
            if _notification_state_value(item) != "active":
                continue
            if str(item.get("notification_key", "")).strip() != dedupe_key:
                continue
            updated = dict(item)
            updated["agency_id"] = payload.get("agency_id", updated.get("agency_id", ""))
            updated["agency_name"] = payload.get("agency_name", updated.get("agency_name", ""))
            updated["category"] = category
            updated["subject"] = payload["subject"]
            updated["message"] = payload["message"]
            updated["related_id"] = payload.get("related_id", updated.get("related_id", ""))
            updated["recipient_label"] = recipient_label
            updated["recipient_email"] = recipient_email
            updated["updated_at"] = datetime.now().isoformat()
            items[index] = updated
            save_notifications(items)
            return updated

    record = {
        "notification_id": payload.get("notification_id") or f"NTF-{uuid.uuid4().hex[:8].upper()}",
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "category": category,
        "subject": payload["subject"],
        "message": payload["message"],
        "related_id": payload.get("related_id", ""),
        "recipient_label": recipient_label,
        "recipient_email": recipient_email,
        "notification_key": dedupe_key,
        "notification_state": "active",
        "handled_by": "",
        "handled_note": "",
        "handled_at": "",
        "deleted_by": "",
        "deleted_at": "",
        "email_status": "queued" if recipient_email else "needs_email_setup",
        "email_error": "",
        "email_last_action_at": "",
        "created_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_notifications(items)
    return record


def list_notifications(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    items = _filter_current_agency(load_notifications())
    if not include_inactive:
        items = [item for item in items if _notification_state_value(item) == "active"]
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)


def update_notification_email_status(
    notification_id: str,
    email_status: str,
    email_error: str = "",
    email_last_action_at: str = "",
) -> dict[str, Any]:
    items = load_notifications()
    notification_id = str(notification_id).strip()
    for index, item in enumerate(items):
        if str(item.get("notification_id", "")).strip() != notification_id:
            continue
        updated = dict(item)
        updated["email_status"] = str(email_status).strip() or updated.get("email_status", "")
        updated["email_error"] = str(email_error).strip()
        updated["email_last_action_at"] = str(email_last_action_at).strip() or datetime.now().strftime("%m/%d/%Y %H:%M")
        updated["updated_at"] = datetime.now().isoformat()
        items[index] = updated
        save_notifications(items)
        return updated
    raise ValueError("No encontre la notificacion indicada.")


def _reopen_provider_document_notification_gate(notification: dict[str, Any]) -> None:
    category = str(notification.get("category", "")).strip()
    if category not in {"provider_document_expiring", "provider_document_expired"}:
        return
    contract_id = str(notification.get("related_id", "")).strip()
    if not contract_id:
        return

    subject = str(notification.get("subject", "")).strip()
    document_name = ""
    if ":" in subject:
        document_name = subject.split(":", 1)[1].strip()
    if not document_name:
        return

    contracts = load_provider_contracts()
    updated_any = False
    for index, item in enumerate(contracts):
        if str(item.get("contract_id", "")).strip() != contract_id:
            continue
        contract = dict(item)
        documents = list(contract.get("documents", []))
        doc_updated = False
        for doc_index, document in enumerate(documents):
            if str(document.get("document_name", "")).strip() != document_name:
                continue
            updated_document = dict(document)
            if category == "provider_document_expiring":
                updated_document["expiry_notice_30_sent_at"] = ""
            if category == "provider_document_expired":
                updated_document["expiry_notice_expired_sent_at"] = ""
            documents[doc_index] = updated_document
            doc_updated = True
            break
        if not doc_updated:
            continue
        contract["documents"] = documents
        contract["updated_at"] = datetime.now().isoformat()
        contracts[index] = contract
        updated_any = True
        break

    if updated_any:
        save_provider_contracts(contracts)


def update_notification_state(
    notification_id: str,
    notification_state: str,
    *,
    acted_by: str = "",
    acted_note: str = "",
) -> dict[str, Any]:
    items = load_notifications()
    notification_id = str(notification_id).strip()
    clean_state = str(notification_state or "active").strip().lower() or "active"
    if clean_state not in {"active", "handled", "deleted"}:
        raise ValueError("El estado de notificacion no es valido.")

    for index, item in enumerate(items):
        if str(item.get("notification_id", "")).strip() != notification_id:
            continue
        updated = dict(item)
        updated["notification_state"] = clean_state
        if clean_state == "handled":
            updated["handled_by"] = str(acted_by).strip()
            updated["handled_note"] = str(acted_note).strip()
            updated["handled_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        if clean_state == "deleted":
            updated["deleted_by"] = str(acted_by).strip()
            updated["handled_note"] = str(acted_note).strip()
            updated["deleted_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        updated["updated_at"] = datetime.now().isoformat()
        items[index] = updated
        save_notifications(items)
        if clean_state in {"handled", "deleted"}:
            _reopen_provider_document_notification_gate(updated)
        return updated
    raise ValueError("No encontre la notificacion indicada.")


def ensure_default_admin_user() -> dict[str, Any]:
    users = load_users()
    if users:
        return _sanitize_user(users[0])
    record = {
        "user_id": f"USR-{uuid.uuid4().hex[:8].upper()}",
        "full_name": "System Administrator",
        "username": "admin",
        "email": "",
        "phone": "",
        "job_title": "Administrator",
        "bio": "",
        "site_location": "",
        "county_name": "",
        "profile_color": "#0d51b8",
        "avatar_file_name": "",
        "avatar_file_path": "",
        "linked_provider_name": "",
        "role": "ADMIN",
        "active": True,
        "password_hash": _password_hash("TFBilling2026!"),
        "module_permissions": _default_permissions_for_role("ADMIN"),
        "permission_overrides": {},
        "failed_attempts": 0,
        "locked_until": "",
        "last_failed_at": "",
        "last_login_at": "",
        "mfa_enabled": False,
        "mfa_secret": "",
        "mfa_pending_secret": "",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    users.append(record)
    save_users(users)
    return _sanitize_user(record)


def list_users() -> list[dict[str, Any]]:
    ensure_default_admin_user()
    sanitized_users = [_sanitize_user(item) for item in load_users()]
    return sorted(sanitized_users, key=lambda item: item.get("updated_at", ""), reverse=True)


def add_user(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_default_admin_user()
    users = load_users()
    username = str(payload.get("username", "")).strip().lower()
    if not username:
        raise ValueError("El username no puede estar vacio.")

    match_index = next((index for index, item in enumerate(users) if item.get("username", "").lower() == username), None)
    previous = users[match_index] if match_index is not None else {}
    raw_password_text = str(payload.get("password", ""))
    password_text = _validate_password_text(raw_password_text) if raw_password_text.strip() else ""
    if match_index is None and not password_text:
        raise ValueError("La contrasena es obligatoria para crear el usuario.")

    record = {
        "user_id": previous.get("user_id", f"USR-{uuid.uuid4().hex[:8].upper()}"),
        "full_name": str(payload.get("full_name", "")).strip(),
        "username": username,
        "email": str(payload.get("email", previous.get("email", ""))).strip(),
        "phone": str(payload.get("phone", previous.get("phone", ""))).strip(),
        "job_title": str(payload.get("job_title", previous.get("job_title", ""))).strip(),
        "bio": str(payload.get("bio", previous.get("bio", ""))).strip(),
        "site_location": str(payload.get("site_location", previous.get("site_location", ""))).strip(),
        "county_name": str(payload.get("county_name", previous.get("county_name", ""))).strip(),
        "profile_color": str(payload.get("profile_color", previous.get("profile_color", "#0d51b8"))).strip() or "#0d51b8",
        "avatar_file_name": str(previous.get("avatar_file_name", "")),
        "avatar_file_path": str(previous.get("avatar_file_path", "")),
        "linked_provider_name": str(payload.get("linked_provider_name", previous.get("linked_provider_name", ""))).strip(),
        "role": str(payload.get("role", "MANAGER")).upper(),
        "active": bool(payload.get("active", True)),
        "password_hash": previous.get("password_hash", ""),
        "module_permissions": _normalize_permissions(payload.get("module_permissions"), str(payload.get("role", previous.get("role", "MANAGER")))),
        "permission_overrides": {
            str(key): bool(value)
            for key, value in (
                payload.get("permission_overrides", previous.get("permission_overrides", {}))
                if isinstance(payload.get("permission_overrides", previous.get("permission_overrides", {})), dict)
                else {}
            ).items()
        },
        "failed_attempts": int(previous.get("failed_attempts", 0)),
        "locked_until": str(previous.get("locked_until", "")),
        "last_failed_at": str(previous.get("last_failed_at", "")),
        "last_login_at": str(previous.get("last_login_at", "")),
        "mfa_enabled": bool(previous.get("mfa_enabled", False)),
        "mfa_secret": str(previous.get("mfa_secret", "")),
        "mfa_pending_secret": str(previous.get("mfa_pending_secret", "")),
        "created_at": previous.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
    }
    if password_text:
        record["password_hash"] = _password_hash(password_text)
    if not record["password_hash"]:
        raise ValueError("No pude guardar la contrasena del usuario.")

    avatar_content = payload.get("avatar_file_content", b"") or b""
    avatar_name = str(payload.get("avatar_file_name", "")).strip()
    if avatar_content:
        upload_name = _profile_asset_filename(username, avatar_name)
        record["avatar_file_path"] = _write_upload_file("user_profiles", upload_name, avatar_content)
        record["avatar_file_name"] = upload_name

    if match_index is None:
        users.insert(0, record)
    else:
        users[match_index] = record
    save_users(users)
    if password_text:
        _clear_password_reset_tokens_for_username(username)
    return _sanitize_user(record)


def get_user_by_username(username: str) -> dict[str, Any] | None:
    ensure_default_admin_user()
    clean_username = username.strip().lower()
    for item in load_users():
        if item.get("username", "").lower() == clean_username:
            return item
    return None


def get_user_public_profile(username: str) -> dict[str, Any]:
    user = get_user_by_username(username)
    if user is None:
        raise ValueError("No encontre ese usuario.")
    return _sanitize_user(user)


def get_user_security_profile(username: str) -> dict[str, Any]:
    user = get_user_by_username(username)
    if user is None:
        raise ValueError("No encontre ese usuario.")
    normalized_permissions = _normalize_permissions(user.get("module_permissions"), str(user.get("role", "MANAGER")))
    return {
        "user_id": user.get("user_id", ""),
        "username": user.get("username", ""),
        "full_name": user.get("full_name", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "job_title": user.get("job_title", ""),
        "bio": user.get("bio", ""),
        "site_location": user.get("site_location", ""),
        "county_name": user.get("county_name", ""),
        "profile_color": user.get("profile_color", "#0d51b8"),
        "avatar_file_name": user.get("avatar_file_name", ""),
        "avatar_file_path": user.get("avatar_file_path", ""),
        "linked_provider_name": user.get("linked_provider_name", ""),
        "role": user.get("role", ""),
        "module_permissions": normalized_permissions,
        "permission_overrides": {
            str(key): bool(value)
            for key, value in (
                user.get("permission_overrides", {})
                if isinstance(user.get("permission_overrides", {}), dict)
                else {}
            ).items()
        },
        "failed_attempts": int(user.get("failed_attempts", 0)),
        "locked_until": str(user.get("locked_until", "")),
        "last_login_at": str(user.get("last_login_at", "")),
        "mfa_enabled": bool(user.get("mfa_enabled", False)),
        "mfa_pending_secret": str(user.get("mfa_pending_secret", "")),
        "mfa_setup_uri": (
            f"otpauth://totp/Blue%20Hope%20ABA%20Solutions:{user.get('username', '')}?secret={user.get('mfa_pending_secret', '')}&issuer=Blue%20Hope%20ABA%20Solutions"
            if user.get("mfa_pending_secret")
            else ""
        ),
    }


def authenticate_user(username: str, password: str) -> dict[str, Any]:
    ensure_default_admin_user()
    clean_username = username.strip().lower()
    users = load_users()
    lockout_attempts = get_lockout_attempts()
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        if not item.get("active", True):
            return {"ok": False, "status": "inactive", "message": "El usuario esta inactivo."}
        locked_until = str(item.get("locked_until", ""))
        if locked_until:
            try:
                if datetime.now() < datetime.fromisoformat(locked_until):
                    return {"ok": False, "status": "locked", "message": _format_lockout_message(locked_until)}
                item["locked_until"] = ""
            except ValueError:
                item["locked_until"] = ""
        if _verify_password(password, str(item.get("password_hash", ""))):
            item["failed_attempts"] = 0
            item["locked_until"] = ""
            item["last_failed_at"] = ""
            item["updated_at"] = datetime.now().isoformat()
            users[index] = item
            save_users(users)
            sanitized = _sanitize_user(item)
            return {
                "ok": True,
                "status": "ok",
                "message": "Login correcto.",
                "user": sanitized,
                "requires_mfa": bool(item.get("mfa_enabled") and item.get("mfa_secret")),
            }
        item["failed_attempts"] = int(item.get("failed_attempts", 0)) + 1
        item["last_failed_at"] = datetime.now().isoformat()
        item["updated_at"] = datetime.now().isoformat()
        message = "Username o password incorrecto."
        if item["failed_attempts"] >= lockout_attempts:
            item["failed_attempts"] = 0
            item["locked_until"] = _lockout_until()
            message = _format_lockout_message(item["locked_until"])
            status = "locked"
        else:
            remaining = lockout_attempts - item["failed_attempts"]
            message = f"Username o password incorrecto. Quedan {remaining} intento(s) antes del bloqueo."
            status = "invalid"
        users[index] = item
        save_users(users)
        return {"ok": False, "status": status, "message": message}
    return {"ok": False, "status": "invalid", "message": "Username o password incorrecto."}


def complete_user_login(username: str) -> dict[str, Any]:
    users = load_users()
    clean_username = username.strip().lower()
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        item["last_login_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        item["updated_at"] = datetime.now().isoformat()
        users[index] = item
        save_users(users)
        return _sanitize_user(item)
    raise ValueError("No encontre ese usuario.")


def change_password(username: str, current_password: str, new_password: str) -> dict[str, Any]:
    users = load_users()
    clean_username = username.strip().lower()
    validated_password = _validate_password_text(new_password)
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        if not _verify_password(current_password, str(item.get("password_hash", ""))):
            raise ValueError("La contrasena actual no es correcta.")
        item["password_hash"] = _password_hash(validated_password)
        item["failed_attempts"] = 0
        item["locked_until"] = ""
        item["last_failed_at"] = ""
        item["updated_at"] = datetime.now().isoformat()
        users[index] = item
        save_users(users)
        _clear_password_reset_tokens_for_username(clean_username)
        return _sanitize_user(item)
    raise ValueError("No encontre ese usuario.")


def initiate_mfa_setup(username: str, current_password: str) -> dict[str, Any]:
    users = load_users()
    clean_username = username.strip().lower()
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        if not _verify_password(current_password, str(item.get("password_hash", ""))):
            raise ValueError("La contrasena actual no es correcta.")
        item["mfa_pending_secret"] = _totp_secret()
        item["updated_at"] = datetime.now().isoformat()
        users[index] = item
        save_users(users)
        return get_user_security_profile(username)
    raise ValueError("No encontre ese usuario.")


def confirm_mfa_setup(username: str, code: str) -> dict[str, Any]:
    users = load_users()
    clean_username = username.strip().lower()
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        pending_secret = str(item.get("mfa_pending_secret", ""))
        if not pending_secret:
            raise ValueError("Primero genera la configuracion MFA.")
        if not _verify_totp(pending_secret, code):
            raise ValueError("El codigo MFA no es valido.")
        item["mfa_secret"] = pending_secret
        item["mfa_pending_secret"] = ""
        item["mfa_enabled"] = True
        item["updated_at"] = datetime.now().isoformat()
        users[index] = item
        save_users(users)
        return get_user_security_profile(username)
    raise ValueError("No encontre ese usuario.")


def disable_mfa(username: str, current_password: str) -> dict[str, Any]:
    users = load_users()
    clean_username = username.strip().lower()
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        if not _verify_password(current_password, str(item.get("password_hash", ""))):
            raise ValueError("La contrasena actual no es correcta.")
        item["mfa_enabled"] = False
        item["mfa_secret"] = ""
        item["mfa_pending_secret"] = ""
        item["updated_at"] = datetime.now().isoformat()
        users[index] = item
        save_users(users)
        return get_user_security_profile(username)
    raise ValueError("No encontre ese usuario.")


def verify_user_mfa(username: str, code: str) -> dict[str, Any]:
    user = get_user_by_username(username)
    if user is None:
        raise ValueError("No encontre ese usuario.")
    if not user.get("mfa_enabled") or not user.get("mfa_secret"):
        raise ValueError("Ese usuario no tiene MFA activo.")
    if not _verify_totp(str(user.get("mfa_secret", "")), code):
        raise ValueError("El codigo MFA no es valido.")
    return _sanitize_user(user)


def update_user_profile(username: str, payload: dict[str, Any]) -> dict[str, Any]:
    users = load_users()
    clean_username = username.strip().lower()
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        item["full_name"] = str(payload.get("full_name", item.get("full_name", ""))).strip()
        item["email"] = str(payload.get("email", item.get("email", ""))).strip()
        item["phone"] = str(payload.get("phone", item.get("phone", ""))).strip()
        item["job_title"] = str(payload.get("job_title", item.get("job_title", ""))).strip()
        item["bio"] = str(payload.get("bio", item.get("bio", ""))).strip()
        item["site_location"] = str(payload.get("site_location", item.get("site_location", ""))).strip()
        item["county_name"] = str(payload.get("county_name", item.get("county_name", ""))).strip()
        item["profile_color"] = str(payload.get("profile_color", item.get("profile_color", "#0d51b8"))).strip() or "#0d51b8"
        item["linked_provider_name"] = str(payload.get("linked_provider_name", item.get("linked_provider_name", ""))).strip()
        avatar_content = payload.get("avatar_file_content", b"") or b""
        avatar_name = str(payload.get("avatar_file_name", "")).strip()
        if avatar_content:
            upload_name = _profile_asset_filename(clean_username, avatar_name)
            item["avatar_file_path"] = _write_upload_file("user_profiles", upload_name, avatar_content)
            item["avatar_file_name"] = upload_name
        item["updated_at"] = datetime.now().isoformat()
        users[index] = item
        save_users(users)
        return _sanitize_user(item)
    raise ValueError("No encontre ese usuario.")


def get_user_avatar_bytes(username: str) -> tuple[bytes, str]:
    user = get_user_by_username(username)
    if user is None:
        raise ValueError("No encontre ese usuario.")
    return (
        _read_upload_file(str(user.get("avatar_file_path", ""))),
        str(user.get("avatar_file_name", f"{username}.png")),
    )


def create_password_reset_token(username: str) -> dict[str, Any]:
    user = get_user_by_username(username)
    if user is None or not user.get("active", True):
        raise ValueError("No encontre un usuario activo con ese username.")

    clean_username = str(user.get("username", "")).strip().lower()
    items = [
        item
        for item in load_password_reset_tokens()
        if item.get("username", "").strip().lower() != clean_username
    ]
    password_reset_minutes = get_password_reset_minutes()
    expires_at = datetime.now() + timedelta(minutes=password_reset_minutes)
    record = {
        "reset_id": f"RST-{uuid.uuid4().hex[:8].upper()}",
        "user_id": user.get("user_id", ""),
        "username": user.get("username", ""),
        "full_name": user.get("full_name", ""),
        "recovery_code": f"{secrets.randbelow(1_000_000):06d}",
        "expires_at": expires_at.isoformat(),
        "used_at": "",
        "created_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_password_reset_tokens(items)
    return {
        "reset_id": record["reset_id"],
        "user_id": record["user_id"],
        "username": record["username"],
        "full_name": record["full_name"],
        "recovery_code": record["recovery_code"],
        "expires_at": expires_at.strftime("%m/%d/%Y %H:%M"),
        "expires_in_minutes": password_reset_minutes,
    }


def reset_password_with_recovery_code(username: str, recovery_code: str, new_password: str) -> dict[str, Any]:
    clean_username = username.strip().lower()
    clean_code = recovery_code.strip()
    tokens = load_password_reset_tokens()
    token_index = None
    token_record: dict[str, Any] | None = None

    for index, item in enumerate(tokens):
        if item.get("username", "").strip().lower() != clean_username:
            continue
        if str(item.get("recovery_code", "")).strip() != clean_code:
            continue
        token_index = index
        token_record = item
        break

    if token_record is None or token_index is None:
        raise ValueError("El codigo de recuperacion no es valido.")

    used_at = str(token_record.get("used_at", ""))
    if used_at:
        raise ValueError("Ese codigo de recuperacion ya fue utilizado.")

    expires_at = str(token_record.get("expires_at", ""))
    try:
        if datetime.now() > datetime.fromisoformat(expires_at):
            raise ValueError("El codigo de recuperacion ya expiro.")
    except ValueError as exc:
        if str(exc) == "El codigo de recuperacion ya expiro.":
            raise
        raise ValueError("No pude validar la vigencia del codigo de recuperacion.") from exc

    validated_password = _validate_password_text(new_password)
    users = load_users()
    for index, item in enumerate(users):
        if item.get("username", "").lower() != clean_username:
            continue
        item["password_hash"] = _password_hash(validated_password)
        item["failed_attempts"] = 0
        item["locked_until"] = ""
        item["last_failed_at"] = ""
        item["updated_at"] = datetime.now().isoformat()
        users[index] = item
        save_users(users)

        token_record["used_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        token_record["updated_at"] = datetime.now().isoformat()
        tokens[token_index] = token_record
        save_password_reset_tokens(tokens)
        return _sanitize_user(item)

    raise ValueError("No encontre ese usuario.")


def add_claim_audit_log(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_claim_audit_logs()
    record = {
        "audit_id": payload.get("audit_id") or f"AUD-{uuid.uuid4().hex[:8].upper()}",
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "claim_id": payload.get("claim_id", ""),
        "action": payload.get("action", "UNKNOWN"),
        "actor_username": payload.get("actor_username", ""),
        "actor_name": payload.get("actor_name", ""),
        "details": payload.get("details", ""),
        "created_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_claim_audit_logs(items)
    return record


def list_claim_audit_logs(claim_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    items = _filter_current_agency(load_claim_audit_logs())
    if claim_id:
        items = [item for item in items if item.get("claim_id") == claim_id]
    items = sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)
    return items[:limit]


def add_system_audit_log(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_system_audit_logs()
    record = {
        "audit_id": payload.get("audit_id") or f"SYS-{uuid.uuid4().hex[:8].upper()}",
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "category": payload.get("category", "general"),
        "entity_type": payload.get("entity_type", ""),
        "entity_id": payload.get("entity_id", ""),
        "entity_name": payload.get("entity_name", ""),
        "action": payload.get("action", "UNKNOWN"),
        "actor_username": payload.get("actor_username", ""),
        "actor_name": payload.get("actor_name", ""),
        "details": payload.get("details", ""),
        "created_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_system_audit_logs(items)
    return record


def list_system_audit_logs(entity_type: str = "", category: str = "", limit: int = 50) -> list[dict[str, Any]]:
    items = _filter_current_agency(load_system_audit_logs())
    if entity_type:
        items = [item for item in items if str(item.get("entity_type", "")).lower() == entity_type.lower()]
    if category:
        items = [item for item in items if str(item.get("category", "")).lower() == category.lower()]
    items = sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)
    return items[:limit]


def list_provider_contracts() -> list[dict[str, Any]]:
    items = [_enrich_provider_contract(item) for item in _filter_current_agency(load_provider_contracts())]
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)


def get_provider_contract_by_id(contract_id: str) -> dict[str, Any] | None:
    clean_contract_id = str(contract_id or "").strip()
    if not clean_contract_id:
        return None
    return next((item for item in list_provider_contracts() if str(item.get("contract_id", "")) == clean_contract_id), None)


def submit_provider_document(
    contract_id: str,
    document_name: str,
    issued_date: str,
    expiration_date: str,
    file_name: str,
    file_content: bytes,
    actor_username: str,
    actor_name: str,
    actor_role: str,
) -> dict[str, Any]:
    items = load_provider_contracts()
    match_index = next((index for index, item in enumerate(items) if str(item.get("contract_id", "")) == str(contract_id or "").strip()), None)
    if match_index is None:
        raise ValueError("No encontre ese expediente de provider.")
    record = items[match_index]
    provider_name = str(record.get("provider_name", "")).strip()
    if not provider_name:
        raise ValueError("Ese expediente no tiene provider asociado.")

    upload_name = _provider_document_filename(provider_name, document_name, file_name)
    folder_name = f"provider_contracts/{_safe_folder_name(provider_name)}"
    file_path = _write_upload_file(folder_name, upload_name, file_content)
    onedrive_file_path = _write_onedrive_file([ONEDRIVE_APP_DIR, "Providers", provider_name], upload_name, file_content)

    documents = list(record.get("documents", []))
    updated = False
    for document in documents:
        if str(document.get("document_name", "")) != document_name:
            continue
        document["issued_date"] = _clean_user_date(issued_date) or str(document.get("issued_date", ""))
        document["expiration_date"] = _clean_user_date(expiration_date) or str(document.get("expiration_date", ""))
        document["file_name"] = Path(upload_name).name
        document["file_path"] = file_path
        document["onedrive_file_path"] = onedrive_file_path
        document["requested_status"] = "Pending"
        document["status"] = "Pending Approval"
        document["delivered"] = False
        document["ignored"] = False
        document["is_expired"] = False
        document["expiring_soon"] = False
        document["days_until_expiration"] = None
        document["expiration_state"] = "ok"
        document["approval_status"] = "pending"
        document["submitted_by_username"] = actor_username
        document["submitted_by_name"] = actor_name
        document["submitted_by_role"] = actor_role.upper()
        document["submitted_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        document["approved_by_username"] = ""
        document["approved_by_name"] = ""
        document["approved_at"] = ""
        document["expiry_notice_30_sent_at"] = ""
        document["expiry_notice_expired_sent_at"] = ""
        updated = True
        break

    if not updated:
        raise ValueError("Ese documento no existe en la configuracion actual del provider.")

    record["documents"] = documents
    record["updated_at"] = datetime.now().isoformat()
    items[match_index] = record
    save_provider_contracts(items)
    add_notification(
        {
            "agency_id": record.get("agency_id", ""),
            "agency_name": record.get("agency_name", ""),
            "category": "provider_document_upload",
            "subject": f"Documento subido por provider: {provider_name}",
            "message": f"{provider_name} subio {document_name} y quedo pendiente de aprobacion por Recursos Humanos.",
            "related_id": record.get("contract_id", ""),
        }
    )
    return _enrich_provider_contract(record)


def approve_provider_document(contract_id: str, document_name: str, approver_username: str, approver_name: str) -> dict[str, Any]:
    items = load_provider_contracts()
    match_index = next((index for index, item in enumerate(items) if str(item.get("contract_id", "")) == str(contract_id or "").strip()), None)
    if match_index is None:
        raise ValueError("No encontre ese expediente de provider.")
    record = items[match_index]
    documents = list(record.get("documents", []))
    updated = False
    for document in documents:
        if str(document.get("document_name", "")) != document_name:
            continue
        document["requested_status"] = "Delivered"
        document["status"] = "Delivered"
        document["delivered"] = True
        document["ignored"] = False
        document["is_expired"] = False
        document["expiring_soon"] = False
        document["days_until_expiration"] = None
        document["expiration_state"] = "ok"
        document["approval_status"] = "approved"
        document["approved_by_username"] = approver_username
        document["approved_by_name"] = approver_name
        document["approved_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        document["expiry_notice_expired_sent_at"] = ""
        updated = True
        break
    if not updated:
        raise ValueError("No encontre ese documento para aprobar.")
    record["documents"] = documents
    record["updated_at"] = datetime.now().isoformat()
    items[match_index] = record
    save_provider_contracts(items)
    add_notification(
        {
            "agency_id": record.get("agency_id", ""),
            "agency_name": record.get("agency_name", ""),
            "category": "provider_document_approved",
            "subject": f"Documento aprobado para {record.get('provider_name', '')}",
            "message": f"Recursos Humanos aprobo {document_name} para {record.get('provider_name', '')}.",
            "related_id": record.get("contract_id", ""),
        }
    )
    return _enrich_provider_contract(record)


def _find_user_for_provider_notification(users: list[dict[str, Any]], match_value: str) -> dict[str, Any] | None:
    clean_match = str(match_value or "").strip().lower()
    if not clean_match:
        return None
    for user in users:
        username = str(user.get("username", "")).strip().lower()
        full_name = str(user.get("full_name", "")).strip().lower()
        if clean_match in {username, full_name}:
            return user
    return None


def _provider_document_notification_targets(contract: dict[str, Any], users: list[dict[str, Any]]) -> list[dict[str, str]]:
    provider_name = str(contract.get("provider_name", "")).strip()
    agency_name = str(contract.get("agency_name", "")).strip() or "Agencia"
    recruiter_name = str(contract.get("recruiter_name", "")).strip()
    targets: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_target(label: str, email: str) -> None:
        clean_label = str(label).strip() or "Sin destinatario"
        clean_email = str(email).strip()
        dedupe_key = clean_email.lower() if clean_email else clean_label.lower()
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        targets.append({"label": clean_label, "email": clean_email})

    add_target(f"Agencia: {agency_name}", _notification_recipient(str(contract.get("agency_id", ""))))

    recruiter_user = _find_user_for_provider_notification(users, recruiter_name)
    if recruiter_user is not None:
        add_target(
            f"Usuario asignado: {str(recruiter_user.get('full_name', '')) or str(recruiter_user.get('username', ''))}",
            str(recruiter_user.get("email", "")),
        )
    elif recruiter_name:
        add_target(f"Usuario asignado: {recruiter_name}", "")

    if provider_name:
        for user in users:
            if not user.get("active", True):
                continue
            linked_name = str(user.get("linked_provider_name", "")).strip().lower()
            if linked_name != provider_name.lower():
                continue
            add_target(
                f"Provider: {str(user.get('full_name', '')) or str(user.get('username', ''))}",
                str(user.get("email", "")),
            )
    return targets


def run_provider_document_expiration_checks(agency_id: str = "") -> list[dict[str, Any]]:
    items = load_provider_contracts()
    if not items:
        return []

    current_agency_id = str(agency_id or "").strip() or get_current_agency_id()
    now = datetime.now()
    today = now.date()
    users = load_users()
    updates: list[dict[str, Any]] = []
    changed = False

    for index, item in enumerate(items):
        if current_agency_id and str(item.get("agency_id", "")).strip() not in {"", current_agency_id}:
            continue

        previous_documents = item.get("documents", [])
        previous_map = {
            str(document.get("document_name", "")): document
            for document in previous_documents
            if document.get("document_name")
        }
        documents, completed_count, total_documents, progress_percent = _build_provider_documents(
            str(item.get("provider_name", "")),
            [],
            previous_documents,
            str(item.get("agency_id", "")),
        )
        recipients = _provider_document_notification_targets(item, users)
        contract_updated = False

        for document in documents:
            document_name = str(document.get("document_name", ""))
            previous = previous_map.get(document_name, {})
            renewed = any(
                str(document.get(field, "")) != str(previous.get(field, ""))
                for field in ("issued_date", "expiration_date", "file_name", "file_path", "requested_status")
            )
            if renewed:
                document["expiry_notice_30_sent_at"] = ""
                document["expiry_notice_expired_sent_at"] = ""

            if document.get("expiring_soon") and not str(document.get("expiry_notice_30_sent_at", "")).strip():
                days_until = document.get("days_until_expiration")
                for recipient in recipients:
                    add_notification(
                        {
                            "agency_id": item.get("agency_id", ""),
                            "agency_name": item.get("agency_name", ""),
                            "category": "provider_document_expiring",
                            "subject": f"Documento proximo a vencer: {document_name}",
                            "message": (
                                f"{document_name} de {item.get('provider_name', '')} vence en "
                                f"{days_until} dia(s). Revisa el expediente antes de que expire."
                            ),
                            "related_id": item.get("contract_id", ""),
                            "recipient_label": recipient["label"],
                            "recipient_email": recipient["email"],
                        }
                    )
                document["expiry_notice_30_sent_at"] = now.isoformat()
                contract_updated = True
                updates.append(
                    {
                        "contract_id": item.get("contract_id", ""),
                        "provider_name": item.get("provider_name", ""),
                        "document_name": document_name,
                        "status": "expiring_soon",
                    }
                )

            if document.get("is_expired") and not str(document.get("expiry_notice_expired_sent_at", "")).strip():
                for recipient in recipients:
                    add_notification(
                        {
                            "agency_id": item.get("agency_id", ""),
                            "agency_name": item.get("agency_name", ""),
                            "category": "provider_document_expired",
                            "subject": f"Documento expirado: {document_name}",
                            "message": (
                                f"{item.get('provider_name', '')} tiene el documento {document_name} expirado. "
                                "El sistema lo marcara como Expired hasta que se cargue una version vigente."
                            ),
                            "related_id": item.get("contract_id", ""),
                            "recipient_label": recipient["label"],
                            "recipient_email": recipient["email"],
                        }
                    )
                document["expiry_notice_expired_sent_at"] = now.isoformat()
                contract_updated = True
                updates.append(
                    {
                        "contract_id": item.get("contract_id", ""),
                        "provider_name": item.get("provider_name", ""),
                        "document_name": document_name,
                        "status": "expired",
                    }
                )

        expired_count = len([document for document in documents if document.get("is_expired")])
        expiring_count = len([document for document in documents if document.get("expiring_soon")])
        delivered_count = len([document for document in documents if document.get("status") == "Delivered"])
        ignored_count = len([document for document in documents if document.get("status") == "Ignored"])

        if (
            documents != previous_documents
            or int(item.get("progress_percent", 0) or 0) != progress_percent
            or int(item.get("total_documents", 0) or 0) != total_documents
            or int(item.get("completed_documents", 0) or 0) != completed_count
            or int(item.get("delivered_documents", 0) or 0) != delivered_count
            or int(item.get("ignored_documents", 0) or 0) != ignored_count
            or int(item.get("expired_documents", 0) or 0) != expired_count
            or int(item.get("expiring_documents", 0) or 0) != expiring_count
            or contract_updated
        ):
            item["documents"] = documents
            item["progress_percent"] = progress_percent
            item["completed_documents"] = completed_count
            item["delivered_documents"] = delivered_count
            item["ignored_documents"] = ignored_count
            item["total_documents"] = total_documents
            item["expired_documents"] = expired_count
            item["expired_document_names"] = [str(document.get("document_name", "")) for document in documents if document.get("is_expired")]
            item["expiring_documents"] = expiring_count
            item["expiring_document_names"] = [str(document.get("document_name", "")) for document in documents if document.get("expiring_soon")]
            item["documents_complete"] = completed_count == total_documents and total_documents > 0
            item["updated_at"] = now.isoformat()
            items[index] = item
            changed = True

    if changed:
        save_provider_contracts(items)
    return updates


def add_calendar_event(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_calendar_events()
    payload = _with_current_agency(payload)
    event_id = payload.get("event_id") or f"EVT-{uuid.uuid4().hex[:8].upper()}"
    title = str(payload.get("title", "")).strip()
    assigned_username = str(payload.get("assigned_username", "")).strip().lower()
    assigned_user = get_user_by_username(assigned_username) if assigned_username else None
    notify_email = bool(payload.get("notify_email", True))
    if not title:
        raise ValueError("Escribe el titulo de la tarea antes de guardarla.")
    if assigned_username and assigned_user is None:
        raise ValueError("El usuario asignado no existe o ya no esta disponible.")
    if notify_email and not assigned_username:
        raise ValueError("Selecciona el usuario asignado antes de activar la alerta por email.")
    record = {
        "event_id": event_id,
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "title": title,
        "category": str(payload.get("category", "task")).strip() or "task",
        "event_date": format_user_date(payload.get("event_date", today_user_date())),
        "due_date": format_user_date(payload.get("due_date", payload.get("event_date", today_user_date()))),
        "assigned_username": assigned_username,
        "assigned_name": str(assigned_user.get("full_name", "")) if assigned_user else str(payload.get("assigned_name", "")).strip(),
        "assigned_email": str(assigned_user.get("email", "")) if assigned_user else "",
        "status": str(payload.get("status", "PENDING")).upper(),
        "related_provider": str(payload.get("related_provider", "")).strip(),
        "description": str(payload.get("description", "")).strip(),
        "created_by_username": str(payload.get("created_by_username", "")).strip(),
        "created_by_name": str(payload.get("created_by_name", "")).strip(),
        "notify_email": notify_email,
        "created_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_calendar_events(items)
    if record["assigned_username"]:
        add_notification(
            {
                "agency_id": record.get("agency_id", ""),
                "agency_name": record.get("agency_name", ""),
                "category": "task",
                "subject": f"Nueva tarea para {record.get('assigned_name') or record['assigned_username']}",
                "message": (
                    f"Se asigno la tarea {record['title']} para el {record['event_date']} "
                    f"con fecha limite {record['due_date']}."
                ),
                "related_id": record["event_id"],
                "recipient_email": record.get("assigned_email", "") if record.get("notify_email") else "",
            }
        )
    return record


def list_calendar_events(assigned_username: str = "", include_all: bool = True) -> list[dict[str, Any]]:
    items = _filter_current_agency(load_calendar_events())
    clean_username = assigned_username.strip().lower()
    if clean_username and not include_all:
        items = [item for item in items if str(item.get("assigned_username", "")).strip().lower() == clean_username]
    return sorted(
        items,
        key=lambda item: (
            parse_user_date(str(item.get("event_date", "")).strip() or today_user_date()),
            parse_user_date(str(item.get("due_date", "")).strip() or today_user_date()),
            str(item.get("updated_at", "")),
        ),
        reverse=False,
    )


def update_calendar_event_status(event_id: str, status: str) -> dict[str, Any]:
    items = load_calendar_events()
    clean_status = str(status or "PENDING").upper()
    for index, item in enumerate(items):
        if item.get("event_id") != event_id:
            continue
        item["status"] = clean_status
        item["updated_at"] = datetime.now().isoformat()
        items[index] = item
        save_calendar_events(items)
        return item
    raise ValueError("No encontre ese evento.")


def add_user_note(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_user_notes()
    payload = _with_current_agency(payload)
    record = {
        "note_id": payload.get("note_id") or f"NTE-{uuid.uuid4().hex[:8].upper()}",
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "username": str(payload.get("username", "")).strip().lower(),
        "full_name": str(payload.get("full_name", "")).strip(),
        "title": str(payload.get("title", "")).strip(),
        "body": str(payload.get("body", "")).strip(),
        "created_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_user_notes(items)
    return record


def list_user_notes(username: str) -> list[dict[str, Any]]:
    clean_username = username.strip().lower()
    items = _filter_current_agency(load_user_notes())
    items = [item for item in items if str(item.get("username", "")).strip().lower() == clean_username]
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)


def list_era_archives() -> list[dict[str, Any]]:
    return sorted(
        _filter_current_agency(load_era_archives()),
        key=lambda item: item.get("updated_at", ""),
        reverse=True,
    )


def get_era_archive_by_id(archive_id: str) -> dict[str, Any] | None:
    return next((item for item in load_era_archives() if item.get("archive_id") == archive_id), None)


def get_era_archive_bytes(archive_id: str) -> tuple[bytes, str]:
    archive = get_era_archive_by_id(archive_id)
    if archive is None:
        raise ValueError("No encontre ese archivo ERA.")
    return _read_upload_file(str(archive.get("file_path", ""))), str(archive.get("file_name", "era_835.txt"))


def _split_name_parts(full_name: str) -> tuple[str, str]:
    name_parts = [part for part in full_name.split() if part]
    if not name_parts:
        return ("", "")
    if len(name_parts) == 1:
        return ("", name_parts[0])
    return (" ".join(name_parts[:-1]), name_parts[-1])


def _claim_from_parsed_837(parsed_837: Parsed837) -> Claim:
    patient_first_name, patient_last_name = _split_name_parts(parsed_837.patient_name or "")
    provider_first_name, provider_last_name = _split_name_parts(parsed_837.provider_name or "")
    service_lines = []
    for index, line in enumerate(parsed_837.service_lines, start=1):
        units = int(line.get("units", 0) or 0)
        charge_amount = float(line.get("charge_amount", 0) or 0)
        unit_price = charge_amount / units if units else charge_amount
        service_lines.append(
            ServiceLine(
                procedure_code=_normalize_cpt_code(str(line.get("procedure_code", ""))),
                charge_amount=charge_amount,
                units=units,
                unit_price=unit_price,
                diagnosis_pointer=str(index),
            )
        )

    total_charge_amount = float(parsed_837.total_charge_amount or 0)
    if not total_charge_amount:
        total_charge_amount = sum(float(line.charge_amount) for line in service_lines)

    return Claim(
        claim_id=str(parsed_837.claim_id or f"UPL-{uuid.uuid4().hex[:8].upper()}"),
        provider=Provider(
            npi=str(parsed_837.provider_npi or ""),
            taxonomy_code=str(parsed_837.provider_taxonomy_code or ""),
            first_name=provider_first_name,
            last_name=provider_last_name,
            organization_name=str(parsed_837.provider_name or ""),
        ),
        patient=Patient(
            member_id=str(parsed_837.member_id or ""),
            first_name=patient_first_name,
            last_name=patient_last_name,
            birth_date=str(parsed_837.patient_birth_date or today_user_date()),
            gender=str(parsed_837.patient_gender or "U"),
            address=Address(
                line1=str(parsed_837.patient_address_line1 or ""),
                city=str(parsed_837.patient_address_city or ""),
                state=str(parsed_837.patient_address_state or ""),
                zip_code=str(parsed_837.patient_address_zip_code or ""),
            ),
        ),
        insurance=InsurancePolicy(
            payer_name=str(parsed_837.payer_name or ""),
            payer_id=str(parsed_837.payer_id or ""),
            policy_number=str(parsed_837.member_id or ""),
            plan_name=None,
        ),
        service_date=str(parsed_837.service_date or today_user_date()),
        diagnosis_codes=list(parsed_837.diagnosis_codes or []),
        service_lines=service_lines,
        total_charge_amount=total_charge_amount,
    )


def add_provider_contract(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_provider_contracts()
    payload = _with_current_agency(payload)
    contract_id = payload.get("contract_id") or f"CTR-{uuid.uuid4().hex[:8].upper()}"
    worker_category = str(payload.get("worker_category", "PROVIDER")).strip().upper() or "PROVIDER"
    office_department = str(payload.get("office_department", "")).strip()
    provider_role = str(payload.get("provider_type", "BCBA")).strip()
    match_provider_type = office_department if worker_category == "OFFICE" and office_department else provider_role
    match_index = next(
        (
            index
            for index, item in enumerate(items)
            if item.get("contract_id") == payload.get("contract_id")
            or (
                item.get("provider_name") == payload.get("provider_name")
                and item.get("provider_type") == match_provider_type
                and item.get("agency_id") == payload.get("agency_id")
            )
        ),
        None,
    )
    previous = items[match_index] if match_index is not None else {}
    worker_category = str(payload.get("worker_category", previous.get("worker_category", worker_category))).strip().upper() or "PROVIDER"
    office_department = str(payload.get("office_department", previous.get("office_department", office_department))).strip()
    provider_role = str(payload.get("provider_type", previous.get("provider_type", provider_role))).strip()
    if worker_category == "OFFICE" and not office_department:
        raise ValueError("Selecciona el departamento de oficina para esta contratacion.")
    if worker_category == "PROVIDER" and not provider_role:
        raise ValueError("Selecciona el tipo de provider para esta contratacion.")
    effective_provider_type = office_department if worker_category == "OFFICE" and office_department else provider_role
    contract_stage = str(payload.get("contract_stage", "NEW"))
    credentialing_start_source = (
        payload.get("credentialing_start_date")
        or previous.get("credentialing_start_date", "")
        or payload.get("start_date")
        or previous.get("start_date", "")
    )
    credentialing_start_date = format_user_date(credentialing_start_source) if credentialing_start_source else ""
    credentialing_due_date = ""
    credentialing_days_remaining = 0
    if credentialing_start_date:
        credentialing_due_date, credentialing_days_remaining = _countdown_fields(credentialing_start_date)
    documents, completed_count, total_documents, progress_percent = _build_provider_documents(
        str(payload.get("provider_name", previous.get("provider_name", ""))),
        payload.get("documents", []),
        previous.get("documents", []),
        str(payload.get("agency_id", previous.get("agency_id", ""))),
    )
    delivered_count = len([document for document in documents if document.get("status") == "Delivered"])
    ignored_count = len([document for document in documents if document.get("status") == "Ignored"])
    expired_count = len([document for document in documents if document.get("is_expired")])
    expiring_count = len([document for document in documents if document.get("expiring_soon")])
    record = {
        "contract_id": previous.get("contract_id", contract_id),
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "provider_name": payload["provider_name"],
        "worker_category": worker_category,
        "provider_type": effective_provider_type,
        "office_department": office_department,
        "provider_npi": payload.get("provider_npi", ""),
        "contract_stage": contract_stage,
        "stage_progress_percent": _stage_progress(contract_stage),
        "progress_percent": progress_percent,
        "completed_documents": completed_count,
        "delivered_documents": delivered_count,
        "ignored_documents": ignored_count,
        "total_documents": total_documents,
        "expired_documents": expired_count,
        "expired_document_names": [str(document.get("document_name", "")) for document in documents if document.get("is_expired")],
        "expiring_documents": expiring_count,
        "expiring_document_names": [str(document.get("document_name", "")) for document in documents if document.get("expiring_soon")],
        "documents_complete": completed_count == total_documents and total_documents > 0,
        "documents": documents,
        "start_date": format_user_date(payload["start_date"]) if payload.get("start_date") else "",
        "expected_start_date": format_user_date(payload["expected_start_date"]) if payload.get("expected_start_date") else "",
        "site_location": str(payload.get("site_location", previous.get("site_location", ""))).strip(),
        "county_name": str(payload.get("county_name", previous.get("county_name", ""))).strip(),
        "recruiter_name": payload.get("recruiter_name", ""),
        "supervisor_name": str(payload.get("supervisor_name", previous.get("supervisor_name", ""))).strip(),
        "credentialing_owner_name": str(payload.get("credentialing_owner_name", previous.get("credentialing_owner_name", ""))).strip(),
        "office_reviewer_name": str(payload.get("office_reviewer_name", previous.get("office_reviewer_name", ""))).strip(),
        "assigned_clients": str(payload.get("assigned_clients", previous.get("assigned_clients", ""))).strip(),
        "credentialing_start_date": credentialing_start_date,
        "credentialing_due_date": credentialing_due_date,
        "credentialing_days_remaining": credentialing_days_remaining,
        "notes": payload.get("notes", ""),
        "created_at": previous.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
    }
    if match_index is None:
        items.insert(0, record)
    else:
        items[match_index] = record
    save_provider_contracts(items)
    add_notification(
        {
            "agency_id": record.get("agency_id", ""),
            "agency_name": record.get("agency_name", ""),
            "category": "contracting",
            "subject": f"Contrato actualizado para {record['provider_name']}",
            "message": (
                f"{record['provider_name']} ({record['provider_type']}) quedo en etapa "
                f"{record['contract_stage']} con expediente documental en {record['progress_percent']}% "
                f"({record['completed_documents']}/{record['total_documents']} requisitos listos)."
            ),
            "related_id": record["contract_id"],
        }
    )
    assignment_users = load_users()
    for assignment_label, assignment_name, category_name, subject_text in (
        ("Recruiter", str(record.get("recruiter_name", "")).strip(), "contracting_assignment", "Nueva contratacion asignada"),
        ("Supervisor", str(record.get("supervisor_name", "")).strip(), "contracting_supervision", "Seguimiento de contratacion"),
        ("Credentialing", str(record.get("credentialing_owner_name", "")).strip(), "credentialing_queue", "Inicia proceso de credenciales"),
        ("Office reviewer", str(record.get("office_reviewer_name", "")).strip(), "provider_review", "Revisa notas y expediente"),
    ):
        if not assignment_name:
            continue
        assigned_user = _find_user_for_provider_notification(assignment_users, assignment_name)
        add_notification(
            {
                "agency_id": record.get("agency_id", ""),
                "agency_name": record.get("agency_name", ""),
                "category": category_name,
                "subject": f"{subject_text}: {record.get('provider_name', '')}",
                "message": (
                    f"{record.get('provider_name', '')} quedo asignado a {assignment_label.lower()} "
                    f"{assignment_name}. Meta de credenciales: {record.get('credentialing_due_date', '') or 'sin fecha'}."
                ),
                "related_id": record["contract_id"],
                "recipient_label": f"{assignment_label}: {assignment_name}",
                "recipient_email": str((assigned_user or {}).get("email", "")),
            }
        )
    return _enrich_provider_contract(record)


def add_uploaded_claim_record(parsed_837: Parsed837, edi_payload: str, filename: str) -> dict[str, Any]:
    claim = _claim_from_parsed_837(parsed_837)
    stored = add_claim_record(claim)
    claims = load_claims()
    index = next((idx for idx, item in enumerate(claims) if item.get("claim_id") == stored.get("claim_id")), None)
    if index is None:
        return stored

    record = claims[index]
    upload_name = filename or f"{claim.claim_id}.edi"
    record["source_type"] = "uploaded_837"
    record["source_file_name"] = Path(upload_name).name
    record["source_file_path"] = _write_upload_file("837", Path(upload_name).name, edi_payload.encode("utf-8"))
    record["edi_payload"] = edi_payload
    record["claim_snapshot"] = asdict(claim)
    record["updated_at"] = datetime.now().isoformat()
    claims[index] = record
    save_claims(claims)
    add_notification(
        {
            "agency_id": record.get("agency_id", ""),
            "agency_name": record.get("agency_name", ""),
            "category": "claim_upload",
            "subject": f"Archivo 837 cargado para claim {claim.claim_id}",
            "message": (
                f"Se subio el archivo {record['source_file_name']} para {record['patient_name']} "
                f"y quedo en el batch con estatus {record['transmission_status']}."
            ),
            "related_id": claim.claim_id,
        }
    )
    return record


def add_uploaded_claim_records(parsed_claims: list[Parsed837], filename: str) -> list[dict[str, Any]]:
    builder = Claim837Builder()
    stored_records: list[dict[str, Any]] = []
    for parsed_claim in parsed_claims:
        claim = _claim_from_parsed_837(parsed_claim)
        individual_payload = builder.build_professional_claim(claim)
        stored_records.append(add_uploaded_claim_record(parsed_claim, individual_payload, filename))
    return stored_records


def get_claim_edi_bytes(claim_id: str) -> tuple[bytes, str]:
    claim = get_claim_by_id(claim_id)
    if claim is None:
        raise ValueError("No encontre ese claim.")
    download_name = f"{claim_id}.edi" if claim.get("source_type") == "uploaded_837" else str(claim.get("source_file_name", f"{claim_id}.edi"))
    if claim.get("source_file_path"):
        return _read_upload_file(str(claim.get("source_file_path", ""))), download_name
    edi_payload = str(claim.get("edi_payload", "")).strip()
    if not edi_payload and claim.get("claim_snapshot"):
        edi_payload = Claim837Builder().build_professional_claim(_claim_from_snapshot(claim["claim_snapshot"]))
    if not edi_payload:
        raise ValueError("Ese claim no tiene un archivo 837 archivado.")
    return edi_payload.encode("utf-8"), f"{claim_id}.edi"


def list_claims() -> list[dict[str, Any]]:
    def _sort_key(item: dict[str, Any]) -> tuple[date, str]:
        batch_date = item.get("batch_date", "")
        try:
            parsed_batch_date = parse_user_date(batch_date)
        except ValueError:
            parsed_batch_date = date.min
        return (parsed_batch_date, item.get("updated_at", ""))

    return sorted(_filter_current_agency(load_claims()), key=_sort_key, reverse=True)


def _claim_from_snapshot(snapshot: dict[str, Any]) -> Claim:
    provider_data = snapshot.get("provider", {})
    patient_data = snapshot.get("patient", {})
    address_data = patient_data.get("address", {})
    insurance_data = snapshot.get("insurance", {})
    service_lines_data = snapshot.get("service_lines", [])

    return Claim(
        claim_id=snapshot["claim_id"],
        provider=Provider(**provider_data),
        patient=Patient(
            member_id=patient_data["member_id"],
            first_name=patient_data["first_name"],
            last_name=patient_data["last_name"],
            birth_date=patient_data["birth_date"],
            gender=patient_data["gender"],
            address=Address(**address_data),
        ),
        insurance=InsurancePolicy(**insurance_data),
        service_date=snapshot["service_date"],
        diagnosis_codes=list(snapshot.get("diagnosis_codes", [])),
        service_lines=[ServiceLine(**line) for line in service_lines_data],
        total_charge_amount=float(snapshot["total_charge_amount"]),
    )


def _authorization_status(item: dict[str, Any]) -> str:
    if float(item.get("remaining_units", 0)) <= 0:
        return "agotada"
    return "activa" if item.get("active", True) else "inactiva"


def list_authorizations() -> list[dict[str, Any]]:
    items = _filter_current_agency(load_authorizations())
    for item in items:
        item["status_label"] = _authorization_status(item)
    return items


def get_authorization_group_records(authorization_group_id: str) -> list[dict[str, Any]]:
    clean_group_id = str(authorization_group_id or "").strip()
    if not clean_group_id:
        return []
    current_agency_id = get_current_agency_id()
    rows = [
        dict(item)
        for item in load_authorizations()
        if str(item.get("authorization_group_id", "")).strip() == clean_group_id
        and (
            not current_agency_id
            or str(item.get("agency_id", "")).strip() in {"", current_agency_id}
        )
    ]
    rows = sorted(rows, key=lambda item: int(float(item.get("authorization_line_number", 0) or 0)))
    for row in rows:
        row["status_label"] = _authorization_status(row)
    return rows


def add_authorization(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_authorizations()
    payload = _with_current_agency(payload)
    start_date = format_user_date(payload["start_date"])
    raw_end_date = str(payload.get("end_date", "")).strip()
    end_date = format_user_date(raw_end_date) if raw_end_date else add_user_date_months(start_date, 6)
    line_payloads = payload.get("lines")
    if not isinstance(line_payloads, list):
        line_payloads = [
            {
                "cpt_code": payload.get("cpt_code", ""),
                "total_units": payload.get("total_units", 0),
                "remaining_units": payload.get("remaining_units", payload.get("total_units", 0)),
            }
        ]

    group_id = payload.get("authorization_group_id") or f"AUTHG-{uuid.uuid4().hex[:8].upper()}"
    records: list[dict[str, Any]] = []
    for index, line_payload in enumerate(line_payloads, start=1):
        cpt_code = _normalize_cpt_code(line_payload.get("cpt_code", ""))
        if not cpt_code:
            continue
        total_units = float(line_payload.get("total_units") or 0)
        remaining_units = float(line_payload.get("remaining_units") or total_units)
        record = {
            "authorization_id": f"AUTH-{uuid.uuid4().hex[:8].upper()}",
            "authorization_group_id": group_id,
            "authorization_line_number": index,
            "client_id": str(payload.get("client_id", "")).strip(),
            "agency_id": payload.get("agency_id", ""),
            "agency_name": payload.get("agency_name", ""),
            "patient_member_id": payload["patient_member_id"],
            "patient_name": payload["patient_name"],
            "payer_name": payload["payer_name"],
            "authorization_number": payload["authorization_number"],
            "cpt_code": cpt_code,
            "start_date": start_date,
            "end_date": end_date,
            "total_units": total_units,
            "remaining_units": remaining_units,
            "notes": payload.get("notes", ""),
            "active": bool(payload.get("active", True)),
            "updated_at": datetime.now().isoformat(),
        }
        items.insert(0, record)
        record["status_label"] = _authorization_status(record)
        records.append(record)

    if not records:
        raise ValueError("Agrega por lo menos un CPT con unidades en la autorizacion.")
    save_authorizations(items)
    return {
        "authorization_group_id": group_id,
        "authorization_number": payload["authorization_number"],
        "patient_name": payload["patient_name"],
        "patient_member_id": payload["patient_member_id"],
        "payer_name": payload["payer_name"],
        "start_date": start_date,
        "end_date": end_date,
        "line_count": len(records),
        "lines": records,
    }


def update_authorization_group(payload: dict[str, Any]) -> dict[str, Any]:
    clean_group_id = str(payload.get("authorization_group_id", "")).strip()
    if not clean_group_id:
        raise ValueError("No encontre la autorizacion que quieres editar.")
    items = load_authorizations()
    current_agency_id = get_current_agency_id()
    match_indexes = [
        index
        for index, item in enumerate(items)
        if str(item.get("authorization_group_id", "")).strip() == clean_group_id
        and (
            not current_agency_id
            or str(item.get("agency_id", "")).strip() in {"", current_agency_id}
        )
    ]
    if not match_indexes:
        raise ValueError("No encontre esa autorizacion para editar.")

    previous_records = [dict(items[index]) for index in match_indexes]
    previous_records = sorted(previous_records, key=lambda item: int(float(item.get("authorization_line_number", 0) or 0)))
    payload = _with_current_agency(payload)
    start_date = format_user_date(payload["start_date"])
    raw_end_date = str(payload.get("end_date", "")).strip()
    end_date = format_user_date(raw_end_date) if raw_end_date else add_user_date_months(start_date, 6)
    line_payloads = payload.get("lines")
    if not isinstance(line_payloads, list):
        line_payloads = []
    records: list[dict[str, Any]] = []
    previous_by_line = {
        int(float(item.get("authorization_line_number", 0) or 0)): item
        for item in previous_records
    }
    for index, line_payload in enumerate(line_payloads, start=1):
        cpt_code = _normalize_cpt_code(line_payload.get("cpt_code", ""))
        if not cpt_code:
            continue
        total_units = float(line_payload.get("total_units") or 0)
        remaining_units = float(line_payload.get("remaining_units") or total_units)
        previous = previous_by_line.get(index, {})
        record = {
            "authorization_id": str(previous.get("authorization_id", "")).strip() or f"AUTH-{uuid.uuid4().hex[:8].upper()}",
            "authorization_group_id": clean_group_id,
            "authorization_line_number": index,
            "client_id": str(payload.get("client_id", "")).strip(),
            "agency_id": payload.get("agency_id", ""),
            "agency_name": payload.get("agency_name", ""),
            "patient_member_id": payload["patient_member_id"],
            "patient_name": payload["patient_name"],
            "payer_name": payload["payer_name"],
            "authorization_number": payload["authorization_number"],
            "cpt_code": cpt_code,
            "start_date": start_date,
            "end_date": end_date,
            "total_units": total_units,
            "remaining_units": remaining_units,
            "notes": payload.get("notes", ""),
            "active": bool(payload.get("active", True)),
            "updated_at": datetime.now().isoformat(),
        }
        record["status_label"] = _authorization_status(record)
        records.append(record)
    if not records:
        raise ValueError("Agrega por lo menos un CPT con unidades en la autorizacion.")

    remaining_items = [item for index, item in enumerate(items) if index not in match_indexes]
    items = records + remaining_items
    save_authorizations(items)
    return {
        "authorization_group_id": clean_group_id,
        "authorization_number": payload["authorization_number"],
        "patient_name": payload["patient_name"],
        "patient_member_id": payload["patient_member_id"],
        "payer_name": payload["payer_name"],
        "start_date": start_date,
        "end_date": end_date,
        "line_count": len(records),
        "lines": records,
    }


def delete_authorization_group(authorization_group_id: str) -> dict[str, Any]:
    clean_group_id = str(authorization_group_id or "").strip()
    if not clean_group_id:
        raise ValueError("Selecciona una autorizacion para borrar.")
    items = load_authorizations()
    current_agency_id = get_current_agency_id()
    matched = [
        dict(item)
        for item in items
        if str(item.get("authorization_group_id", "")).strip() == clean_group_id
        and (
            not current_agency_id
            or str(item.get("agency_id", "")).strip() in {"", current_agency_id}
        )
    ]
    if not matched:
        raise ValueError("No encontre esa autorizacion para borrar.")
    remaining_items = [
        item
        for item in items
        if not (
            str(item.get("authorization_group_id", "")).strip() == clean_group_id
            and (
                not current_agency_id
                or str(item.get("agency_id", "")).strip() in {"", current_agency_id}
            )
        )
    ]
    save_authorizations(remaining_items)
    first = matched[0]
    return {
        "authorization_group_id": clean_group_id,
        "authorization_number": str(first.get("authorization_number", "")),
        "patient_name": str(first.get("patient_name", "")),
        "client_id": str(first.get("client_id", "")),
        "line_count": len(matched),
    }


def consume_authorization_units(claim: Claim) -> list[dict[str, Any]]:
    authorizations = load_authorizations()
    service_date = parse_user_date(claim.service_date)
    updates: list[dict[str, Any]] = []

    for line in claim.service_lines:
        for item in authorizations:
            if not item.get("active", True):
                continue
            if item.get("patient_member_id") != claim.patient.member_id:
                continue
            if _normalize_cpt_code(item.get("cpt_code")) != _normalize_cpt_code(line.procedure_code):
                continue

            start_date = parse_user_date(item["start_date"])
            end_date = parse_user_date(item["end_date"])
            if service_date < start_date or service_date > end_date:
                continue

            previous_units = float(item.get("remaining_units", 0))
            used_units = min(previous_units, float(line.units))
            item["remaining_units"] = max(previous_units - float(line.units), 0.0)
            item["updated_at"] = datetime.now().isoformat()
            updates.append(
                {
                    "authorization_number": item["authorization_number"],
                    "cpt_code": item["cpt_code"],
                    "used_units": used_units,
                    "remaining_units": item["remaining_units"],
                }
            )
            break

    if updates:
        save_authorizations(authorizations)
    return updates


def add_claim_record(
    claim: Claim,
    tracking_id: str = "",
    transmission_status: str = "queued",
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claims = load_claims()
    current_agency = get_current_agency()
    edi_payload = Claim837Builder().build_professional_claim(claim)
    record = {
        "claim_id": claim.claim_id,
        "agency_id": str(current_agency.get("agency_id", "")) if current_agency else "",
        "agency_name": str(current_agency.get("agency_name", "")) if current_agency else "",
        "payer_claim_number": "",
        "patient_name": f"{claim.patient.first_name} {claim.patient.last_name}".strip(),
        "member_id": claim.patient.member_id,
        "payer_name": claim.insurance.payer_name,
        "service_date": format_user_date(claim.service_date),
        "total_charge_amount": float(claim.total_charge_amount),
        "paid_amount": 0.0,
        "balance_amount": float(claim.total_charge_amount),
        "status": "pending" if transmission_status == "transmitted" else "draft",
        "tracking_id": tracking_id,
        "transmission_status": transmission_status,
        "batch_date": today_user_date(),
        "transmitted_at": datetime.now().strftime("%m/%d/%Y %H:%M") if transmission_status == "transmitted" else "",
        "source_type": "manual",
        "source_file_name": "",
        "source_file_path": "",
        "edi_payload": edi_payload,
        "claim_snapshot": asdict(claim),
        "service_lines": [
            {
                "procedure_code": line.procedure_code,
                "unit_price": float(line.unit_price),
                "charge_amount": float(line.charge_amount),
                "units": int(line.units),
                "minutes": int(line.units) * 15,
                "diagnosis_pointer": line.diagnosis_pointer,
            }
            for line in claim.service_lines
        ],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    if isinstance(extra_metadata, dict):
        for key, value in extra_metadata.items():
            clean_key = str(key or "").strip()
            if not clean_key:
                continue
            record[clean_key] = value
    existing_index = next((index for index, item in enumerate(claims) if item.get("claim_id") == claim.claim_id), None)
    if existing_index is None:
        claims.insert(0, record)
    else:
        previous = claims[existing_index]
        record["payer_claim_number"] = previous.get("payer_claim_number", "")
        record["tracking_id"] = tracking_id if transmission_status == "transmitted" else ""
        record["transmission_status"] = transmission_status
        record["batch_date"] = previous.get("batch_date", record["batch_date"])
        record["transmitted_at"] = record["transmitted_at"] if transmission_status == "transmitted" else ""
        record["source_type"] = previous.get("source_type", record["source_type"])
        record["source_file_name"] = previous.get("source_file_name", "")
        record["source_file_path"] = previous.get("source_file_path", "")
        record["created_at"] = previous.get("created_at", record["created_at"])
        claims[existing_index] = record
    save_claims(claims)
    return record


def transmit_claim_record(claim_id: str, connector: Any) -> dict[str, Any]:
    claims = load_claims()
    index = next((idx for idx, item in enumerate(claims) if item.get("claim_id") == claim_id), None)
    if index is None:
        raise ValueError("No encontre ese claim para transmitir.")

    record = claims[index]
    if record.get("transmission_status") == "transmitted":
        return {
            "claim_id": claim_id,
            "tracking_id": record.get("tracking_id", ""),
            "transmission_status": "transmitted",
            "message": "Ese claim ya fue transmitido.",
        }

    snapshot = record.get("claim_snapshot", {})
    claim = _claim_from_snapshot(snapshot) if snapshot else _claim_from_parsed_837(
        Parsed837(
            transaction_set_control_number="",
            payer_name=record.get("payer_name"),
            patient_name=record.get("patient_name"),
            member_id=record.get("member_id"),
            provider_name="",
            provider_npi="",
            claim_id=record.get("claim_id"),
            service_date=record.get("service_date"),
            total_charge_amount=float(record.get("total_charge_amount", 0) or 0),
            service_lines=list(record.get("service_lines", [])),
        )
    )
    edi_payload = str(record.get("edi_payload", "")).strip() or Claim837Builder().build_professional_claim(claim)
    result = connector.submit_claim(claim, edi_payload)
    record["tracking_id"] = str(result.get("tracking_id", ""))
    record["transmission_status"] = "transmitted"
    if record.get("status") == "draft":
        record["status"] = "pending"
    record["transmitted_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
    record["edi_payload"] = edi_payload
    record["updated_at"] = datetime.now().isoformat()
    claims[index] = record
    save_claims(claims)
    add_notification(
        {
            "agency_id": record.get("agency_id", ""),
            "agency_name": record.get("agency_name", ""),
            "category": "claim_transmit",
            "subject": f"Claim transmitido {claim_id}",
            "message": (
                f"El claim {claim_id} de {record.get('patient_name', '')} fue transmitido "
                f"al payer {record.get('payer_name', '')} con tracking {record['tracking_id']}."
            ),
            "related_id": claim_id,
        }
    )
    return {
        "claim_id": claim_id,
        "tracking_id": record["tracking_id"],
        "transmission_status": record["transmission_status"],
        "edi_preview": str(result.get("edi_preview", "")),
    }


def transmit_daily_batch(
    connector: Any,
    batch_date: str | None = None,
) -> list[dict[str, Any]]:
    target_batch_date = format_user_date(batch_date) if batch_date else today_user_date()
    claims = load_claims()
    updates: list[dict[str, Any]] = []

    for record in claims:
        if record.get("batch_date") != target_batch_date:
            continue
        if record.get("transmission_status") == "transmitted":
            continue
        snapshot = record.get("claim_snapshot", {})
        claim = _claim_from_snapshot(snapshot) if snapshot else _claim_from_parsed_837(
            Parsed837(
                transaction_set_control_number="",
                payer_name=record.get("payer_name"),
                patient_name=record.get("patient_name"),
                member_id=record.get("member_id"),
                provider_name="",
                provider_npi="",
                claim_id=record.get("claim_id"),
                service_date=record.get("service_date"),
                total_charge_amount=float(record.get("total_charge_amount", 0) or 0),
                service_lines=list(record.get("service_lines", [])),
            )
        )
        edi_payload = str(record.get("edi_payload", "")).strip() or Claim837Builder().build_professional_claim(claim)
        result = connector.submit_claim(claim, edi_payload)
        record["tracking_id"] = str(result.get("tracking_id", ""))
        record["transmission_status"] = "transmitted"
        if record.get("status") == "draft":
            record["status"] = "pending"
        record["transmitted_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        record["edi_payload"] = edi_payload
        record["updated_at"] = datetime.now().isoformat()
        updates.append(
            {
                "claim_id": record.get("claim_id", ""),
                "tracking_id": record.get("tracking_id", ""),
                "batch_date": target_batch_date,
                "transmission_status": record.get("transmission_status", ""),
            }
        )
        add_notification(
            {
                "agency_id": record.get("agency_id", ""),
                "agency_name": record.get("agency_name", ""),
                "category": "claim_transmit",
                "subject": f"Claim transmitido {record.get('claim_id', '')}",
                "message": (
                    f"El claim {record.get('claim_id', '')} de {record.get('patient_name', '')} "
                    f"fue transmitido dentro del batch del {target_batch_date}."
                ),
                "related_id": record.get("claim_id", ""),
            }
        )

    if updates:
        save_claims(claims)
    return updates


def _status_from_era(claim_status_code: str, charge_amount: float, paid_amount: float) -> str:
    denied_codes = {"4", "22", "23"}
    if claim_status_code in denied_codes or paid_amount <= 0:
        return "denied"
    if paid_amount >= charge_amount:
        return "paid"
    if 0 < paid_amount < charge_amount:
        return "partial"
    return "pending"


def apply_era_to_claims(parsed_835: Parsed835) -> list[dict[str, Any]]:
    claims = load_claims()
    updates: list[dict[str, Any]] = []
    claim_map = {claim["claim_id"]: claim for claim in claims}

    for detail in parsed_835.claim_details:
        claim_id = detail["claim_id"]
        if claim_id not in claim_map:
            continue
        claim = claim_map[claim_id]
        charge_amount = float(detail["charge_amount"])
        paid_amount = float(detail["paid_amount"])
        status = _status_from_era(detail["claim_status_code"], charge_amount, paid_amount)
        claim["paid_amount"] = paid_amount
        claim["balance_amount"] = max(charge_amount - paid_amount, 0.0)
        claim["payer_claim_number"] = detail.get("payer_claim_number", "") or claim.get("payer_claim_number", "")
        claim["status"] = status
        claim["updated_at"] = datetime.now().isoformat()
        claim["last_era_control_number"] = parsed_835.transaction_set_control_number
        updates.append(
            {
                "claim_id": claim_id,
                "payer_claim_number": claim.get("payer_claim_number", ""),
                "status": status,
                "paid_amount": paid_amount,
                "balance_amount": claim["balance_amount"],
            }
        )
        add_notification(
            {
                "agency_id": claim.get("agency_id", ""),
                "agency_name": claim.get("agency_name", ""),
                "category": "claim_result",
                "subject": f"Resultado del claim {claim_id}",
                "message": (
                    f"El payer devolvio el claim {claim_id} con estatus {status.upper()}, "
                    f"paid amount {paid_amount:.2f} y balance {claim['balance_amount']:.2f}."
                ),
                "related_id": claim_id,
            }
        )

    if updates:
        save_claims(claims)
    return updates


def add_era_archive(
    parsed_835: Parsed835,
    raw_content: str,
    filename: str,
    claim_updates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = load_era_archives()
    payload = _with_current_agency({})
    archive_id = f"ERA-{uuid.uuid4().hex[:8].upper()}"
    file_name = Path(filename or f"era_{parsed_835.transaction_set_control_number or archive_id}.txt").name
    file_path = _write_upload_file("835", file_name, raw_content.encode("utf-8"))
    record = {
        "archive_id": archive_id,
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "file_name": file_name,
        "file_path": file_path,
        "transaction_set_control_number": parsed_835.transaction_set_control_number,
        "payer_name": parsed_835.payer_name or "",
        "payee_name": parsed_835.payee_name or "",
        "payment_amount": float(parsed_835.payment_amount or 0),
        "claim_count": len(parsed_835.claim_details),
        "claim_updates_count": len(claim_updates or []),
        "claim_ids": [detail.get("claim_id", "") for detail in parsed_835.claim_details],
        "imported_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_era_archives(items)
    add_notification(
        {
            "agency_id": record.get("agency_id", ""),
            "agency_name": record.get("agency_name", ""),
            "category": "era_import",
            "subject": f"ERA 835 importado {record['transaction_set_control_number']}",
            "message": (
                f"Se importo el archivo {record['file_name']} del payer {record['payer_name']} "
                f"con {record['claim_updates_count']} claims actualizados."
            ),
            "related_id": archive_id,
        }
    )
    return record


def _next_run_date(from_day: date) -> date:
    run_days = get_eligibility_run_days()
    for month_offset in range(0, 24):
        month_index = (from_day.month - 1) + month_offset
        year = from_day.year + (month_index // 12)
        month = (month_index % 12) + 1
        month_last_day = calendar.monthrange(year, month)[1]
        for configured_day in run_days:
            candidate = date(year, month, min(configured_day, month_last_day))
            if candidate > from_day:
                return candidate
    return from_day + timedelta(days=1)


def _is_eligibility_run_day(check_day: date) -> bool:
    month_last_day = calendar.monthrange(check_day.year, check_day.month)[1]
    return any(check_day.day == min(configured_day, month_last_day) for configured_day in get_eligibility_run_days())


def _find_matching_roster_entry(roster: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any] | None:
    clean_member_id = str(payload.get("member_id", "")).strip().lower()
    clean_payer_id = str(payload.get("payer_id", "")).strip().lower()
    clean_provider_npi = str(payload.get("provider_npi", "")).strip().lower()
    clean_first_name = str(payload.get("patient_first_name", "") or payload.get("first_name", "")).strip().lower()
    clean_last_name = str(payload.get("patient_last_name", "") or payload.get("last_name", "")).strip().lower()
    clean_birth_date = _clean_user_date(payload.get("patient_birth_date") or payload.get("birth_date"))
    clean_agency_id = str(payload.get("agency_id", "")).strip()

    for item in roster:
        if clean_agency_id and str(item.get("agency_id", "")).strip() != clean_agency_id:
            continue
        if clean_member_id and str(item.get("member_id", "")).strip().lower() != clean_member_id:
            continue
        item_payer_id = str(item.get("payer_id", "")).strip().lower()
        item_provider_npi = str(item.get("provider_npi", "")).strip().lower()
        if clean_payer_id or clean_provider_npi:
            if clean_payer_id and item_payer_id != clean_payer_id:
                continue
            if clean_provider_npi and item_provider_npi != clean_provider_npi:
                continue
            return item
        if clean_first_name and str(item.get("patient_first_name", "")).strip().lower() != clean_first_name:
            continue
        if clean_last_name and str(item.get("patient_last_name", "")).strip().lower() != clean_last_name:
            continue
        if clean_birth_date and _clean_user_date(item.get("patient_birth_date", "")) != clean_birth_date:
            continue
        return item
    return None


def _sync_client_to_roster(client: dict[str, Any]) -> None:
    roster = load_eligibility_roster()
    today = datetime.now().date()
    default_next_run = format_user_date(today if _is_eligibility_run_day(today) else _next_run_date(today))
    existing = _find_matching_roster_entry(roster, client)

    if existing is None:
        existing = {
            "roster_id": f"ELG-{uuid.uuid4().hex[:8].upper()}",
            "last_checked_at": "",
            "last_result": "",
            "messages": [],
            "next_run_date": default_next_run,
        }
        roster.insert(0, existing)

    existing.update(
        {
            "active": bool(client.get("active", True) and client.get("auto_eligibility", True)),
            "payer_id": client.get("payer_id", ""),
            "provider_npi": client.get("provider_npi", ""),
            "member_id": client.get("member_id", ""),
            "patient_first_name": client.get("first_name", ""),
            "patient_last_name": client.get("last_name", ""),
            "patient_birth_date": client.get("birth_date", ""),
            "service_date": client.get("service_date", today_user_date()),
            "last_checked_at": client.get("last_eligibility_checked_at", existing.get("last_checked_at", "")),
            "last_result": client.get("last_eligibility_result", existing.get("last_result", "")),
            "messages": client.get("last_messages", existing.get("messages", [])),
            "next_run_date": existing.get("next_run_date") or default_next_run,
            "updated_at": datetime.now().isoformat(),
        }
    )
    save_eligibility_roster(roster)


def _split_assignment_names(raw_value: Any) -> set[str]:
    normalized = str(raw_value or "").replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    return {
        " ".join(piece.split())
        for piece in normalized.split("\n")
        if " ".join(piece.split())
    }


def _serialize_assignment_names(names: set[str]) -> str:
    return ", ".join(sorted(names, key=str.casefold))


def _client_linked_contract_ids(client: dict[str, Any], provider_contracts: list[dict[str, Any]]) -> set[str]:
    linked_ids = {
        str(client.get("bcba_contract_id", "")).strip(),
        str(client.get("bcaba_contract_id", "")).strip(),
        str(client.get("rbt_contract_id", "")).strip(),
    }
    clean_provider_npi = str(client.get("provider_npi", "")).strip()
    if clean_provider_npi:
        matched_contract_ids = [
            str(item.get("contract_id", "")).strip()
            for item in provider_contracts
            if str(item.get("provider_npi", "")).strip() == clean_provider_npi
            and str(item.get("contract_id", "")).strip()
        ]
        if len(matched_contract_ids) == 1:
            linked_ids.add(matched_contract_ids[0])
    return {contract_id for contract_id in linked_ids if contract_id}


def _sync_client_provider_assignments(current_client: dict[str, Any], previous_client: dict[str, Any] | None = None) -> None:
    current_name = f"{current_client.get('first_name', '')} {current_client.get('last_name', '')}".strip()
    previous_payload = previous_client or {}
    previous_name = f"{previous_payload.get('first_name', '')} {previous_payload.get('last_name', '')}".strip()
    agency_id = str(current_client.get("agency_id", "") or previous_payload.get("agency_id", "")).strip()
    items = load_provider_contracts()
    relevant_contracts = [
        item
        for item in items
        if not agency_id or str(item.get("agency_id", "")).strip() == agency_id
    ]
    previous_ids = _client_linked_contract_ids(previous_payload, relevant_contracts)
    current_ids = _client_linked_contract_ids(current_client, relevant_contracts)
    touched_ids = previous_ids | current_ids
    if not touched_ids:
        return

    touched = False
    for item in items:
        contract_id = str(item.get("contract_id", "")).strip()
        if contract_id not in touched_ids:
            continue
        names = _split_assignment_names(item.get("assigned_clients", ""))
        if previous_name:
            names.discard(previous_name)
        if contract_id in current_ids and current_name:
            names.add(current_name)
        item["assigned_clients"] = _serialize_assignment_names(names)
        item["updated_at"] = datetime.now().isoformat()
        touched = True

    if touched:
        save_provider_contracts(items)


def add_client(payload: dict[str, Any]) -> dict[str, Any]:
    clients = load_clients()
    payload = _with_current_agency(payload)
    client_id = payload.get("client_id") or f"CLT-{uuid.uuid4().hex[:8].upper()}"
    match_index = next(
        (
            index
            for index, item in enumerate(clients)
            if item.get("client_id") == payload.get("client_id")
            or (
                item.get("member_id") == payload.get("member_id")
                and item.get("payer_id") == payload.get("payer_id")
            )
        ),
        None,
    )
    previous = clients[match_index] if match_index is not None else {}
    client_name = f"{payload.get('first_name', previous.get('first_name', ''))} {payload.get('last_name', previous.get('last_name', ''))}".strip()
    documents, delivered_count, total_documents, progress_percent = _build_client_documents(
        client_name,
        payload.get("documents", []),
        previous.get("documents", []),
    )
    record = {
        "client_id": previous.get("client_id", client_id),
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "first_name": payload["first_name"],
        "last_name": payload["last_name"],
        "preferred_language": str(payload.get("preferred_language", previous.get("preferred_language", ""))).strip(),
        "diagnosis": str(payload.get("diagnosis", previous.get("diagnosis", ""))).strip(),
        "member_id": payload["member_id"],
        "birth_date": format_user_date(payload["birth_date"]),
        "service_date": format_user_date(payload["service_date"]),
        "payer_name": payload["payer_name"],
        "payer_id": payload["payer_id"],
        "insurance_effective_date": format_user_date(payload.get("insurance_effective_date", previous.get("insurance_effective_date", ""))),
        "subscriber_name": str(payload.get("subscriber_name", previous.get("subscriber_name", ""))).strip(),
        "subscriber_id": str(payload.get("subscriber_id", previous.get("subscriber_id", ""))).strip(),
        "provider_npi": payload["provider_npi"],
        "site_location": str(payload.get("site_location", previous.get("site_location", ""))).strip(),
        "county_name": str(payload.get("county_name", previous.get("county_name", ""))).strip(),
        "gender": payload.get("gender", ""),
        "medicaid_id": payload.get("medicaid_id", ""),
        "address_line1": str(payload.get("address_line1", previous.get("address_line1", ""))).strip(),
        "address_city": str(payload.get("address_city", previous.get("address_city", ""))).strip(),
        "address_state": str(payload.get("address_state", previous.get("address_state", ""))).strip(),
        "address_zip_code": str(payload.get("address_zip_code", previous.get("address_zip_code", ""))).strip(),
        "caregiver_name": str(payload.get("caregiver_name", previous.get("caregiver_name", ""))).strip(),
        "caregiver_relationship": str(payload.get("caregiver_relationship", previous.get("caregiver_relationship", ""))).strip(),
        "caregiver_phone": str(payload.get("caregiver_phone", previous.get("caregiver_phone", ""))).strip(),
        "caregiver_email": str(payload.get("caregiver_email", previous.get("caregiver_email", ""))).strip(),
        "physician_name": str(payload.get("physician_name", previous.get("physician_name", ""))).strip(),
        "physician_npi": str(payload.get("physician_npi", previous.get("physician_npi", ""))).strip(),
        "physician_phone": str(payload.get("physician_phone", previous.get("physician_phone", ""))).strip(),
        "physician_address": str(payload.get("physician_address", previous.get("physician_address", ""))).strip(),
        "bcba_contract_id": str(payload.get("bcba_contract_id", previous.get("bcba_contract_id", ""))).strip(),
        "bcaba_contract_id": str(payload.get("bcaba_contract_id", previous.get("bcaba_contract_id", ""))).strip(),
        "rbt_contract_id": str(payload.get("rbt_contract_id", previous.get("rbt_contract_id", ""))).strip(),
        "notes": payload.get("notes", ""),
        "active": bool(payload.get("active", True)),
        "auto_eligibility": bool(payload.get("auto_eligibility", True)),
        "documents": documents,
        "delivered_documents": delivered_count,
        "total_documents": total_documents,
        "progress_percent": progress_percent,
        "last_eligibility_result": previous.get("last_eligibility_result", ""),
        "last_eligibility_checked_at": previous.get("last_eligibility_checked_at", ""),
        "last_plan_name": previous.get("last_plan_name", ""),
        "last_subscriber_id": previous.get("last_subscriber_id", ""),
        "last_messages": previous.get("last_messages", []),
        "created_at": previous.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
    }

    if match_index is None:
        clients.insert(0, record)
    else:
        clients[match_index] = record

    save_clients(clients)
    _sync_client_provider_assignments(record, previous if previous else None)
    _sync_client_to_roster(record)
    return _enrich_client(record)


def run_client_eligibility_checks(
    connector: Any,
    client_ids: list[str] | None = None,
    actor_username: str = "system",
    actor_name: str = "Blue Hope",
) -> list[dict[str, Any]]:
    clients = load_clients()
    selected = set(client_ids or [])
    updates: list[dict[str, Any]] = []

    for client in clients:
        if selected and client.get("client_id") not in selected:
            continue
        if not client.get("active", True):
            continue
        if not selected and not client.get("auto_eligibility", True):
            continue

        request = EligibilityRequest(
            payer_id=client["payer_id"],
            provider_npi=client["provider_npi"],
            member_id=client["member_id"],
            patient_first_name=client["first_name"],
            patient_last_name=client["last_name"],
            patient_birth_date=client["birth_date"],
            service_date=client["service_date"],
        )
        response: EligibilityResponse = connector.check_eligibility(request)
        client["last_eligibility_result"] = response.coverage_status
        client["last_eligibility_checked_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        client["last_plan_name"] = response.plan_name or ""
        client["last_subscriber_id"] = response.subscriber_id
        client["last_messages"] = response.messages
        client["updated_at"] = datetime.now().isoformat()
        updates.append(
            {
                "client_id": client["client_id"],
                "patient_name": f"{client['first_name']} {client['last_name']}".strip(),
                "member_id": client["member_id"],
                "payer_name": client["payer_name"],
                "coverage_status": response.coverage_status,
                "plan_name": response.plan_name,
                "subscriber_id": response.subscriber_id,
            }
        )
        add_eligibility_history_entry(
            {
                "agency_id": client.get("agency_id", ""),
                "agency_name": client.get("agency_name", ""),
                "insured_name": f"{client['last_name']}, {client['first_name']}".strip(", "),
                "payer_name": client.get("payer_name", ""),
                "policy_number": client.get("member_id", ""),
                "benefit": "30",
                "procedure": "",
                "status": "complete",
                "service_date": client.get("service_date", ""),
                "actor_username": actor_username,
                "actor_name": actor_name,
            }
        )
        add_notification(
            {
                "agency_id": client.get("agency_id", ""),
                "agency_name": client.get("agency_name", ""),
                "category": "eligibility",
                "subject": f"Elegibilidad {response.coverage_status} para {client['first_name']} {client['last_name']}",
                "message": (
                    f"{client['first_name']} {client['last_name']} quedo con elegibilidad "
                    f"{response.coverage_status} en {client['payer_name']}."
                ),
                "related_id": client["client_id"],
            }
        )
        add_system_audit_log(
            {
                "agency_id": client.get("agency_id", ""),
                "agency_name": client.get("agency_name", ""),
                "category": "client",
                "entity_type": "client",
                "entity_id": client["client_id"],
                "entity_name": f"{client['first_name']} {client['last_name']}".strip(),
                "action": "CLIENT_ELIGIBILITY_CHECK",
                "actor_username": actor_username,
                "actor_name": actor_name,
                "details": (
                    f"Elegibilidad {response.coverage_status} con payer {client['payer_name']} "
                    f"y member ID {client['member_id']}."
                ),
            }
        )
        _sync_client_to_roster(client)

    if updates:
        save_clients(clients)
    return updates


def add_payer_enrollment(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_payer_enrollments()
    payload = _with_current_agency(payload)
    enrollment_id = payload.get("enrollment_id") or f"ENR-{uuid.uuid4().hex[:8].upper()}"
    match_index = next(
        (
            index
            for index, item in enumerate(items)
            if item.get("enrollment_id") == payload.get("enrollment_id")
            or (
                item.get("payer_name") == payload.get("payer_name")
                and item.get("npi") == payload.get("npi")
            )
        ),
        None,
    )
    previous = items[match_index] if match_index is not None else {}
    expected_completion_date = ""
    days_remaining = 0
    if payload.get("credentials_submitted_date"):
        expected_completion_date, days_remaining = _countdown_fields(payload["credentials_submitted_date"])
    record = {
        "enrollment_id": previous.get("enrollment_id", enrollment_id),
        "contract_id": str(payload.get("contract_id", previous.get("contract_id", ""))).strip(),
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "provider_name": payload["provider_name"],
        "ssn": payload.get("ssn", ""),
        "npi": payload.get("npi", ""),
        "medicaid_id": payload.get("medicaid_id", ""),
        "payer_name": payload["payer_name"],
        "site_location": str(payload.get("site_location", previous.get("site_location", ""))).strip(),
        "county_name": str(payload.get("county_name", previous.get("county_name", ""))).strip(),
        "credentialing_owner_name": str(payload.get("credentialing_owner_name", previous.get("credentialing_owner_name", ""))).strip(),
        "supervisor_name": str(payload.get("supervisor_name", previous.get("supervisor_name", ""))).strip(),
        "enrollment_status": payload.get("enrollment_status", "SUBMITTED"),
        "credentials_submitted_date": format_user_date(payload["credentials_submitted_date"]),
        "effective_date": format_user_date(payload["effective_date"]) if payload.get("effective_date") else "",
        "expected_completion_date": expected_completion_date,
        "days_remaining": days_remaining,
        "notes": payload.get("notes", ""),
        "created_at": previous.get("created_at", datetime.now().isoformat()),
        "updated_at": datetime.now().isoformat(),
    }

    if match_index is None:
        items.insert(0, record)
    else:
        items[match_index] = record

    save_payer_enrollments(items)
    add_notification(
        {
            "agency_id": record.get("agency_id", ""),
            "agency_name": record.get("agency_name", ""),
            "category": "enrollment",
            "subject": f"Enrollment guardado para {record['provider_name']}",
            "message": (
                f"{record['provider_name']} con payer {record['payer_name']} "
                f"quedo en {record['enrollment_status']} y tiene {record['days_remaining']} dias estimados restantes."
            ),
            "related_id": record["enrollment_id"],
        }
    )
    assignment_users = load_users()
    for assignment_label, assignment_name, category_name, subject_text in (
        ("Credentialing", str(record.get("credentialing_owner_name", "")).strip(), "credentialing_queue", "Nuevo enrollment asignado"),
        ("Supervisor", str(record.get("supervisor_name", "")).strip(), "credentialing_supervision", "Seguimiento de enrollment"),
    ):
        if not assignment_name:
            continue
        assigned_user = _find_user_for_provider_notification(assignment_users, assignment_name)
        add_notification(
            {
                "agency_id": record.get("agency_id", ""),
                "agency_name": record.get("agency_name", ""),
                "category": category_name,
                "subject": f"{subject_text}: {record.get('provider_name', '')}",
                "message": (
                    f"{record.get('provider_name', '')} con payer {record.get('payer_name', '')} "
                    f"quedo asignado a {assignment_label.lower()} {assignment_name}. "
                    f"Fecha estimada: {record.get('expected_completion_date', '') or 'sin fecha'}."
                ),
                "related_id": record["enrollment_id"],
                "recipient_label": f"{assignment_label}: {assignment_name}",
                "recipient_email": str((assigned_user or {}).get("email", "")),
            }
        )
    return record


def add_roster_entry(payload: dict[str, Any]) -> dict[str, Any]:
    roster = load_eligibility_roster()
    payload = _with_current_agency(payload)
    today = datetime.now().date()
    initial_run_date = today if _is_eligibility_run_day(today) else _next_run_date(today)
    existing = _find_matching_roster_entry(roster, payload)
    record = {
        "roster_id": (existing or {}).get("roster_id", payload.get("roster_id") or f"ELG-{uuid.uuid4().hex[:8].upper()}"),
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "active": bool(payload.get("active", True)),
        "payer_id": payload.get("payer_id", ""),
        "provider_npi": payload.get("provider_npi", ""),
        "member_id": payload["member_id"],
        "patient_first_name": payload["patient_first_name"],
        "patient_last_name": payload["patient_last_name"],
        "patient_birth_date": format_user_date(payload["patient_birth_date"]),
        "service_date": format_user_date(payload["service_date"]),
        "last_checked_at": payload.get("last_checked_at", (existing or {}).get("last_checked_at", "")),
        "last_result": payload.get("last_result", (existing or {}).get("last_result", "")),
        "messages": payload.get("messages", (existing or {}).get("messages", [])),
        "next_run_date": payload.get("next_run_date") or str((existing or {}).get("next_run_date", "")) or format_user_date(initial_run_date),
        "updated_at": datetime.now().isoformat(),
    }
    if existing is None:
        roster.insert(0, record)
    else:
        existing.update(record)
    save_eligibility_roster(roster)
    return record


def list_eligibility_roster() -> list[dict[str, Any]]:
    return _filter_current_agency(load_eligibility_roster())


def add_eligibility_history_entry(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_eligibility_history()
    payload = _with_current_agency(payload)
    record = {
        "history_id": payload.get("history_id") or f"EHX-{uuid.uuid4().hex[:8].upper()}",
        "agency_id": payload.get("agency_id", ""),
        "agency_name": payload.get("agency_name", ""),
        "checked_at": payload.get("checked_at") or datetime.now().strftime("%m/%d/%Y %I:%M%p"),
        "insured_name": str(payload.get("insured_name", "")).strip(),
        "payer_name": str(payload.get("payer_name", "")).strip(),
        "policy_number": str(payload.get("policy_number", "")).strip(),
        "benefit": str(payload.get("benefit", "30")).strip() or "30",
        "procedure": str(payload.get("procedure", "")).strip(),
        "status": str(payload.get("status", "")).strip(),
        "service_date": str(payload.get("service_date", "")).strip(),
        "actor_username": str(payload.get("actor_username", "")).strip(),
        "actor_name": str(payload.get("actor_name", "")).strip(),
        "updated_at": datetime.now().isoformat(),
    }
    items.insert(0, record)
    save_eligibility_history(items)
    return record


def list_eligibility_history(limit: int = 30) -> list[dict[str, Any]]:
    items = sorted(_filter_current_agency(load_eligibility_history()), key=lambda item: item.get("updated_at", ""), reverse=True)
    return items[:limit]


def count_eligibility_history() -> int:
    return len(_filter_current_agency(load_eligibility_history()))


def run_due_eligibility_checks(
    connector: Any,
    today: date | None = None,
    actor_username: str = "system",
    actor_name: str = "Blue Hope Scheduler",
) -> list[dict[str, Any]]:
    roster = load_eligibility_roster()
    clients = load_clients()
    run_date = today or datetime.now().date()
    updates: list[dict[str, Any]] = []

    for entry in roster:
        if not entry.get("active", True):
            continue

        next_run = parse_user_date(entry.get("next_run_date") or today_user_date())
        if run_date < next_run:
            continue

        entity_id = str(entry.get("roster_id", ""))
        entity_name = f"{entry['patient_first_name']} {entry['patient_last_name']}".strip()
        request = EligibilityRequest(
            payer_id=entry["payer_id"],
            provider_npi=entry["provider_npi"],
            member_id=entry["member_id"],
            patient_first_name=entry["patient_first_name"],
            patient_last_name=entry["patient_last_name"],
            patient_birth_date=entry["patient_birth_date"],
            service_date=entry["service_date"],
        )
        response: EligibilityResponse = connector.check_eligibility(request)
        entry["last_checked_at"] = datetime.now().strftime("%m/%d/%Y %H:%M")
        entry["last_result"] = response.coverage_status
        entry["messages"] = response.messages
        entry["next_run_date"] = format_user_date(_next_run_date(run_date))
        entry["updated_at"] = datetime.now().isoformat()
        for client in clients:
            if (
                client.get("member_id") == entry["member_id"]
                and client.get("payer_id") == entry["payer_id"]
                and client.get("provider_npi") == entry["provider_npi"]
            ):
                client["last_eligibility_result"] = response.coverage_status
                client["last_eligibility_checked_at"] = entry["last_checked_at"]
                client["last_plan_name"] = response.plan_name or ""
                client["last_subscriber_id"] = response.subscriber_id
                client["last_messages"] = response.messages
                client["updated_at"] = datetime.now().isoformat()
                entity_id = str(client.get("client_id", entity_id))
                entity_name = f"{client.get('first_name', '')} {client.get('last_name', '')}".strip() or entity_name
                break
        updates.append(
            {
                "member_id": entry["member_id"],
                "patient_name": f"{entry['patient_first_name']} {entry['patient_last_name']}",
                "coverage_status": response.coverage_status,
                "next_run_date": entry["next_run_date"],
            }
        )
        add_eligibility_history_entry(
            {
                "agency_id": entry.get("agency_id", ""),
                "agency_name": entry.get("agency_name", ""),
                "insured_name": f"{entry['patient_last_name']}, {entry['patient_first_name']}".strip(", "),
                "payer_name": next(
                    (
                        client.get("payer_name", "")
                        for client in clients
                        if client.get("member_id") == entry["member_id"]
                    ),
                    "",
                ),
                "policy_number": entry.get("member_id", ""),
                "benefit": "30",
                "procedure": "",
                "status": "complete",
                "service_date": entry.get("service_date", ""),
                "actor_username": actor_username,
                "actor_name": actor_name,
            }
        )
        add_notification(
            {
                "agency_id": entry.get("agency_id", ""),
                "agency_name": entry.get("agency_name", ""),
                "category": "eligibility",
                "subject": f"Elegibilidad automatica {response.coverage_status} para {entry['patient_first_name']} {entry['patient_last_name']}",
                "message": (
                    f"La corrida automatica del roster dejo a {entry['patient_first_name']} "
                    f"{entry['patient_last_name']} en {response.coverage_status}. "
                    f"La siguiente corrida sera el {entry['next_run_date']}."
                ),
                "related_id": entry["roster_id"],
            }
        )
        add_system_audit_log(
            {
                "agency_id": entry.get("agency_id", ""),
                "agency_name": entry.get("agency_name", ""),
                "category": "client",
                "entity_type": "client",
                "entity_id": entity_id,
                "entity_name": entity_name,
                "action": "CLIENT_ELIGIBILITY_AUTO",
                "actor_username": actor_username,
                "actor_name": actor_name,
                "details": (
                    f"Corrida automatica dejo la elegibilidad en {response.coverage_status}. "
                    f"Proxima revision {entry['next_run_date']}."
                ),
            }
        )

    if updates:
        save_eligibility_roster(roster)
        save_clients(clients)
    return updates
