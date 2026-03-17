from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from billing_app.services.local_store import (
    get_claim_by_id,
    get_provider_contract_by_id,
    list_claim_audit_logs,
)
from billing_app.services.operations_portal import get_operational_session_detail
from billing_app.services.openai_responses_client import OpenAIResponsesError, create_structured_response


AI_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "primary_output": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {"type": "string"},
        },
        "next_steps": {
            "type": "array",
            "items": {"type": "string"},
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
    },
    "required": ["headline", "summary", "primary_output", "findings", "next_steps", "risk_level"],
}

FUTURE_FUNCTION_TOOL_SCHEMAS = {
    "clients": {
        "type": "function",
        "name": "lookup_client_workflow",
        "description": "Future tool placeholder to fetch a client workflow snapshot by client_id.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "client_id": {"type": "string"},
            },
            "required": ["client_id"],
        },
    },
    "providers": {
        "type": "function",
        "name": "lookup_provider_profile",
        "description": "Future tool placeholder to fetch provider compliance and onboarding data by contract_id.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "contract_id": {"type": "string"},
            },
            "required": ["contract_id"],
        },
    },
    "documents": {
        "type": "function",
        "name": "lookup_document_status",
        "description": "Future tool placeholder to inspect required documents and expiration state.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entity_type": {"type": "string"},
                "entity_id": {"type": "string"},
            },
            "required": ["entity_type", "entity_id"],
        },
    },
    "notes": {
        "type": "function",
        "name": "lookup_session_note",
        "description": "Future tool placeholder to retrieve a note draft or signed note by session_id.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    },
    "billing": {
        "type": "function",
        "name": "lookup_billing_validation",
        "description": "Future tool placeholder to inspect billing holds, warnings, and queue status for a session.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    },
    "claims": {
        "type": "function",
        "name": "lookup_claim_status",
        "description": "Future tool placeholder to retrieve claim status, follow-up history, and payment state by claim_id.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "claim_id": {"type": "string"},
            },
            "required": ["claim_id"],
        },
    },
}


@dataclass(frozen=True)
class AIAssistantActionSpec:
    name: str
    label: str
    domain: str
    primary_output_label: str
    instructions: str
    future_tool_scopes: tuple[str, ...]


AI_ACTION_SPECS: dict[str, AIAssistantActionSpec] = {
    "improve_session_note": AIAssistantActionSpec(
        name="improve_session_note",
        label="Improve Session Note",
        domain="aba_notes",
        primary_output_label="Improved Session Note",
        instructions=(
            "You are Blue Hope AI Note Assistant for an ABA practice. "
            "Work only from the structured payload provided. The payload is intentionally minimized to reduce PHI exposure. "
            "Do not invent clinical events, outcomes, or participant details. "
            "Rewrite the note in clear, payer-safe, professional language while preserving the original meaning. "
            "If the note is too weak, identify what is missing and produce the strongest compliant rewrite possible using only the supplied facts. "
            "Return concise JSON only."
        ),
        future_tool_scopes=("clients", "notes", "billing"),
    ),
    "explain_claim_denial": AIAssistantActionSpec(
        name="explain_claim_denial",
        label="Explain Claim Denial",
        domain="claims",
        primary_output_label="Denial Explanation",
        instructions=(
            "You are Blue Hope AI Billing Assistant for an ABA practice. "
            "Explain the most likely operational reason for the claim denial or rejection using only the structured fields provided. "
            "Clearly separate observed facts from likely causes, avoid legal or clinical overstatements, and suggest practical next steps for billing follow-up. "
            "If exact payer denial reason codes are missing, say that explicitly and explain using the available evidence only. "
            "Return concise JSON only."
        ),
        future_tool_scopes=("claims", "billing", "documents"),
    ),
    "check_missing_provider_documents": AIAssistantActionSpec(
        name="check_missing_provider_documents",
        label="Check Missing Provider Documents",
        domain="providers",
        primary_output_label="Document Review",
        instructions=(
            "You are Blue Hope AI Compliance Assistant for provider onboarding. "
            "Review the structured provider document checklist summary and identify blockers, missing items, expiring items, and the cleanest next actions. "
            "Stay operational and concise. Do not ask for data that already exists in the payload. "
            "Return concise JSON only."
        ),
        future_tool_scopes=("providers", "documents"),
    ),
}


def available_ai_actions() -> dict[str, dict[str, Any]]:
    return {
        key: {
            "label": spec.label,
            "domain": spec.domain,
            "primary_output_label": spec.primary_output_label,
            "future_tool_scopes": list(spec.future_tool_scopes),
        }
        for key, spec in AI_ACTION_SPECS.items()
    }


