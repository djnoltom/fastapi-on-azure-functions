from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ProviderRole(str, Enum):
    BCBA = "BCBA"
    BCABA = "BCaBA"
    RBT = "RBT"


class ServiceCode(str, Enum):
    CPT_97151 = "97151"
    CPT_97153 = "97153"
    CPT_97155 = "97155"
    CPT_97156 = "97156"


class ServiceModifier(str, Enum):
    XP = "XP"
    HN = "HN"
    TS = "TS"


class ServiceContext(str, Enum):
    ASSESSMENT = "assessment"
    REASSESSMENT = "reassessment"
    DIRECT = "direct"
    SUPERVISION_RBT = "supervision_rbt"
    SUPERVISION_BCABA = "supervision_bcaba"
    PARENT_TRAINING = "parent_training"


class DocumentType(str, Enum):
    APPOINTMENT_NOTE = "Appointment Note"
    ANALYST_SERVICE_LOG = "Analyst Service Log"
    RBT_SERVICE_LOG = "RBT Service Log"
    SUPERVISION_LOG = "Supervision Log"
    SUPERVISION_SERVICE_LOG = "Supervision Service Log"
    ASSESSMENT = "Assessment"
    REASSESSMENT = "Reassessment"


@dataclass(slots=True)
class Client:
    id: str
    full_name: str
    insurance_id: str = ""
    diagnoses: str = ""
    pa_number: str = ""
    pa_start_date: str = ""
    pa_end_date: str = ""
    approved_units: str = ""
    caregiver_name: str = ""


@dataclass(slots=True)
class Provider:
    id: str
    full_name: str
    role: ProviderRole
    credentials: str = ""
    assigned_client_ids: set[str] = field(default_factory=set)

    def assign_client(self, client_id: str) -> None:
        self.assigned_client_ids.add(client_id)

    def can_view_client(self, client_id: str) -> bool:
        return client_id in self.assigned_client_ids


@dataclass(slots=True)
class Appointment:
    id: str
    provider_id: str
    client_id: str
    start_at: datetime
    end_at: datetime
    service_context: ServiceContext
    service_code: ServiceCode
    service_modifier: ServiceModifier | None
    unit_rate: float
    document_type: DocumentType
    place_of_service: str = "Home (12)"
    caregiver_name: str = ""
    caregiver_signature: str = ""
    session_note: str = ""
    supervising_provider_id: str | None = None

    def overlaps(self, other: "Appointment") -> bool:
        return self.start_at < other.end_at and other.start_at < self.end_at

    @property
    def billing_code(self) -> str:
        if self.service_modifier is None:
            return f"CPT-{self.service_code.value}"
        return f"CPT-{self.service_code.value}-{self.service_modifier.value}"


@dataclass(slots=True, frozen=True)
class BillingRule:
    service_code: ServiceCode
    modifier: ServiceModifier | None
    unit_rate: float
    billable: bool
    payable: bool
    counts_for_lead: bool
    description: str

    @property
    def billing_code(self) -> str:
        if self.modifier is None:
            return f"CPT-{self.service_code.value}"
        return f"CPT-{self.service_code.value}-{self.modifier.value}"
