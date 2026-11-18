"""Typed Pydantic document extractors using Google GenAI and deterministic offline parsing."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator


def _coerce_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"true", "1", "yes", "si"}


def _clean_nullable_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in {"", "null", "none", "nan"}:
        return None
    return s


class PayrollRecordItem(BaseModel):
    worker_id: str = ""
    payment_confirmed_for_id: bool = False
    payment_date: str | None = None
    paid_period: str | None = None
    employer_tax_id: str | None = None
    employer_name: str | None = None

    @field_validator("payment_confirmed_for_id", mode="before")
    @classmethod
    def _v_bool(cls, v: Any) -> bool:
        return _coerce_bool(v)

    @field_validator("payment_date", "paid_period", "employer_tax_id", "employer_name", mode="before")
    @classmethod
    def _v_str(cls, v: Any) -> str | None:
        return _clean_nullable_str(v)


class PayrollExtraction(BaseModel):
    is_payroll_document: bool = False
    employer_tax_id: str | None = None
    employer_name: str | None = None
    worker_id_found: str | None = None
    payment_date: str | None = None
    paid_period: str | None = None
    payment_confirmed_for_id: bool = False
    records: list[PayrollRecordItem] = Field(default_factory=list)

    @field_validator("is_payroll_document", "payment_confirmed_for_id", mode="before")
    @classmethod
    def _v_bool(cls, v: Any) -> bool:
        return _coerce_bool(v)

    @field_validator(
        "employer_tax_id", "employer_name", "worker_id_found", "payment_date", "paid_period", mode="before"
    )
    @classmethod
    def _v_str(cls, v: Any) -> str | None:
        return _clean_nullable_str(v)


class SafetyCertExtraction(BaseModel):
    is_safety_certificate: bool = False
    course_dates_found: list[str] = Field(default_factory=list)
    most_recent_course_date: str | None = None
    employer_name: str | None = None
    employer_tax_id: str | None = None
    training_provider: str | None = None
    worker_id_found: str | None = None
    course_type: str | None = None
    certificate_valid_for_id: bool = False

    @field_validator("is_safety_certificate", "certificate_valid_for_id", mode="before")
    @classmethod
    def _v_bool(cls, v: Any) -> bool:
        return _coerce_bool(v)

    @field_validator(
        "most_recent_course_date",
        "employer_name",
        "employer_tax_id",
        "training_provider",
        "worker_id_found",
        "course_type",
        mode="before",
    )
    @classmethod
    def _v_str(cls, v: Any) -> str | None:
        return _clean_nullable_str(v)


class DocumentSplitDecision(BaseModel):
    document_type: str = "UNKNOWN"
    confidence: str = "LOW"
    should_split: bool = True
    reason: str = ""
    evidence: str = ""


class LlmExtractor(Protocol):
    """Protocol implemented by Gemini extractor and offline deterministic mock extractor."""

    def extract_payroll(self, text: str, worker_id: str = "") -> PayrollExtraction | None:
        ...

    def extract_safety_cert(self, text: str, worker_id: str = "") -> SafetyCertExtraction | None:
        ...

    def classify_for_split(self, text: str, filename: str = "") -> DocumentSplitDecision:
        ...


def _stable_cache_key(prefix: str, text: str, worker_id: str = "") -> str:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:24]
    cc_norm = re.sub(r"[^0-9]", "", str(worker_id or ""))
    return f"{prefix}::{digest}::{cc_norm}"


class GeminiComplianceExtractor:
    """Structured JSON extractor powered by Google GenAI (`google-genai` SDK v1)."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str = "gemini-2.5-flash",
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name
        self._client: Any = None
        self._cache: dict[str, Any] = {}

    def _get_client(self) -> Any:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("GEMINI_API_KEY is required for live Gemini extraction.")
            from google import genai  # type: ignore[import-untyped]

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _generate_json(self, prompt: str) -> dict[str, Any] | None:
        try:
            client = self._get_client()
            response = client.models.generate_content(model=self.model_name, contents=prompt)
            cleaned = (response.text or "").strip().replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned)
        except Exception as exc:
            logging.error("Gemini extraction error: %s", exc)
            return None

    def extract_payroll(self, text: str, worker_id: str = "") -> PayrollExtraction | None:
        key = _stable_cache_key("payroll", text, worker_id)
        if key in self._cache:
            return self._cache[key]

        prompt = (
            "You are a compliance document analyst. Extract structured payroll / social security "
            "information as valid JSON ONLY matching keys: is_payroll_document, employer_tax_id, "
            "employer_name, worker_id_found, payment_date (YYYY-MM-DD), paid_period (YYYY-MM), "
            "payment_confirmed_for_id, records (list of {worker_id, payment_confirmed_for_id, "
            "payment_date, paid_period, employer_tax_id, employer_name}). "
            f"Target worker_id: '{worker_id}'. Distinguish natural person independent contractors "
            "(tax_id == worker_id) from companies.\n---\n{text}\n---"
        ).format(text=text[:12000])

        payload = self._generate_json(prompt)
        if not payload:
            return None
        parsed = PayrollExtraction.model_validate(payload)
        self._cache[key] = parsed
        return parsed

    def extract_safety_cert(self, text: str, worker_id: str = "") -> SafetyCertExtraction | None:
        key = _stable_cache_key("safety", text, worker_id)
        if key in self._cache:
            return self._cache[key]

        prompt = (
            "You are a safety-at-heights certification analyst. Extract structured JSON ONLY with "
            "keys: is_safety_certificate, course_dates_found (YYYY-MM-DD chronological list, excluding "
            "expiration dates), most_recent_course_date (YYYY-MM-DD), employer_name, employer_tax_id "
            "(without check digit), training_provider, worker_id_found, course_type, "
            f"certificate_valid_for_id. Target worker_id: '{worker_id}'.\n---\n{text}\n---"
        ).format(text=text[:12000])

        payload = self._generate_json(prompt)
        if not payload:
            return None
        parsed = SafetyCertExtraction.model_validate(payload)
        self._cache[key] = parsed
        return parsed

    def classify_for_split(self, text: str, filename: str = "") -> DocumentSplitDecision:
        prompt = (
            "Classify whether this PDF must be split page-by-page. Return JSON ONLY with keys: "
            "document_type (INDIVIDUAL_CERTIFICATES | CONSOLIDATED_PAYROLL | MIXED_DOCUMENT | OTHER), "
            "confidence (HIGH | MEDIUM | LOW), should_split (bool), reason, evidence. "
            f"Filename: '{filename}'. Rules: multi-employee payroll tables -> should_split=false; "
            "single-worker certificate per page -> should_split=true.\n---\n"
            f"{text[:5000]}\n---"
        )
        payload = self._generate_json(prompt)
        if not payload:
            return DocumentSplitDecision(
                document_type="FALLBACK",
                confidence="LOW",
                should_split=True,
                reason="Conservative fallback on Gemini failure",
                evidence=filename,
            )
        return DocumentSplitDecision.model_validate(payload)


