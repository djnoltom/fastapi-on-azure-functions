from __future__ import annotations

from billing_app.models import Parsed837
from billing_app.services.date_utils import format_user_date


def _empty_parsed_837() -> Parsed837:
    return Parsed837(
        transaction_set_control_number="",
        payer_name=None,
        patient_name=None,
        member_id=None,
        provider_name=None,
        provider_npi=None,
        claim_id=None,
        service_date=None,
        total_charge_amount=None,
    )


class Claim837Parser:
    """Parses one or many professional 837 claims from the same X12 payload."""

    def parse(self, content: str) -> Parsed837:
        claims = self.parse_many(content)
        return claims[0] if claims else _empty_parsed_837()

    def parse_many(self, content: str) -> list[Parsed837]:
        segments = [segment.strip() for segment in content.split("~") if segment.strip()]
        if not segments:
            return []

        claims: list[Parsed837] = []
        tx_control = ""
        payer_name = None
        payer_id = None
        provider_name = None
        provider_npi = None
        provider_taxonomy_code = None
        current_entity = ""
        current_patient = self._blank_patient()
        current_claim: dict | None = None

        def finalize_current_claim() -> None:
            nonlocal current_claim
            if current_claim is None:
                return
            claims.append(
                Parsed837(
                    transaction_set_control_number=tx_control,
                    payer_name=current_claim.get("payer_name"),
                    patient_name=current_claim.get("patient_name"),
                    member_id=current_claim.get("member_id"),
                    provider_name=current_claim.get("provider_name"),
                    provider_npi=current_claim.get("provider_npi"),
                    claim_id=current_claim.get("claim_id"),
                    service_date=current_claim.get("service_date"),
                    total_charge_amount=current_claim.get("total_charge_amount"),
                    payer_id=current_claim.get("payer_id"),
                    provider_taxonomy_code=current_claim.get("provider_taxonomy_code"),
                    patient_birth_date=current_claim.get("patient_birth_date"),
                    patient_gender=current_claim.get("patient_gender"),
                    patient_address_line1=current_claim.get("patient_address_line1"),
                    patient_address_city=current_claim.get("patient_address_city"),
                    patient_address_state=current_claim.get("patient_address_state"),
                    patient_address_zip_code=current_claim.get("patient_address_zip_code"),
                    diagnosis_codes=list(current_claim.get("diagnosis_codes", [])),
                    service_lines=list(current_claim.get("service_lines", [])),
                )
            )
            current_claim = None

        for segment in segments:
            elements = segment.split("*")
            tag = elements[0]

            if tag == "ST" and len(elements) > 2:
                finalize_current_claim()
                tx_control = elements[2]
                current_entity = ""
                continue

            if tag == "HL" and len(elements) > 3 and elements[3] == "22":
                finalize_current_claim()
                current_entity = ""
                current_patient = self._blank_patient()
                continue

            if tag == "SE":
                finalize_current_claim()
                current_entity = ""
                continue

            if tag == "NM1" and len(elements) > 2:
                entity_code = elements[1]
                current_entity = entity_code
                if entity_code == "40":
                    payer_name = elements[3] if len(elements) > 3 else payer_name
                    payer_id = elements[-1] if len(elements) > 1 else payer_id
                elif entity_code == "85":
                    provider_name = elements[3] if len(elements) > 3 else provider_name
                    provider_npi = elements[-1] if len(elements) > 1 else provider_npi
                elif entity_code == "IL":
                    finalize_current_claim()
                    last_name = elements[3] if len(elements) > 3 else ""
                    first_name = elements[4] if len(elements) > 4 else ""
                    current_patient = {
                        "patient_name": " ".join(part for part in (first_name, last_name) if part).strip() or None,
                        "member_id": elements[-1] if len(elements) > 1 else None,
                        "patient_birth_date": "",
                        "patient_gender": "U",
                        "patient_address_line1": "",
                        "patient_address_city": "",
                        "patient_address_state": "",
                        "patient_address_zip_code": "",
                    }
                elif entity_code == "PR":
                    payer_name = elements[3] if len(elements) > 3 else payer_name
                    payer_id = elements[-1] if len(elements) > 1 else payer_id
                continue

            if tag == "PRV" and len(elements) > 3 and elements[1] == "BI":
                provider_taxonomy_code = elements[3]
                if current_claim is not None:
                    current_claim["provider_taxonomy_code"] = provider_taxonomy_code
                continue

            if tag == "N3" and len(elements) > 1 and current_entity == "IL":
                current_patient["patient_address_line1"] = elements[1]
                if current_claim is not None:
                    current_claim["patient_address_line1"] = elements[1]
                continue

            if tag == "N4" and len(elements) > 3 and current_entity == "IL":
                current_patient["patient_address_city"] = elements[1]
                current_patient["patient_address_state"] = elements[2]
                current_patient["patient_address_zip_code"] = elements[3]
                if current_claim is not None:
                    current_claim["patient_address_city"] = elements[1]
                    current_claim["patient_address_state"] = elements[2]
                    current_claim["patient_address_zip_code"] = elements[3]
                continue

            if tag == "DMG" and len(elements) > 3 and current_entity == "IL":
                try:
                    current_patient["patient_birth_date"] = format_user_date(elements[2])
                except ValueError:
                    current_patient["patient_birth_date"] = elements[2]
                current_patient["patient_gender"] = elements[3] or "U"
                if current_claim is not None:
                    current_claim["patient_birth_date"] = current_patient["patient_birth_date"]
                    current_claim["patient_gender"] = current_patient["patient_gender"]
                continue

            if tag == "CLM" and len(elements) > 2:
                finalize_current_claim()
                try:
                    total_charge_amount = float(elements[2])
                except ValueError:
                    total_charge_amount = None
                current_claim = {
                    "payer_name": payer_name,
                    "payer_id": payer_id,
                    "patient_name": current_patient.get("patient_name"),
                    "member_id": current_patient.get("member_id"),
                    "provider_name": provider_name,
                    "provider_npi": provider_npi,
                    "provider_taxonomy_code": provider_taxonomy_code,
                    "claim_id": elements[1],
                    "service_date": None,
                    "total_charge_amount": total_charge_amount,
                    "patient_birth_date": current_patient.get("patient_birth_date", ""),
                    "patient_gender": current_patient.get("patient_gender", "U"),
                    "patient_address_line1": current_patient.get("patient_address_line1", ""),
                    "patient_address_city": current_patient.get("patient_address_city", ""),
                    "patient_address_state": current_patient.get("patient_address_state", ""),
                    "patient_address_zip_code": current_patient.get("patient_address_zip_code", ""),
                    "diagnosis_codes": [],
                    "service_lines": [],
                }
                continue

            if current_claim is None:
                continue

            if tag == "HI":
                diagnosis_codes: list[str] = []
                for value in elements[1:]:
                    if ":" in value:
                        _, code = value.split(":", 1)
                        if code:
                            diagnosis_codes.append(code)
                    elif value:
                        diagnosis_codes.append(value)
                current_claim["diagnosis_codes"] = diagnosis_codes
            elif tag == "DTP" and len(elements) > 3 and elements[1] == "472":
                try:
                    current_claim["service_date"] = format_user_date(elements[3])
                except ValueError:
                    current_claim["service_date"] = elements[3]
            elif tag == "SV1" and len(elements) > 4:
                procedure_part = elements[1].replace("HC:", "")
                procedure_code = procedure_part.replace(":", "-")
                try:
                    charge_amount = float(elements[2])
                except ValueError:
                    charge_amount = 0.0
                try:
                    units = int(float(elements[4]))
                except ValueError:
                    units = 0
                current_claim["service_lines"].append(
                    {
                        "procedure_code": procedure_code,
                        "charge_amount": charge_amount,
                        "units": units,
                    }
                )

        finalize_current_claim()
        return claims

    @staticmethod
    def _blank_patient() -> dict[str, str | None]:
        return {
            "patient_name": None,
            "member_id": None,
            "patient_birth_date": "",
            "patient_gender": "U",
            "patient_address_line1": "",
            "patient_address_city": "",
            "patient_address_state": "",
            "patient_address_zip_code": "",
        }
