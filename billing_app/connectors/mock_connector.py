from __future__ import annotations

from billing_app.connectors.base import BillingConnector
from billing_app.models import Claim, EligibilityRequest, EligibilityResponse


class MockClearinghouseConnector(BillingConnector):
    """Mock connector to validate the application flow before a real integration."""

    def submit_claim(self, claim: Claim, edi_payload: str) -> dict:
        return {
            "status": "accepted",
            "claim_id": claim.claim_id,
            "payer_id": claim.insurance.payer_id,
            "tracking_id": f"TRK-{claim.claim_id}",
            "edi_preview": edi_payload[:180],
        }

    def check_eligibility(self, request: EligibilityRequest) -> EligibilityResponse:
        is_eligible = bool(
            request.member_id
            and request.patient_first_name
            and request.patient_last_name
            and request.patient_birth_date
        )
        messages = ["Active coverage found"] if is_eligible else ["Missing patient or policy data"]
        return EligibilityResponse(
            is_eligible=is_eligible,
            coverage_status="ACTIVE" if is_eligible else "NOT_FOUND",
            plan_name="Commercial PPO" if is_eligible else None,
            subscriber_id=request.member_id,
            messages=messages,
        )
