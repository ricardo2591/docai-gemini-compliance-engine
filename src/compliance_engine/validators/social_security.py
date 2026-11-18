"""Social-security / payroll compliance validation module."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from compliance_engine.llm.extractor import LlmExtractor
from compliance_engine.ocr.fuzzy_match import (
    id_in_text_fuzzy,
    map_lookalikes_to_digits,
    name_similarity,
    normalize_name,
)
from compliance_engine.validators.pila_calendar import compute_payroll_cutoff_date


class PayrollValidator:
    """Validates social-security payroll receipts and calculates monthly coverage."""

    REQUIRED_KEYWORDS = [
        "planilla",
        "parafiscales",
        "PILA",
        "seguridad social",
        "aportes",
        "payroll",
    ]
    EXCLUDE_KEYWORDS = ["trabajo en alturas", "curso alturas", "work at heights"]

    @staticmethod
    def detect_independent_contractor(
        text: str | None,
        tax_id: str | None,
        worker_id: str | None,
    ) -> bool:
        """Distinguish independent natural-person filings (tax_id == worker_id) from companies."""
        if not text or not tax_id or not worker_id:
            return False
        tax_digits = re.sub(r"[^0-9]", "", str(tax_id))
        worker_digits = re.sub(r"[^0-9]", "", str(worker_id))
        if not tax_digits or tax_digits != worker_digits:
            return False
        return True

    @staticmethod
    def calculate_valid_until(tax_id: str | None, payment_date: str | None) -> str | None:
        return compute_payroll_cutoff_date(tax_id, payment_date)

    @staticmethod
    def calculate_status(valid_until: str | None, permit_end_date: str | None) -> str:
        """Return 'VALID' when payroll validity covers `permit_end_date`, else 'EXPIRED'."""
        if not valid_until or not permit_end_date:
            return "EXPIRED"
        try:
            valid_dt = datetime.strptime(str(valid_until), "%Y-%m-%d")
            end_dt = datetime.strptime(str(permit_end_date), "%Y-%m-%d")
            return "VALID" if valid_dt >= end_dt else "EXPIRED"
        except ValueError:
            return "EXPIRED"

    @classmethod
    def validate_from_text(
        cls,
        text: str,
        file_name: str,
        worker_id: str,
        extractor: LlmExtractor,
        worker_name: str | None = None,
    ) -> dict[str, Any]:
        """Extract structured payroll metadata and validate coverage for `worker_id`."""
        result: dict[str, Any] = {
            "payroll_company_name": None,
            "payroll_tax_id": None,
            "payroll_document": file_name,
            "payroll_payment_date": None,
            "payroll_valid_until": None,
            "payroll_status": None,
            "is_valid_payroll_doc": False,
        }

        if not text or not text.strip():
            return result

        ai_data = extractor.extract_payroll(text, worker_id)
        if ai_data is None or not ai_data.is_payroll_document:
            return result

        target_norm = re.sub(r"[^0-9]", "", str(worker_id))
        ai_found_norm = re.sub(r"[^0-9]", "", str(ai_data.worker_id_found or ""))
        confirmed = ai_data.payment_confirmed_for_id and (ai_found_norm == target_norm)

        # Fallback if LLM missed exact ID match or consolidated table row
        if not confirmed:
            for item in ai_data.records:
                item_id = re.sub(r"[^0-9]", "", item.worker_id)
                if item_id == target_norm and item.payment_confirmed_for_id:
                    confirmed = True
                    break

        if not confirmed:
            digits_exact = target_norm in re.sub(r"[^0-9]", "", map_lookalikes_to_digits(text))
            name_ok = bool(
                worker_name
                and name_similarity(normalize_name(worker_name), normalize_name(text)) >= 0.5
            )
            if digits_exact:
                confirmed = True
            elif worker_name:
                # Require corroborating name overlap before accepting distance-1 OCR fallback
                confirmed = id_in_text_fuzzy(worker_id, text) and name_ok
            else:
                confirmed = id_in_text_fuzzy(worker_id, text)

        if not confirmed:
            return result

        raw_tax_id = ai_data.employer_tax_id
        if not raw_tax_id:
            return result

        clean_tax_id = re.sub(r"[^0-9]", "", raw_tax_id)
        if not clean_tax_id:
            return result

        if clean_tax_id == target_norm and not cls.detect_independent_contractor(
            text, clean_tax_id, worker_id
        ):
            return result

        result["payroll_tax_id"] = clean_tax_id
        result["payroll_company_name"] = (
            ai_data.employer_name
            or (f"Independent Contractor - {target_norm}" if clean_tax_id == target_norm else None)
        )

        if ai_data.payment_date:
            result["payroll_payment_date"] = ai_data.payment_date
            result["payroll_valid_until"] = cls.calculate_valid_until(clean_tax_id, ai_data.payment_date)

        result["is_valid_payroll_doc"] = True
        return result
