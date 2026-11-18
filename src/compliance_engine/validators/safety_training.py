"""Work-at-heights / occupational safety training certificate validator."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from dateutil.relativedelta import relativedelta

from compliance_engine.llm.extractor import LlmExtractor
from compliance_engine.ocr.fuzzy_match import id_in_text_fuzzy


class SafetyTrainingValidator:
    """Validates work-at-heights certificates and computes 18-month validity cutoffs."""

    REQUIRED_KEYWORDS = [
        "curso alturas",
        "trabajo en alturas",
        "reentrenamiento",
        "certificado alturas",
        "work at heights",
        "safety training",
    ]
    EXCLUDE_KEYWORDS = ["planilla", "parafiscales", "pila", "liquidacion detallada"]

    @staticmethod
    def select_most_recent_course_date(
        dates_found: list[str],
        llm_selected_date: str | None = None,
    ) -> str | None:
        """Deterministically override LLM date selection by sorting ISO dates chronologically."""
        valid_dates: list[tuple[datetime, str]] = []
        for raw_date in dates_found or []:
            if not raw_date or str(raw_date).strip().lower() == "null":
                continue
            try:
                parsed = datetime.strptime(str(raw_date).strip(), "%Y-%m-%d")
                valid_dates.append((parsed, str(raw_date).strip()))
            except ValueError:
                continue

        if not valid_dates:
            return llm_selected_date

        valid_dates.sort(key=lambda pair: pair[0])
        return valid_dates[-1][1]

    @staticmethod
    def calculate_valid_until(course_date: str | None, validity_months: int = 18) -> str | None:
        """Add `validity_months` calendar months (default 18) using `relativedelta`."""
        if not course_date or str(course_date).strip().lower() == "null":
            return None
        try:
            course_dt = datetime.strptime(str(course_date).strip(), "%Y-%m-%d")
            return (course_dt + relativedelta(months=validity_months)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def calculate_status(valid_until: str | None, permit_end_date: str | None) -> str:
        """Return 'VALID' if safety training covers `permit_end_date`, else 'EXPIRED'."""
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
        validity_months: int = 18,
    ) -> dict[str, Any]:
        """Extract structured certificate attributes and verify worker + latest course date."""
        result: dict[str, Any] = {
            "safety_employer_name": None,
            "safety_tax_id": None,
            "safety_document": file_name,
            "safety_training_date": None,
            "safety_valid_until": None,
            "safety_status": None,
            "is_valid_safety_doc": False,
        }

        if not text or not text.strip():
            return result

        ai_data = extractor.extract_safety_cert(text, worker_id)
        if ai_data is None or not ai_data.is_safety_certificate:
            return result

        target_norm = re.sub(r"[^0-9]", "", str(worker_id))
        ai_found_norm = re.sub(r"[^0-9]", "", str(ai_data.worker_id_found or ""))
        cert_ok = ai_data.certificate_valid_for_id and (ai_found_norm == target_norm)
        if not cert_ok and not id_in_text_fuzzy(worker_id, text):
            return result

        latest_date = cls.select_most_recent_course_date(
            ai_data.course_dates_found,
            ai_data.most_recent_course_date,
        )

        clean_tax_id = re.sub(r"[^0-9]", "", str(ai_data.employer_tax_id or "")) or None
        if clean_tax_id == target_norm:
            result["safety_tax_id"] = target_norm
            result["safety_employer_name"] = ai_data.employer_name or f"Independent Contractor - {target_norm}"
        elif clean_tax_id:
            result["safety_tax_id"] = clean_tax_id
            result["safety_employer_name"] = ai_data.employer_name
        elif ai_data.employer_name:
            result["safety_employer_name"] = ai_data.employer_name
        else:
            return result

        if latest_date:
            result["safety_training_date"] = latest_date
            result["safety_valid_until"] = cls.calculate_valid_until(latest_date, validity_months)

        result["is_valid_safety_doc"] = bool(result["safety_training_date"] and result["safety_employer_name"])
        return result
