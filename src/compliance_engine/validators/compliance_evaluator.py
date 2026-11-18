"""Deterministic final access-approval and rejection evaluator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class EvaluationOutcome:
    """Audit-ready access evaluation result for a contractor request row."""

    overall_status: str
    rejection_reason: str
    observations: str

    def to_dict(self) -> dict[str, str]:
        return {
            "overall_status": self.overall_status,
            "rejection_reason": self.rejection_reason,
            "observations": self.observations,
        }


class ComplianceEvaluator:
    """Pure deterministic compliance rule engine decoupled from LLM extraction."""

    STATUS_APPROVED = "APPROVED (see observations)"
    STATUS_REJECTED = "REJECTED (see reason and observations)"

    def __init__(
        self,
        facility_operator_name: str = "Facility Operator",
        template_display_name: str = "Access Request Template",
    ) -> None:
        self.facility_operator_name = facility_operator_name
        self.template_display_name = template_display_name

    @staticmethod
    def _norm(value: Any) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def _parse_date(value: Any) -> datetime | None:
        if not value or str(value).strip().lower() in {"null", "none", "nan"}:
            return None
        try:
            return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _digits(value: Any) -> str:
        if not value:
            return ""
        return re.sub(r"[^0-9]", "", str(value))

    def evaluate(self, row: dict[str, Any]) -> EvaluationOutcome:
        payroll_status = self._norm(row.get("payroll_status"))
        safety_status = self._norm(row.get("safety_status"))

        permit_start = self._parse_date(row.get("permit_start_date"))
        permit_end = self._parse_date(row.get("permit_end_date"))
        payroll_valid_until = self._parse_date(row.get("payroll_valid_until"))
        payroll_payment_date = self._parse_date(row.get("payroll_payment_date"))

        payroll_tax_id = self._digits(row.get("payroll_tax_id"))
        safety_tax_id = self._digits(row.get("safety_tax_id"))

        # Rule 1: Reject immediately if social-security / payroll compliance fails
        if payroll_status in {"EXPIRED", "MISSING_DOCUMENTS", ""}:
            if payroll_status in {"MISSING_DOCUMENTS", ""}:
                return EvaluationOutcome(
                    overall_status=self.STATUS_REJECTED,
                    rejection_reason="Access denied: no valid social-security payment proof was found.",
                    observations=(
                        f"Attach a valid social-security filing for this worker or remove the worker "
                        f"from {self.template_display_name} and submit a new request."
                    ),
                )

            if payroll_valid_until and permit_end and payroll_valid_until < permit_end:
                return EvaluationOutcome(
                    overall_status=self.STATUS_REJECTED,
                    rejection_reason="Payroll validity period does not cover the full requested permit window.",
                    observations=(
                        f"Adjust the permit end date to align with payroll validity through "
                        f"{payroll_valid_until.strftime('%Y-%m-%d')} and resubmit."
                    ),
                )

            if payroll_payment_date and permit_start and (
                (payroll_payment_date.year != permit_start.year)
                or (payroll_payment_date.month != permit_start.month)
            ):
                return EvaluationOutcome(
                    overall_status=self.STATUS_REJECTED,
                    rejection_reason="Payroll payment date does not match the requested permit month.",
                    observations="Provide social-security payment proof corresponding to the current active month.",
                )

            return EvaluationOutcome(
                overall_status=self.STATUS_REJECTED,
                rejection_reason="Social-security validity does not cover the requested permit window.",
                observations="Submit current social-security documentation or adjust permit dates.",
            )

        # Rule 2: Approved base access, subject to work-at-heights authorization rules
        if safety_status == "VALID" and payroll_tax_id and safety_tax_id and payroll_tax_id == safety_tax_id:
            return EvaluationOutcome(
                overall_status=self.STATUS_APPROVED,
                rejection_reason="NO_ISSUES",
                observations=(
                    "Worker meets all social-security and safety training requirements and is "
                    "authorized for general and elevated work during the permit window."
                ),
            )

        if safety_status == "EXPIRED":
            return EvaluationOutcome(
                overall_status=self.STATUS_APPROVED,
                rejection_reason="Not authorized for work at heights: expired safety certificate.",
                observations=(
                    f"Elevated work is prohibited; {self.facility_operator_name} assumes no liability "
                    f"for unauthorized work at heights."
                ),
            )

        if safety_status in {"MISSING_DOCUMENTS", ""}:
            return EvaluationOutcome(
                overall_status=self.STATUS_APPROVED,
                rejection_reason="Not authorized for work at heights: missing training certificate.",
                observations=(
                    f"Elevated work is prohibited; {self.facility_operator_name} assumes no liability "
                    f"for unauthorized work at heights."
                ),
            )

        if safety_status == "VALID" and payroll_tax_id and safety_tax_id and payroll_tax_id != safety_tax_id:
            return EvaluationOutcome(
                overall_status=self.STATUS_APPROVED,
                rejection_reason=(
                    "Not authorized for work at heights under Resolution 4272/2021: training "
                    "employer tax ID does not match the social-security contributing employer."
                ),
                observations=(
                    f"Elevated work is prohibited until training sponsorship matches the active "
                    f"employer; {self.facility_operator_name} assumes no liability."
                ),
            )

        return EvaluationOutcome(
            overall_status=self.STATUS_APPROVED,
            rejection_reason="NO_ISSUES",
            observations="Worker meets general access approval criteria.",
        )
