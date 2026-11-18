"""OCR cascade backends: Google Document AI (layout/tables) -> Cloud Vision -> Local PDF."""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pypdf import PdfReader

from compliance_engine.ocr.fuzzy_match import (
    extract_candidate_worker_ids,
    map_lookalikes_to_digits,
)


@dataclass
class OcrExtractionResult:
    """Structured OCR output returned by any backend."""

    full_text: str
    page_texts: list[str] = field(default_factory=list)
    has_tables: bool = False
    table_page_ids: list[list[str]] = field(default_factory=list)
    source: str = "local_pdf"


class OcrBackend(Protocol):
    """Protocol implemented by local and cloud OCR engines."""

    name: str

    def extract(self, pdf_bytes: bytes, file_name: str = "") -> OcrExtractionResult | None:
        ...


class LocalPdfTextBackend:
    """Zero-credential local PDF text extractor for digital PDFs and offline testing."""

    name = "local_pdf"

    def extract(self, pdf_bytes: bytes, file_name: str = "") -> OcrExtractionResult | None:
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
            page_texts = [(page.extract_text() or "").strip() for page in reader.pages]
            full_text = "\n\n".join(t for t in page_texts if t)
            if not full_text:
                return None

            has_tables = any(" | " in t or "worker id" in t.lower() or "ibc" in t.lower() for t in page_texts)
            table_page_ids = [extract_candidate_worker_ids(t) for t in page_texts]
            return OcrExtractionResult(
                full_text=full_text,
                page_texts=page_texts,
                has_tables=has_tables,
                table_page_ids=table_page_ids,
                source=self.name,
            )
        except Exception as exc:
            logging.warning("Local PDF extraction failed for %s: %s", file_name, exc)
            return None


def _extract_anchor_text(doc: Any, anchor: Any) -> str:
    if not anchor or not getattr(anchor, "text_segments", None):
        return ""
    full = getattr(doc, "text", "") or ""
    parts: list[str] = []
    for seg in anchor.text_segments:
        start = int(getattr(seg, "start_index", 0) or 0)
        end = int(getattr(seg, "end_index", 0) or 0)
        if end > start:
            parts.append(full[start:end])
    return "".join(parts).strip()


