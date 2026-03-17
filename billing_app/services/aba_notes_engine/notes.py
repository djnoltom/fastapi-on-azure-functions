from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .models import Appointment, Client, DocumentType, Provider, ProviderRole, ServiceContext


@dataclass(slots=True, frozen=True)
class ServiceLogEntry:
    appointment_id: str
    session_date: date
    start_time: str
    end_time: str
    session_end_at: datetime
    note_due_at: datetime
    hours: float
    units: int
    billing_code: str
    units_display: str
    place_of_service: str
    caregiver_name: str
    caregiver_signature: str
    unit_rate: float


@dataclass(slots=True)
class ServiceLog:
    id: str
    week_start: date
    week_end: date
    document_type: DocumentType
    provider_id: str
    provider_name: str
    provider_credentials: str
    client_id: str
    client_name: str
    insurance_id: str
    diagnoses: str
    pa_number: str
    pa_start_date: str
    pa_end_date: str
    approved_units: str
    entries: list[ServiceLogEntry] = field(default_factory=list)
    caregiver_name: str = ""
    caregiver_signature: str = ""
    caregiver_signed_at: datetime | None = None
    provider_signature: str = ""
    provider_signature_date: str = ""
    notes: str = ""
    reviewed_by: str = ""
    reviewed_at: datetime | None = None
    closed_by: str = ""
    closed_at: datetime | None = None
    rejected_by: str = ""
    rejected_at: datetime | None = None
    rejected_reason: str = ""
    reopened_by: str = ""
    reopened_at: datetime | None = None
    reopen_reason: str = ""
    remaining_authorized_units: dict[str, int] = field(default_factory=dict)

    @property
    def total_units(self) -> int:
        return sum(entry.units for entry in self.entries)

    @property
    def total_hours(self) -> float:
        return sum(entry.hours for entry in self.entries)

    @property
    def total_amount(self) -> float:
        return sum(entry.unit_rate * entry.units for entry in self.entries)

    @property
    def total_days(self) -> int:
        return len({entry.session_date for entry in self.entries})

    @property
    def signed(self) -> bool:
        return bool(self.caregiver_signature)

    @property
    def latest_note_due_at(self) -> datetime | None:
        if not self.entries:
            return None
        return max(entry.note_due_at for entry in self.entries)

    def deadline_status(self, now: datetime | None = None) -> str:
        if not self.entries:
            return "no_entries"
        if now is None:
            now = datetime.now()
        latest_due = self.latest_note_due_at
        if latest_due is None:
            return "no_entries"
        if now > latest_due:
            return "late"
        if now + timedelta(hours=12) >= latest_due:
            return "due_soon"
        return "on_time"

    @property
    def is_reviewed(self) -> bool:
        return self.reviewed_at is not None

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None


def start_of_week(value: date) -> date:
    return value - timedelta(days=value.weekday())


def parse_approved_units(raw_value: str) -> dict[str, int]:
    parsed: dict[str, int] = {}
    if not raw_value.strip():
        return parsed
    for chunk in raw_value.split(","):
        piece = chunk.strip()
        if ":" not in piece:
            continue
        code, amount = piece.split(":", 1)
        try:
            parsed[code.strip()] = int(amount.strip())
        except ValueError:
            continue
    return parsed


def build_weekly_service_logs(
    *,
    appointments: list[Appointment],
    providers: dict[str, Provider],
    clients: dict[str, Client],
    week_of: date,
) -> list[ServiceLog]:
    week_start = start_of_week(week_of)
    week_end = week_start + timedelta(days=6)
    grouped: dict[tuple[str, str, DocumentType], ServiceLog] = {}

    def build_units_display(appointment: Appointment, provider: Provider, units: int) -> str:
        if (
            provider.role == ProviderRole.BCBA
            and appointment.service_context == ServiceContext.SUPERVISION_RBT
        ):
            return f"97155 ({units}), 97153-XP ({units})***"
        return f"{appointment.billing_code.replace('CPT-', '')} ({units})"

    for appointment in sorted(appointments, key=lambda item: item.start_at):
        session_day = appointment.start_at.date()
        if session_day < week_start or session_day > week_end:
            continue

        provider = providers[appointment.provider_id]
        client = clients[appointment.client_id]
        key = (provider.id, client.id, appointment.document_type)

        if key not in grouped:
            grouped[key] = ServiceLog(
                id=(
                    f"log-{provider.id}-{client.id}-"
                    f"{appointment.document_type.value}-{week_start.isoformat()}"
                ),
                week_start=week_start,
                week_end=week_end,
                document_type=appointment.document_type,
                provider_id=provider.id,
                provider_name=provider.full_name,
                provider_credentials=provider.credentials,
                client_id=client.id,
                client_name=client.full_name,
                insurance_id=client.insurance_id,
                diagnoses=client.diagnoses,
                pa_number=client.pa_number,
                pa_start_date=client.pa_start_date,
                pa_end_date=client.pa_end_date,
                approved_units=client.approved_units,
                caregiver_name=client.caregiver_name,
            )

        duration_hours = (appointment.end_at - appointment.start_at).total_seconds() / 3600
        units = round(duration_hours / 0.25)
        grouped[key].entries.append(
            ServiceLogEntry(
                appointment_id=appointment.id,
                session_date=session_day,
                start_time=appointment.start_at.strftime("%H:%M"),
                end_time=appointment.end_at.strftime("%H:%M"),
                session_end_at=appointment.end_at,
                note_due_at=appointment.end_at + timedelta(hours=48),
                hours=round(duration_hours, 2),
                units=units,
                billing_code=appointment.billing_code,
                units_display=build_units_display(appointment, provider, units),
                place_of_service=appointment.place_of_service,
                caregiver_name=appointment.caregiver_name or client.caregiver_name,
                caregiver_signature=appointment.caregiver_signature,
                unit_rate=appointment.unit_rate,
            )
        )
        if appointment.caregiver_signature:
            grouped[key].caregiver_signature = appointment.caregiver_signature
            grouped[key].caregiver_signed_at = appointment.end_at

    return list(grouped.values())
