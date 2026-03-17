from __future__ import annotations

from abc import ABC, abstractmethod

from billing_app.models import Claim, EligibilityRequest, EligibilityResponse


class BillingConnector(ABC):
    @abstractmethod
    def submit_claim(self, claim: Claim, edi_payload: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def check_eligibility(self, request: EligibilityRequest) -> EligibilityResponse:
        raise NotImplementedError
