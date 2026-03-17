from __future__ import annotations

from typing import Any


NORMALIZED_ROLES = (
    "ADMIN",
    "MANAGER",
    "HR",
    "RECRUITER",
    "CREDENTIALING",
    "BCBA",
    "BCABA",
    "RBT",
    "OFFICE",
    "BILLING",
)

ROLE_LABELS: dict[str, str] = {
    "ADMIN": "Admin",
    "MANAGER": "Manager",
    "HR": "HR",
    "RECRUITER": "Recruiter",
    "CREDENTIALING": "Credentialing",
    "BCBA": "BCBA",
    "BCABA": "BCaBA",
    "RBT": "RBT",
    "OFFICE": "Office",
    "BILLING": "Billing",
    "GENERAL": "Manager",
    "RECURSOS_HUMANOS": "HR",
    "OFICINA": "Office",
    "CLINICO": "BCBA / Clinical",
    "PROVEEDOR": "Provider",
}

PAGE_LABELS: dict[str, str] = {
    "dashboard": "Dashboard",
    "hr": "HR Pipeline",
    "claims": "Claims",
    "eligibility": "Eligibility",
    "clients": "Clients",
    "aba_notes": "Session Notes",
    "payments": "Billing",
    "enrollments": "Credentialing",
    "payers": "Payers",
    "agencies": "Agencies",
    "providers": "Providers",
    "agenda": "Calendar",
    "notifications": "Notifications",
    "users": "Users",
    "security": "Settings",
}

PAGE_PERMISSION_MAP: dict[str, tuple[str, ...]] = {
    "dashboard": ("dashboard.view",),
    "hr": ("hr.pipeline.view",),
    "claims": ("claims.view",),
    "eligibility": ("eligibility.view",),
    "clients": ("clients.view", "clients.assigned.view"),
    "aba_notes": ("notes.view", "sessions.assigned.view"),
    "payments": ("billing.view",),
    "enrollments": ("providers.credentials.view",),
    "payers": ("finance.view_rates",),
    "agencies": ("settings.manage",),
    "providers": ("providers.view",),
    "agenda": ("schedule.view", "sessions.assigned.view"),
    "notifications": ("notifications.view",),
    "users": ("users.view",),
    "security": ("settings.view",),
}

DEFAULT_PAGE_BY_ROLE: dict[str, str] = {
    "ADMIN": "dashboard",
    "MANAGER": "dashboard",
    "HR": "hr",
    "RECRUITER": "providers",
    "CREDENTIALING": "providers",
    "BCBA": "clients",
    "BCABA": "clients",
    "RBT": "clients",
    "OFFICE": "dashboard",
    "BILLING": "claims",
}

PROVIDER_ROLES = {"BCBA", "BCABA", "RBT"}

ROLE_ALIASES = {
    "GENERAL": "MANAGER",
    "RECURSOS_HUMANOS": "HR",
    "OFICINA": "OFFICE",
    "CLINICO": "BCBA",
    "PROVIDER": "RBT",
    "PROVEEDOR": "RBT",
    "CREDENTIALING_SPECIALIST": "CREDENTIALING",
}

