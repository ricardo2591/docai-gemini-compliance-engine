"""End-to-end compliance pipeline verification on synthetic PDF and Excel fixtures."""

import json
from pathlib import Path

import openpyxl
import pandas as pd

from compliance_engine.config import OUTPUT_COLUMNS, load_config
from compliance_engine.pipelines.runner import CompliancePipelineRunner


def test_full_pipeline_end_to_end_on_synthetic_fixtures(tmp_path: Path) -> None:
    cfg = load_config()
    assert (cfg.fixtures_dir / "REQ-1001_access_request.xlsx").exists()

    runner = CompliancePipelineRunner(cfg, use_cloud=False)
    summary = runner.run_directory(input_dir=cfg.fixtures_dir, output_dir=tmp_path)

    assert summary.total_records == 4
    assert summary.approved_records == 3
    assert summary.rejected_records == 1
    assert summary.output_excel_path.exists()
    assert summary.output_json_path.exists()

    # Validate output Excel integrity & strict column schema
    wb = openpyxl.load_workbook(summary.output_excel_path, data_only=True)
    ws = wb["Validation_Results"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    assert headers == OUTPUT_COLUMNS

    for row in ws.iter_rows(min_row=2, values_only=True):
        for cell_val in row:
            if cell_val is not None:
                assert str(cell_val).lower() not in {"nan", "nat", "none"}

    # Validate exact deterministic outcomes per synthetic worker
    payload = json.loads(summary.output_json_path.read_text(encoding="utf-8"))
    by_id = {rec["worker_id"]: rec for rec in payload["records"]}

    # 1. Jane Doe -> Fully Approved
    w1 = by_id["901234561"]
    assert w1["payroll_status"] == "VALID"
    assert w1["safety_status"] == "VALID"
    assert w1["overall_status"].startswith("APPROVED")
    assert w1["rejection_reason"] == "NO_ISSUES"

    # 2. Robert Smith -> Chronological date override + Resolution 4272/2021 tax ID mismatch
    w2 = by_id["901234562"]
    assert w2["safety_training_date"] == "2025-11-20"
    assert w2["overall_status"].startswith("APPROVED")
    assert "Resolution 4272/2021" in w2["rejection_reason"]

    # 3. Elena Gomez -> Expired work-at-heights training
    w3 = by_id["901234563"]
    assert w3["payroll_status"] == "VALID"
    assert w3["safety_status"] == "EXPIRED"
    assert "expired safety certificate" in w3["rejection_reason"]

    # 4. Marco Silva -> Missing payroll documents -> Rejected
    w4 = by_id["901234564"]
    assert w4["payroll_status"] == "MISSING_DOCUMENTS"
    assert w4["overall_status"].startswith("REJECTED")

    # Confirm pandas reads the exported workbook cleanly without corruption
    df_out = pd.read_excel(summary.output_excel_path, dtype=str)
    assert df_out.shape == (4, len(OUTPUT_COLUMNS))
