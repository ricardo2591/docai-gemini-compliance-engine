"""Unit tests for DebouncedTicketWatcher and PDF classification/splitting."""

from pathlib import Path
from threading import Event
from unittest.mock import patch

from pypdf import PdfReader

from compliance_engine.config import load_config
from compliance_engine.llm.extractor import RuleBasedMockExtractor
from compliance_engine.ocr.cascade import CompositeOcrCascade, LocalPdfTextBackend
from compliance_engine.pdf.splitter import (
    PdfDocumentSplitter,
    classify_by_filename,
    separate_pdf_pages,
)
from compliance_engine.watcher.queue_watcher import DebouncedTicketWatcher


def test_debounced_ticket_watcher_groups_and_dispatches() -> None:
    done = Event()
    dispatched: list[tuple[str, list[str]]] = []

    def _callback(ticket: str, files: list[str]) -> None:
        dispatched.append((ticket, files))
        done.set()

    watcher = DebouncedTicketWatcher(on_ticket_ready=_callback, delay_seconds=0.05, max_workers=1)
    try:
        assert watcher.handle_file_arrival("untaggedfile.pdf") is None
        watcher.handle_file_arrival("REQ-1001_access.xlsx")
        watcher.handle_file_arrival("REQ-1001_cert.pdf")
        assert done.wait(timeout=2.0), "Debounced watcher did not dispatch ticket in time"
        assert len(dispatched) == 1
        assert dispatched[0][0] == "REQ-1001"
        assert len(dispatched[0][1]) == 2
    finally:
        watcher.stop()


def test_pdf_splitter_keeps_consolidated_and_splits_individual(tmp_path: Path) -> None:
    cfg = load_config()
    cascade = CompositeOcrCascade([LocalPdfTextBackend()], min_chars=20)
    splitter = PdfDocumentSplitter(cascade, RuleBasedMockExtractor())

    payroll_res = splitter.process_file(
        cfg.fixtures_dir / "REQ-1001_payroll_consolidated.pdf",
        tmp_path / "out_payroll",
    )
    assert payroll_res.action_taken == "KEPT_CONSOLIDATED"
    assert len(payroll_res.generated_files) == 1

    certs_res = splitter.process_file(
        cfg.fixtures_dir / "REQ-1001_safety_certificates.pdf",
        tmp_path / "out_certs",
    )
    assert certs_res.action_taken == "SPLIT_PAGES"
    assert len(certs_res.generated_files) == 3
    for generated_pdf in certs_res.generated_files:
        assert len(PdfReader(str(generated_pdf)).pages) == 1

    # Single-page helper sanity check
    raw_pages = separate_pdf_pages((cfg.fixtures_dir / "REQ-1001_safety_certificates.pdf").read_bytes())
    assert len(raw_pages) == 3


def test_classify_by_filename_fallback() -> None:
    assert classify_by_filename("planilla_pila_julio.pdf").should_split is False
    assert classify_by_filename("curso_alturas_equipo.pdf").should_split is True
    assert classify_by_filename("unrecognized_scan.pdf").should_split is True


def test_splitter_uses_filename_fallback_when_ocr_empty(tmp_path: Path) -> None:
    cfg = load_config()
    cascade = CompositeOcrCascade([LocalPdfTextBackend()], min_chars=20)
    splitter = PdfDocumentSplitter(cascade, RuleBasedMockExtractor())

    with patch.object(cascade, "extract", return_value=None):
        res = splitter.process_file(
            cfg.fixtures_dir / "REQ-1001_payroll_consolidated.pdf",
            tmp_path / "fallback",
        )
    assert res.document_type == "CONSOLIDATED_PAYROLL"
    assert res.action_taken == "KEPT_CONSOLIDATED"