ROLE_PERMISSION_MATRIX: dict[str, set[str]] = {
    "ADMIN": {"admin.full"},
    "MANAGER": {
        "dashboard.view",
        "providers.view",
        "providers.create",
        "providers.edit",
        "providers.assign_recruiter",
        "providers.set_priority",
        "providers.set_rate",
        "providers.documents.view",
        "providers.documents.verify",
        "providers.credentials.view",
        "providers.credentials.edit",
        "hr.pipeline.view",
        "hr.pipeline.manage",
        "clients.view",
        "clients.edit",
        "clients.authorizations.view",
        "clients.authorizations.edit",
        "sessions.view",
        "sessions.create",
        "sessions.edit",
        "notes.view",
        "notes.write",
        "notes.review",
        "notes.close",
        "supervision.view",
        "schedule.view",
        "billing.view",
        "claims.view",
        "claims.submit",
        "finance.view_rates",
        "finance.view_totals",
        "finance.view_paid_amount",
        "finance.view_reimbursement",
        "reports.view",
        "reports.financial",
        "notifications.view",
        "users.view",
        "users.edit",
        "settings.view",
        "eligibility.view",
    },
    "HR": {
        "dashboard.view",
        "providers.view",
        "providers.create",
        "providers.edit",
        "providers.documents.view",
        "providers.documents.verify",
        "providers.credentials.view",
        "hr.pipeline.view",
        "hr.pipeline.manage",
        "schedule.view",
        "notifications.view",
        "users.view",
        "settings.view",
        "eligibility.view",
    },
    "RECRUITER": {
        "dashboard.view",
        "providers.view",
        "hr.pipeline.view",
        "recruiter.assigned.view",
        "recruiter.notes.write",
        "notes.view",
        "notes.write",
        "notifications.view",
        "settings.view",
    },
    "CREDENTIALING": {
        "dashboard.view",
        "providers.view",
        "providers.documents.view",
        "providers.documents.verify",
        "providers.credentials.view",
        "providers.credentials.edit",
        "hr.pipeline.view",
        "notifications.view",
        "settings.view",
    },
    "BCBA": {
        "dashboard.view",
        "providers.documents.view",
        "clients.assigned.view",
        "clients.authorizations.view",
        "sessions.assigned.view",
        "sessions.create",
        "sessions.edit",
        "notes.view",
        "notes.write",
        "notes.review",
        "notes.close",
        "supervision.view",
        "schedule.view",
        "notifications.view",
        "settings.view",
    },
    "BCABA": {
        "dashboard.view",
        "providers.documents.view",
        "clients.assigned.view",
        "clients.authorizations.view",
        "sessions.assigned.view",
        "sessions.create",
        "sessions.edit",
        "notes.view",
        "notes.write",
        "supervision.view",
        "schedule.view",
        "notifications.view",
        "settings.view",
    },
    "RBT": {
        "dashboard.view",
        "providers.documents.view",
        "clients.assigned.view",
        "clients.authorizations.view",
        "sessions.assigned.view",
        "sessions.edit",
        "notes.view",
        "notes.write",
        "schedule.view",
        "notifications.view",
        "settings.view",
    },
    "OFFICE": {
        "dashboard.view",
        "sessions.view",
        "schedule.view",
        "billing.view",
        "claims.view",
        "notifications.view",
        "settings.view",
        "eligibility.view",
    },
    "BILLING": {
        "dashboard.view",
        "billing.view",
        "claims.view",
        "claims.submit",
        "finance.view_rates",
        "finance.view_totals",
        "finance.view_paid_amount",
        "finance.view_reimbursement",
        "reports.view",
        "reports.financial",
        "notifications.view",
        "settings.view",
        "payers.view",
    },
}

SIDEBAR_PAGE_META: dict[str, dict[str, Any]] = {
    "dashboard": {
        "page": "dashboard",
        "title": "Dashboard",
        "copy": "Operational overview and priorities",
        "icon": "DB",
    },
    "providers": {
        "page": "providers",
        "title": "Providers",
        "copy": "Pipeline, profiles, and compliance",
        "icon": "PR",
    },
    "hr": {
        "page": "hr",
        "title": "HR Pipeline",
        "copy": "Recruiting, onboarding, and credentialing",
        "icon": "HR",
    },
    "clients": {
        "page": "clients",
        "title": "Clients",
        "copy": "Assigned caseload and workflow center",
        "icon": "PT",
    },
    "agenda": {
        "page": "agenda",
        "title": "Schedule",
        "copy": "Calendar, deadlines, and worklist",
        "icon": "CA",
    },
    "aba_notes": {
        "page": "aba_notes",
        "title": "Session Notes",
        "copy": "Sessions, notes, and service logs",
        "icon": "NB",
    },
    "claims": {
        "page": "claims",
        "title": "Billing",
        "copy": "Claims, remits, and payer workflow",
        "icon": "BL",
        "active_pages": ("claims", "payments", "payers"),
    },
    "notifications": {
        "page": "notifications",
        "title": "Notifications",
        "copy": "Alerts, emails, and internal follow-up",
        "icon": "NT",
    },
    "security": {
        "page": "security",
        "title": "Settings",
        "copy": "Security and platform configuration",
        "icon": "SF",
        "active_pages": ("security", "agencies"),
    },
    "eligibility": {
        "page": "eligibility",
        "title": "Eligibility",
        "copy": "Coverage checks and roster support",
        "icon": "EL",
    },
    "users": {
        "page": "users",
        "title": "Users",
        "copy": "User access and profiles",
        "icon": "US",
    },
    "enrollments": {
        "page": "enrollments",
        "title": "Credentialing",
        "copy": "Payer enrollments and setup",
        "icon": "EN",
    },
}

