from __future__ import annotations

from .models import BillingRule, ProviderRole, ServiceCode, ServiceContext, ServiceModifier
from .rates import get_rate_entry


def get_billing_rule(
    provider_role: ProviderRole,
    context: ServiceContext,
) -> BillingRule:
    if provider_role in {ProviderRole.BCBA, ProviderRole.BCABA} and context == ServiceContext.ASSESSMENT:
        rate = get_rate_entry("CPT-97151")
        return BillingRule(
            service_code=ServiceCode.CPT_97151,
            modifier=None,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=True,
            counts_for_lead=provider_role == ProviderRole.BCBA,
            description="Assessment billed as 97151.",
        )

    if provider_role in {ProviderRole.BCBA, ProviderRole.BCABA} and context == ServiceContext.REASSESSMENT:
        rate = get_rate_entry("CPT-97151-TS")
        return BillingRule(
            service_code=ServiceCode.CPT_97151,
            modifier=ServiceModifier.TS,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=True,
            counts_for_lead=provider_role == ProviderRole.BCBA,
            description="Reassessment billed as 97151-TS.",
        )

    if provider_role == ProviderRole.RBT and context == ServiceContext.DIRECT:
        rate = get_rate_entry("CPT-97153")
        return BillingRule(
            service_code=ServiceCode.CPT_97153,
            modifier=None,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=True,
            counts_for_lead=False,
            description="RBT direct service.",
        )

    if provider_role == ProviderRole.BCBA and context == ServiceContext.DIRECT:
        rate = get_rate_entry("CPT-97155")
        return BillingRule(
            service_code=ServiceCode.CPT_97155,
            modifier=None,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=True,
            counts_for_lead=True,
            description="BCBA direct analyst service.",
        )

    if provider_role == ProviderRole.BCBA and context == ServiceContext.SUPERVISION_RBT:
        rate = get_rate_entry("CPT-97153-XP")
        return BillingRule(
            service_code=ServiceCode.CPT_97153,
            modifier=ServiceModifier.XP,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=False,
            counts_for_lead=True,
            description="BCBA supervising an RBT; billed as 97153-XP and counts to lead only.",
        )

    if provider_role == ProviderRole.BCBA and context == ServiceContext.SUPERVISION_BCABA:
        rate = get_rate_entry("CPT-97155-XP")
        return BillingRule(
            service_code=ServiceCode.CPT_97155,
            modifier=ServiceModifier.XP,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=False,
            counts_for_lead=True,
            description="BCBA supervising a BCaBA; billed as 97155-XP and counts to lead only.",
        )

    if provider_role == ProviderRole.BCABA and context == ServiceContext.SUPERVISION_RBT:
        rate = get_rate_entry("CPT-97155-HN")
        return BillingRule(
            service_code=ServiceCode.CPT_97155,
            modifier=ServiceModifier.HN,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=True,
            counts_for_lead=False,
            description="BCaBA supervising an RBT; billed as 97155-HN.",
        )

    if provider_role == ProviderRole.BCBA and context == ServiceContext.PARENT_TRAINING:
        rate = get_rate_entry("CPT-97156")
        return BillingRule(
            service_code=ServiceCode.CPT_97156,
            modifier=None,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=True,
            counts_for_lead=True,
            description="BCBA parent training.",
        )

    if provider_role == ProviderRole.BCABA and context == ServiceContext.PARENT_TRAINING:
        rate = get_rate_entry("CPT-97156-HN")
        return BillingRule(
            service_code=ServiceCode.CPT_97156,
            modifier=ServiceModifier.HN,
            unit_rate=rate.unit_rate,
            billable=True,
            payable=True,
            counts_for_lead=False,
            description="BCaBA parent training; billed as 97156-HN.",
        )

    raise ValueError(
        f"No billing rule configured for role={provider_role.value} context={context.value}."
    )
