"""Excel template reader and sanitized multi-column exporter."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from compliance_engine.config import OUTPUT_COLUMNS, AppConfig


def sanitize_cell_value(value: Any) -> str:
    """Normalize None/NaN/NaT/literal nulls to clean empty strings before sheet export."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "nan", "nat", "null"}:
        return ""
    return text


def read_access_request_excel(excel_path: Path, cfg: AppConfig) -> pd.DataFrame | None:
    """Parse contractor access-request spreadsheet header dates and worker roster."""
    tmpl_cfg = cfg.raw["excel_template"]
    start_cell = tmpl_cfg.get("start_date_cell", "D3")
    end_cell = tmpl_cfg.get("end_date_cell", "E3")
    header_row = int(tmpl_cfg.get("table_header_row_0idx", 7))
    col_mapping: dict[str, str] = tmpl_cfg["column_mapping"]

    workbook = openpyxl.load_workbook(excel_path, data_only=True)
    sheet = workbook.active
    raw_start = sheet[start_cell].value
    raw_end = sheet[end_cell].value

    try:
        start_dt = pd.to_datetime(raw_start)
        end_dt = pd.to_datetime(raw_end)
        if pd.isna(start_dt) or pd.isna(end_dt):
            raise ValueError("Missing start or end date")
    except Exception:
        logging.warning("Invalid permit dates (%s/%s) in '%s'; skipping.", start_cell, end_cell, excel_path.name)
        return None

    df = pd.read_excel(excel_path, header=header_row)
    df = df.rename(columns=col_mapping)
    expected_input_cols = ["full_name", "insurance_provider", "health_provider", "worker_id", "contractor_name", "email"]
    missing = [col for col in expected_input_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing expected input columns in '{excel_path.name}': {missing}")

    df = df[expected_input_cols].dropna(subset=["worker_id"]).copy()
    if df.empty:
        return None

    df["worker_id"] = df["worker_id"].astype(str).str.split(".").str[0].str.strip()
    request_id = excel_path.stem.split("_")[0]
    df["request_id"] = request_id
    df["validated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df["permit_start_date"] = start_dt.strftime("%Y-%m-%d")
    df["permit_end_date"] = end_dt.strftime("%Y-%m-%d")

    df = df.reindex(columns=OUTPUT_COLUMNS)
    for col in OUTPUT_COLUMNS:
        df[col] = df[col].astype("object")
    return df


def export_results_excel(master_df: pd.DataFrame, output_path: Path) -> Path:
    """Write validated compliance records to a clean Excel workbook using `get_column_letter`."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sanitized = master_df.copy()
    for col in sanitized.columns:
        sanitized[col] = sanitized[col].map(sanitize_cell_value)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Validation_Results"

    header_fill = PatternFill(start_color="1B4332", end_color="1B4332", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    headers = list(sanitized.columns)
    for col_idx, col_name in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = header_fill
        cell.font = header_font

    for row_idx, row_vals in enumerate(sanitized.itertuples(index=False, name=None), start=2):
        for col_idx, val in enumerate(row_vals, start=1):
            ws.cell(row=row_idx, column=col_idx, value=val)

    for col_idx, col_name in enumerate(headers, start=1):
        col_letter = get_column_letter(col_idx)
        max_len = max([len(str(col_name))] + [len(str(v)) for v in sanitized[col_name].tolist()])
        ws.column_dimensions[col_letter].width = min(max_len + 3, 46)

    wb.save(output_path)
    return output_path