class GoogleCloudOcrBackend:
    """Google Cloud Document AI primary parser with Cloud Vision fallback."""

    name = "google_cloud"

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        processor_id: str | None = None,
    ) -> None:
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.getenv("DOCUMENT_AI_LOCATION", "us")
        self.processor_id = processor_id or os.getenv("DOCUMENT_AI_PROCESSOR_ID")
        self._docai_client: Any = None
        self._vision_client: Any = None

    def _try_document_ai(self, pdf_bytes: bytes) -> OcrExtractionResult | None:
        if not self.project_id or not self.processor_id:
            return None
        try:
            from google.cloud import documentai  # type: ignore[import-untyped]
        except ImportError:
            return None

        if self._docai_client is None:
            self._docai_client = documentai.DocumentProcessorServiceClient()

        name = self._docai_client.processor_path(self.project_id, self.location, self.processor_id)
        request = documentai.ProcessRequest(
            name=name,
            raw_document=documentai.RawDocument(content=pdf_bytes, mime_type="application/pdf"),
        )
        result = self._docai_client.process_document(request=request)
        doc = getattr(result, "document", None)
        if not doc or not getattr(doc, "text", "").strip():
            return None

        header_tokens = ("identific", "documento", "cédula", "cedula", "cc", "worker id")
        page_texts: list[str] = []
        table_page_ids: list[list[str]] = []
        has_tables = False

        for page in getattr(doc, "pages", []) or []:
            if getattr(page, "tables", None):
                has_tables = has_tables or len(page.tables) > 0
            layout = getattr(page, "layout", None)
            p_text = _extract_anchor_text(doc, getattr(layout, "text_anchor", None)) if layout else ""
            page_texts.append(p_text)

            page_ids: list[str] = []
            for table in getattr(page, "tables", []) or []:
                id_cols: list[int] = []
                header_rows = list(getattr(table, "header_rows", []) or [])
                body_rows = list(getattr(table, "body_rows", []) or [])
                first_row = header_rows[0] if header_rows else (body_rows[0] if body_rows else None)
                if first_row:
                    for idx, cell in enumerate(getattr(first_row, "cells", []) or []):
                        txt = _extract_anchor_text(doc, getattr(getattr(cell, "layout", None), "text_anchor", None)).lower()
                        if any(tok in txt for tok in header_tokens):
                            id_cols.append(idx)
                for brow in body_rows:
                    cells = list(getattr(brow, "cells", []) or [])
                    for idx in id_cols:
                        if idx < len(cells):
                            raw = _extract_anchor_text(doc, getattr(getattr(cells[idx], "layout", None), "text_anchor", None))
                            mapped = map_lookalikes_to_digits(raw)
                            for match in re.findall(r"(?<!\d)(\d{8,10})(?!\d)", mapped):
                                if match not in page_ids:
                                    page_ids.append(match)
            table_page_ids.append(page_ids)

        return OcrExtractionResult(
            full_text=doc.text.strip(),
            page_texts=page_texts,
            has_tables=has_tables,
            table_page_ids=table_page_ids,
            source="document_ai",
        )

    def _try_vision(self, pdf_bytes: bytes) -> OcrExtractionResult | None:
        try:
            from google.cloud import vision  # type: ignore[import-untyped]
        except ImportError:
            return None

        if self._vision_client is None:
            self._vision_client = vision.ImageAnnotatorClient()

        request = vision.AnnotateFileRequest(
            input_config=vision.InputConfig(content=pdf_bytes, mime_type="application/pdf"),
            features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
        )
        response = self._vision_client.batch_annotate_files(requests=[request])
        page_texts = [
            resp.full_text_annotation.text
            for resp in response.responses[0].responses
            if getattr(resp, "full_text_annotation", None)
        ]
        full_text = "\n\n".join(page_texts).strip()
        if not full_text:
            return None
        return OcrExtractionResult(
            full_text=full_text,
            page_texts=page_texts,
            source="cloud_vision",
        )

    def extract(self, pdf_bytes: bytes, file_name: str = "") -> OcrExtractionResult | None:
        docai_res = self._try_document_ai(pdf_bytes)
        if docai_res and len(docai_res.full_text) > 50:
            return docai_res

        vision_res = self._try_vision(pdf_bytes)
        if vision_res and len(vision_res.full_text) > 50:
            return vision_res

        return docai_res or vision_res


class CompositeOcrCascade:
    """Ordered OCR pipeline falling through backends until sufficient text is extracted."""

    def __init__(
        self,
        backends: list[OcrBackend],
        min_chars: int = 50,
    ) -> None:
        self.backends = backends
        self.min_chars = min_chars

    def extract(self, pdf_bytes: bytes, file_name: str = "") -> OcrExtractionResult | None:
        best_partial: OcrExtractionResult | None = None
        for backend in self.backends:
            res = backend.extract(pdf_bytes, file_name=file_name)
            if res and len(res.full_text.strip()) >= self.min_chars:
                return res
            if res and best_partial is None:
                best_partial = res
        return best_partial


def decide_scope_from_pages(
    page_texts: list[str],
    has_table_signals: bool,
    table_page_ids: list[list[str]] | None = None,
) -> tuple[str, list[list[str]]]:
    """Classify PDF scope as 'individual', 'batch', or 'unknown' across pages."""
    page_ids: list[list[str]] = []
    all_ids: list[str] = []

    for idx, text in enumerate(page_texts):
        ids = extract_candidate_worker_ids(text)
        if table_page_ids and idx < len(table_page_ids):
            for tid in table_page_ids[idx]:
                if tid not in ids:
                    ids.append(tid)
        page_ids.append(ids)
        for cand in ids:
            if cand not in all_ids:
                all_ids.append(cand)

    multi_id_page = any(len(ids) >= 2 for ids in page_ids)
    if has_table_signals and multi_id_page:
        return "batch", page_ids
    if len(all_ids) == 1:
        return "individual", page_ids
    if len(all_ids) >= 2 and all(len(ids) <= 1 for ids in page_ids):
        return "individual", page_ids
    return "unknown", page_ids
