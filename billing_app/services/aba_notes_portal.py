from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from billing_app.services.aba_notes_engine import (
    Appointment,
    DocumentType,
    NoteWorkflowState,
    Provider,
    ProviderRole,
    Scheduler,
    ServiceCode,
    ServiceContext,
    ServiceLog,
    ServiceModifier,
    SchedulingError,
    Client as AbaClient,
    get_billing_rule,
)
from billing_app.services.date_utils import format_user_date, parse_user_date, today_user_date
from billing_app.services.local_store import (
    get_current_agency,
    get_current_agency_id,
    load_authorizations,
    list_authorizations,
    list_clients,
    list_provider_contracts,
    save_authorizations,
)


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
ABA_APPOINTMENTS_FILE = DATA_DIR / "aba_notes_appointments.json"
ABA_NOTE_STATES_FILE = DATA_DIR / "aba_note_states.json"

SERVICE_CONTEXT_LABELS: dict[str, str] = {
    ServiceContext.ASSESSMENT.value: "Assessment",
    ServiceContext.REASSESSMENT.value: "Reassessment",
    ServiceContext.DIRECT.value: "Servicio directo",
    ServiceContext.SUPERVISION_RBT.value: "Supervision a RBT",
    ServiceContext.SUPERVISION_BCABA.value: "Supervision a BCaBA",
    ServiceContext.PARENT_TRAINING.value: "Entrenamiento a padres",
}


def _load_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _save_list(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, ensure_ascii=True), encoding="utf-8")


def _normalize_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _client_name(client: dict[str, Any]) -> str:
    return f"{client.get('first_name', '')} {client.get('last_name', '')}".strip()


def _split_assignments(raw_value: str) -> set[str]:
    normalized = (
        str(raw_value or "")
        .replace("\r", "\n")
        .replace(";", "\n")
        .replace(",", "\n")
    )
    return {
        _normalize_name(piece)
        for piece in normalized.split("\n")
        if _normalize_name(piece)
    }


def _normalize_cpt_code(value: str) -> str:
    clean = str(value or "").strip().upper().replace("CPT-", "").replace("CPT ", "")
    return " ".join(clean.split())


def _provider_role_from_type(value: str) -> ProviderRole | None:
    clean = _normalize_name(str(value or "")).replace(".", "")
    if clean == "bcba":
        return ProviderRole.BCBA
    if clean == "bcaba":
        return ProviderRole.BCABA
    if clean == "rbt":
        return ProviderRole.RBT
    return None


def _supported_provider_records(provider_contract_ids: set[str] | None = None) -> list[dict[str, Any]]:
    supported: list[dict[str, Any]] = []
    for item in list_provider_contracts():
        role = _provider_role_from_type(str(item.get("provider_type", "")))
        if role is None:
            continue
        contract_id = str(item.get("contract_id", "")).strip()
        if provider_contract_ids is not None and contract_id not in provider_contract_ids:
            continue
        supported.append(item)
    return supported


