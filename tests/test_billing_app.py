import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from billing_app.models import (
    Address,
    Claim,
    InsurancePolicy,
    Patient,
    Provider,
    ServiceLine,
)
from billing_app.services.claim_builder import Claim837Builder, _format_procedure_code
from billing_app.services.claim_parser import Claim837Parser
from billing_app.services.date_utils import add_user_date_months
from billing_app.services import local_store
from billing_app.services.remit_parser import Era835Parser


class BillingAppTests(unittest.TestCase):
    def test_provider_document_status_marks_expired_when_date_passes(self) -> None:
        snapshot = local_store._provider_document_status_snapshot(
            requested_status="Delivered",
            approval_status="approved",
            expiration_date="03/01/2026",
            has_document=True,
            today=date(2026, 3, 13),
        )

        self.assertEqual(snapshot["display_status"], "Expired")
        self.assertTrue(snapshot["is_expired"])

    def test_provider_document_status_marks_expiring_soon_within_30_days(self) -> None:
        snapshot = local_store._provider_document_status_snapshot(
            requested_status="Delivered",
            approval_status="approved",
            expiration_date="04/01/2026",
            has_document=True,
            today=date(2026, 3, 13),
        )

        self.assertEqual(snapshot["display_status"], "Delivered")
        self.assertTrue(snapshot["expiring_soon"])

    def test_add_user_rejects_short_passwords(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            with (
                patch.object(local_store, "DATA_DIR", temp_path),
                patch.object(local_store, "UPLOADS_DIR", temp_path / "uploads"),
                patch.object(local_store, "USERS_FILE", temp_path / "users.json"),
                patch.object(local_store, "PASSWORD_RESET_TOKENS_FILE", temp_path / "password_reset_tokens.json"),
            ):
                with self.assertRaisesRegex(ValueError, "al menos 8 caracteres"):
                    local_store.add_user(
                        {
                            "username": "tester",
                            "full_name": "Test User",
                            "password": "1234567",
                        }
                    )

    def test_change_password_clears_outstanding_recovery_codes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            with (
                patch.object(local_store, "DATA_DIR", temp_path),
                patch.object(local_store, "UPLOADS_DIR", temp_path / "uploads"),
                patch.object(local_store, "USERS_FILE", temp_path / "users.json"),
                patch.object(local_store, "PASSWORD_RESET_TOKENS_FILE", temp_path / "password_reset_tokens.json"),
            ):
                local_store.add_user(
                    {
                        "username": "tester",
                        "full_name": "Test User",
                        "password": "ValidPass1",
                    }
                )
                local_store.create_password_reset_token("tester")

                self.assertEqual(len(local_store.load_password_reset_tokens()), 1)

                local_store.change_password("tester", "ValidPass1", "ValidPass2")

                self.assertEqual(local_store.load_password_reset_tokens(), [])

    def test_reset_password_rejects_short_passwords(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            with (
                patch.object(local_store, "DATA_DIR", temp_path),
                patch.object(local_store, "UPLOADS_DIR", temp_path / "uploads"),
                patch.object(local_store, "USERS_FILE", temp_path / "users.json"),
                patch.object(local_store, "PASSWORD_RESET_TOKENS_FILE", temp_path / "password_reset_tokens.json"),
            ):
                local_store.add_user(
                    {
                        "username": "tester",
                        "full_name": "Test User",
                        "password": "ValidPass1",
                    }
                )
                recovery = local_store.create_password_reset_token("tester")

                with self.assertRaisesRegex(ValueError, "al menos 8 caracteres"):
                    local_store.reset_password_with_recovery_code(
                        "tester",
                        recovery["recovery_code"],
                        "1234567",
                    )

    def test_add_user_date_months_keeps_mm_dd_yyyy_format(self) -> None:
        self.assertEqual(add_user_date_months("03/13/2026", 6), "09/13/2026")

    def test_system_configuration_persists_operational_values(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            with (
                patch.object(local_store, "DATA_DIR", temp_path),
                patch.object(local_store, "SETTINGS_FILE", temp_path / "settings.json"),
            ):
                saved = local_store.save_system_configuration(
                    {
                        "default_landing_page": "clients",
                        "session_timeout_minutes": "45",
                        "mfa_timeout_minutes": "12",
                        "password_reset_minutes": "25",
                        "lockout_attempts": "4",
                        "lockout_minutes": "20",
                        "billing_unit_minutes": "20",
                    }
                )

                self.assertEqual(saved["default_landing_page"], "clients")
                self.assertEqual(local_store.get_session_timeout_seconds(), 45 * 60)
                self.assertEqual(local_store.get_mfa_session_timeout_seconds(), 12 * 60)
                self.assertEqual(local_store.get_password_reset_minutes(), 25)
                self.assertEqual(local_store.get_lockout_attempts(), 4)
                self.assertEqual(local_store.get_lockout_minutes(), 20)
                self.assertEqual(local_store.get_billing_unit_minutes(), 20)

    def test_system_configuration_changes_next_eligibility_run_date(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            with (
                patch.object(local_store, "DATA_DIR", temp_path),
                patch.object(local_store, "SETTINGS_FILE", temp_path / "settings.json"),
            ):
                local_store.save_system_configuration({"eligibility_run_days": "5,20"})

                self.assertEqual(local_store._next_run_date(date(2026, 3, 14)), date(2026, 3, 20))
                self.assertEqual(local_store._next_run_date(date(2026, 3, 21)), date(2026, 4, 5))

    def test_procedure_code_modifiers_are_normalized_for_edi(self) -> None:
        self.assertEqual(_format_procedure_code("97151-TS"), "97151:TS")
        self.assertEqual(_format_procedure_code("97151 TS"), "97151:TS")
        self.assertEqual(_format_procedure_code("97155-HN"), "97155:HN")

    def test_claim_builder_outputs_837_markers(self) -> None:
        claim = Claim(
            claim_id="10004567",
            provider=Provider(
                npi="1234567893",
                taxonomy_code="207Q00000X",
                first_name="Ana",
                last_name="Lopez",
            ),
            patient=Patient(
                member_id="ABC123456",
                first_name="Juan",
                last_name="Perez",
                birth_date="19800115",
                gender="M",
                address=Address(
                    line1="123 Main St",
                    city="Los Angeles",
                    state="CA",
                    zip_code="90001",
                ),
            ),
            insurance=InsurancePolicy(
                payer_name="Demo Health Plan",
                payer_id="99999",
                policy_number="POL123456",
            ),
            service_date="20260311",
            diagnosis_codes=["J109"],
            service_lines=[
                ServiceLine(
                    procedure_code="99213",
                    charge_amount=125.00,
                    units=1,
                )
            ],
            total_charge_amount=125.00,
        )

        edi = Claim837Builder().build_professional_claim(claim)

        self.assertIn("ST*837*", edi)
        self.assertIn("CLM*10004567*125.00", edi)
        self.assertIn("SV1*HC:99213*125.00", edi)

    def test_claim_builder_uses_provider_organization_name(self) -> None:
        claim = Claim(
            claim_id="20004567",
            provider=Provider(
                npi="1234567893",
                taxonomy_code="207Q00000X",
                first_name="Ana",
                last_name="Lopez",
                organization_name="Blue Hope Behavioral Services",
            ),
            patient=Patient(
                member_id="ABC123456",
                first_name="Juan",
                last_name="Perez",
                birth_date="19800115",
                gender="M",
                address=Address(
                    line1="123 Main St",
                    city="Los Angeles",
                    state="CA",
                    zip_code="90001",
                ),
            ),
            insurance=InsurancePolicy(
                payer_name="Demo Health Plan",
                payer_id="99999",
                policy_number="POL123456",
            ),
            service_date="20260311",
            diagnosis_codes=["J109"],
            service_lines=[
                ServiceLine(
                    procedure_code="99213",
                    charge_amount=125.00,
                    units=1,
                )
            ],
            total_charge_amount=125.00,
        )

        edi = Claim837Builder().build_professional_claim(claim)

        self.assertIn("NM1*85*2*Blue Hope Behavioral Services", edi)

    def test_837_parser_splits_multiple_claims_in_one_file(self) -> None:
        content = (
            "ISA*00*          *00*          *ZZ*21794          *ZZ*CLAIMMD        *260314*0505*^*00501*100000325*1*P*:~"
            "GS*HC*21794*CLAIMMD*20260314*0505*100000325*X*005010X222A1~"
            "ST*837*000000000*005010X222A1~"
            "BHT*0019*00*00100000325*20260314*0505*CH~"
            "NM1*41*2*BLUE HOPE BEHAVIORAL SERVICES*****46*1508525882~"
            "NM1*40*2*SUNSHINE HEALTH*****46*68069~"
            "HL*1**20*1~"
            "PRV*BI*PXC*103K00000X~"
            "NM1*85*2*BLUE HOPE BEHAVIORAL SERVICES*****XX*1508525882~"
            "HL*2*1*22*0~"
            "SBR*P*18*******MC~"
            "NM1*IL*1*FRAGA CASTILLO*ALBERTO****MI*9657949131~"
            "N3*212 S CHURCH AVE. UNIT 101~"
            "N4*TAMPA*FL*336099998~"
            "DMG*D8*20180328*M~"
            "NM1*PR*2*SUNSHINE HEALTH*****PI*68069~"
            "CLM*1A5727D797D112C548DE*686.56***11:B:1*Y*A*Y*Y*P~"
            "HI*ABK:F840~"
            "LX*1~"
            "SV1*HC:97153*196.16*UN*16*12**1~"
            "DTP*472*D8*20260223~"
            "HL*3*1*22*0~"
            "SBR*P*18*******MC~"
            "NM1*IL*1*ANDRADES*ANDREA****MI*9557714158~"
            "N3*6860 HILLS DR~"
            "N4*NEW PORT RICHEY*FL*346539998~"
            "DMG*D8*20160711*F~"
            "NM1*PR*2*SUNSHINE HEALTH*****PI*68069~"
            "CLM*B1EA7F43F80F52546EA9*833.68***11:B:1*Y*A*Y*Y*P~"
            "HI*ABK:F902~"
            "LX*1~"
            "SV1*HC:97153*245.20*UN*20*12**1~"
            "DTP*472*D8*20260226~"
            "SE*30*000000000~"
            "GE*1*100000325~"
            "IEA*1*100000325~"
        )

        claims = Claim837Parser().parse_many(content)

        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0].claim_id, "1A5727D797D112C548DE")
        self.assertEqual(claims[0].patient_name, "ALBERTO FRAGA CASTILLO")
        self.assertEqual(claims[1].claim_id, "B1EA7F43F80F52546EA9")
        self.assertEqual(claims[1].patient_name, "ANDREA ANDRADES")
        self.assertEqual(claims[1].diagnosis_codes, ["F902"])
        self.assertEqual(len(claims[1].service_lines), 1)

    def test_parser_extracts_835_summary(self) -> None:
        content = (
            "ST*835*0001~"
            "BPR*I*150.00*C*CHK************20260311~"
            "N1*PR*Demo Health Plan~"
            "N1*PE*Demo Clinic~"
            "CLP*10004567*1*170.00*150.00*20.00*12*POL123456*11*1~"
        )

        parsed = Era835Parser().parse(content)

        self.assertEqual(parsed.transaction_set_control_number, "0001")
        self.assertEqual(parsed.payer_name, "Demo Health Plan")
        self.assertEqual(parsed.payee_name, "Demo Clinic")
        self.assertEqual(parsed.payment_amount, 150.00)
        self.assertEqual(len(parsed.claim_statuses), 1)


if __name__ == "__main__":
    unittest.main()