def planned_function_tools(scopes: tuple[str, ...]) -> list[dict[str, Any]]:
    return [FUTURE_FUNCTION_TOOL_SCHEMAS[scope] for scope in scopes if scope in FUTURE_FUNCTION_TOOL_SCHEMAS]


def _safe_int(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _split_client_names(raw_value: str) -> list[str]:
    normalized = str(raw_value or "").replace("\r", "\n").replace(";", "\n").replace(",", "\n")
    return [piece.strip() for piece in normalized.split("\n") if piece.strip()]


def _mask_known_tokens(value: str, replacements: list[str]) -> str:
    clean = str(value or "")
    for replacement in replacements:
        token = str(replacement or "").strip()
        if not token:
            continue
        clean = re.sub(re.escape(token), "[REDACTED]", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "[DATE]", clean)
    clean = re.sub(r"\b[A-Z0-9]{7,}\b", "[ID]", clean)
    return " ".join(clean.split())[:2800]


def _normalize_claim_history(claim_id: str) -> list[str]:
    rows = []
    for item in list_claim_audit_logs(claim_id=claim_id, limit=8):
        action = str(item.get("action", "")).strip()
        details = str(item.get("details", "")).strip()
        created_at = str(item.get("created_at", "")).strip()
        rows.append(" | ".join(part for part in [created_at, action, details] if part))
    return rows


def _build_session_note_payload(session_id: str, provider_contract_ids: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    session = get_operational_session_detail(session_id, provider_contract_ids)
    if session is None:
        raise ValueError("No encontre esa sesion para mejorar la nota.")

    raw_note = str(session.get("session_note", "")).strip()
    if not raw_note:
        raise ValueError("Esta sesion todavia no tiene texto de nota para mejorar.")

    redacted_note = _mask_known_tokens(
        raw_note,
        [
            str(session.get("client_name", "")),
            str(session.get("provider_name", "")),
            str(session.get("member_id", "")),
        ],
    )
    payload = {
        "workflow_stage": "session_note",
        "event_type": str(session.get("event_type", "")).strip(),
        "cpt_code": str(session.get("cpt_code", "")).strip(),
        "billing_code": str(session.get("billing_code", "")).strip(),
        "place_of_service": str(session.get("place_of_service", "")).strip(),
        "units": _safe_int(session.get("units", 0)),
        "hours": round(_safe_float(session.get("hours", 0)), 2),
        "note_status": str(session.get("note_status", "")).strip(),
        "session_note": redacted_note,
    }
    return payload, session


def _build_claim_denial_payload(claim_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    claim = get_claim_by_id(claim_id)
    if claim is None:
        raise ValueError("No encontre ese claim para revisar la denegacion.")

    status = str(claim.get("status", "")).strip().lower()
    transmission_status = str(claim.get("transmission_status", "")).strip().lower()
    if status not in {"denied", "draft", "pending", "partial"} and transmission_status != "transmitted":
        raise ValueError("Este claim no tiene una denegacion o issue clara para explicar todavia.")

    service_lines = []
    for line in claim.get("service_lines", []):
        if not isinstance(line, dict):
            continue
        service_lines.append(
            {
                "procedure_code": str(line.get("procedure_code", "")).strip(),
                "units": _safe_int(line.get("units", 0)),
                "charge_amount": round(_safe_float(line.get("charge_amount", 0)), 2),
            }
        )

    payload = {
        "workflow_stage": "claim_follow_up",
        "claim_status": str(claim.get("status", "")).strip(),
        "transmission_status": str(claim.get("transmission_status", "")).strip(),
        "payer_name": str(claim.get("payer_name", "")).strip(),
        "service_date": str(claim.get("service_date", "")).strip(),
        "total_charge_amount": round(_safe_float(claim.get("total_charge_amount", 0)), 2),
        "paid_amount": round(_safe_float(claim.get("paid_amount", 0)), 2),
        "balance_amount": round(_safe_float(claim.get("balance_amount", 0)), 2),
        "tracking_id_present": bool(str(claim.get("tracking_id", "")).strip()),
        "payer_claim_number_present": bool(str(claim.get("payer_claim_number", "")).strip()),
        "service_lines": service_lines,
        "recent_history": _normalize_claim_history(claim_id),
    }
    return payload, claim


def _build_provider_documents_payload(contract_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    provider = get_provider_contract_by_id(contract_id)
    if provider is None:
        raise ValueError("No encontre ese provider para revisar el checklist.")

    documents = provider.get("documents", [])
    if not isinstance(documents, list):
        documents = []

    missing_documents: list[str] = []
    expired_documents: list[str] = []
    expiring_documents: list[str] = []
    delivered_documents = 0
    ignored_documents = 0
    for document in documents:
        if not isinstance(document, dict):
            continue
        document_name = str(document.get("document_name", "")).strip()
        status = str(document.get("status", "")).strip().lower()
        if status == "delivered":
            delivered_documents += 1
        elif status == "ignored":
            ignored_documents += 1
        else:
            missing_documents.append(document_name)
        if bool(document.get("is_expired")):
            expired_documents.append(document_name)
        elif bool(document.get("expiring_soon")):
            expiring_documents.append(document_name)

    payload = {
        "workflow_stage": "provider_compliance",
        "worker_category": str(provider.get("worker_category", "")).strip(),
        "provider_type": str(provider.get("provider_type", "")).strip(),
        "contract_stage": str(provider.get("contract_stage", "")).strip(),
        "documents_complete": bool(provider.get("documents_complete")),
        "completed_documents": _safe_int(provider.get("completed_documents", 0)),
        "delivered_documents": delivered_documents,
        "ignored_documents": ignored_documents,
        "total_documents": _safe_int(provider.get("total_documents", 0)),
        "expired_documents": expired_documents[:25],
        "expiring_documents": expiring_documents[:25],
        "missing_documents": missing_documents[:25],
        "assigned_clients_count": len(_split_client_names(str(provider.get("assigned_clients", "")).strip())),
        "credentialing_status": str(provider.get("credentialing_status_summary", "")).strip(),
        "credentialing_due_date": str(provider.get("credentialing_due_date", "")).strip(),
    }
    return payload, provider


def _build_action_payload(
    action_name: str,
    *,
    session_id: str = "",
    claim_id: str = "",
    contract_id: str = "",
    provider_contract_ids: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    clean_action_name = str(action_name or "").strip()
    if clean_action_name == "improve_session_note":
        return _build_session_note_payload(session_id, provider_contract_ids)
    if clean_action_name == "explain_claim_denial":
        return _build_claim_denial_payload(claim_id)
    if clean_action_name == "check_missing_provider_documents":
        return _build_provider_documents_payload(contract_id)
    raise ValueError("La accion de IA solicitada no existe.")


def run_ai_assistant_action(
    action_name: str,
    *,
    session_id: str = "",
    claim_id: str = "",
    contract_id: str = "",
    provider_contract_ids: set[str] | None = None,
) -> dict[str, Any]:
    spec = AI_ACTION_SPECS.get(str(action_name or "").strip())
    if spec is None:
        raise ValueError("La accion de IA solicitada no existe.")

    payload, source_record = _build_action_payload(
        spec.name,
        session_id=session_id,
        claim_id=claim_id,
        contract_id=contract_id,
        provider_contract_ids=provider_contract_ids,
    )
    response = create_structured_response(
        instructions=spec.instructions,
        input_payload={
            "action": spec.name,
            "payload": payload,
            "privacy_mode": "minimal_structured_data_only",
            "future_tool_scopes": list(spec.future_tool_scopes),
        },
        schema_name="bhas_ai_action_result",
        schema=AI_RESULT_SCHEMA,
        schema_description="Structured response for a Blue Hope AI assistant action.",
        metadata={
            "module": "bhas_ai_assistant",
            "action": spec.name,
        },
    )
    structured_output = response.get("output", {})
    if not isinstance(structured_output, dict):
        raise OpenAIResponsesError("OpenAI no devolvio una salida estructurada util.")

    findings = structured_output.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    next_steps = structured_output.get("next_steps", [])
    if not isinstance(next_steps, list):
        next_steps = []

    return {
        "action_name": spec.name,
        "action_label": spec.label,
        "domain": spec.domain,
        "primary_output_label": spec.primary_output_label,
        "headline": str(structured_output.get("headline", "")).strip() or spec.label,
        "summary": str(structured_output.get("summary", "")).strip(),
        "primary_output": str(structured_output.get("primary_output", "")).strip(),
        "findings": [str(item).strip() for item in findings if str(item).strip()],
        "next_steps": [str(item).strip() for item in next_steps if str(item).strip()],
        "risk_level": str(structured_output.get("risk_level", "")).strip().lower() or "medium",
        "model": str(response.get("model", "")).strip(),
        "response_id": str(response.get("response_id", "")).strip(),
        "payload_preview": payload,
        "planned_function_tools": planned_function_tools(spec.future_tool_scopes),
        "planned_tool_scopes": list(spec.future_tool_scopes),
        "source_id": (
            str(source_record.get("session_id", "")).strip()
            or str(source_record.get("claim_id", "")).strip()
            or str(source_record.get("contract_id", "")).strip()
        ),
        "structured_only": True,
    }
