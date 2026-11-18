"""Integration tests for Excel IO (>26 columns), null sanitization, and OCR cascade."""

from datetime import datetime
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

from compliance_engine.config import load_config
from compliance_engine.ocr.cascade import (
    CompositeOcrCascade,
    OcrExtractionResult,
    decide_scope_from_pages,
)
from compliance_engine.pipelines.excel_io import (
    export_results_excel,
    read_access_request_excel,
    sanitize_cell_value,
)


def test_sanitize_and_excel_export_beyond_column_z(tmp_path: Path) -> None:
    assert sanitize_cell_value(None) == ""
    assert sanitize_cell_value(np.nan) == ""
    assert sanitize_cell_value("null") == ""
    assert sanitize_cell_value("  VALID  ") == "VALID"

    # Build a 30-column DataFrame (columns 27..30 map to AA..AD, catching legacy chr(65+n) bug)
    data = {f"col_{i:02d}": [f"val_{i}", None] for i in range(1, 31)}
    df = pd.DataFrame(data)
    out_xlsx = export_results_excel(df, tmp_path / "wide_report.xlsx")

    wb = openpyxl.load_workbook(out_xlsx, data_only=True)
    ws = wb.active
    assert ws.max_column == 30
    assert ws["AD1"].value == "col_30"
    assert ws["AD2"].value == "val_30"
    assert ws["AD3"].value in (None, "")


def test_read_access_request_excel_handles_invalid_inputs(tmp_path: Path) -> None:
    cfg = load_config()

    # Invalid dates -> None
    bad_dates = tmp_path / "REQ-BAD_dates.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["D3"] = "not-a-date"
    ws["E3"] = datetime(2026, 7, 5)
    wb.save(bad_dates)
    assert read_access_request_excel(bad_dates, cfg) is None

    # Missing required columns -> ValueError
    missing_cols = tmp_path / "REQ-BAD_cols.xlsx"
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2["D3"] = datetime(2026, 7, 1)
    ws2["E3"] = datetime(2026, 7, 5)
    ws2.cell(row=8, column=1, value="WrongHeader")
    wb2.save(missing_cols)
    with pytest.raises(ValueError, match="Missing expected input columns"):
        read_access_request_excel(missing_cols, cfg)


def test_composite_ocr_cascade_and_scope_decision() -> None:
    class EmptyBackend:
        name = "empty"

        def extract(self, pdf_bytes: bytes, file_name: str = "") -> OcrExtractionResult | None:
            return OcrExtractionResult(full_text="short", source="empty")

    class RichBackend:
        name = "rich"

        def extract(self, pdf_bytes: bytes, file_name: str = "") -> OcrExtractionResult | None:
            return OcrExtractionResult(
                full_text="PLANILLA PILA\nCC: 901234561\nCC: 901234562\nValid text over 50 chars.",
                page_texts=["CC: 901234561\nCC: 901234562"],
                has_tables=True,
                source="rich",
            )

    cascade = CompositeOcrCascade([EmptyBackend(), RichBackend()], min_chars=30)
    res = cascade.extract(b"%PDF-dummy", "test.pdf")
    assert res is not None
    assert res.source == "rich"

    scope_batch, _ = decide_scope_from_pages(res.page_texts, has_table_signals=True)
    assert scope_batch == "batch"

    scope_ind, _ = decide_scope_from_pages(["CC: 901234561", "CC: 901234562"], has_table_signals=False)
    assert scope_ind == "individual"