class RuleBasedMockExtractor:
    """Deterministic parser for offline/CI execution over digital or synthetic documents."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def extract_payroll(self, text: str, worker_id: str = "") -> PayrollExtraction | None:
        key = _stable_cache_key("mock_payroll", text, worker_id)
        if key in self._cache:
            return self._cache[key]

        low = text.lower()
        is_payroll = any(k in low for k in ("planilla", "pila", "parafiscales", "seguridad social", "payroll"))
        is_course = any(k in low for k in ("trabajo en alturas", "certificado curso", "work at heights"))
        if is_course and not is_payroll:
            return PayrollExtraction(is_payroll_document=False)
        if not is_payroll:
            return PayrollExtraction(is_payroll_document=False)

        tax_match = re.search(r"(?:nit|tax\s*id)\s*[:#-]?\s*(\d{6,12})", text, re.IGNORECASE)
        company_match = re.search(r"(?:empresa\s*aportante|aportante|employer)\s*[:#-]?\s*([^\n|]+)", text, re.IGNORECASE)
        date_match = re.search(r"(?:fecha\s*(?:de)?\s*pago|payment\s*date)\s*[:#-]?\s*(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
        period_match = re.search(r"(?:periodo|period)\s*[:#-]?\s*(\d{4}-\d{2})", text, re.IGNORECASE)

        employer_tax_id = tax_match.group(1) if tax_match else None
        employer_name = company_match.group(1).strip() if company_match else None
        payment_date = date_match.group(1) if date_match else None
        paid_period = period_match.group(1) if period_match else (payment_date[:7] if payment_date else None)

        all_ids = list(dict.fromkeys(re.findall(r"(?:cc|worker\s*id|c\.c\.)\s*[:#-]?\s*(\d{7,11})", text, re.IGNORECASE)))
        target_norm = re.sub(r"[^0-9]", "", worker_id)
        records = [
            PayrollRecordItem(
                worker_id=wid,
                payment_confirmed_for_id=True,
                payment_date=payment_date,
                paid_period=paid_period,
                employer_tax_id=employer_tax_id,
                employer_name=employer_name,
            )
            for wid in all_ids
        ]

        confirmed = bool(target_norm and target_norm in all_ids)
        found_id = target_norm if confirmed else (all_ids[0] if len(all_ids) == 1 else None)

        result = PayrollExtraction(
            is_payroll_document=True,
            employer_tax_id=employer_tax_id,
            employer_name=employer_name,
            worker_id_found=found_id,
            payment_date=payment_date,
            paid_period=paid_period,
            payment_confirmed_for_id=confirmed,
            records=records,
        )
        self._cache[key] = result
        return result

    def extract_safety_cert(self, text: str, worker_id: str = "") -> SafetyCertExtraction | None:
        key = _stable_cache_key("mock_safety", text, worker_id)
        if key in self._cache:
            return self._cache[key]

        low = text.lower()
        is_cert = any(
            k in low for k in ("alturas", "reentrenamiento", "work at heights", "safety training")
        )
        is_payroll = any(k in low for k in ("planilla", "pila", "liquidacion detallada"))
        if is_payroll and not is_cert:
            return SafetyCertExtraction(is_safety_certificate=False)
        if not is_cert:
            return SafetyCertExtraction(is_safety_certificate=False)

        tax_match = re.search(r"(?:nit\s*empleador|employer\s*tax\s*id)\s*[:#-]?\s*(\d{6,12})", text, re.IGNORECASE)
        emp_match = re.search(r"(?:empleador|employer)\s*[:#-]?\s*([^\n|]+)", text, re.IGNORECASE)
        prov_match = re.search(r"(?:centro\s*capacitador|training\s*provider)\s*[:#-]?\s*([^\n|]+)", text, re.IGNORECASE)
        course_dates = sorted(
            set(
                re.findall(
                    r"(?:fecha\s*(?:de)?\s*(?:curso|realizacion|certificado)|course\s*date)\s*[:#-]?\s*(\d{4}-\d{2}-\d{2})",
                    text,
                    re.IGNORECASE,
                )
            )
        )
        ids = re.findall(r"(?:cc|worker\s*id|c\.c\.)\s*[:#-]?\s*(\d{7,11})", text, re.IGNORECASE)
        target_norm = re.sub(r"[^0-9]", "", worker_id)
        matched = bool(target_norm and target_norm in ids)

        result = SafetyCertExtraction(
            is_safety_certificate=True,
            course_dates_found=course_dates,
            most_recent_course_date=course_dates[-1] if course_dates else None,
            employer_name=emp_match.group(1).strip() if emp_match else None,
            employer_tax_id=tax_match.group(1) if tax_match else None,
            training_provider=prov_match.group(1).strip() if prov_match else None,
            worker_id_found=target_norm if matched else (ids[0] if ids else None),
            course_type="SAFETY_AT_HEIGHTS",
            certificate_valid_for_id=matched,
        )
        self._cache[key] = result
        return result

    def classify_for_split(self, text: str, filename: str = "") -> DocumentSplitDecision:
        low = text.lower()
        if any(k in low for k in ("liquidacion detallada", "total afiliados", "consolidated payroll", "planilla")):
            return DocumentSplitDecision(
                document_type="CONSOLIDATED_PAYROLL",
                confidence="HIGH",
                should_split=False,
                reason="Multi-employee consolidated payroll detected",
                evidence=filename,
            )
        return DocumentSplitDecision(
            document_type="INDIVIDUAL_CERTIFICATES",
            confidence="HIGH",
            should_split=True,
            reason="Individual worker certificate(s) detected",
            evidence=filename,
        )