def _authorization_summary_by_client(clients: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {
        str(client.get("client_id", "")).strip(): {
            "units": {},
            "authorization_numbers": [],
            "start_dates": [],
            "end_dates": [],
        }
        for client in clients
        if str(client.get("client_id", "")).strip()
    }
    member_to_client_id = {
        str(client.get("member_id", "")).strip(): str(client.get("client_id", "")).strip()
        for client in clients
        if str(client.get("member_id", "")).strip() and str(client.get("client_id", "")).strip()
    }
    for item in list_authorizations():
        if not item.get("active", True):
            continue
        client_id = str(item.get("client_id", "")).strip()
        if not client_id:
            client_id = member_to_client_id.get(str(item.get("patient_member_id", "")).strip(), "")
        if not client_id:
            continue
        bucket = summary.setdefault(
            client_id,
            {"units": {}, "authorization_numbers": [], "start_dates": [], "end_dates": []},
        )
        cpt_code = _normalize_cpt_code(str(item.get("cpt_code", "")))
        try:
            remaining_units = int(round(float(item.get("remaining_units", 0) or 0)))
        except (TypeError, ValueError):
            remaining_units = 0
        if cpt_code:
            bucket["units"][cpt_code] = int(bucket["units"].get(cpt_code, 0)) + remaining_units
        authorization_number = str(item.get("authorization_number", "")).strip()
        if authorization_number and authorization_number not in bucket["authorization_numbers"]:
            bucket["authorization_numbers"].append(authorization_number)
        start_date = str(item.get("start_date", "")).strip()
        end_date = str(item.get("end_date", "")).strip()
        if start_date:
            bucket["start_dates"].append(start_date)
        if end_date:
            bucket["end_dates"].append(end_date)
    return summary


def _provider_client_records(
    provider_record: dict[str, Any],
    clients: list[dict[str, Any]],
    *,
    allow_unassigned_fallback: bool = True,
) -> list[dict[str, Any]]:
    assigned_names = _split_assignments(str(provider_record.get("assigned_clients", "")))
    provider_npi = str(provider_record.get("provider_npi", "")).strip()
    matched = []
    for client in clients:
        client_name_key = _normalize_name(_client_name(client))
        client_provider_npi = str(client.get("provider_npi", "")).strip()
        if assigned_names and client_name_key in assigned_names:
            matched.append(client)
            continue
        if provider_npi and client_provider_npi and provider_npi == client_provider_npi:
            matched.append(client)
    if matched:
        return matched
    if allow_unassigned_fallback:
        return clients
    return []


def _client_case_provider_ids(
    client_record: dict[str, Any],
    provider_records: list[dict[str, Any]],
) -> set[str]:
    explicit_ids = {
        str(client_record.get("bcba_contract_id", "")).strip(),
        str(client_record.get("bcaba_contract_id", "")).strip(),
        str(client_record.get("rbt_contract_id", "")).strip(),
    }
    explicit_ids = {item for item in explicit_ids if item}
    if explicit_ids:
        return explicit_ids

    client_name_key = _normalize_name(_client_name(client_record))
    client_provider_npi = str(client_record.get("provider_npi", "")).strip()
    matched_ids: set[str] = set()
    for provider_record in provider_records:
        contract_id = str(provider_record.get("contract_id", "")).strip()
        if not contract_id:
            continue
        assigned_names = _split_assignments(str(provider_record.get("assigned_clients", "")))
        provider_npi = str(provider_record.get("provider_npi", "")).strip()
        if assigned_names and client_name_key in assigned_names:
            matched_ids.add(contract_id)
            continue
        if provider_npi and client_provider_npi and provider_npi == client_provider_npi:
            matched_ids.add(contract_id)
    return matched_ids


def _signature_preview_text(value: Any) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if clean.startswith("data:image/"):
        return "Drawn signature on file"
    return "Legacy typed signature on file"


def _drawn_signature_value(value: Any) -> str:
    clean = str(value or "").strip()
    return clean if clean.startswith("data:image/") else ""


def _document_signature_value(value: Any) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if clean.startswith("data:image/"):
        return clean
    return _signature_preview_text(clean)


def list_aba_provider_options(
    provider_contract_ids: set[str] | None = None,
    client_id: str = "",
    *,
    allow_unassigned_fallback: bool = True,
) -> list[dict[str, Any]]:
    provider_records = _supported_provider_records(provider_contract_ids)
    case_provider_ids: set[str] = set()
    clean_client_id = str(client_id or "").strip()
    if clean_client_id:
        client_record = next(
            (item for item in list_clients() if str(item.get("client_id", "")).strip() == clean_client_id),
            None,
        )
        if client_record is not None:
            case_provider_ids = _client_case_provider_ids(client_record, provider_records)

    options = []
    for item in provider_records:
        role = _provider_role_from_type(str(item.get("provider_type", "")))
        if role is None:
            continue
        contract_id = str(item.get("contract_id", "")).strip()
        client_records = _provider_client_records(
            item,
            list_clients(),
            allow_unassigned_fallback=allow_unassigned_fallback,
        )
        client_ids = [
            str(client.get("client_id", "")).strip()
            for client in client_records
            if str(client.get("client_id", "")).strip()
        ]
        if case_provider_ids and contract_id not in case_provider_ids:
            continue
        options.append(
            {
                "provider_contract_id": contract_id,
                "provider_name": str(item.get("provider_name", "")),
                "provider_role": role.value,
                "provider_type": str(item.get("provider_type", "")),
                "provider_credentials": str(item.get("provider_type", "")) or role.value,
                "assigned_clients": str(item.get("assigned_clients", "")),
                "site_location": str(item.get("site_location", "")),
                "client_ids": client_ids,
            }
        )
    if not options and clean_client_id and allow_unassigned_fallback:
        return list_aba_provider_options(provider_contract_ids, "", allow_unassigned_fallback=allow_unassigned_fallback)
    return sorted(options, key=lambda item: (item.get("provider_name", ""), item.get("provider_role", "")))


def list_aba_client_options(
    provider_contract_id: str = "",
    provider_contract_ids: set[str] | None = None,
    *,
    allow_unassigned_fallback: bool = True,
) -> list[dict[str, Any]]:
    clients = list_clients()
    if provider_contract_id:
        provider_record = next(
            (
                item
                for item in _supported_provider_records(provider_contract_ids)
                if str(item.get("contract_id", "")).strip() == str(provider_contract_id).strip()
            ),
            None,
        )
        if provider_record is not None:
            clients = _provider_client_records(
                provider_record,
                clients,
                allow_unassigned_fallback=allow_unassigned_fallback,
            )
    options = []
    for client in clients:
        client_id = str(client.get("client_id", "")).strip()
        if not client_id:
            continue
        options.append(
            {
                "client_id": client_id,
                "client_name": _client_name(client),
                "member_id": str(client.get("member_id", "")),
                "payer_name": str(client.get("payer_name", "")),
            }
        )
    return sorted(options, key=lambda item: item.get("client_name", ""))


def _load_appointment_records(provider_contract_ids: set[str] | None = None) -> list[dict[str, Any]]:
    items = _load_list(ABA_APPOINTMENTS_FILE)
    current_agency_id = get_current_agency_id()
    filtered = []
    for item in items:
        agency_id = str(item.get("agency_id", "")).strip()
        provider_id = str(item.get("provider_contract_id", "")).strip()
        if current_agency_id and agency_id not in {"", current_agency_id}:
            continue
        if provider_contract_ids is not None and provider_id not in provider_contract_ids:
            continue
        filtered.append(item)
    return filtered


def _save_appointment_records(items: list[dict[str, Any]]) -> None:
    _save_list(ABA_APPOINTMENTS_FILE, items)


def _load_note_state_records(provider_contract_ids: set[str] | None = None) -> list[dict[str, Any]]:
    items = _load_list(ABA_NOTE_STATES_FILE)
    current_agency_id = get_current_agency_id()
    filtered = []
    for item in items:
        agency_id = str(item.get("agency_id", "")).strip()
        provider_id = str(item.get("provider_contract_id", "")).strip()
        if current_agency_id and agency_id not in {"", current_agency_id}:
            continue
        if provider_contract_ids is not None and provider_id not in provider_contract_ids:
            continue
        filtered.append(item)
    return filtered


def _save_note_state_records(items: list[dict[str, Any]]) -> None:
    _save_list(ABA_NOTE_STATES_FILE, items)


def _serialize_datetime(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _deserialize_datetime(value: object) -> datetime | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        return datetime.fromisoformat(clean)
    except ValueError:
        return None


def _serialize_note_state(
    log_id: str,
    agency_id: str,
    agency_name: str,
    provider_contract_id: str,
    state: NoteWorkflowState,
) -> dict[str, Any]:
    return {
        "log_id": log_id,
        "agency_id": agency_id,
        "agency_name": agency_name,
        "provider_contract_id": provider_contract_id,
        "reviewed_by": state.reviewed_by,
        "reviewed_at": _serialize_datetime(state.reviewed_at),
        "closed_by": state.closed_by,
        "closed_at": _serialize_datetime(state.closed_at),
        "rejected_by": state.rejected_by,
        "rejected_at": _serialize_datetime(state.rejected_at),
        "rejected_reason": state.rejected_reason,
        "reopened_by": state.reopened_by,
        "reopened_at": _serialize_datetime(state.reopened_at),
        "reopen_reason": state.reopen_reason,
        "caregiver_signature": state.caregiver_signature,
        "caregiver_signed_at": _serialize_datetime(state.caregiver_signed_at),
        "provider_signature": state.provider_signature,
        "provider_signature_date": state.provider_signature_date,
        "updated_at": datetime.now().isoformat(),
    }


def _deserialize_note_state(item: dict[str, Any]) -> NoteWorkflowState:
    return NoteWorkflowState(
        reviewed_by=str(item.get("reviewed_by", "")),
        reviewed_at=_deserialize_datetime(item.get("reviewed_at")),
        closed_by=str(item.get("closed_by", "")),
        closed_at=_deserialize_datetime(item.get("closed_at")),
        rejected_by=str(item.get("rejected_by", "")),
        rejected_at=_deserialize_datetime(item.get("rejected_at")),
        rejected_reason=str(item.get("rejected_reason", "")),
        reopened_by=str(item.get("reopened_by", "")),
        reopened_at=_deserialize_datetime(item.get("reopened_at")),
        reopen_reason=str(item.get("reopen_reason", "")),
        caregiver_signature=str(item.get("caregiver_signature", "")),
        caregiver_signed_at=_deserialize_datetime(item.get("caregiver_signed_at")),
        provider_signature=str(item.get("provider_signature", "")),
        provider_signature_date=str(item.get("provider_signature_date", "")),
    )


def _deserialize_appointment(item: dict[str, Any]) -> Appointment:
    modifier_value = str(item.get("service_modifier", "")).strip()
    return Appointment(
        id=str(item.get("appointment_id", "")),
        provider_id=str(item.get("provider_contract_id", "")),
        client_id=str(item.get("client_id", "")),
        start_at=datetime.fromisoformat(str(item.get("start_at", ""))),
        end_at=datetime.fromisoformat(str(item.get("end_at", ""))),
        service_context=ServiceContext(str(item.get("service_context", ServiceContext.DIRECT.value))),
        service_code=ServiceCode(str(item.get("service_code", ServiceCode.CPT_97153.value))),
        service_modifier=ServiceModifier(modifier_value) if modifier_value else None,
        unit_rate=float(item.get("unit_rate", 0) or 0),
        document_type=DocumentType(str(item.get("document_type", DocumentType.RBT_SERVICE_LOG.value))),
        place_of_service=str(item.get("place_of_service", "")) or "Home (12)",
        caregiver_name=str(item.get("caregiver_name", "")),
        caregiver_signature=str(item.get("caregiver_signature", "")),
        session_note=str(item.get("session_note", "")),
        supervising_provider_id=str(item.get("supervising_provider_id", "")).strip() or None,
    )


def _build_scheduler(
    provider_contract_ids: set[str] | None = None,
    *,
    allow_unassigned_fallback: bool = True,
) -> Scheduler:
    scheduler = Scheduler()
    provider_records = _supported_provider_records(provider_contract_ids)
    client_records = list_clients()
    authorization_summary = _authorization_summary_by_client(client_records)

    for provider_record in provider_records:
        role = _provider_role_from_type(str(provider_record.get("provider_type", "")))
        if role is None:
            continue
        provider = Provider(
            id=str(provider_record.get("contract_id", "")),
            full_name=str(provider_record.get("provider_name", "")),
            role=role,
            credentials=str(provider_record.get("provider_type", "")) or role.value,
        )
        scheduler.add_provider(provider)

    for client_record in client_records:
        client_id = str(client_record.get("client_id", "")).strip()
        if not client_id:
            continue
        auth_data = authorization_summary.get(client_id, {})
        approved_units = ", ".join(
            f"{code}: {int(units)}"
            for code, units in sorted((auth_data.get("units", {}) or {}).items())
        )
        scheduler.add_client(
            AbaClient(
                id=client_id,
                full_name=_client_name(client_record),
                insurance_id=str(client_record.get("member_id", "")),
                diagnoses=str(client_record.get("notes", "")),
                pa_number=", ".join(auth_data.get("authorization_numbers", [])),
                pa_start_date=min(auth_data.get("start_dates", [""])) if auth_data.get("start_dates") else "",
                pa_end_date=max(auth_data.get("end_dates", [""])) if auth_data.get("end_dates") else "",
                approved_units=approved_units,
                caregiver_name="",
            )
        )

    for provider_record in provider_records:
        provider_id = str(provider_record.get("contract_id", ""))
        if provider_id not in scheduler.providers:
            continue
        for client_record in _provider_client_records(
            provider_record,
            client_records,
            allow_unassigned_fallback=allow_unassigned_fallback,
        ):
            client_id = str(client_record.get("client_id", "")).strip()
            if client_id and client_id in scheduler.clients:
                scheduler.assign_client_to_provider(provider_id, client_id)

    for appointment_record in _load_appointment_records(provider_contract_ids):
        try:
            scheduler.appointments.append(_deserialize_appointment(appointment_record))
        except (ValueError, KeyError):
            continue

    for item in _load_note_state_records(provider_contract_ids):
        log_id = str(item.get("log_id", "")).strip()
        if log_id:
            scheduler.note_states[log_id] = _deserialize_note_state(item)

    return scheduler


def _appointment_units(start_at: datetime, end_at: datetime) -> int:
    return max(0, round((end_at - start_at).total_seconds() / 900))


def _find_provider_record(provider_contract_id: str, provider_contract_ids: set[str] | None = None) -> dict[str, Any]:
    clean_id = str(provider_contract_id or "").strip()
    if not clean_id:
        raise ValueError("Selecciona un provider.")
    for item in _supported_provider_records(provider_contract_ids):
        if str(item.get("contract_id", "")).strip() == clean_id:
            return item
    raise ValueError("No encontre ese provider para Notas ABA.")


def _find_client_record(
    client_id: str,
    provider_record: dict[str, Any],
    *,
    allow_unassigned_fallback: bool = True,
) -> dict[str, Any]:
    clean_id = str(client_id or "").strip()
    if not clean_id:
        raise ValueError("Selecciona un cliente.")
    client_records = _provider_client_records(
        provider_record,
        list_clients(),
        allow_unassigned_fallback=allow_unassigned_fallback,
    )
    for item in client_records:
        if str(item.get("client_id", "")).strip() == clean_id:
            return item
    raise ValueError("Ese cliente no esta disponible para el provider seleccionado.")


def _resolve_service_log_for_appointment(scheduler: Scheduler, appointment: Appointment) -> ServiceLog | None:
    for log in scheduler.get_weekly_service_logs(
        week_of=appointment.start_at.date(),
        provider_id=appointment.provider_id,
    ):
        if log.client_id == appointment.client_id and log.document_type == appointment.document_type:
            return log
    return None


def _upsert_note_state_record(log_id: str, provider_contract_id: str, state: NoteWorkflowState) -> None:
    state_records = _load_list(ABA_NOTE_STATES_FILE)
    current_agency = get_current_agency()
    current_agency_id = str((current_agency or {}).get("agency_id", ""))
    serialized = _serialize_note_state(
        log_id,
        current_agency_id,
        str((current_agency or {}).get("agency_name", "")),
        provider_contract_id,
        state,
    )
    match_index = next(
        (
            index
            for index, item in enumerate(state_records)
            if str(item.get("log_id", "")).strip() == log_id
            and (
                not current_agency_id
                or str(item.get("agency_id", "")).strip() in {"", current_agency_id}
            )
        ),
        None,
    )
    if match_index is None:
        state_records.insert(0, serialized)
    else:
        state_records[match_index] = serialized
    _save_note_state_records(state_records)


def _document_for(role: ProviderRole, context: ServiceContext) -> DocumentType:
    if context == ServiceContext.ASSESSMENT:
        return DocumentType.ASSESSMENT
    if context == ServiceContext.REASSESSMENT:
        return DocumentType.REASSESSMENT
    if context == ServiceContext.DIRECT:
        return DocumentType.ANALYST_SERVICE_LOG if role in {ProviderRole.BCBA, ProviderRole.BCABA} else DocumentType.RBT_SERVICE_LOG
    if context == ServiceContext.SUPERVISION_RBT:
        return DocumentType.SUPERVISION_LOG
    if context == ServiceContext.SUPERVISION_BCABA:
        return DocumentType.SUPERVISION_SERVICE_LOG
    return DocumentType.APPOINTMENT_NOTE


def _format_remaining_units(remaining_units: dict[str, int]) -> str:
    if not remaining_units:
        return "-"
    return ", ".join(f"{code}: {amount}" for code, amount in sorted(remaining_units.items()))


def _appointment_record_units(item: dict[str, Any]) -> int:
    try:
        start_at = datetime.fromisoformat(str(item.get("start_at", "")))
        end_at = datetime.fromisoformat(str(item.get("end_at", "")))
    except ValueError:
        return int(float(item.get("units", 0) or 0))
    return _appointment_units(start_at, end_at)


def _appointment_record_with_derived_fields(item: dict[str, Any]) -> dict[str, Any]:
    try:
        start_at = datetime.fromisoformat(str(item.get("start_at", "")))
        end_at = datetime.fromisoformat(str(item.get("end_at", "")))
    except ValueError:
        return dict(item)
    units = _appointment_units(start_at, end_at)
    unit_rate = float(item.get("unit_rate", 0) or 0)
    modifier = str(item.get("service_modifier", "")).strip()
    billing_code = str(item.get("service_code", ""))
    if modifier:
        billing_code = f"{billing_code}-{modifier}"
    actual_start_at = str(item.get("actual_start_at", "")).strip()
    actual_end_at = str(item.get("actual_end_at", "")).strip()
    actual_start_label = ""
    actual_end_label = ""
    try:
        if actual_start_at:
            actual_start_label = datetime.fromisoformat(actual_start_at).strftime("%H:%M")
        if actual_end_at:
            actual_end_label = datetime.fromisoformat(actual_end_at).strftime("%H:%M")
    except ValueError:
        actual_start_label = ""
        actual_end_label = ""
    return {
        **item,
        "appointment_date": start_at.strftime("%m/%d/%Y"),
        "start_time_label": start_at.strftime("%H:%M"),
        "end_time_label": end_at.strftime("%H:%M"),
        "actual_start_time_label": actual_start_label,
        "actual_end_time_label": actual_end_label,
        "billing_code": billing_code,
        "context_label": SERVICE_CONTEXT_LABELS.get(str(item.get("service_context", "")), str(item.get("service_context", ""))),
        "units": units,
        "estimated_total": unit_rate * units,
        "session_status": str(item.get("session_status", "")).strip() or "Scheduled",
        "billing_status": str(item.get("billing_status", "")).strip() or "Not Ready",
        "note_status": str(item.get("note_status", "")).strip() or "Draft",
        "document_status": str(item.get("document_status", "")).strip() or "Unlocked",
        "claim_status": str(item.get("claim_status", "")).strip() or "Draft",
    }


def get_aba_appointment_detail(
    appointment_id: str,
    provider_contract_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    clean_id = str(appointment_id or "").strip()
    if not clean_id:
        return None
    for item in _load_appointment_records(provider_contract_ids):
        if str(item.get("appointment_id", "")).strip() == clean_id:
            return _appointment_record_with_derived_fields(item)
    return None


def _authorization_match_for_appointment(
    appointment_record: dict[str, Any],
    client_record: dict[str, Any],
    authorizations: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]] | None:
    client_id = str(client_record.get("client_id", "")).strip() or str(appointment_record.get("client_id", "")).strip()
    member_id = str(client_record.get("member_id", "")).strip() or str(appointment_record.get("client_member_id", "")).strip()
    service_code = _normalize_cpt_code(str(appointment_record.get("service_code", "")))
    try:
        service_date = parse_user_date(str(appointment_record.get("appointment_date", "")).strip())
    except ValueError:
        return None
    current_agency_id = get_current_agency_id()
    candidates: list[tuple[date, int, dict[str, Any]]] = []
    for index, item in enumerate(authorizations):
        agency_id = str(item.get("agency_id", "")).strip()
        if current_agency_id and agency_id not in {"", current_agency_id}:
            continue
        if not bool(item.get("active", True)):
            continue
        auth_client_id = str(item.get("client_id", "")).strip()
        auth_member_id = str(item.get("patient_member_id", "")).strip()
        if client_id and auth_client_id and auth_client_id != client_id:
            continue
        if not auth_client_id and member_id and auth_member_id != member_id:
            continue
        if _normalize_cpt_code(str(item.get("cpt_code", ""))) != service_code:
            continue
        try:
            start_date = parse_user_date(str(item.get("start_date", "")))
            end_date = parse_user_date(str(item.get("end_date", "")))
        except ValueError:
            continue
        if service_date < start_date or service_date > end_date:
            continue
        candidates.append((end_date, index, item))
    if not candidates:
        return None
    candidates.sort(key=lambda value: (value[0], str(value[2].get("authorization_number", ""))))
    _, index, record = candidates[0]
    return (index, record)


def _consume_authorization_for_appointment(
    appointment_record: dict[str, Any],
    client_record: dict[str, Any],
) -> dict[str, Any]:
    if str(appointment_record.get("authorization_consumed_at", "")).strip():
        return {
            "authorization_id": str(appointment_record.get("authorization_id", "")).strip(),
            "authorization_number": str(appointment_record.get("authorization_number", "")).strip(),
            "used_units": int(float(appointment_record.get("authorization_consumed_units", 0) or 0)),
            "remaining_units": float(appointment_record.get("authorization_remaining_after", 0) or 0),
            "status": "already_consumed",
        }
    authorizations = load_authorizations()
    match = _authorization_match_for_appointment(appointment_record, client_record, authorizations)
    if match is None:
        raise ValueError("No hay autorizacion activa para cerrar esta sesion.")
    index, record = match
    units = _appointment_record_units(appointment_record)
    remaining_units = float(record.get("remaining_units", 0) or 0)
    if units <= 0:
        raise ValueError("La sesion no tiene units validas para consumir autorizacion.")
    if remaining_units < units:
        raise ValueError("La autorizacion no tiene units suficientes para cerrar esta sesion.")
    authorizations[index]["remaining_units"] = max(remaining_units - units, 0.0)
    authorizations[index]["updated_at"] = datetime.now().isoformat()
    save_authorizations(authorizations)
    appointment_record["authorization_id"] = str(record.get("authorization_id", "")).strip()
    appointment_record["authorization_number"] = str(record.get("authorization_number", "")).strip()
    appointment_record["authorization_consumed_units"] = units
    appointment_record["authorization_consumed_at"] = datetime.now().isoformat()
    appointment_record["authorization_remaining_after"] = float(authorizations[index].get("remaining_units", 0) or 0)
    return {
        "authorization_id": appointment_record["authorization_id"],
        "authorization_number": appointment_record["authorization_number"],
        "used_units": units,
        "remaining_units": appointment_record["authorization_remaining_after"],
        "status": "consumed",
    }


def _release_authorization_for_appointment(appointment_record: dict[str, Any]) -> dict[str, Any]:
    consumed_at = str(appointment_record.get("authorization_consumed_at", "")).strip()
    authorization_id = str(appointment_record.get("authorization_id", "")).strip()
    consumed_units = int(float(appointment_record.get("authorization_consumed_units", 0) or 0))
    if not consumed_at or not authorization_id or consumed_units <= 0:
        return {
            "authorization_id": authorization_id,
            "authorization_number": str(appointment_record.get("authorization_number", "")).strip(),
            "used_units": 0,
            "remaining_units": float(appointment_record.get("authorization_remaining_after", 0) or 0),
            "status": "nothing_to_release",
        }
    if str(appointment_record.get("claim_id", "")).strip():
        raise ValueError("No puedes reabrir una nota ya ligada a un claim.")
    authorizations = load_authorizations()
    current_agency_id = get_current_agency_id()
    match_index = next(
        (
            index
            for index, item in enumerate(authorizations)
            if str(item.get("authorization_id", "")).strip() == authorization_id
            and (
                not current_agency_id
                or str(item.get("agency_id", "")).strip() in {"", current_agency_id}
            )
        ),
        None,
    )
    if match_index is None:
        raise ValueError("No encontre la autorizacion que ya habia consumido esta sesion.")
    previous_units = float(authorizations[match_index].get("remaining_units", 0) or 0)
    authorizations[match_index]["remaining_units"] = previous_units + consumed_units
    authorizations[match_index]["updated_at"] = datetime.now().isoformat()
    save_authorizations(authorizations)
    appointment_record["authorization_consumed_units"] = 0
    appointment_record["authorization_consumed_at"] = ""
    appointment_record["authorization_remaining_after"] = float(authorizations[match_index].get("remaining_units", 0) or 0)
    return {
        "authorization_id": authorization_id,
        "authorization_number": str(appointment_record.get("authorization_number", "")).strip(),
        "used_units": consumed_units,
        "remaining_units": appointment_record["authorization_remaining_after"],
        "status": "released",
    }


def get_aba_billing_preview(
    provider_contract_id: str = "",
    client_id: str = "",
    service_context: str = "",
    appointment_date: str = "",
    start_time: str = "",
    end_time: str = "",
    provider_contract_ids: set[str] | None = None,
    *,
    allow_unassigned_fallback: bool = True,
) -> dict[str, Any]:
    if not provider_contract_id:
        return {"message": "Selecciona un provider para ver CPT, documento y unidades."}
    provider_record = _find_provider_record(provider_contract_id, provider_contract_ids)
    provider_role = _provider_role_from_type(str(provider_record.get("provider_type", "")))
    if provider_role is None:
        return {"message": "Este tipo de provider no tiene reglas ABA cargadas."}

    try:
        context = ServiceContext(str(service_context or ServiceContext.DIRECT.value))
        rule = get_billing_rule(provider_role, context)
    except ValueError as exc:
        return {"message": str(exc)}

    units = 0
    remaining_units_label = "-"
    try:
        session_day = parse_user_date(appointment_date or today_user_date())
        start_at = datetime.strptime(
            f"{format_user_date(session_day)} {str(start_time or '09:00').strip()}",
            "%m/%d/%Y %H:%M",
        )
        end_at = datetime.strptime(
            f"{format_user_date(session_day)} {str(end_time or '10:00').strip()}",
            "%m/%d/%Y %H:%M",
        )
        units = _appointment_units(start_at, end_at)
    except ValueError:
        remaining_units_label = "Fecha u hora invalida"
    else:
        if client_id:
            try:
                _find_client_record(
                    str(client_id).strip(),
                    provider_record,
                    allow_unassigned_fallback=allow_unassigned_fallback,
                )
                scheduler = _build_scheduler(
                    provider_contract_ids,
                    allow_unassigned_fallback=allow_unassigned_fallback,
                )
                if str(client_id).strip() in scheduler.clients:
                    remaining_units_label = _format_remaining_units(
                        scheduler.get_remaining_authorized_units(client_id=str(client_id).strip(), as_of=session_day)
                    )
            except ValueError as exc:
                remaining_units_label = str(exc)

    modifier = f"-{rule.modifier.value}" if rule.modifier else ""
    return {
        "document_type": _document_for(provider_role, context).value,
        "billing_code": f"{rule.service_code.value}{modifier}",
        "required_cpt": rule.service_code.value,
        "provider_role": provider_role.value,
        "service_context": context.value,
        "units": units,
        "unit_rate": rule.unit_rate,
        "estimated_total": rule.unit_rate * units,
        "remaining_units": remaining_units_label,
        "description": rule.description,
        "payable": rule.payable,
    }


def add_aba_appointment(
    payload: dict[str, Any],
    provider_contract_ids: set[str] | None = None,
    *,
    allow_unassigned_fallback: bool = True,
) -> dict[str, Any]:
    provider_record = _find_provider_record(str(payload.get("provider_contract_id", "")), provider_contract_ids)
    provider_role = _provider_role_from_type(str(provider_record.get("provider_type", "")))
    if provider_role is None:
        raise ValueError("Ese provider no tiene un rol ABA compatible.")
    client_record = _find_client_record(
        str(payload.get("client_id", "")),
        provider_record,
        allow_unassigned_fallback=allow_unassigned_fallback,
    )
    session_day = parse_user_date(str(payload.get("appointment_date", "")) or today_user_date())
    start_at = datetime.strptime(
        f"{format_user_date(session_day)} {str(payload.get('start_time', '')).strip()}",
        "%m/%d/%Y %H:%M",
    )
    end_at = datetime.strptime(
        f"{format_user_date(session_day)} {str(payload.get('end_time', '')).strip()}",
        "%m/%d/%Y %H:%M",
    )
    context = ServiceContext(str(payload.get("service_context", ServiceContext.DIRECT.value)))
    rule = get_billing_rule(provider_role, context)
    document_type = _document_for(provider_role, context)
    scheduler = _build_scheduler(
        provider_contract_ids,
        allow_unassigned_fallback=allow_unassigned_fallback,
    )
    appointment = Appointment(
        id=f"ABASES-{uuid.uuid4().hex[:8].upper()}",
        provider_id=str(provider_record.get("contract_id", "")),
        client_id=str(client_record.get("client_id", "")),
        start_at=start_at,
        end_at=end_at,
        service_context=context,
        service_code=rule.service_code,
        service_modifier=rule.modifier,
        unit_rate=rule.unit_rate,
        document_type=document_type,
        place_of_service=str(payload.get("place_of_service", "")).strip() or "Home (12)",
        caregiver_name=str(payload.get("caregiver_name", "")).strip(),
        caregiver_signature=_drawn_signature_value(payload.get("caregiver_signature", "")),
        session_note=str(payload.get("session_note", "")).strip(),
        supervising_provider_id=str(payload.get("supervising_provider_id", "")).strip() or None,
    )
    scheduler.create_appointment(appointment)
    provider_signature = _drawn_signature_value(payload.get("provider_signature", ""))
    service_log = _resolve_service_log_for_appointment(scheduler, appointment)
    if service_log is not None and (appointment.caregiver_signature or provider_signature):
        state = scheduler.note_states.setdefault(service_log.id, NoteWorkflowState())
        if appointment.caregiver_signature:
            state.caregiver_signature = appointment.caregiver_signature
            state.caregiver_signed_at = datetime.now()
        if provider_signature:
            state.provider_signature = provider_signature
            state.provider_signature_date = datetime.now().strftime("%Y-%m-%d")
        _upsert_note_state_record(service_log.id, appointment.provider_id, state)

    current_agency = get_current_agency()
    appointment_units = _appointment_units(start_at, end_at)
    matched_authorization = _authorization_match_for_appointment(
        {
            "client_id": appointment.client_id,
            "client_member_id": str(client_record.get("member_id", "")),
            "service_code": appointment.service_code.value,
            "appointment_date": format_user_date(session_day),
        },
        client_record,
        load_authorizations(),
    )
    authorization_id = ""
    authorization_number = ""
    if matched_authorization is not None:
        _, matched_authorization_record = matched_authorization
        authorization_id = str(matched_authorization_record.get("authorization_id", "")).strip()
        authorization_number = str(matched_authorization_record.get("authorization_number", "")).strip()
    record = {
        "appointment_id": appointment.id,
        "agency_id": str((current_agency or {}).get("agency_id", "")),
        "agency_name": str((current_agency or {}).get("agency_name", "")),
        "provider_contract_id": appointment.provider_id,
        "provider_name": str(provider_record.get("provider_name", "")),
        "provider_role": provider_role.value,
        "provider_credentials": str(provider_record.get("provider_type", "")) or provider_role.value,
        "client_id": appointment.client_id,
        "client_name": _client_name(client_record),
        "client_member_id": str(client_record.get("member_id", "")),
        "start_at": appointment.start_at.isoformat(),
        "end_at": appointment.end_at.isoformat(),
        "service_context": appointment.service_context.value,
        "service_code": appointment.service_code.value,
        "service_modifier": appointment.service_modifier.value if appointment.service_modifier else "",
        "unit_rate": appointment.unit_rate,
        "document_type": appointment.document_type.value,
        "place_of_service": appointment.place_of_service,
        "caregiver_name": appointment.caregiver_name,
        "caregiver_signature": appointment.caregiver_signature,
        "provider_signature": provider_signature,
        "session_note": appointment.session_note,
        "service_log_id": service_log.id if service_log is not None else "",
        "supervising_provider_id": appointment.supervising_provider_id or "",
        "authorization_id": authorization_id,
        "authorization_number": authorization_number,
        "authorization_reserved_units": appointment_units,
        "authorization_consumed_units": 0,
        "authorization_consumed_at": "",
        "authorization_remaining_after": "",
        "session_status": "Scheduled",
        "billing_status": "Not Ready",
        "note_status": "Draft",
        "document_status": "Unlocked",
        "claim_status": "Draft",
        "actual_start_at": "",
        "actual_end_at": "",
        "confirmed_at": "",
        "completed_at": "",
        "cancelled_at": "",
        "locked_at": "",
        "cancellation_reason": "",
        "no_show_reason": "",
        "claim_id": "",
        "claim_batch_id": "",
        "claim_generated_at": "",
        "created_by_username": str(payload.get("created_by_username", "")),
        "created_by_name": str(payload.get("created_by_name", "")),
        "created_at": datetime.now().strftime("%m/%d/%Y %H:%M"),
        "updated_at": datetime.now().isoformat(),
    }
    items = _load_list(ABA_APPOINTMENTS_FILE)
    items.insert(0, record)
    _save_appointment_records(items)
    return {
        **_appointment_record_with_derived_fields(record),
        "billing_code": appointment.billing_code.replace("CPT-", ""),
        "units": appointment_units,
        "estimated_total": appointment.unit_rate * appointment_units,
    }


def list_aba_appointments(provider_contract_ids: set[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    for item in _load_appointment_records(provider_contract_ids):
        enriched = _appointment_record_with_derived_fields(item)
        if str(enriched.get("start_at", "")).strip():
            rows.append(enriched)
    return sorted(rows, key=lambda item: item.get("start_at", ""), reverse=True)


def update_aba_session_event(
    *,
    action: str,
    appointment_id: str,
    actor_username: str,
    actor_name: str,
    actual_start_time: str = "",
    actual_end_time: str = "",
    reason: str = "",
    provider_contract_ids: set[str] | None = None,
) -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    clean_appointment_id = str(appointment_id or "").strip()
    if not clean_appointment_id:
        raise ValueError("Selecciona una sesion ABA.")
    items = _load_list(ABA_APPOINTMENTS_FILE)
    current_agency_id = get_current_agency_id()
    match_index = next(
        (
            index
            for index, item in enumerate(items)
            if str(item.get("appointment_id", "")).strip() == clean_appointment_id
            and (
                not current_agency_id
                or str(item.get("agency_id", "")).strip() in {"", current_agency_id}
            )
            and (
                provider_contract_ids is None
                or str(item.get("provider_contract_id", "")).strip() in provider_contract_ids
            )
        ),
        None,
    )
    if match_index is None:
        raise ValueError("No encontre esa sesion ABA.")
    record = dict(items[match_index])
    session_date = parse_user_date(str(_appointment_record_with_derived_fields(record).get("appointment_date", "")))
    scheduled_start = datetime.fromisoformat(str(record.get("start_at", "")))
    scheduled_end = datetime.fromisoformat(str(record.get("end_at", "")))
    actual_start_value = str(actual_start_time or "").strip()
    actual_end_value = str(actual_end_time or "").strip()
    if actual_start_value:
        actual_start_at = datetime.strptime(
            f"{format_user_date(session_date)} {actual_start_value}",
            "%m/%d/%Y %H:%M",
        )
        record["actual_start_at"] = actual_start_at.isoformat()
    if actual_end_value:
        actual_end_at = datetime.strptime(
            f"{format_user_date(session_date)} {actual_end_value}",
            "%m/%d/%Y %H:%M",
        )
        record["actual_end_at"] = actual_end_at.isoformat()
    if clean_action == "confirm":
        record["session_status"] = "Confirmed"
        record["confirmed_at"] = datetime.now().isoformat()
    elif clean_action == "start":
        record["session_status"] = "In Progress"
        if not str(record.get("actual_start_at", "")).strip():
            record["actual_start_at"] = scheduled_start.isoformat()
    elif clean_action == "complete":
        record["session_status"] = "Completed"
        if not str(record.get("actual_start_at", "")).strip():
            record["actual_start_at"] = scheduled_start.isoformat()
        if not str(record.get("actual_end_at", "")).strip():
            record["actual_end_at"] = scheduled_end.isoformat()
        record["completed_at"] = datetime.now().isoformat()
    elif clean_action == "cancel":
        if not reason.strip():
            raise ValueError("Escribe el motivo de cancelacion.")
        record["session_status"] = "Cancelled"
        record["cancelled_at"] = datetime.now().isoformat()
        record["cancellation_reason"] = reason.strip()
    elif clean_action == "no_show":
        if not reason.strip():
            raise ValueError("Escribe el motivo del no show.")
        record["session_status"] = "No Show"
        record["cancelled_at"] = datetime.now().isoformat()
        record["no_show_reason"] = reason.strip()
    elif clean_action == "reopen":
        record["session_status"] = "Confirmed"
        record["completed_at"] = ""
        record["cancelled_at"] = ""
        record["cancellation_reason"] = ""
        record["no_show_reason"] = ""
    else:
        raise ValueError("Accion de sesion no valida.")
    record["last_actor_username"] = actor_username
    record["last_actor_name"] = actor_name
    record["updated_at"] = datetime.now().isoformat()
    items[match_index] = record
    _save_appointment_records(items)
    detail = get_aba_appointment_detail(clean_appointment_id, provider_contract_ids)
    if detail is None:
        raise ValueError("Actualice la sesion, pero no pude recargarla.")
    return detail


def attach_claim_to_aba_sessions(
    *,
    session_ids: list[str],
    claim_id: str,
    batch_id: str = "",
) -> int:
    clean_session_ids = {str(item or "").strip() for item in session_ids if str(item or "").strip()}
    if not clean_session_ids or not str(claim_id or "").strip():
        return 0
    items = _load_list(ABA_APPOINTMENTS_FILE)
    current_agency_id = get_current_agency_id()
    updated = 0
    for index, item in enumerate(items):
        appointment_id = str(item.get("appointment_id", "")).strip()
        if appointment_id not in clean_session_ids:
            continue
        if current_agency_id and str(item.get("agency_id", "")).strip() not in {"", current_agency_id}:
            continue
        record = dict(item)
        record["claim_id"] = str(claim_id).strip()
        record["claim_batch_id"] = str(batch_id or "").strip()
        record["claim_status"] = "Ready for Submission"
        record["billing_status"] = "Included in Claim"
        record["session_status"] = "Billed"
        record["claim_generated_at"] = datetime.now().isoformat()
        record["updated_at"] = datetime.now().isoformat()
        items[index] = record
        updated += 1
    if updated:
        _save_appointment_records(items)
    return updated


def _all_scheduler_logs(scheduler: Scheduler, provider_contract_ids: set[str] | None = None) -> list[ServiceLog]:
    week_starts = {
        appointment.start_at.date() - timedelta(days=appointment.start_at.date().weekday())
        for appointment in scheduler.appointments
    }
    collected: dict[str, ServiceLog] = {}
    for week_start in sorted(week_starts, reverse=True):
        for log in scheduler.get_weekly_service_logs(week_of=week_start):
            if provider_contract_ids is not None and log.provider_id not in provider_contract_ids:
                continue
            collected[log.id] = log
    return sorted(
        collected.values(),
        key=lambda item: (item.week_start, item.provider_name.lower(), item.client_name.lower(), item.document_type.value.lower()),
        reverse=True,
    )


def _workflow_status(log: ServiceLog) -> str:
    if log.is_closed:
        return "Closed"
    if log.rejected_at is not None:
        return "Rejected"
    if log.is_reviewed:
        return "Reviewed"
    return "Draft"


def _deadline_status_label(log: ServiceLog) -> str:
    labels = {
        "late": "Late",
        "due_soon": "Due soon",
        "on_time": "On time",
        "no_entries": "No entries",
    }
    return labels.get(log.deadline_status(), "On time")


def list_aba_service_logs(provider_contract_ids: set[str] | None = None) -> list[dict[str, Any]]:
    scheduler = _build_scheduler(provider_contract_ids)
    rows = []
    for log in _all_scheduler_logs(scheduler, provider_contract_ids):
        rows.append(
            {
                "log_id": log.id,
                "provider_contract_id": log.provider_id,
                "provider_name": log.provider_name,
                "client_id": log.client_id,
                "client_name": log.client_name,
                "document_type": log.document_type.value,
                "week_start": log.week_start.strftime("%m/%d/%Y"),
                "week_end": log.week_end.strftime("%m/%d/%Y"),
                "total_hours": log.total_hours,
                "total_units": log.total_units,
                "total_amount": log.total_amount,
                "latest_note_due_at": log.latest_note_due_at.strftime("%m/%d/%Y %H:%M") if log.latest_note_due_at else "",
                "deadline_status": _deadline_status_label(log),
                "workflow_status": _workflow_status(log),
                "reviewed_by": log.reviewed_by,
                "closed_by": log.closed_by,
                "rejected_by": log.rejected_by,
                "reopen_reason": log.reopen_reason,
            }
        )
    return rows


def get_aba_service_log_detail(log_id: str, provider_contract_ids: set[str] | None = None) -> dict[str, Any] | None:
    clean_log_id = str(log_id or "").strip()
    if not clean_log_id:
        return None
    scheduler = _build_scheduler(provider_contract_ids)
    selected = next((item for item in _all_scheduler_logs(scheduler, provider_contract_ids) if item.id == clean_log_id), None)
    if selected is None:
        return None
    log_agency_id = next(
        (
            str(item.get("agency_id", "")).strip()
            for item in _load_list(ABA_APPOINTMENTS_FILE)
            if str(item.get("service_log_id", "")).strip() == clean_log_id
            and str(item.get("agency_id", "")).strip()
        ),
        get_current_agency_id(),
    )
    preview_title, preview_body = _render_log_preview(selected)
    return {
        "log_id": selected.id,
        "agency_id": log_agency_id,
        "provider_contract_id": selected.provider_id,
        "provider_name": selected.provider_name,
        "client_name": selected.client_name,
        "document_type": selected.document_type.value,
        "week_start": selected.week_start.strftime("%m/%d/%Y"),
        "week_end": selected.week_end.strftime("%m/%d/%Y"),
        "deadline_status": _deadline_status_label(selected),
        "workflow_status": _workflow_status(selected),
        "latest_note_due_at": selected.latest_note_due_at.strftime("%m/%d/%Y %H:%M") if selected.latest_note_due_at else "",
        "total_hours": selected.total_hours,
        "total_units": selected.total_units,
        "total_amount": selected.total_amount,
        "remaining_authorized_units": _format_remaining_units(selected.remaining_authorized_units),
        "reviewed_by": selected.reviewed_by,
        "reviewed_at": selected.reviewed_at.strftime("%m/%d/%Y %H:%M") if selected.reviewed_at else "",
        "closed_by": selected.closed_by,
        "closed_at": selected.closed_at.strftime("%m/%d/%Y %H:%M") if selected.closed_at else "",
        "rejected_by": selected.rejected_by,
        "rejected_at": selected.rejected_at.strftime("%m/%d/%Y %H:%M") if selected.rejected_at else "",
        "rejected_reason": selected.rejected_reason,
        "reopened_by": selected.reopened_by,
        "reopened_at": selected.reopened_at.strftime("%m/%d/%Y %H:%M") if selected.reopened_at else "",
        "reopen_reason": selected.reopen_reason,
        "caregiver_signature": selected.caregiver_signature,
        "provider_signature": selected.provider_signature,
        "preview_title": preview_title,
        "preview_body": preview_body,
    }


def update_aba_service_log_workflow(
    *,
    action: str,
    log_id: str,
    supervisor_name: str,
    reason: str = "",
    caregiver_signature: str = "",
    provider_signature: str = "",
    provider_contract_ids: set[str] | None = None,
) -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    clean_log_id = str(log_id or "").strip()
    clean_supervisor = str(supervisor_name or "").strip()
    if not clean_log_id:
        raise ValueError("Selecciona una nota semanal.")
    if not clean_supervisor:
        raise ValueError("Escribe el nombre del supervisor.")

    scheduler = _build_scheduler(provider_contract_ids)
    log = next((item for item in _all_scheduler_logs(scheduler, provider_contract_ids) if item.id == clean_log_id), None)
    if log is None:
        raise ValueError("No encontre esa nota ABA.")

    clean_caregiver_signature = _drawn_signature_value(caregiver_signature)
    clean_provider_signature = _drawn_signature_value(provider_signature)
    related_items = _load_list(ABA_APPOINTMENTS_FILE)
    current_agency_id = get_current_agency_id()
    related_appointments: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(related_items):
        if str(item.get("service_log_id", "")).strip() != clean_log_id:
            continue
        if current_agency_id and str(item.get("agency_id", "")).strip() not in {"", current_agency_id}:
            continue
        if provider_contract_ids is not None and str(item.get("provider_contract_id", "")).strip() not in provider_contract_ids:
            continue
        related_appointments.append((index, dict(item)))

    if clean_action == "close":
        if not clean_caregiver_signature and not bool(str((scheduler.note_states.get(clean_log_id) or NoteWorkflowState()).caregiver_signature).strip()):
            raise ValueError("La nota necesita firma del caregiver antes de cerrarse.")
        if not clean_provider_signature and not bool(str((scheduler.note_states.get(clean_log_id) or NoteWorkflowState()).provider_signature).strip()):
            raise ValueError("La nota necesita firma del provider antes de cerrarse.")
        invalid_sessions = []
        for _, item in related_appointments:
            session_status = str(item.get("session_status", "")).strip()
            if session_status in {"Completed", "Ready for Billing", "Billing Hold", "Locked", "Billed", "Submitted"}:
                continue
            try:
                end_at = datetime.fromisoformat(str(item.get("end_at", "")))
            except ValueError:
                invalid_sessions.append(str(item.get("appointment_id", "")).strip())
                continue
            if end_at <= datetime.now():
                continue
            invalid_sessions.append(str(item.get("appointment_id", "")).strip())
        if invalid_sessions:
            raise ValueError("Solo puedes cerrar notas de sesiones ya completadas.")

    if clean_action == "review":
        scheduler.review_service_log(
            log_id=clean_log_id,
            supervisor_name=clean_supervisor,
            provider_signature=clean_provider_signature,
        )
    elif clean_action == "close":
        scheduler.close_service_log(
            log_id=clean_log_id,
            supervisor_name=clean_supervisor,
            caregiver_signature=clean_caregiver_signature,
            provider_signature=clean_provider_signature,
        )
    elif clean_action == "reject":
        scheduler.reject_service_log(log_id=clean_log_id, supervisor_name=clean_supervisor, reason=reason)
    elif clean_action == "reopen":
        scheduler.reopen_service_log(log_id=clean_log_id, supervisor_name=clean_supervisor, reason=reason)
    else:
        raise ValueError("Accion ABA no valida.")

    state = scheduler.note_states.get(clean_log_id)
    if state is None:
        raise ValueError("No pude actualizar el workflow de la nota.")

    _upsert_note_state_record(clean_log_id, log.provider_id, state)
    if clean_action in {"review", "reject", "close", "reopen"} and related_appointments:
        clients_by_id = {
            str(item.get("client_id", "")).strip(): item
            for item in list_clients()
            if str(item.get("client_id", "")).strip()
        }
        for index, appointment_record in related_appointments:
            client_record = clients_by_id.get(str(appointment_record.get("client_id", "")).strip(), {})
            if clean_action == "review":
                appointment_record["billing_status"] = "Not Ready"
                appointment_record["note_status"] = "Supervisor Review"
                appointment_record["document_status"] = "Ready for Review"
            elif clean_action == "reject":
                appointment_record["billing_status"] = "Validation Error"
                appointment_record["note_status"] = "Draft"
                appointment_record["document_status"] = "Rejected"
                appointment_record["session_status"] = "Completed"
                appointment_record["locked_at"] = ""
            elif clean_action == "close":
                consumption = _consume_authorization_for_appointment(appointment_record, client_record)
                appointment_record["billing_status"] = "Ready to Bill"
                appointment_record["note_status"] = "Locked"
                appointment_record["document_status"] = "Locked"
                appointment_record["session_status"] = "Locked"
                appointment_record["locked_at"] = datetime.now().isoformat()
                appointment_record["authorization_id"] = str(consumption.get("authorization_id", "")).strip()
                appointment_record["authorization_number"] = str(consumption.get("authorization_number", "")).strip()
            else:
                _release_authorization_for_appointment(appointment_record)
                appointment_record["billing_status"] = "Not Ready"
                appointment_record["note_status"] = "Completed"
                appointment_record["document_status"] = "Ready for Review"
                appointment_record["session_status"] = "Completed"
                appointment_record["locked_at"] = ""
            appointment_record["updated_at"] = datetime.now().isoformat()
            related_items[index] = appointment_record
        _save_appointment_records(related_items)
    detail = get_aba_service_log_detail(clean_log_id, provider_contract_ids)
    if detail is None:
        raise ValueError("Actualice la nota, pero no pude recargar el detalle.")
    return detail


def _render_log_preview(log: ServiceLog) -> tuple[str, str]:
    lines = _render_service_log(log) if log.document_type in {DocumentType.ANALYST_SERVICE_LOG, DocumentType.RBT_SERVICE_LOG} else _render_form_note(log)
    return (log.document_type.value, "\n".join(lines))


def _render_form_note(log: ServiceLog) -> list[str]:
    entry = log.entries[0] if log.entries else None
    title_map = {
        DocumentType.APPOINTMENT_NOTE: "APPOINTMENT NOTE",
        DocumentType.ASSESSMENT: "ASSESSMENT",
        DocumentType.REASSESSMENT: "REASSESSMENT",
        DocumentType.SUPERVISION_LOG: "SUPERVISION NOTE",
        DocumentType.SUPERVISION_SERVICE_LOG: "SUPERVISION SERVICE NOTE",
    }
    summary_map = {
        DocumentType.APPOINTMENT_NOTE: "Session Summary",
        DocumentType.ASSESSMENT: "Assessment Summary",
        DocumentType.REASSESSMENT: "Reassessment Summary",
        DocumentType.SUPERVISION_LOG: "Supervision Summary",
        DocumentType.SUPERVISION_SERVICE_LOG: "Supervision Service Summary",
    }
    return [
        title_map.get(log.document_type, log.document_type.value.upper()),
        f"Week reference: {log.week_start.strftime('%m/%d/%Y')} to {log.week_end.strftime('%m/%d/%Y')}",
        "",
        "Recipient Details",
        f"Name: {log.client_name}",
        "Date of Birth: ____________________",
        f"Insurance #: {log.insurance_id}",
        f"Diagnosis: {log.diagnoses}",
        "",
        "Provider Details",
        f"Provider: {log.provider_name}",
        f"Credentials: {log.provider_credentials}",
        f"PA #: {log.pa_number}",
        f"PA Dates: {log.pa_start_date} to {log.pa_end_date}",
        f"Approved Units: {log.approved_units or '-'}",
        "",
        "Appointment Details",
        f"Date: {entry.session_date.strftime('%m/%d/%Y') if entry else log.week_start.strftime('%m/%d/%Y')}",
        f"Time In: {entry.start_time if entry else '____________________'}",
        f"Time Out: {entry.end_time if entry else '____________________'}",
        f"Place of Service: {entry.place_of_service if entry else 'Home (12)'}",
        f"Caregiver: {entry.caregiver_name if entry and entry.caregiver_name else '____________________'}",
        "",
        summary_map.get(log.document_type, "Session Summary"),
        "Presenting concerns: ____________________________________________",
        "Interventions/activities: _______________________________________",
        f"Client response: {log.notes or '____________________________________________'}",
        "Barriers/safety concerns: _______________________________________",
        "Plan for next session: __________________________________________",
        "",
        "Signatures",
        f"Provider Signature: {_signature_preview_text(log.provider_signature) or '____________________'}",
        f"Provider Signature Date: {log.provider_signature_date or '____________________'}",
        f"Supervisor Signature: {log.reviewed_by or '____________________'}",
        f"Caregiver Signature: {_signature_preview_text(log.caregiver_signature) or '____________________'}",
        f"Caregiver Signature Date: {log.caregiver_signed_at.strftime('%Y-%m-%d') if log.caregiver_signed_at else '____________________'}",
        "",
        "Closure Rule",
        "This note must be reviewed by a supervisor and then closed before it becomes read-only.",
    ]


def _render_service_log(log: ServiceLog) -> list[str]:
    title = "ANALYST SERVICE LOG" if log.document_type == DocumentType.ANALYST_SERVICE_LOG else "RBT SERVICE LOG"
    note_deadline_label = log.latest_note_due_at.strftime("%m/%d/%Y") if log.latest_note_due_at else "-"
    supervisor_signature = log.closed_by or log.reviewed_by or "____________________"
    supervisor_signature_date = (
        log.closed_at.strftime("%m/%d/%Y")
        if log.closed_at
        else log.reviewed_at.strftime("%m/%d/%Y")
        if log.reviewed_at
        else "____________________"
    )
    caregiver_signature_value = _document_signature_value(log.caregiver_signature)
    provider_signature_value = _document_signature_value(log.provider_signature)
    lines = [
        title,
        f"Week Range: {log.week_start.strftime('%m/%d/%Y')} to {log.week_end.strftime('%m/%d/%Y')}",
        "",
        "Case Overview",
        f"Recipient: {log.client_name}",
        f"Insurance: {log.insurance_id}",
        f"Diagnosis: {log.diagnoses}",
        f"Provider: {log.provider_name}",
        f"Credentials: {log.provider_credentials}",
        f"PA Number: {log.pa_number}",
        f"PA Start Date: {log.pa_start_date}",
        f"PA End Date: {log.pa_end_date}",
        "",
        "Authorization Summary",
        f"Approved Units: {log.approved_units or '-'}",
        f"Used Units: {log.total_units}",
        f"Remaining Units: {_format_remaining_units(log.remaining_authorized_units)}",
        f"Total Days: {log.total_days}",
        f"Note Deadline: {note_deadline_label}",
        f"Status: {_workflow_status(log)} / {_deadline_status_label(log)}",
        f"Workflow: {_workflow_status(log)}",
        "",
        "Session Log Table",
        "DATE | TIME IN | TIME OUT | HOURS | UNITS | CPT CODE | PLACE OF SERVICE | CAREGIVER / CLIENT | SIGNATURE STATUS | NOTE DEADLINE",
    ]
    for entry in log.entries:
        lines.append(
            f"{entry.session_date.strftime('%m/%d/%Y')} | {entry.start_time} | {entry.end_time} | "
            f"{entry.hours:.2f} | {entry.units} | {entry.billing_code.replace('CPT-', '')} | {entry.place_of_service} | "
            f"{entry.caregiver_name or log.client_name or '-'} | {'Signed' if str(entry.caregiver_signature or '').strip() else 'Pending'} | "
            f"{entry.note_due_at.strftime('%m/%d/%Y')}"
        )
    lines.extend(
        [
            "",
            "Totals",
            f"Total Hours: {log.total_hours:.2f}",
            f"Total Units: {log.total_units}",
            f"Total Billed: ${log.total_amount:.2f}",
            "",
            "Review Status",
            f"Reviewed by: {log.reviewed_by or '-'}",
            f"Reviewed at: {log.reviewed_at.strftime('%m/%d/%Y %H:%M') if log.reviewed_at else '-'}",
            f"Closed by: {log.closed_by or '-'}",
            f"Closed at: {log.closed_at.strftime('%m/%d/%Y %H:%M') if log.closed_at else '-'}",
            f"Rejected by: {log.rejected_by or '-'}",
            f"Rejected at: {log.rejected_at.strftime('%m/%d/%Y %H:%M') if log.rejected_at else '-'}",
            f"Reject reason: {log.rejected_reason or '-'}",
            f"Reopened by: {log.reopened_by or '-'}",
            f"Reopened at: {log.reopened_at.strftime('%m/%d/%Y %H:%M') if log.reopened_at else '-'}",
            "",
            "Signatures",
            f"Caregiver Signature: {caregiver_signature_value or '____________________'}",
            f"Caregiver Date: {log.caregiver_signed_at.strftime('%m/%d/%Y') if log.caregiver_signed_at else '____________________'}",
            f"Provider Signature: {provider_signature_value or '____________________'}",
            f"Provider Date: {log.provider_signature_date or '____________________'}",
            f"HR / Supervisor Signature: {supervisor_signature}",
            f"HR / Supervisor Date: {supervisor_signature_date}",
        ]
    )
    return lines
