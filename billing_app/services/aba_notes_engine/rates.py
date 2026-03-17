from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class RateEntry:
    billing_code: str
    unit_rate: float


RATE_SCHEDULE: dict[str, RateEntry] = {
    "CPT-97153": RateEntry(billing_code="CPT-97153", unit_rate=12.26),
    "CPT-97155-HN": RateEntry(billing_code="CPT-97155-HN", unit_rate=15.37),
    "CPT-97155": RateEntry(billing_code="CPT-97155", unit_rate=19.17),
    "CPT-97151": RateEntry(billing_code="CPT-97151", unit_rate=19.05),
    "CPT-97151-TS": RateEntry(billing_code="CPT-97151-TS", unit_rate=19.05),
    "CPT-97156": RateEntry(billing_code="CPT-97156", unit_rate=19.05),
    "CPT-97156-HN": RateEntry(billing_code="CPT-97156-HN", unit_rate=15.24),
    "CPT-97153-XP": RateEntry(billing_code="CPT-97153-XP", unit_rate=0.0),
    "CPT-97155-XP": RateEntry(billing_code="CPT-97155-XP", unit_rate=0.0),
}


def get_rate_entry(billing_code: str) -> RateEntry:
    try:
        return RATE_SCHEDULE[billing_code]
    except KeyError as error:
        raise ValueError(f"No rate configured for billing code {billing_code}.") from error
