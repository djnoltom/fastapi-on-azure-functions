from __future__ import annotations

from billing_app.models import Parsed835


class Era835Parser:
    """Very small parser for a subset of 835 segments."""

    def parse(self, content: str) -> Parsed835:
        segments = [segment.strip() for segment in content.split("~") if segment.strip()]
        tx_control = ""
        payer_name = None
        payee_name = None
        payment_amount = None
        claim_statuses = []
        claim_details = []

        for segment in segments:
            elements = segment.split("*")
            tag = elements[0]

            if tag == "ST" and len(elements) > 2:
                tx_control = elements[2]
            elif tag == "N1" and len(elements) > 2:
                entity_code = elements[1]
                if entity_code == "PR":
                    payer_name = elements[2]
                elif entity_code == "PE":
                    payee_name = elements[2]
            elif tag == "BPR" and len(elements) > 2:
                try:
                    payment_amount = float(elements[2])
                except ValueError:
                    payment_amount = None
            elif tag == "CLP" and len(elements) > 2:
                detail = {
                    "claim_id": elements[1],
                    "claim_status_code": elements[2],
                    "charge_amount": float(elements[3]),
                    "paid_amount": float(elements[4]),
                    "payer_claim_number": elements[7] if len(elements) > 7 else "",
                }
                claim_statuses.append(
                    " ".join(
                        [
                            f"claim={detail['claim_id']}",
                            f"payer_claim={detail['payer_claim_number'] or 'N/A'}",
                            f"status={detail['claim_status_code']}",
                            f"charge={detail['charge_amount']:.2f}",
                            f"paid={detail['paid_amount']:.2f}",
                        ]
                    )
                )
                claim_details.append(detail)

        return Parsed835(
            transaction_set_control_number=tx_control,
            payer_name=payer_name,
            payee_name=payee_name,
            payment_amount=payment_amount,
            claim_statuses=claim_statuses,
            claim_details=claim_details,
        )
