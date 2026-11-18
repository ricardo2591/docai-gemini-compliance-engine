"""End-to-end compliance pipeline runner coordinating split, OCR, LLM, and evaluation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from compliance_engine.config import AppConfig
from compliance_engine.llm.extractor import (
    GeminiComplianceExtractor,
    LlmExtractor,
    RuleBasedMockExtractor,
)
from compliance_engine.ocr.cascade import (
    CompositeOcrCascade,
    GoogleCloudOcrBackend,
    LocalPdfTextBackend,
)
from compliance_engine.ocr.fuzzy_match import (
    id_in_text_fuzzy,
    map_lookalikes_to_digits,
    match_by_keywords,
    name_similarity,
    normalize_name,
)
from compliance_engine.pdf.splitter import PdfDocumentSplitter
from compliance_engine.pipelines.excel_io import (
    export_results_excel,
    read_access_request_excel,
    sanitize_cell_value,
)
from compliance_engine.validators.compliance_evaluator import ComplianceEvaluator
from compliance_engine.validators.safety_training import SafetyTrainingValidator
from compliance_engine.validators.social_security import PayrollValidator


@dataclass(frozen=True)
class PipelineRunSummary:
    """Structured summary produced by a batch pipeline execution."""

    total_records: int
    approved_records: int
    rejected_records: int
    split_pdfs_generated: int
    output_excel_path: Path
    output_json_path: Path


class CompliancePipelineRunner:
    """Modular compliance validation orchestrator replacing monolithic scripts."""

    def __init__(
        self,
        cfg: AppConfig,
        ocr_cascade: CompositeOcrCascade | None = None,
        extractor: LlmExtractor | None = None,
        use_cloud: bool = False,
    ) -> None:
        self.cfg = cfg
        pipe_cfg = cfg.raw["pipeline"]
        min_chars = int(pipe_cfg.get("min_text_length_chars", 50))

        if ocr_cascade is not None:
            self.ocr = ocr_cascade
        else:
            backends = [LocalPdfTextBackend()]
            if use_cloud:
                backends.insert(0, GoogleCloudOcrBackend())
            self.ocr = CompositeOcrCascade(backends=backends, min_chars=min_chars)

        if extractor is not None:
            self.extractor = extractor
        elif use_cloud:
            self.extractor = GeminiComplianceExtractor(model_name=str(pipe_cfg["gemini_model"]))
        else:
            self.extractor = RuleBasedMockExtractor()

        self.splitter = PdfDocumentSplitter(self.ocr, self.extractor)
        self.evaluator = ComplianceEvaluator(
            facility_operator_name=str(pipe_cfg.get("facility_operator_name", "Facility Operator")),
            template_display_name=str(pipe_cfg.get("template_display_name", "Access Request Template")),
        )
        self.validity_months = int(pipe_cfg.get("safety_training_validity_months", 18))

    def _prepare_ticket_pdfs(self, ticket_prefix: str, source_dir: Path, staged_dir: Path) -> list[Path]:
        """Split multi-page individual certificates while keeping consolidated payroll intact."""
        raw_pdfs = sorted(source_dir.glob(f"{ticket_prefix}*.pdf"))
        prepared: list[Path] = []
        for pdf_path in raw_pdfs:
            res = self.splitter.process_file(pdf_path, staged_dir)
            prepared.extend(res.generated_files)
        return prepared

    def _index_documents(self, pdf_paths: list[Path]) -> list[dict[str, Any]]:
        indexed: list[dict[str, Any]] = []
        for path in pdf_paths:
            ocr_res = self.ocr.extract(path.read_bytes(), file_name=path.name)
            text = ocr_res.full_text if ocr_res else ""
            indexed.append({"path": path, "name": path.name, "text": text})
        return indexed

    def _find_candidate(
        self,
        docs: list[dict[str, Any]],
        worker_id: str,
        required_kw: list[str],
        exclude_kw: list[str],
        worker_name: str | None = None,
    ) -> dict[str, Any] | None:
        target_norm = "".join(ch for ch in str(worker_id) if ch.isdigit())
        for doc in docs:
            text = doc["text"]
            if not text:
                continue
            if not match_by_keywords(required_kw, exclude_kw, doc["name"], text):
                continue
            digits_exact = target_norm in "".join(
                ch for ch in map_lookalikes_to_digits(text) if ch.isdigit()
            )
            if digits_exact:
                return doc
            if id_in_text_fuzzy(worker_id, text) and (
                not worker_name
                or name_similarity(normalize_name(worker_name), normalize_name(text)) >= 0.5
            ):
                return doc
        return None

    def run_directory(
        self,
        input_dir: Path | None = None,
        output_dir: Path | None = None,
    ) -> PipelineRunSummary:
        """Process all request workbooks and supporting PDFs under `input_dir`."""
        src_dir = input_dir or self.cfg.fixtures_dir
        out_dir = output_dir or self.cfg.output_dir
        staged_dir = out_dir / "staged_pdfs"
        out_dir.mkdir(parents=True, exist_ok=True)

        excel_files = sorted(p for p in src_dir.glob("*.xlsx") if not p.name.startswith("~$"))
        if not excel_files:
            raise FileNotFoundError(f"No Excel access request workbooks found in {src_dir}")

        all_frames: list[pd.DataFrame] = []
        split_count = 0

        for excel_path in excel_files:
            df = read_access_request_excel(excel_path, self.cfg)
            if df is None or df.empty:
                continue

            ticket_prefix = excel_path.stem.split("_")[0]
            prepared_pdfs = self._prepare_ticket_pdfs(ticket_prefix, src_dir, staged_dir)
            split_count += len(prepared_pdfs)
            docs = self._index_documents(prepared_pdfs)

            for idx, row in df.iterrows():
                worker_id = str(row["worker_id"])
                worker_name = str(row["full_name"])
                permit_end = str(row["permit_end_date"])

                # 1. Social security / payroll validation
                payroll_doc = self._find_candidate(
                    docs,
                    worker_id,
                    PayrollValidator.REQUIRED_KEYWORDS,
                    PayrollValidator.EXCLUDE_KEYWORDS,
                    worker_name=worker_name,
                )
                if payroll_doc is not None:
                    pf_data = PayrollValidator.validate_from_text(
                        payroll_doc["text"],
                        payroll_doc["name"],
                        worker_id,
                        self.extractor,
                        worker_name=worker_name,
                    )
                    if pf_data.get("is_valid_payroll_doc"):
                        for col in (
                            "payroll_company_name",
                            "payroll_tax_id",
                            "payroll_document",
                            "payroll_payment_date",
                            "payroll_valid_until",
                        ):
                            df.at[idx, col] = pf_data.get(col)
                        df.at[idx, "company_tax_id"] = pf_data.get("payroll_tax_id")
                        df.at[idx, "payroll_status"] = PayrollValidator.calculate_status(
                            pf_data.get("payroll_valid_until"), permit_end
                        )
                    else:
                        df.at[idx, "payroll_status"] = "MISSING_DOCUMENTS"
                else:
                    df.at[idx, "payroll_status"] = "MISSING_DOCUMENTS"

                # 2. Safety training validation
                safety_doc = self._find_candidate(
                    docs,
                    worker_id,
                    SafetyTrainingValidator.REQUIRED_KEYWORDS,
                    SafetyTrainingValidator.EXCLUDE_KEYWORDS,
                    worker_name=worker_name,
                )
                if safety_doc is not None:
                    sa_data = SafetyTrainingValidator.validate_from_text(
                        safety_doc["text"],
                        safety_doc["name"],
                        worker_id,
                        self.extractor,
                        validity_months=self.validity_months,
                    )
                    if sa_data.get("is_valid_safety_doc"):
                        for col in (
                            "safety_employer_name",
                            "safety_tax_id",
                            "safety_document",
                            "safety_training_date",
                            "safety_valid_until",
                        ):
                            df.at[idx, col] = sa_data.get(col)
                        df.at[idx, "safety_status"] = SafetyTrainingValidator.calculate_status(
                            sa_data.get("safety_valid_until"), permit_end
                        )
                    else:
                        df.at[idx, "safety_status"] = "MISSING_DOCUMENTS"
                else:
                    df.at[idx, "safety_status"] = "MISSING_DOCUMENTS"

                # 3. Deterministic compliance decision
                outcome = self.evaluator.evaluate(df.loc[idx].to_dict())
                for key, val in outcome.to_dict().items():
                    df.at[idx, key] = val

            all_frames.append(df)

        if not all_frames:
            raise RuntimeError("No valid worker records were processed.")

        master_df = pd.concat(all_frames, ignore_index=True)
        excel_out = export_results_excel(master_df, out_dir / "compliance_validation_report.xlsx")

        records_clean = [
            {k: sanitize_cell_value(v) for k, v in rec.items()}
            for rec in master_df.to_dict(orient="records")
        ]
        approved = sum(1 for r in records_clean if r["overall_status"].startswith("APPROVED"))
        rejected = len(records_clean) - approved

        json_out = out_dir / "compliance_summary.json"
        summary_payload = {
            "total_records": len(records_clean),
            "approved_records": approved,
            "rejected_records": rejected,
            "split_pdfs_generated": split_count,
            "records": records_clean,
        }
        json_out.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
        logging.info(
            "Pipeline finished: %d workers (%d approved, %d rejected).",
            len(records_clean),
            approved,
            rejected,
        )
        return PipelineRunSummary(
            total_records=len(records_clean),
            approved_records=approved,
            rejected_records=rejected,
            split_pdfs_generated=split_count,
            output_excel_path=excel_out,
            output_json_path=json_out,
        )