SIDEBAR_BY_ROLE: dict[str, tuple[str, ...]] = {
    "ADMIN": ("dashboard", "providers", "hr", "clients", "agenda", "aba_notes", "claims", "notifications", "security"),
    "MANAGER": ("dashboard", "providers", "hr", "clients", "agenda", "claims", "notifications", "security"),
    "HR": ("dashboard", "providers", "hr", "notifications", "security"),
    "RECRUITER": ("dashboard", "providers", "hr", "notifications", "security"),
    "CREDENTIALING": ("dashboard", "providers", "hr", "notifications", "security"),
    "BCBA": ("dashboard", "clients", "agenda", "aba_notes", "notifications", "security"),
    "BCABA": ("dashboard", "clients", "agenda", "aba_notes", "notifications", "security"),
    "RBT": ("dashboard", "clients", "agenda", "aba_notes", "notifications", "security"),
    "OFFICE": ("dashboard", "agenda", "claims", "notifications", "security"),
    "BILLING": ("dashboard", "claims", "notifications", "security"),
}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _provider_type_key(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def role_label(value: Any, *, linked_provider_type: str = "") -> str:
    normalized = normalize_role(value, linked_provider_type=linked_provider_type)
    return ROLE_LABELS.get(normalized, str(value or "").title() or "User")


def normalize_role(value: Any, *, linked_provider_type: str = "") -> str:
    clean = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    provider_type = _provider_type_key(linked_provider_type)
    if clean in {"BCABA", "BCABA_"}:
        return "BCABA"
    if clean == "PROVEEDOR":
        if provider_type in PROVIDER_ROLES:
            return provider_type
        return "RBT"
    if clean in ROLE_ALIASES:
        mapped = ROLE_ALIASES[clean]
        if mapped == "RBT" and provider_type in PROVIDER_ROLES:
            return provider_type
        return mapped
    if clean in NORMALIZED_ROLES:
        return clean
    return clean or "OFFICE"


def normalized_role_from_user(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> str:
    if not user:
        return ""
    linked_provider_type = str(user.get("linked_provider_type", "")).strip()
    linked_provider_name = _normalize_text(user.get("linked_provider_name", ""))
    if not linked_provider_type and linked_provider_name and provider_contracts:
        for item in provider_contracts:
            if _normalize_text(item.get("provider_name", "")) == linked_provider_name:
                linked_provider_type = str(item.get("provider_type", "")).strip()
                break
    if not linked_provider_type and provider_contracts and str(user.get("role", "")).strip().upper() in {"PROVEEDOR", "PROVIDER", "BCBA", "BCABA", "RBT"}:
        full_name = _normalize_text(user.get("full_name", ""))
        username = _normalize_text(user.get("username", ""))
        for item in provider_contracts:
            provider_name = _normalize_text(item.get("provider_name", ""))
            if provider_name and provider_name in {linked_provider_name, full_name, username}:
                linked_provider_type = str(item.get("provider_type", "")).strip()
                break
    return normalize_role(user.get("role", ""), linked_provider_type=linked_provider_type)


def is_provider_role(role_or_user: str | dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    if isinstance(role_or_user, dict) or role_or_user is None:
        return normalized_role_from_user(role_or_user, provider_contracts) in PROVIDER_ROLES
    return normalize_role(role_or_user) in PROVIDER_ROLES


def _role_permissions(normalized_role: str) -> set[str]:
    return set(ROLE_PERMISSION_MATRIX.get(normalized_role, {"dashboard.view", "settings.view"}))


def _permission_overrides(user: dict[str, Any] | None) -> dict[str, bool]:
    raw = (user or {}).get("permission_overrides", {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): bool(value) for key, value in raw.items()}


def has_permission(user_or_role: dict[str, Any] | str | None, permission: str, provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    if not permission:
        return True
    if isinstance(user_or_role, dict):
        user = user_or_role
        overrides = _permission_overrides(user)
        if permission in overrides:
            return overrides[permission]
        normalized_role = normalized_role_from_user(user, provider_contracts)
    else:
        user = None
        normalized_role = normalize_role(user_or_role or "")
    permissions = _role_permissions(normalized_role)
    return "admin.full" in permissions or permission in permissions


def has_any_permission(user_or_role: dict[str, Any] | str | None, permissions: tuple[str, ...] | list[str] | set[str], provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    return any(has_permission(user_or_role, permission, provider_contracts) for permission in permissions)


def default_module_permissions_for_role(role: str) -> dict[str, bool]:
    normalized_role = normalize_role(role)
    return {
        page_key: has_any_permission(normalized_role, PAGE_PERMISSION_MAP.get(page_key, ()), None)
        for page_key in PAGE_LABELS
    }


def allowed_pages_for_user(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> set[str]:
    if not user:
        return set()
    defaults = {
        page_key
        for page_key, permissions in PAGE_PERMISSION_MAP.items()
        if has_any_permission(user, permissions, provider_contracts)
    }
    module_permissions = user.get("module_permissions")
    if isinstance(module_permissions, dict) and module_permissions:
        visible = set()
        for page_key in defaults:
            if bool(module_permissions.get(page_key, True)):
                visible.add(page_key)
        defaults = visible
    if has_any_permission(user, PAGE_PERMISSION_MAP.get("security", ()), provider_contracts):
        defaults.add("security")
    return defaults


def sidebar_items_for_user(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if not user:
        return []
    normalized_role = normalized_role_from_user(user, provider_contracts)
    allowed = allowed_pages_for_user(user, provider_contracts)
    ordered_pages = list(SIDEBAR_BY_ROLE.get(normalized_role, ("dashboard", "security")))
    for page in sorted(allowed):
        if page not in ordered_pages:
            ordered_pages.append(page)
    items: list[dict[str, Any]] = []
    for page in ordered_pages:
        if page not in allowed:
            continue
        meta = dict(SIDEBAR_PAGE_META.get(page, {}))
        if not meta:
            meta = {
                "page": page,
                "title": PAGE_LABELS.get(page, page.title()),
                "copy": PAGE_LABELS.get(page, page.title()),
                "icon": page[:2].upper(),
            }
        items.append(meta)
    return items


def default_page_for_role(role: str) -> str:
    normalized_role = normalize_role(role)
    return DEFAULT_PAGE_BY_ROLE.get(normalized_role, "dashboard")


def can_access_page(user: dict[str, Any] | None, page: str, provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    return page in allowed_pages_for_user(user, provider_contracts)


def can_view_financial_rates(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    return has_permission(user, "finance.view_rates", provider_contracts)


def can_view_financial_totals(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    return has_permission(user, "finance.view_totals", provider_contracts)


def can_view_paid_amounts(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    return has_permission(user, "finance.view_paid_amount", provider_contracts)


def can_view_reimbursement(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]] | None = None) -> bool:
    return has_permission(user, "finance.view_reimbursement", provider_contracts)


def _matches_user_assignment(user: dict[str, Any] | None, raw_value: Any) -> bool:
    if not user:
        return False
    expected = _normalize_text(raw_value)
    if not expected:
        return False
    full_name = _normalize_text(user.get("full_name", ""))
    username = _normalize_text(user.get("username", ""))
    email = _normalize_text(user.get("email", ""))
    return expected in {full_name, username, email}


def _linked_provider_records(user: dict[str, Any] | None, provider_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not user:
        return []
    linked_name = _normalize_text(user.get("linked_provider_name", ""))
    full_name = _normalize_text(user.get("full_name", ""))
    username = _normalize_text(user.get("username", ""))
    matches = []
    for item in provider_contracts:
        provider_name = _normalize_text(item.get("provider_name", ""))
        if provider_name and provider_name in {linked_name, full_name, username}:
            matches.append(item)
    return matches


def _client_name(client: dict[str, Any]) -> str:
    return f"{client.get('first_name', '')} {client.get('last_name', '')}".strip()


def _client_has_provider_assignment(client: dict[str, Any], provider_records: list[dict[str, Any]], role_key: str) -> bool:
    provider_ids = {str(item.get("contract_id", "")).strip() for item in provider_records if str(item.get("contract_id", "")).strip()}
    provider_names = {_normalize_text(item.get("provider_name", "")) for item in provider_records if _normalize_text(item.get("provider_name", ""))}
    if str(client.get(f"{role_key}_contract_id", "")).strip() in provider_ids:
        return True
    if _normalize_text(client.get(f"{role_key}_provider_name", "")) in provider_names:
        return True
    care_team = {
        _normalize_text(item)
        for item in (client.get("care_team_names", []) if isinstance(client.get("care_team_names", []), list) else [])
        if _normalize_text(item)
    }
    return bool(care_team & provider_names)


def filter_provider_contracts_for_user(
    user: dict[str, Any] | None,
    provider_contracts: list[dict[str, Any]],
    clients: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not user:
        return []
    normalized_role = normalized_role_from_user(user, provider_contracts)
    if has_permission(user, "admin.full", provider_contracts) or normalized_role in {"MANAGER", "HR"}:
        return list(provider_contracts)
    if normalized_role == "RECRUITER":
        return [item for item in provider_contracts if _matches_user_assignment(user, item.get("recruiter_name", ""))]
    if normalized_role == "CREDENTIALING":
        return [item for item in provider_contracts if _matches_user_assignment(user, item.get("credentialing_owner_name", ""))]
    if normalized_role in PROVIDER_ROLES:
        return _linked_provider_records(user, provider_contracts)
    if normalized_role == "OFFICE" and has_permission(user, "providers.view", provider_contracts):
        return list(provider_contracts)
    return list(provider_contracts) if has_permission(user, "providers.view", provider_contracts) else []


def filter_clients_for_user(
    user: dict[str, Any] | None,
    clients: list[dict[str, Any]],
    provider_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not user:
        return []
    normalized_role = normalized_role_from_user(user, provider_contracts)
    if normalized_role == "BILLING" and (has_permission(user, "claims.view", provider_contracts) or has_permission(user, "billing.view", provider_contracts)):
        return list(clients)
    if has_permission(user, "admin.full", provider_contracts) or has_permission(user, "clients.view", provider_contracts):
        return list(clients)
    if not has_permission(user, "clients.assigned.view", provider_contracts):
        return []
    provider_records = _linked_provider_records(user, provider_contracts)
    if normalized_role == "BCBA":
        return [item for item in clients if _client_has_provider_assignment(item, provider_records, "bcba")]
    if normalized_role == "BCABA":
        return [item for item in clients if _client_has_provider_assignment(item, provider_records, "bcaba")]
    if normalized_role == "RBT":
        return [item for item in clients if _client_has_provider_assignment(item, provider_records, "rbt")]
    return []


def filter_authorizations_for_user(
    user: dict[str, Any] | None,
    authorizations: list[dict[str, Any]],
    visible_clients: list[dict[str, Any]],
    provider_contracts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not user:
        return []
    if has_permission(user, "clients.view", provider_contracts):
        return list(authorizations)
    visible_client_ids = {str(item.get("client_id", "")).strip() for item in visible_clients if str(item.get("client_id", "")).strip()}
    visible_member_ids = {str(item.get("member_id", "")).strip() for item in visible_clients if str(item.get("member_id", "")).strip()}
    return [
        item
        for item in authorizations
        if (
            str(item.get("client_id", "")).strip() in visible_client_ids
            or str(item.get("patient_member_id", "")).strip() in visible_member_ids
        )
    ]


def filter_sessions_for_user(
    user: dict[str, Any] | None,
    sessions: list[dict[str, Any]],
    visible_clients: list[dict[str, Any]],
    provider_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not user:
        return []
    normalized_role = normalized_role_from_user(user, provider_contracts)
    if normalized_role == "BILLING" and (has_permission(user, "claims.view", provider_contracts) or has_permission(user, "billing.view", provider_contracts)):
        return list(sessions)
    if has_permission(user, "sessions.view", provider_contracts):
        return list(sessions)
    if not has_permission(user, "sessions.assigned.view", provider_contracts):
        return []
    visible_client_ids = {str(item.get("client_id", "")).strip() for item in visible_clients if str(item.get("client_id", "")).strip()}
    visible_provider_ids = {
        str(item.get("contract_id", "")).strip()
        for item in _linked_provider_records(user, provider_contracts)
        if str(item.get("contract_id", "")).strip()
    }
    visible_provider_names = {
        _normalize_text(item.get("provider_name", ""))
        for item in _linked_provider_records(user, provider_contracts)
        if _normalize_text(item.get("provider_name", ""))
    }
    filtered: list[dict[str, Any]] = []
    for item in sessions:
        client_id = str(item.get("client_id", "")).strip()
        provider_id = str(item.get("provider_contract_id", "") or item.get("provider_id", "")).strip()
        provider_name = _normalize_text(item.get("provider_name", "") or item.get("linked_provider_name", ""))
        if client_id and client_id in visible_client_ids:
            filtered.append(item)
            continue
        if provider_id and provider_id in visible_provider_ids:
            filtered.append(item)
            continue
        if provider_name and provider_name in visible_provider_names:
            filtered.append(item)
            continue
        if normalized_role == "BCBA" and client_id in visible_client_ids:
            filtered.append(item)
    return filtered


def filter_claims_for_user(
    user: dict[str, Any] | None,
    claims: list[dict[str, Any]],
    visible_clients: list[dict[str, Any]],
    provider_contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not user:
        return []
    normalized_role = normalized_role_from_user(user, provider_contracts)
    if normalized_role == "RBT" and not has_permission(user, "claims.view", provider_contracts):
        return []
    if has_permission(user, "claims.view", provider_contracts) and not has_permission(user, "clients.assigned.view", provider_contracts):
        return list(claims)
    visible_member_ids = {str(item.get("member_id", "")).strip() for item in visible_clients if str(item.get("member_id", "")).strip()}
    visible_client_names = {_normalize_text(_client_name(item)) for item in visible_clients if _normalize_text(_client_name(item))}
    visible_provider_names = {
        _normalize_text(item.get("provider_name", ""))
        for item in _linked_provider_records(user, provider_contracts)
        if _normalize_text(item.get("provider_name", ""))
    }
    filtered = []
    for item in claims:
        patient_name = _normalize_text(item.get("patient_name", ""))
        member_id = str(item.get("member_id", "")).strip()
        rendering_provider = _normalize_text(item.get("rendering_provider_name", ""))
        if member_id and member_id in visible_member_ids:
            filtered.append(item)
            continue
        if patient_name and patient_name in visible_client_names:
            filtered.append(item)
            continue
        if rendering_provider and rendering_provider in visible_provider_names:
            filtered.append(item)
    return filtered


def is_assigned_client(
    user: dict[str, Any] | None,
    client: dict[str, Any] | None,
    provider_contracts: list[dict[str, Any]],
) -> bool:
    if not user or not client:
        return False
    visible = filter_clients_for_user(user, [client], provider_contracts)
    client_id = str(client.get("client_id", "")).strip()
    return any(str(item.get("client_id", "")).strip() == client_id for item in visible)


def is_assigned_session(
    user: dict[str, Any] | None,
    session: dict[str, Any] | None,
    visible_clients: list[dict[str, Any]],
    provider_contracts: list[dict[str, Any]],
) -> bool:
    if not user or not session:
        return False
    visible = filter_sessions_for_user(user, [session], visible_clients, provider_contracts)
    session_id = str(session.get("session_id", "")).strip()
    if session_id:
        return any(str(item.get("session_id", "")).strip() == session_id for item in visible)
    return bool(visible)


def is_assigned_candidate(
    user: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    provider_contracts: list[dict[str, Any]] | None = None,
) -> bool:
    if not user or not candidate:
        return False
    normalized_role = normalized_role_from_user(user, provider_contracts)
    if has_permission(user, "admin.full", provider_contracts) or normalized_role in {"MANAGER", "HR"}:
        return True
    if normalized_role == "RECRUITER":
        return _matches_user_assignment(user, candidate.get("recruiter_name", "")) or _matches_user_assignment(user, candidate.get("recruiter_id", ""))
    if normalized_role == "CREDENTIALING":
        return _matches_user_assignment(user, candidate.get("credentialing_owner_name", "")) or _matches_user_assignment(user, candidate.get("credentialing_specialist_id", ""))
    return False
