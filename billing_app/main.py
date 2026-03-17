from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from billing_app.connectors.mock_connector import MockClearinghouseConnector
from billing_app.models import (
    Address,
    Claim,
    EligibilityRequest,
    InsurancePolicy,
    Patient,
    Provider,
    ServiceLine,
)
from billing_app.services.claim_builder import Claim837Builder
from billing_app.services.claimmd_template import process_claimmd_template as process_claimmd_template_file
from billing_app.services.claim_parser import Claim837Parser
from billing_app.services.eligibility import EligibilityService
from billing_app.services.remit_parser import Era835Parser


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_claim(payload: dict) -> Claim:
    provider = Provider(**payload["provider"])
    patient = Patient(
        **{
            **payload["patient"],
            "address": Address(**payload["patient"]["address"]),
        }
    )
    insurance = InsurancePolicy(**payload["insurance"])
    service_lines = [ServiceLine(**line) for line in payload["service_lines"]]
    return Claim(
        claim_id=payload["claim_id"],
        provider=provider,
        patient=patient,
        insurance=insurance,
        service_date=payload["service_date"],
        diagnosis_codes=payload["diagnosis_codes"],
        service_lines=service_lines,
        total_charge_amount=payload["total_charge_amount"],
    )


def _build_eligibility_request(payload: dict) -> EligibilityRequest:
    return EligibilityRequest(**payload)


def submit_claim(file_path: str) -> None:
    payload = _load_json(Path(file_path))
    claim = _build_claim(payload)
    builder = Claim837Builder()
    connector = MockClearinghouseConnector()
    edi_payload = builder.build_professional_claim(claim)
    result = connector.submit_claim(claim, edi_payload)
    print(json.dumps({"submission": result, "x12_837": edi_payload}, indent=2))


def check_eligibility(file_path: str) -> None:
    payload = _load_json(Path(file_path))
    request = _build_eligibility_request(payload)
    service = EligibilityService(MockClearinghouseConnector())
    response = service.check(request)
    print(json.dumps(asdict(response), indent=2))


def parse_835(file_path: str) -> None:
    content = Path(file_path).read_text(encoding="utf-8")
    parser = Era835Parser()
    parsed = parser.parse(content)
    print(json.dumps(asdict(parsed), indent=2))


def parse_837(file_path: str) -> None:
    content = Path(file_path).read_text(encoding="utf-8")
    parser = Claim837Parser()
    parsed_claims = parser.parse_many(content)
    if len(parsed_claims) == 1:
        print(json.dumps(asdict(parsed_claims[0]), indent=2))
        return
    print(json.dumps({"count": len(parsed_claims), "claims": [asdict(item) for item in parsed_claims]}, indent=2))


def process_claimmd_template(file_path: str, output_dir: str | None = None) -> None:
    result = process_claimmd_template_file(file_path, output_dir)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Blue Hope ABA Solutions for claims, eligibility and ERA")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit-claim", help="Generate and submit an 837P claim")
    submit.add_argument("--file", required=True, help="Path to a claim JSON file")

    eligibility = subparsers.add_parser("check-eligibility", help="Run automatic eligibility")
    eligibility.add_argument("--file", required=True, help="Path to an eligibility JSON file")

    remit = subparsers.add_parser("parse-835", help="Parse an 835 ERA file")
    remit.add_argument("--file", required=True, help="Path to an 835 text file")

    claim_parse = subparsers.add_parser("parse-837", help="Parse an 837 claim file")
    claim_parse.add_argument("--file", required=True, help="Path to an 837 text file")

    claimmd = subparsers.add_parser(
        "process-claimmd-template",
        help="Read a Claim.MD eligibility template and export ready/incomplete rows",
    )
    claimmd.add_argument("--file", required=True, help="Path to the Claim.MD .xlsx template")
    claimmd.add_argument(
        "--output-dir",
        help="Optional output directory for generated CSV/JSON files",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "submit-claim":
        submit_claim(args.file)
    elif args.command == "check-eligibility":
        check_eligibility(args.file)
    elif args.command == "parse-835":
        parse_835(args.file)
    elif args.command == "parse-837":
        parse_837(args.file)
    elif args.command == "process-claimmd-template":
        process_claimmd_template(args.file, args.output_dir)


if __name__ == "__main__":
    main()
