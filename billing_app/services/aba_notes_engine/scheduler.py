from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .models import Appointment, Client, DocumentType, Provider
from .notes import ServiceLog, build_weekly_service_logs, parse_approved_units, start_of_week


class SchedulingError(ValueError):
    pass


@dataclass(slots=True)
class NoteWorkflowState:
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
    caregiver_signature: str = ""
    caregiver_signed_at: datetime | None = None
    provider_signature: str = ""
    provider_signature_date: str = ""


@dataclass(slots=True)
class Scheduler:
    providers: dict[str, Provider] = field(default_factory=dict)
    clients: dict[str, Client] = field(default_factory=dict)
    appointments: list[Appointment] = field(default_factory=list)
    note_states: dict[str, NoteWorkflowState] = field(default_factory=dict)

    def add_provider(self, provider: Provider) -> None:
        self.providers[provider.id] = provider

    def add_client(self, client: Client) -> None:
        self.clients[client.id] = client

    def assign_client_to_provider(self, provider_id: str, client_id: str) -> None:
        provider = self.providers[provider_id]
        if client_id not in self.clients:
            raise SchedulingError(f"Client {client_id} does not exist.")
        provider.assign_client(client_id)

    def get_visible_clients(self, provider_id: str) -> list[Client]:
        provider = self.providers[provider_id]
        return [
            client
            for client in self.clients.values()
            if provider.can_view_client(client.id)
        ]

    def create_appointment(self, appointment: Appointment) -> None:
        if appointment.provider_id not in self.providers:
            raise SchedulingError("Provider does not exist.")
        if appointment.client_id not in self.clients:
            raise SchedulingError("Client does not exist.")
        if appointment.start_at >= appointment.end_at:
            raise SchedulingError("End time must be after start time.")

        provider = self.providers[appointment.provider_id]
        if not provider.can_view_client(appointment.client_id):
            raise SchedulingError(
                "Provider cannot schedule a client that is not assigned."
            )

        note_id = self._service_log_id(
            provider_id=appointment.provider_id,
            client_id=appointment.client_id,
            document_type=appointment.document_type,
            week_of=appointment.start_at.date(),
        )
        note_state = self.note_states.get(note_id)
        if note_state and note_state.closed_at is not None:
            raise SchedulingError(
                "This note is closed. You cannot add or change units for this week."
            )

        for existing in self.appointments:
            same_provider = existing.provider_id == appointment.provider_id
            same_client = existing.client_id == appointment.client_id
            if existing.overlaps(appointment) and (same_provider or same_client):
                raise SchedulingError(
                    "This schedule conflicts with another appointment."
                )

        units_needed = self._appointment_authorization_units(appointment)
        remaining_units = self.get_remaining_authorized_units(
            client_id=appointment.client_id,
            as_of=appointment.start_at.date(),
        )
        shortages = [
            f"{code}: need {units}, remaining {remaining_units.get(code, 0)}"
            for code, units in units_needed.items()
            if remaining_units.get(code, 0) < units
        ]
        if shortages:
            raise SchedulingError(
                "Not enough authorized units. " + "; ".join(shortages)
            )

        self.appointments.append(appointment)

    def get_weekly_service_logs(
        self,
        *,
        week_of: date,
        provider_id: str | None = None,
    ) -> list[ServiceLog]:
        logs = build_weekly_service_logs(
            appointments=self.appointments,
            providers=self.providers,
            clients=self.clients,
            week_of=week_of,
        )
        for log in logs:
            state = self.note_states.get(log.id)
            if state is not None:
                log.reviewed_by = state.reviewed_by
                log.reviewed_at = state.reviewed_at
                log.closed_by = state.closed_by
                log.closed_at = state.closed_at
                log.rejected_by = state.rejected_by
                log.rejected_at = state.rejected_at
                log.rejected_reason = state.rejected_reason
                log.reopened_by = state.reopened_by
                log.reopened_at = state.reopened_at
                log.reopen_reason = state.reopen_reason
                log.caregiver_signature = state.caregiver_signature
                log.caregiver_signed_at = state.caregiver_signed_at
                log.provider_signature = state.provider_signature
                log.provider_signature_date = state.provider_signature_date
            if not log.caregiver_signature:
                fallback_entry = next(
                    (
                        entry
                        for entry in sorted(log.entries, key=lambda item: item.session_end_at, reverse=True)
                        if str(entry.caregiver_signature or "").strip()
                    ),
                    None,
                )
                if fallback_entry is not None:
                    log.caregiver_signature = fallback_entry.caregiver_signature
                    if log.caregiver_signed_at is None:
                        log.caregiver_signed_at = fallback_entry.session_end_at
            log.remaining_authorized_units = self.get_remaining_authorized_units(
                client_id=log.client_id,
                as_of=week_of,
            )
        if provider_id is None:
            return logs
        return [log for log in logs if log.provider_id == provider_id]

    def get_note_deadlines(
        self,
        *,
        week_of: date,
        provider_id: str | None = None,
        now: datetime | None = None,
    ) -> list[tuple[ServiceLog, str]]:
        logs = self.get_weekly_service_logs(week_of=week_of, provider_id=provider_id)
        if now is None:
            now = datetime.now()
        return [(log, log.deadline_status(now)) for log in logs]

    def review_service_log(
        self,
        *,
        log_id: str,
        supervisor_name: str,
        provider_signature: str = "",
    ) -> None:
        state = self.note_states.setdefault(log_id, NoteWorkflowState())
        state.reviewed_by = supervisor_name
        state.reviewed_at = datetime.now()
        state.rejected_by = ""
        state.rejected_at = None
        state.rejected_reason = ""
        if provider_signature:
            state.provider_signature = provider_signature
            state.provider_signature_date = datetime.now().strftime("%Y-%m-%d")

    def close_service_log(
        self,
        *,
        log_id: str,
        supervisor_name: str,
        caregiver_signature: str = "",
        provider_signature: str = "",
    ) -> None:
        state = self.note_states.setdefault(log_id, NoteWorkflowState())
        if state.reviewed_at is None:
            raise SchedulingError("The supervisor must review the note before closing it.")
        state.closed_by = supervisor_name
        state.closed_at = datetime.now()
        if caregiver_signature:
            state.caregiver_signature = caregiver_signature
            state.caregiver_signed_at = datetime.now()
        if provider_signature:
            state.provider_signature = provider_signature
            state.provider_signature_date = datetime.now().strftime("%Y-%m-%d")

    def reject_service_log(
        self,
        *,
        log_id: str,
        supervisor_name: str,
        reason: str,
    ) -> None:
        if not reason.strip():
            raise SchedulingError("A rejection reason is required.")
        state = self.note_states.setdefault(log_id, NoteWorkflowState())
        state.rejected_by = supervisor_name
        state.rejected_at = datetime.now()
        state.rejected_reason = reason.strip()
        state.reviewed_by = ""
        state.reviewed_at = None
        state.closed_by = ""
        state.closed_at = None

    def reopen_service_log(
        self,
        *,
        log_id: str,
        supervisor_name: str,
        reason: str,
    ) -> None:
        if not reason.strip():
            raise SchedulingError("A reopen reason is required.")
        state = self.note_states.setdefault(log_id, NoteWorkflowState())
        if state.closed_at is None and state.rejected_at is None:
            raise SchedulingError("Only closed or rejected notes can be reopened.")
        state.closed_by = ""
        state.closed_at = None
        state.reviewed_by = ""
        state.reviewed_at = None
        state.reopened_by = supervisor_name
        state.reopened_at = datetime.now()
        state.reopen_reason = reason.strip()

    def get_remaining_authorized_units(self, *, client_id: str, as_of: date) -> dict[str, int]:
        client = self.clients[client_id]
        remaining = parse_approved_units(client.approved_units)
        for appointment in self.appointments:
            if appointment.client_id != client_id:
                continue
            if appointment.start_at.date() > as_of:
                continue
            for code, units in self._appointment_authorization_units(appointment).items():
                remaining[code] = remaining.get(code, 0) - units
        return remaining

    def _appointment_authorization_units(self, appointment: Appointment) -> dict[str, int]:
        units = round((appointment.end_at - appointment.start_at).total_seconds() / 900)
        base_code = appointment.service_code.value
        if appointment.service_context.name == "SUPERVISION_RBT":
            provider = self.providers[appointment.provider_id]
            if provider.role.name == "BCBA":
                return {"97155": units, "97153": units}
        return {base_code: units}

    def _service_log_id(
        self,
        *,
        provider_id: str,
        client_id: str,
        document_type: DocumentType,
        week_of: date,
    ) -> str:
        week_start = start_of_week(week_of)
        return f"log-{provider_id}-{client_id}-{document_type.value}-{week_start.isoformat()}"
