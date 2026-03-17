from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from billing_app.models import Address, Claim, InsurancePolicy, Patient, Provider, ServiceLine
from billing_app.services.aba_notes_portal import get_aba_service_log_detail, list_aba_appointments
from billing_app.services.date_utils import parse_user_date, today_user_date
from billing_app.services.local_store import (
    add_claim_record,
    get_payer_configured_unit_price,
    list_authorizations,
    list_claims,
    list_clients,
    list_provider_contracts,
)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalize_cpt_code(value: Any) -> str:
    return str(value or "").strip().upper().replace("CPT-", "").replace("CPT ", "")


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    return int(round(_safe_float(value)))


def _iso_to_datetime(value: Any) -> datetime | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        return datetime.fromisoformat(clean)
    except ValueError:
        return None


def _user_date(value: Any) -> date | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        return parse_user_date(clean)
    except ValueError:
        return None


def _split_full_name(value: Any) -> tuple[str, str]:
    parts = [piece for piece in str(value or "").strip().split() if piece]
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (" ".join(parts[:-1]), parts[-1])


def _session_engine_status(appointment: dict[str, Any], now: datetime) -> str:
    raw_status = str(appointment.get("session_status", "")).strip()
    if raw_status:
        return raw_status
    start_at = _iso_to_datetime(appointment.get("start_at"))
    end_at = _iso_to_datetime(appointment.get("end_at"))
    if start_at and now < start_at:
        return "Scheduled"
    if start_at and end_at and start_at <= now < end_at:
        return "In Progress"
    if end_at and end_at <= now:
        return "Completed"
    return "Scheduled"


def _provider_active(provider_record: dict[str, Any]) -> bool:
    return str(provider_record.get("contract_stage", "")).strip().upper() in {"APPROVED", "ACTIVE"}


def _provider_compliant(provider_record: dict[str, Any]) -> bool:
    return bool(provider_record.get("documents_complete")) and int(provider_record.get("expired_documents", 0) or 0) == 0


def _note_status_label(log_detail: dict[str, Any] | None, appointment: dict[str, Any]) -> str:
    workflow_status = str((log_detail or {}).get("workflow_status", "")).strip().lower()
    if workflow_status == "closed":
        return "Approved"
    if workflow_status == "reviewed":
        return "Under Review"
    if workflow_status == "rejected":
        return "Rejected"
    has_content = any(
        [
            str(appointment.get("session_note", "")).strip(),
            str((log_detail or {}).get("provider_signature", "")).strip(),
            str((log_detail or {}).get("caregiver_signature", "")).strip(),
            str(appointment.get("provider_signature", "")).strip(),
            str(appointment.get("caregiver_signature", "")).strip(),
        ]
    )
    return "Submitted" if has_content else "Draft"


def _claim_status_label(claim_record: dict[str, Any] | None) -> str:
    if not claim_record:
        return ""
    status = str(claim_record.get("status", "")).strip().lower()
    transmission_status = str(claim_record.get("transmission_status", "")).strip().lower()
    if status == "paid":
        return "Paid"
    if status == "partial":
        return "Partially Paid"
    if status == "denied":
        return "Denied"
    if transmission_status == "transmitted":
        return "Submitted"
    if status == "draft":
        return "Ready to Submit"
    return status.replace("_", " ").title() or "Ready to Submit"


