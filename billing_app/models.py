from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Address:
    line1: str
    city: str
    state: str
    zip_code: str


@dataclass
class Provider:
    npi: str
    taxonomy_code: str
    first_name: str
    last_name: str
    organization_name: Optional[str] = None


@dataclass
class Patient:
    member_id: str
    first_name: str
    last_name: str
    birth_date: str
    gender: str
    address: Address


@dataclass
class InsurancePolicy:
    payer_name: str
    payer_id: str
    policy_number: str
    plan_name: Optional[str] = None


@dataclass
class ServiceLine:
    procedure_code: str
    charge_amount: float
    units: int
    unit_price: float = 0.0
    diagnosis_pointer: str = "1"


@dataclass
class Claim:
    claim_id: str
    provider: Provider
    patient: Patient
    insurance: InsurancePolicy
    service_date: str
    diagnosis_codes: List[str]
    service_lines: List[ServiceLine]
    total_charge_amount: float


@dataclass
class EligibilityRequest:
    payer_id: str
    provider_npi: str
    member_id: str
    patient_first_name: str
    patient_last_name: str
    patient_birth_date: str
    service_date: str
    patient_middle_name: str = ""
    patient_gender: str = ""


@dataclass
class EligibilityResponse:
    is_eligible: bool
    coverage_status: str
    plan_name: Optional[str]
    subscriber_id: str
    messages: List[str] = field(default_factory=list)


@dataclass
class Parsed835:
    transaction_set_control_number: str
    payer_name: Optional[str]
    payee_name: Optional[str]
    payment_amount: Optional[float]
    claim_statuses: List[str] = field(default_factory=list)
    claim_details: List[dict] = field(default_factory=list)


@dataclass
class Parsed837:
    transaction_set_control_number: str
    payer_name: Optional[str]
    patient_name: Optional[str]
    member_id: Optional[str]
    provider_name: Optional[str]
    provider_npi: Optional[str]
    claim_id: Optional[str]
    service_date: Optional[str]
    total_charge_amount: Optional[float]
    payer_id: Optional[str] = None
    provider_taxonomy_code: Optional[str] = None
    patient_birth_date: Optional[str] = None
    patient_gender: Optional[str] = None
    patient_address_line1: Optional[str] = None
    patient_address_city: Optional[str] = None
    patient_address_state: Optional[str] = None
    patient_address_zip_code: Optional[str] = None
    diagnosis_codes: List[str] = field(default_factory=list)
    service_lines: List[dict] = field(default_factory=list)
