from __future__ import annotations

from datetime import datetime

from billing_app.models import Claim
from billing_app.services.date_utils import format_edi_date


def _sanitize_amount(value: float) -> str:
    return f"{value:.2f}".replace(".", "")


def _format_procedure_code(value: str) -> str:
    clean = value.strip().replace("CPT ", "").strip()
    for separator in ("-", " "):
        if separator in clean:
            code, modifier = [part.strip() for part in clean.split(separator, 1)]
            if modifier:
                return f"{code}:{modifier}"
    return clean


class Claim837Builder:
    """Generates a compact 837P-like X12 payload for MVP/demo purposes."""

    def build_professional_claim(self, claim: Claim) -> str:
        now = datetime.utcnow()
        date_stamp = now.strftime("%y%m%d")
        time_stamp = now.strftime("%H%M")
        control_number = claim.claim_id[-9:].rjust(9, "0")
        current_edi_date = now.strftime("%Y%m%d")

        patient = claim.patient
        provider = claim.provider
        insurance = claim.insurance
        provider_name = (provider.organization_name or f"{provider.first_name} {provider.last_name}").strip() or "DEMO CLINIC"

        segments = [
            f"ISA*00*          *00*          *ZZ*SENDERID       *ZZ*{insurance.payer_id:<15}*{date_stamp}*{time_stamp}*^*00501*{control_number}*1*T*:",
            f"GS*HC*SENDER*RECEIVER*{current_edi_date}*{time_stamp}*1*X*005010X222A1",
            "ST*837*0001*005010X222A1",
            f"BHT*0019*00*{claim.claim_id}*{current_edi_date}*{time_stamp}*CH",
            "NM1*41*2*BILLING APP*****46*SENDERID",
            "PER*IC*SUPPORT*TE*5555551212",
            f"NM1*40*2*{insurance.payer_name}*****46*{insurance.payer_id}",
            "HL*1**20*1",
            f"NM1*85*2*{provider_name}*****XX*{provider.npi}",
            f"PRV*BI*PXC*{provider.taxonomy_code}",
            "HL*2*1*22*0",
            f"SBR*P*18*******CI",
            f"NM1*IL*1*{patient.last_name}*{patient.first_name}****MI*{patient.member_id}",
            f"N3*{patient.address.line1}",
            f"N4*{patient.address.city}*{patient.address.state}*{patient.address.zip_code}",
            f"DMG*D8*{format_edi_date(patient.birth_date)}*{patient.gender}",
            "CLM*"
            + f"{claim.claim_id}*{claim.total_charge_amount:.2f}***11:B:1*Y*A*Y*I",
        ]
        if claim.diagnosis_codes:
            segments.append("HI*" + "*".join(f"ABK:{code}" for code in claim.diagnosis_codes))

        lx = 1
        for line in claim.service_lines:
            segments.extend(
                [
                    f"LX*{lx}",
                    f"SV1*HC:{_format_procedure_code(line.procedure_code)}*{line.charge_amount:.2f}*UN*{line.units}***{line.diagnosis_pointer}",
                    f"DTP*472*D8*{format_edi_date(claim.service_date)}",
                ]
            )
            lx += 1

        segment_count = len(segments) + 1
        segments.extend(
            [
                f"SE*{segment_count}*0001",
                "GE*1*1",
                f"IEA*1*{control_number}",
            ]
        )
        return "~".join(segments) + "~"