def _match_authorization(
    appointment: dict[str, Any],
    client_record: dict[str, Any],
    authorization_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    client_id = str(client_record.get("client_id", "")).strip()
    member_id = str(client_record.get("member_id", "")).strip()
    service_date = _user_date(appointment.get("appointment_date"))
    service_code = _normalize_cpt_code(appointment.get("service_code", ""))
    if not service_date or not service_code:
        return None

    matches: list[dict[str, Any]] = []
    for item in authorization_records:
        auth_client_id = str(item.get("client_id", "")).strip()
        auth_member_id = str(item.get("patient_member_id", "")).strip()
        if client_id and auth_client_id and auth_client_id != client_id:
            continue
        if not auth_client_id and member_id and auth_member_id != member_id:
            continue
        if _normalize_cpt_code(item.get("cpt_code", "")) != service_code:
            continue
        start_date = _user_date(item.get("start_date"))
        end_date = _user_date(item.get("end_date"))
        if not start_date or not end_date or service_date < start_date or service_date > end_date:
            continue
        matches.append(item)

    if not matches:
        return None
    matches.sort(
        key=lambda item: (_user_date(item.get("end_date")) or date.min, _safe_float(item.get("remaining_units", 0))),
        reverse=True,
    )
    return matches[0]


def _match_claim(
    appointment: dict[str, Any],
    client_record: dict[str, Any],
    claim_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    appointment_id = str(appointment.get("appointment_id", "")).strip() or str(appointment.get("session_id", "")).strip()
    member_id = str(client_record.get("member_id", "")).strip()
    payer_name = _normalize_text(client_record.get("payer_name", ""))
    service_date = str(appointment.get("appointment_date", "")).strip()
    service_code = _normalize_cpt_code(appointment.get("service_code", ""))
    for claim in claim_records:
        session_ids = claim.get("session_ids", [])
        if isinstance(session_ids, list) and appointment_id and appointment_id in {str(item).strip() for item in session_ids}:
            return claim
        if member_id and str(claim.get("member_id", "")).strip() != member_id:
            continue
        if payer_name and _normalize_text(claim.get("payer_name", "")) != payer_name:
            continue
        if service_date and str(claim.get("service_date", "")).strip() != service_date:
            continue
        service_lines = claim.get("service_lines", [])
        if not isinstance(service_lines, list):
            service_lines = []
        if service_code and not any(_normalize_cpt_code(line.get("procedure_code", "")) == service_code for line in service_lines):
            continue
        return claim
    return None


def _validation_result(validation_type: str, passed: bool, code: str, message: str, *, warning: bool = False) -> dict[str, str]:
    status = "warning" if warning and not passed else ("pass" if passed else "fail")
    return {
        "validation_type": validation_type,
        "status": status,
        "code": code,
        "message": message,
    }


def _build_session_validations(
    appointment: dict[str, Any],
    client_record: dict[str, Any],
    provider_record: dict[str, Any],
    authorization_record: dict[str, Any] | None,
    log_detail: dict[str, Any] | None,
) -> list[dict[str, str]]:
    end_at = _iso_to_datetime(appointment.get("actual_end_at")) or _iso_to_datetime(appointment.get("end_at"))
    session_engine_status = _session_engine_status(appointment, datetime.now())
    note_status = _note_status_label(log_detail, appointment)
    units = _safe_int(appointment.get("units", 0))
    diagnosis_text = str(client_record.get("diagnosis", "")).strip() or str(client_record.get("notes", "")).strip()
    authorization_consumed = bool(str(appointment.get("authorization_consumed_at", "")).strip())
    authorization_linked = bool(authorization_record is not None or str(appointment.get("authorization_id", "")).strip())
    authorization_has_units = authorization_consumed or (
        authorization_record is not None and _safe_float(authorization_record.get("remaining_units", 0)) >= units
    )
    return [
        _validation_result(
            "session_complete",
            session_engine_status in {"Completed", "Locked", "Billed", "Submitted", "Paid"} and bool(end_at and end_at <= datetime.now()),
            "SESSION_NOT_COMPLETE",
            "La sesion todavia no termina y no puede pasar a billing.",
        ),
        _validation_result("session_note", note_status == "Approved", "MISSING_APPROVED_NOTE", "La sesion necesita una nota cerrada/aprobada antes de billing."),
        _validation_result(
            "provider_signature",
            bool(str((log_detail or {}).get("provider_signature", "") or appointment.get("provider_signature", "")).strip()),
            "MISSING_PROVIDER_SIGNATURE",
            "Falta la firma del provider en la nota o en la sesion.",
        ),
        _validation_result("client_active", bool(client_record.get("active", True)), "CLIENT_INACTIVE", "El cliente esta inactivo."),
        _validation_result("provider_active", _provider_active(provider_record), "PROVIDER_NOT_ACTIVE", "El provider todavia no esta activo para operar."),
        _validation_result("provider_compliance", _provider_compliant(provider_record), "PROVIDER_NON_COMPLIANT", "El provider tiene documentos pendientes o vencidos."),
        _validation_result(
            "payer_data",
            bool(str(client_record.get("payer_name", "")).strip() and str(client_record.get("member_id", "")).strip()),
            "MISSING_PAYER_DATA",
            "Faltan payer o member ID en el expediente del cliente.",
        ),
        _validation_result("authorization_link", authorization_linked, "MISSING_AUTHORIZATION", "No hay autorizacion activa para este CPT y fecha de servicio."),
        _validation_result(
            "authorization_units",
            authorization_has_units,
            "AUTH_UNITS_EXCEEDED",
            "Las units de la sesion exceden lo restante en la autorizacion.",
        ),
        _validation_result("rendering_provider_npi", bool(str(provider_record.get("provider_npi", "")).strip()), "MISSING_RENDERING_PROVIDER", "Falta el NPI del rendering provider."),
        _validation_result(
            "diagnosis",
            bool(diagnosis_text),
            "MISSING_DIAGNOSIS",
            "El expediente del cliente no tiene diagnostico o nota clinica cargada para el claim.",
            warning=True,
        ),
        _validation_result(
            "credentialing",
            "enrolled" in _normalize_text(provider_record.get("credentialing_status_summary", "")),
            "PROVIDER_NOT_CREDENTIALED",
            "El provider no aparece como enrolled con payer en credentialing.",
            warning=True,
        ),
    ]


def _billing_hold_reason(results: list[dict[str, str]]) -> str:
    return "; ".join(item["message"] for item in results if item.get("status") == "fail")[:280]


def _validation_counts(results: list[dict[str, str]]) -> dict[str, int]:
    summary = {"pass": 0, "warning": 0, "fail": 0}
    for item in results:
        status = str(item.get("status", "")).strip().lower()
        if status in summary:
            summary[status] += 1
    return summary


def _billing_queue_status(can_bill: bool, claim_record: dict[str, Any] | None, claim_status_label: str) -> str:
    if claim_record is not None:
        if claim_status_label == "Paid":
            return "paid"
        if claim_status_label == "Denied":
            return "denied"
        if claim_status_label in {"Partially Paid", "Submitted"}:
            return "submitted"
        return "billed"
    return "ready" if can_bill else "hold"


def _event_type_label(appointment: dict[str, Any]) -> str:
    context = _normalize_text(appointment.get("service_context", ""))
    cpt_code = _normalize_cpt_code(appointment.get("service_code", ""))
    if "assessment" in context or cpt_code == "97151":
        return "ASSESSMENT"
    if "caregiver" in context or cpt_code == "97156":
        return "CAREGIVER_TRAINING"
    if "supervision" in context or cpt_code == "97155":
        return "SUPERVISION"
    return "APPOINTMENT"


def _calendar_status_label(
    appointment: dict[str, Any],
    note_status: str,
    claim_status_label: str,
    now: datetime,
) -> str:
    engine_status = _session_engine_status(appointment, now).lower()
    if claim_status_label == "Paid" or note_status == "Approved":
        return "locked"
    if engine_status == "cancelled":
        return "cancelled"
    if engine_status == "no show":
        return "missed"
    if engine_status == "completed":
        return "completed"
    if engine_status == "in progress":
        return "in_progress"
    if engine_status == "confirmed":
        return "confirmed"
    return "scheduled"


def _clinical_document_status(note_status: str) -> str:
    clean = str(note_status or "").strip().lower()
    if clean == "approved":
        return "locked"
    if clean == "under review":
        return "reviewed"
    if clean == "submitted":
        return "signed"
    return "draft"


def _billing_validation_status(results: list[dict[str, str]]) -> str:
    counts = _validation_counts(results)
    if counts["fail"]:
        return "error"
    if counts["warning"]:
        return "warning"
    return "ready"


def _period_window(service_date: date) -> tuple[date, date]:
    period_start = service_date - timedelta(days=service_date.weekday())
    period_end = period_start + timedelta(days=6)
    return (period_start, period_end)


def _session_status_label(
    appointment: dict[str, Any],
    note_status: str,
    can_bill: bool,
    claim_record: dict[str, Any] | None,
    claim_status_label: str,
    now: datetime,
) -> str:
    engine_status = _session_engine_status(appointment, now)
    if claim_record is not None:
        if claim_status_label == "Paid":
            return "Paid"
        if claim_status_label == "Denied":
            return "Denied"
        if claim_status_label in {"Partially Paid", "Submitted"}:
            return "Submitted to Payer"
        return "Billed"
    if engine_status in {"Cancelled", "No Show"}:
        return engine_status
    if engine_status in {"Scheduled", "Confirmed", "In Progress"}:
        return engine_status
    if note_status == "Rejected":
        return "Note Rejected"
    if note_status == "Draft":
        return "Pending Note"
    if note_status == "Submitted":
        return "Note Submitted"
    if note_status == "Under Review":
        return "Under Clinical Review"
    return "Ready for Billing" if can_bill else "Billing Hold"


def _session_progress_percent(session_status: str, claim_status_label: str) -> int:
    if claim_status_label == "Paid":
        return 100
    if claim_status_label == "Partially Paid":
        return 95
    if claim_status_label == "Submitted":
        return 88
    if session_status in {"Cancelled", "No Show", "Denied"}:
        return 100
    if session_status == "Billed":
        return 82
    if session_status == "Ready for Billing":
        return 72
    if session_status == "Under Clinical Review":
        return 56
    if session_status == "Note Submitted":
        return 44
    if session_status == "Pending Note":
        return 26
    if session_status == "Confirmed":
        return 14
    if session_status == "In Progress":
        return 18
    return 10


def _session_timeline(
    appointment: dict[str, Any],
    log_detail: dict[str, Any] | None,
    claim_record: dict[str, Any] | None,
    session_status: str,
    note_status: str,
    billing_queue_status: str,
    claim_status_label: str,
) -> list[dict[str, str]]:
    engine_status = str(appointment.get("session_status", "")).strip()
    timeline = [
        {
            "step": "Session Created",
            "status": "done",
            "at": str(appointment.get("created_at", "")),
            "owner": str(appointment.get("created_by_name", "") or appointment.get("created_by_username", "")),
            "note": "La sesion entro al scheduler ABA.",
        }
    ]
    if str(appointment.get("confirmed_at", "")).strip():
        timeline.append(
            {
                "step": "Confirmed",
                "status": "done",
                "at": str(appointment.get("confirmed_at", "")).replace("T", " "),
                "owner": str(appointment.get("last_actor_name", "") or appointment.get("provider_name", "")),
                "note": "La sesion fue confirmada dentro del calendario compartido.",
            }
        )
    if str(appointment.get("actual_start_at", "")).strip():
        timeline.append(
            {
                "step": "In Progress",
                "status": "done",
                "at": str(appointment.get("actual_start_at", "")).replace("T", " "),
                "owner": str(appointment.get("provider_name", "")),
                "note": "El provider inicio la sesion en tiempo real.",
            }
        )
    if session_status not in {"Scheduled", "Confirmed", "In Progress", "Cancelled", "No Show"}:
        timeline.append(
            {
                "step": "Completed",
                "status": "done",
                "at": str(appointment.get("actual_end_at", "") or appointment.get("completed_at", "") or appointment.get("end_at", "")).replace("T", " "),
                "owner": str(appointment.get("provider_name", "")),
                "note": "La sesion ya cerró su ventana operativa.",
            }
        )
    if engine_status in {"Cancelled", "No Show"}:
        timeline.append(
            {
                "step": engine_status,
                "status": "warning",
                "at": str(appointment.get("cancelled_at", "")).replace("T", " "),
                "owner": str(appointment.get("last_actor_name", "") or appointment.get("provider_name", "")),
                "note": str(appointment.get("cancellation_reason", "") or appointment.get("no_show_reason", "") or "La sesion no llego a completarse."),
            }
        )
    if note_status != "Draft":
        timeline.append(
            {
                "step": "Note Submitted",
                "status": "done" if note_status in {"Submitted", "Under Review", "Approved"} else "warning",
                "at": str((log_detail or {}).get("latest_note_due_at", "")),
                "owner": str(appointment.get("provider_name", "")),
                "note": f"Estado de nota: {note_status}.",
            }
        )
    if note_status in {"Under Review", "Approved", "Rejected"}:
        timeline.append(
            {
                "step": "Clinical Review",
                "status": "done" if note_status == "Approved" else ("warning" if note_status == "Rejected" else "current"),
                "at": str((log_detail or {}).get("reviewed_at", "") or (log_detail or {}).get("rejected_at", "")),
                "owner": str((log_detail or {}).get("reviewed_by", "") or (log_detail or {}).get("rejected_by", "")),
                "note": f"Workflow del log: {str((log_detail or {}).get('workflow_status', '')).strip() or note_status}.",
            }
        )
    if str(appointment.get("authorization_consumed_at", "")).strip():
        timeline.append(
            {
                "step": "Authorization Consumed",
                "status": "done",
                "at": str(appointment.get("authorization_consumed_at", "")).replace("T", " "),
                "owner": "Workflow Engine",
                "note": (
                    f"Auth {appointment.get('authorization_number', '') or '-'} desconto "
                    f"{int(float(appointment.get('authorization_consumed_units', 0) or 0))} units."
                ),
            }
        )
    timeline.append(
        {
            "step": "Billing Queue",
            "status": "done" if billing_queue_status in {"billed", "submitted", "paid"} else ("warning" if billing_queue_status == "hold" else "current"),
            "at": str(appointment.get("updated_at", "")),
            "owner": "Billing",
            "note": f"Cola: {billing_queue_status.upper()} | Session status: {session_status}.",
        }
    )
    if claim_record is not None:
        timeline.append(
            {
                "step": "Claim Generated",
                "status": "done",
                "at": str(claim_record.get("created_at", "")),
                "owner": "Billing",
                "note": f"Claim {claim_record.get('claim_id', '')} listo para {claim_status_label or 'submit'}.",
            }
        )
        if claim_status_label:
            timeline.append(
                {
                    "step": "Claim Outcome",
                    "status": "done" if claim_status_label in {"Paid", "Partially Paid"} else ("warning" if claim_status_label == "Denied" else "current"),
                    "at": str(claim_record.get("transmitted_at", "") or claim_record.get("updated_at", "")),
                    "owner": "Payer",
                    "note": f"Estatus del claim: {claim_status_label}.",
                }
            )
    return timeline


def list_operational_sessions(provider_contract_ids: set[str] | None = None, *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now()
    appointments = list_aba_appointments(provider_contract_ids)
    clients_by_id = {str(item.get("client_id", "")).strip(): item for item in list_clients() if str(item.get("client_id", "")).strip()}
    providers_by_id = {str(item.get("contract_id", "")).strip(): item for item in list_provider_contracts() if str(item.get("contract_id", "")).strip()}
    authorizations = list_authorizations()
    claims = list_claims()
    rows: list[dict[str, Any]] = []

    for appointment in appointments:
        client_record = clients_by_id.get(str(appointment.get("client_id", "")).strip(), {})
        provider_record = providers_by_id.get(str(appointment.get("provider_contract_id", "")).strip(), {})
        log_detail = get_aba_service_log_detail(str(appointment.get("service_log_id", "")), provider_contract_ids)
        authorization_record = _match_authorization(appointment, client_record, authorizations)
        claim_record = _match_claim(appointment, client_record, claims)
        note_status = _note_status_label(log_detail, appointment)
        validations = _build_session_validations(appointment, client_record, provider_record, authorization_record, log_detail)
        can_bill = not any(item.get("status") == "fail" for item in validations)
        claim_status_label = _claim_status_label(claim_record)
        billing_queue_status = _billing_queue_status(can_bill, claim_record, claim_status_label)
        session_status = _session_status_label(appointment, note_status, can_bill, claim_record, claim_status_label, now)
        calendar_status = _calendar_status_label(appointment, note_status, claim_status_label, now)
        document_status = _clinical_document_status(note_status)
        validation_status = _billing_validation_status(validations)
        session_engine_status = _session_engine_status(appointment, now)
        unit_price = get_payer_configured_unit_price(str(appointment.get("service_code", "")), client_record.get("payer_name", ""), client_record.get("payer_id", ""))
        if unit_price is None:
            unit_price = _safe_float(appointment.get("unit_rate", 0))
        units = _safe_int(appointment.get("units", 0))
        event_type = _event_type_label(appointment)
        service_date_value = _user_date(appointment.get("appointment_date"))
        period_start, period_end = _period_window(service_date_value) if service_date_value is not None else (None, None)
        rows.append(
            {
                "session_id": str(appointment.get("appointment_id", "")),
                "event_id": str(appointment.get("appointment_id", "")),
                "appointment_id": str(appointment.get("appointment_id", "")),
                "service_log_id": str(appointment.get("service_log_id", "")),
                "client_id": str(appointment.get("client_id", "")),
                "provider_contract_id": str(appointment.get("provider_contract_id", "")),
                "client_name": str(appointment.get("client_name", "")),
                "provider_name": str(appointment.get("provider_name", "")),
                "provider_type": str(provider_record.get("provider_type", appointment.get("provider_credentials", ""))),
                "event_type": event_type,
                "payer_name": str(client_record.get("payer_name", "")),
                "payer_id": str(client_record.get("payer_id", "")),
                "member_id": str(client_record.get("member_id", "")),
                "service_date": str(appointment.get("appointment_date", "")),
                "scheduled_start_time": str(appointment.get("start_time_label", "")),
                "scheduled_end_time": str(appointment.get("end_time_label", "")),
                "actual_start_time": str(appointment.get("actual_start_time_label", "") or appointment.get("scheduled_start_time", "")),
                "actual_end_time": str(appointment.get("actual_end_time_label", "") or appointment.get("scheduled_end_time", "")),
                "location": str(client_record.get("site_location", "")),
                "county_name": str(client_record.get("county_name", "")),
                "cpt_code": _normalize_cpt_code(appointment.get("service_code", "")),
                "billing_code": str(appointment.get("billing_code", "")),
                "place_of_service": str(appointment.get("place_of_service", "")),
                "units": units,
                "hours": round(units / 4.0, 2),
                "billed_amount": round(unit_price * units, 2),
                "session_status": session_status,
                "session_engine_status": session_engine_status,
                "calendar_status": calendar_status,
                "note_status": note_status,
                "clinical_document_status": document_status,
                "provider_signature_present": bool(
                    str((log_detail or {}).get("provider_signature", "") or appointment.get("provider_signature", "")).strip()
                ),
                "caregiver_signature_present": bool(
                    str((log_detail or {}).get("caregiver_signature", "") or appointment.get("caregiver_signature", "")).strip()
                ),
                "billing_queue_status": billing_queue_status,
                "billing_status": str(appointment.get("billing_status", "")).strip() or ("Ready to Bill" if can_bill else "Not Ready"),
                "billing_validation_status": validation_status,
                "claim_status": claim_status_label,
                "can_bill": can_bill,
                "billing_hold_reason": _billing_hold_reason(validations),
                "validation_results": validations,
                "validation_summary": _validation_counts(validations),
                "validation_messages": [str(item.get("message", "")).strip() for item in validations if str(item.get("message", "")).strip()],
                "authorization_id": str((authorization_record or {}).get("authorization_id", "")),
                "authorization_number": str((authorization_record or {}).get("authorization_number", "")),
                "authorization_status": str((authorization_record or {}).get("status_label", "")),
                "authorization_remaining_units": _safe_float((authorization_record or {}).get("remaining_units", 0)),
                "authorization_end_date": str((authorization_record or {}).get("end_date", "")),
                "authorization_consumed_units": _safe_float(appointment.get("authorization_consumed_units", 0)),
                "authorization_consumed_at": str(appointment.get("authorization_consumed_at", "")),
                "period_start": period_start.isoformat() if period_start is not None else "",
                "period_end": period_end.isoformat() if period_end is not None else "",
                "claim_id": str((claim_record or {}).get("claim_id", "")),
                "claim_tracking_id": str((claim_record or {}).get("tracking_id", "")),
                "claim_transmission_status": str((claim_record or {}).get("transmission_status", "")),
                "paid_amount": _safe_float((claim_record or {}).get("paid_amount", 0)),
                "balance_amount": _safe_float((claim_record or {}).get("balance_amount", 0)),
                "timeline": _session_timeline(appointment, log_detail, claim_record, session_status, note_status, billing_queue_status, claim_status_label),
                "progress_percent": _session_progress_percent(session_status, claim_status_label),
                "provider_active": _provider_active(provider_record),
                "provider_compliant": _provider_compliant(provider_record),
                "provider_npi": str(provider_record.get("provider_npi", "")),
                "credentialing_status": str(provider_record.get("credentialing_status_summary", "")),
                "client_active": bool(client_record.get("active", True)),
                "session_note": str(appointment.get("session_note", "")),
                "participants": [str(appointment.get("provider_name", "")).strip(), str(appointment.get("client_name", "")).strip()],
                "cancellation_reason": str(appointment.get("cancellation_reason", "")),
                "no_show_reason": str(appointment.get("no_show_reason", "")),
            }
        )

    rows.sort(
        key=lambda item: (_user_date(item.get("service_date")) or date.min, str(item.get("scheduled_start_time", "")), str(item.get("client_name", "")).lower()),
        reverse=True,
    )
    return rows


def get_operational_session_detail(session_id: str, provider_contract_ids: set[str] | None = None) -> dict[str, Any] | None:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return None
    return next((item for item in list_operational_sessions(provider_contract_ids) if str(item.get("session_id", "")).strip() == clean_session_id), None)


def build_operations_dashboards(sessions: list[dict[str, Any]], *, today: date | None = None) -> dict[str, dict[str, Any]]:
    today = today or parse_user_date(today_user_date())
    claims = list_claims()
    authorizations = list_authorizations()
    providers = list_provider_contracts()
    clients = list_clients()
    operations = {"sessions_scheduled_today": 0, "sessions_in_progress": 0, "sessions_completed_today": 0, "pending_notes": 0, "notes_under_review": 0, "sessions_ready_for_billing": 0, "billing_holds": 0, "claims_submitted": 0, "claims_denied": 0, "claims_paid": 0, "low_authorization_alerts": 0}
    scheduler = {"provider_availability_gaps": 0, "unassigned_clients": 0, "overlapping_sessions": 0, "expired_auth_warnings": 0, "providers_non_compliant": 0, "urgent_coverage_needs": 0}
    billing = {"ready_to_bill_count": 0, "on_hold_count": 0, "denied_claims_count": 0, "rejected_claims_count": 0, "claims_pending_follow_up": 0, "paid_today": 0, "underpayments": 0, "aging_0_30": 0, "aging_31_60": 0, "aging_61_plus": 0}
    authorization = {"active_authorizations": 0, "expiring_within_30_days": 0, "exhausted_auths": 0, "low_units_remaining": 0, "usage_percent": 0}

    for session in sessions:
        session_date = _user_date(session.get("service_date"))
        if session_date == today:
            operations["sessions_scheduled_today"] += 1
            if session.get("session_status") == "In Progress":
                operations["sessions_in_progress"] += 1
            if session.get("session_status") in {"Pending Note", "Note Submitted", "Under Clinical Review", "Ready for Billing", "Billing Hold", "Billed", "Submitted to Payer", "Denied", "Paid"}:
                operations["sessions_completed_today"] += 1
        if session.get("note_status") in {"Draft", "Rejected"}:
            operations["pending_notes"] += 1
        if session.get("note_status") == "Under Review":
            operations["notes_under_review"] += 1
        if session.get("billing_queue_status") == "ready":
            operations["sessions_ready_for_billing"] += 1
            billing["ready_to_bill_count"] += 1
        if session.get("billing_queue_status") == "hold":
            operations["billing_holds"] += 1
            billing["on_hold_count"] += 1

    for claim in claims:
        status = str(claim.get("status", "")).strip().lower()
        transmission_status = str(claim.get("transmission_status", "")).strip().lower()
        created_at = _iso_to_datetime(claim.get("created_at"))
        updated_at = _iso_to_datetime(claim.get("updated_at"))
        age_days = (today - (created_at.date() if created_at else today)).days
        if transmission_status == "transmitted":
            operations["claims_submitted"] += 1
        if status == "denied":
            operations["claims_denied"] += 1
            billing["denied_claims_count"] += 1
        if status == "paid":
            operations["claims_paid"] += 1
            if updated_at and updated_at.date() == today:
                billing["paid_today"] += 1
        if status == "partial":
            billing["underpayments"] += 1
        if status in {"pending", "partial", "denied"} or transmission_status == "transmitted":
            billing["claims_pending_follow_up"] += 1
        if transmission_status != "transmitted" and status == "draft":
            billing["rejected_claims_count"] += 1
        if age_days <= 30:
            billing["aging_0_30"] += 1
        elif age_days <= 60:
            billing["aging_31_60"] += 1
        else:
            billing["aging_61_plus"] += 1

    total_authorized_units = 0.0
    total_used_units = 0.0
    for item in authorizations:
        total_units = _safe_float(item.get("total_units", 0))
        remaining_units = _safe_float(item.get("remaining_units", 0))
        end_date = _user_date(item.get("end_date"))
        if bool(item.get("active", True)):
            authorization["active_authorizations"] += 1
        if remaining_units <= 0:
            authorization["exhausted_auths"] += 1
        elif remaining_units <= 8:
            authorization["low_units_remaining"] += 1
            operations["low_authorization_alerts"] += 1
        if end_date is not None and 0 <= (end_date - today).days <= 30:
            authorization["expiring_within_30_days"] += 1
            scheduler["expired_auth_warnings"] += 1
        total_authorized_units += total_units
        total_used_units += max(total_units - remaining_units, 0.0)
    authorization["usage_percent"] = int(round((total_used_units / total_authorized_units) * 100)) if total_authorized_units else 0

    scheduler["providers_non_compliant"] = sum(1 for item in providers if not _provider_compliant(item))
    scheduler["unassigned_clients"] = sum(1 for item in clients if not any([str(item.get("bcba_contract_id", "")).strip(), str(item.get("bcaba_contract_id", "")).strip(), str(item.get("rbt_contract_id", "")).strip()]))
    scheduler["provider_availability_gaps"] = sum(1 for item in providers if _provider_active(item) and not item.get("assigned_clients"))
    scheduler["urgent_coverage_needs"] = scheduler["unassigned_clients"] + scheduler["providers_non_compliant"]
    return {"operations": operations, "scheduler": scheduler, "billing": billing, "authorization": authorization}


def build_claim_batches(
    sessions: list[dict[str, Any]],
    *,
    include_non_ready: bool = False,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for session in sessions:
        billing_queue_status = str(session.get("billing_queue_status", "")).strip().lower()
        if not include_non_ready and billing_queue_status != "ready":
            continue
        client_id = str(session.get("client_id", "")).strip()
        provider_contract_id = str(session.get("provider_contract_id", "")).strip()
        payer_name = str(session.get("payer_name", "")).strip()
        authorization_key = str(session.get("authorization_id", "")).strip() or str(session.get("authorization_number", "")).strip()
        period_start = str(session.get("period_start", "")).strip()
        period_end = str(session.get("period_end", "")).strip()
        group_key = (client_id, provider_contract_id, payer_name, authorization_key, period_start, period_end)
        if group_key not in grouped:
            grouped[group_key] = {
                "batch_id": f"BATCH-{len(grouped) + 1:03d}",
                "client_id": client_id,
                "client_name": str(session.get("client_name", "")).strip(),
                "provider_contract_id": provider_contract_id,
                "provider_name": str(session.get("provider_name", "")).strip(),
                "payer_name": payer_name,
                "authorization_id": str(session.get("authorization_id", "")).strip(),
                "authorization_number": str(session.get("authorization_number", "")).strip(),
                "period_start": period_start,
                "period_end": period_end,
                "sessions_included": [],
                "session_count": 0,
                "units_total": 0,
                "claim_amount": 0.0,
                "units_by_cpt": {},
                "validation_errors": [],
                "status": "ready",
                "existing_claim_id": str(session.get("claim_id", "")).strip(),
            }
        batch = grouped[group_key]
        batch["sessions_included"].append(session)
        batch["session_count"] += 1
        units = _safe_int(session.get("units", 0))
        batch["units_total"] += units
        batch["claim_amount"] = round(_safe_float(batch.get("claim_amount", 0)) + _safe_float(session.get("billed_amount", 0)), 2)
        cpt_code = str(session.get("cpt_code", "")).strip() or "UNKNOWN"
        batch["units_by_cpt"][cpt_code] = int(batch["units_by_cpt"].get(cpt_code, 0)) + units
        for validation in session.get("validation_results", []):
            status = str(validation.get("status", "")).strip().lower()
            message = str(validation.get("message", "")).strip()
            if not message:
                continue
            if status == "fail":
                batch["status"] = "error"
            elif status == "warning" and batch["status"] != "error":
                batch["status"] = "warning"
            if status in {"fail", "warning"} and message not in batch["validation_errors"]:
                batch["validation_errors"].append(message)
        existing_claim_id = str(session.get("claim_id", "")).strip()
        if existing_claim_id:
            batch["existing_claim_id"] = existing_claim_id

    rows = list(grouped.values())
    for batch in rows:
        units_by_cpt = batch.get("units_by_cpt", {})
        batch["units_by_cpt_label"] = ", ".join(
            f"{code}: {count}u" for code, count in sorted(units_by_cpt.items(), key=lambda item: item[0])
        ) or "-"
    rows.sort(
        key=lambda item: (str(item.get("period_start", "")), str(item.get("client_name", "")).lower(), str(item.get("payer_name", "")).lower()),
        reverse=True,
    )
    return rows


def list_shared_calendar_events(
    *,
    provider_contract_ids: set[str] | None = None,
    client_id: str = "",
    provider_contract_id: str = "",
) -> list[dict[str, Any]]:
    clean_client_id = str(client_id or "").strip()
    clean_provider_id = str(provider_contract_id or "").strip()
    rows = list_operational_sessions(provider_contract_ids)
    if clean_client_id:
        rows = [item for item in rows if str(item.get("client_id", "")).strip() == clean_client_id]
    if clean_provider_id:
        rows = [item for item in rows if str(item.get("provider_contract_id", "")).strip() == clean_provider_id]
    rows.sort(
        key=lambda item: (
            _user_date(item.get("service_date")) or date.max,
            str(item.get("scheduled_start_time", "")),
            str(item.get("client_name", "")).lower(),
        )
    )
    return rows


def _session_grouped_service_lines(sessions: list[dict[str, Any]]) -> list[ServiceLine]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for session in sessions:
        procedure_code = str(session.get("cpt_code", "")).strip()
        diagnosis_pointer = "1"
        key = (procedure_code, diagnosis_pointer)
        if key not in grouped:
            grouped[key] = {
                "procedure_code": procedure_code,
                "units": 0,
                "charge_amount": 0.0,
                "unit_price": 0.0,
                "diagnosis_pointer": diagnosis_pointer,
            }
        grouped[key]["units"] += _safe_int(session.get("units", 0))
        grouped[key]["charge_amount"] = round(_safe_float(grouped[key]["charge_amount"]) + _safe_float(session.get("billed_amount", 0)), 2)
        grouped[key]["unit_price"] = _safe_float(session.get("billed_amount", 0)) / max(_safe_int(session.get("units", 0)), 1)
    lines = [
        ServiceLine(
            procedure_code=str(item.get("procedure_code", "")),
            charge_amount=round(_safe_float(item.get("charge_amount", 0)), 2),
            units=_safe_int(item.get("units", 0)),
            unit_price=round(_safe_float(item.get("unit_price", 0)), 2),
            diagnosis_pointer=str(item.get("diagnosis_pointer", "1")),
        )
        for item in grouped.values()
        if str(item.get("procedure_code", "")).strip()
    ]
    return sorted(lines, key=lambda item: item.procedure_code)


def _claim_for_batch(batch: dict[str, Any]) -> Claim:
    sessions = batch.get("sessions_included", [])
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("Ese batch no tiene sesiones ligadas.")
    first_session = sessions[0]
    clients_by_id = {str(item.get("client_id", "")).strip(): item for item in list_clients() if str(item.get("client_id", "")).strip()}
    providers_by_id = {str(item.get("contract_id", "")).strip(): item for item in list_provider_contracts() if str(item.get("contract_id", "")).strip()}
    client_record = clients_by_id.get(str(first_session.get("client_id", "")).strip(), {})
    provider_record = providers_by_id.get(str(first_session.get("provider_contract_id", "")).strip(), {})
    if not client_record:
        raise ValueError("No encontre el cliente de ese batch.")
    if not provider_record:
        raise ValueError("No encontre el provider de ese batch.")
    provider_first_name, provider_last_name = _split_full_name(provider_record.get("provider_name", ""))
    diagnosis_codes = [
        code.strip()
        for code in str(client_record.get("diagnosis", "") or client_record.get("notes", "")).split(",")
        if code.strip()
    ]
    service_lines = _session_grouped_service_lines(sessions)
    if not service_lines:
        raise ValueError("Ese batch no tiene lineas facturables.")
    claim_id = str(batch.get("existing_claim_id", "")).strip() or f"BTH-{uuid.uuid4().hex[:8].upper()}"
    total_charge_amount = round(sum(line.charge_amount for line in service_lines), 2)
    return Claim(
        claim_id=claim_id,
        provider=Provider(
            npi=str(provider_record.get("provider_npi", "")),
            taxonomy_code=str(provider_record.get("taxonomy_code", "")),
            first_name=provider_first_name,
            last_name=provider_last_name,
            organization_name=str(provider_record.get("provider_name", "")),
        ),
        patient=Patient(
            member_id=str(client_record.get("member_id", "")),
            first_name=str(client_record.get("first_name", "")),
            last_name=str(client_record.get("last_name", "")),
            birth_date=str(client_record.get("birth_date", "")),
            gender=str(client_record.get("gender", "")) or "U",
            address=Address(
                line1=str(client_record.get("address_line1", "")),
                city=str(client_record.get("address_city", "")),
                state=str(client_record.get("address_state", "")) or "FL",
                zip_code=str(client_record.get("address_zip_code", "")),
            ),
        ),
        insurance=InsurancePolicy(
            payer_name=str(first_session.get("payer_name", "")),
            payer_id=str(first_session.get("payer_id", "")),
            policy_number=str(client_record.get("subscriber_id", "") or client_record.get("member_id", "")),
            plan_name=str(client_record.get("last_plan_name", "")),
        ),
        service_date=str(batch.get("period_start", "") or first_session.get("service_date", "")),
        diagnosis_codes=diagnosis_codes[:3] or [""],
        service_lines=service_lines,
        total_charge_amount=total_charge_amount,
    )


def create_claim_from_batch(batch_id: str, provider_contract_ids: set[str] | None = None) -> dict[str, Any]:
    clean_batch_id = str(batch_id or "").strip()
    if not clean_batch_id:
        raise ValueError("Selecciona un batch para generar el claim.")
    sessions = list_operational_sessions(provider_contract_ids)
    batch = next((item for item in build_claim_batches(sessions) if str(item.get("batch_id", "")).strip() == clean_batch_id), None)
    if batch is None:
        raise ValueError("No encontre ese batch listo para claim.")
    claim = _claim_for_batch(batch)
    session_ids = [str(item.get("session_id", "")).strip() for item in batch.get("sessions_included", []) if str(item.get("session_id", "")).strip()]
    stored = add_claim_record(
        claim,
        extra_metadata={
            "source_type": "session_batch",
            "batch_id": clean_batch_id,
            "batch_period_start": str(batch.get("period_start", "")),
            "batch_period_end": str(batch.get("period_end", "")),
            "authorization_id": str(batch.get("authorization_id", "")),
            "authorization_number": str(batch.get("authorization_number", "")),
            "session_ids": session_ids,
            "provider_contract_id": str(batch.get("provider_contract_id", "")),
            "provider_name": str(batch.get("provider_name", "")),
        },
    )
    return {
        "batch_id": clean_batch_id,
        "claim_id": str(stored.get("claim_id", "")),
        "session_ids": session_ids,
        "record": stored,
        "sessions_included": batch.get("sessions_included", []),
    }


def build_claim_form_from_session(session_id: str, provider_contract_ids: set[str] | None = None) -> dict[str, str] | None:
    session = get_operational_session_detail(session_id, provider_contract_ids)
    if session is None:
        return None
    clients_by_id = {str(item.get("client_id", "")).strip(): item for item in list_clients() if str(item.get("client_id", "")).strip()}
    providers_by_id = {str(item.get("contract_id", "")).strip(): item for item in list_provider_contracts() if str(item.get("contract_id", "")).strip()}
    client_record = clients_by_id.get(str(session.get("client_id", "")).strip(), {})
    provider_record = providers_by_id.get(str(session.get("provider_contract_id", "")).strip(), {})
    provider_first_name, provider_last_name = _split_full_name(provider_record.get("provider_name", ""))
    payer_name = str(session.get("payer_name", "")).strip()
    payer_id = str(session.get("payer_id", "")).strip()
    unit_price = get_payer_configured_unit_price(str(session.get("cpt_code", "")), payer_name, payer_id)
    if unit_price is None:
        unit_price = round(_safe_float(session.get("billed_amount", 0)) / max(_safe_int(session.get("units", 0)), 1), 2)
    return {
        "claim_id": f"SES-{str(session.get('session_id', '')).replace('ABASES-', '') or 'AUTO'}",
        "service_date": str(session.get("service_date", "")),
        "provider_npi": str(provider_record.get("provider_npi", "")),
        "provider_taxonomy_code": str(provider_record.get("taxonomy_code", "")),
        "provider_first_name": provider_first_name,
        "provider_last_name": provider_last_name,
        "provider_organization_name": str(provider_record.get("provider_name", "")),
        "patient_member_id": str(client_record.get("member_id", "")),
        "patient_birth_date": str(client_record.get("birth_date", "")),
        "patient_first_name": str(client_record.get("first_name", "")),
        "patient_last_name": str(client_record.get("last_name", "")),
        "patient_gender": str(client_record.get("gender", "")) or "U",
        "patient_address_line1": str(client_record.get("address_line1", "")),
        "patient_city": str(client_record.get("address_city", "")),
        "patient_state": str(client_record.get("address_state", "")) or "FL",
        "patient_zip_code": str(client_record.get("address_zip_code", "")),
        "insurance_payer_name": payer_name,
        "insurance_payer_id": payer_id,
        "insurance_policy_number": str(client_record.get("subscriber_id", "") or client_record.get("member_id", "")),
        "insurance_plan_name": str(client_record.get("last_plan_name", "")),
        "diagnosis_code_1": str(client_record.get("diagnosis", "")).split(",")[0].strip(),
        "diagnosis_code_2": "",
        "diagnosis_code_3": "",
        "service_line_1_procedure_code": str(session.get("cpt_code", "")),
        "service_line_1_unit_price": f"{unit_price:.2f}",
        "service_line_1_units": str(_safe_int(session.get("units", 0))),
        "service_line_1_diagnosis_pointer": "1",
        "service_line_2_procedure_code": "",
        "service_line_2_unit_price": "",
        "service_line_2_units": "",
        "service_line_2_diagnosis_pointer": "2",
        "service_line_3_procedure_code": "",
        "service_line_3_unit_price": "",
        "service_line_3_units": "",
        "service_line_3_diagnosis_pointer": "3",
        "session_source_id": str(session.get("session_id", "")),
    }
