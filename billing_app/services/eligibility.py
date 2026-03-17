from __future__ import annotations

from billing_app.connectors.base import BillingConnector
from billing_app.models import EligibilityRequest, EligibilityResponse


class EligibilityService:
    def __init__(self, connector: BillingConnector) -> None:
        self.connector = connector

    def check(self, request: EligibilityRequest) -> EligibilityResponse:
        return self.connector.check_eligibility(request)
