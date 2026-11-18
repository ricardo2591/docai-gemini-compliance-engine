"""PDF classifier and page splitter replacing deprecated PyPDF2 & legacy genai calls."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from compliance_engine.llm.extractor import DocumentSplitDecision, LlmExtractor
from compliance_engine.ocr.cascade import CompositeOcrCascade


@dataclass(frozen=True)
class PdfSplitResult:
    """Outcome of classifying and optionally splitting a multi-page PDF document."""

    original_filename: str
    document_type: str
    action_taken: str
    page_count: int
    generated_files: list[Path] = field(default_factory=list)


def classify_by_filename(filename: str) -> DocumentSplitDecision:
    """Fallback filename heuristic when OCR text is unavailable."""
    lowered = filename.lower()
    cert_keywords = ("curso", "alturas", "certificado", "certificate", "safety", "training")
    payroll_keywords = ("planilla", "seguridad", "social", "pila", "parafiscales", "payroll")

    if any(kw in lowered for kw in payroll_keywords):
        return DocumentSplitDecision(
            document_type="CONSOLIDATED_PAYROLL",
            confidence="MEDIUM",
            should_split=False,
            reason="Filename contains consolidated payroll keywords",
            evidence=filename,
        )
    if any(kw in lowered for kw in cert_keywords):
        return DocumentSplitDecision(
            document_type="INDIVIDUAL_CERTIFICATES",
            confidence="MEDIUM",
            should_split=True,
            reason="Filename contains training/certificate keywords",
            evidence=filename,
        )
    return DocumentSplitDecision(
        document_type="UNKNOWN",
        confidence="LOW",
        should_split=True,
        reason="Conservative split fallback for unrecognized filename",
        evidence=filename,
    )


def separate_pdf_pages(pdf_bytes: bytes) -> list[bytes]:
    """Split a PDF byte payload into individual single-page PDF byte payloads."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[bytes] = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        pages.append(buf.getvalue())
    return pages


class PdfDocumentSplitter:
    """Classify multi-page PDFs with OCR + LLM and burst individual certificates."""

    def __init__(self, ocr_cascade: CompositeOcrCascade, llm_extractor: LlmExtractor) -> None:
        self.ocr_cascade = ocr_cascade
        self.llm_extractor = llm_extractor

    def process_file(self, pdf_path: Path, output_dir: Path) -> PdfSplitResult:
        """Process one local PDF file; returns generated paths inside `output_dir`."""
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_bytes = pdf_path.read_bytes()
        page_count = len(PdfReader(io.BytesIO(pdf_bytes)).pages)

        if page_count <= 1:
            dst = output_dir / pdf_path.name
            if dst.resolve() != pdf_path.resolve():
                dst.write_bytes(pdf_bytes)
            return PdfSplitResult(
                original_filename=pdf_path.name,
                document_type="SINGLE_PAGE",
                action_taken="KEPT_AS_IS",
                page_count=page_count,
                generated_files=[dst],
            )

        ocr_res = self.ocr_cascade.extract(pdf_bytes, file_name=pdf_path.name)
        if ocr_res and ocr_res.full_text.strip():
            decision = self.llm_extractor.classify_for_split(ocr_res.full_text, pdf_path.name)
        else:
            decision = classify_by_filename(pdf_path.name)

        if not decision.should_split:
            dst = output_dir / pdf_path.name
            if dst.resolve() != pdf_path.resolve():
                dst.write_bytes(pdf_bytes)
            return PdfSplitResult(
                original_filename=pdf_path.name,
                document_type=decision.document_type,
                action_taken="KEPT_CONSOLIDATED",
                page_count=page_count,
                generated_files=[dst],
            )

        generated: list[Path] = []
        stem = pdf_path.stem
        for idx, page_bytes in enumerate(separate_pdf_pages(pdf_bytes), start=1):
            out_file = output_dir / f"{stem}_page_{idx:02d}.pdf"
            out_file.write_bytes(page_bytes)
            generated.append(out_file)

        return PdfSplitResult(
            original_filename=pdf_path.name,
            document_type=decision.document_type,
            action_taken="SPLIT_PAGES",
            page_count=page_count,
            generated_files=generated,
        )
