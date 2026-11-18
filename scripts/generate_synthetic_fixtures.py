"""Generate 100% synthetic Excel access requests and PDF compliance fixtures.

Every entity, worker name, national ID, tax ID, and date is purely synthetic
and designed to exercise consolidated payroll retention, multi-page certificate
splitting, chronological course date selection, and Resolution 4272/2021 checks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import openpyxl
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from compliance_engine.config import load_config


def _write_pdf(path: Path, pages: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    for page_lines in pages:
        y = 750
        c.setFont("Helvetica", 11)
        for line in page_lines:
            c.drawString(50, y, line)
            y -= 20
        c.showPage()
    c.save()
    return path


def _build_synthetic_request_excel(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Access_Request"

    ws["A1"] = "CONTRACTOR SITE ACCESS REQUEST"
    ws["C3"] = "Permit Window:"
    ws["D3"] = datetime(2026, 7, 1)
    ws["E3"] = datetime(2026, 7, 10)

    # Header row at 0-based index 7 -> Excel row 8
    headers = ["Nombre Personal", "ARL", "EPS", "CC", "Contratista", "Correo"]
    for col_idx, h in enumerate(headers, start=1):
        ws.cell(row=8, column=col_idx, value=h)

    rows = [
        ["Jane Doe", "ARL Demo Safe", "EPS Demo Health", "901234561", "ACME Contractor SAS", "jane.doe@example.com"],
        ["Robert Smith", "ARL Demo Safe", "EPS Demo Health", "901234562", "ACME Contractor SAS", "robert.smith@example.com"],
        ["Elena Gomez", "ARL Demo Safe", "EPS Demo Health", "901234563", "ACME Contractor SAS", "elena.gomez@example.com"],
        ["Marco Silva", "ARL Demo Safe", "EPS Demo Health", "901234564", "ACME Contractor SAS", "marco.silva@example.com"],
    ]
    for r_offset, row in enumerate(rows, start=9):
        for col_idx, val in enumerate(row, start=1):
            ws.cell(row=r_offset, column=col_idx, value=val)

    wb.save(path)
    return path


def generate_all_fixtures(output_dir: Path | None = None) -> list[Path]:
    """Generate clean synthetic workbook and PDFs inside `output_dir`."""
    cfg = load_config()
    dst = output_dir or cfg.fixtures_dir
    dst.mkdir(parents=True, exist_ok=True)

    excel_path = _build_synthetic_request_excel(dst / "REQ-1001_access_request.xlsx")

    # 2-page consolidated social-security payroll PDF (should NOT be split)
    payroll_pdf = _write_pdf(
        dst / "REQ-1001_payroll_consolidated.pdf",
        pages=[
            [
                "PLANILLA INTEGRADA DE LIQUIDACION DE APORTES (PILA) - CONSOLIDATED PAYROLL",
                "Total Afiliados: 3 | Sucursal Principal",
                "Empresa Aportante: ACME Contractor SAS",
                "NIT: 900123451",
                "Fecha de Pago: 2026-07-02 | Periodo: 2026-07",
                "Worker ID | Full Name | IBC | Contribution Status",
                "CC: 901234561 | Jane Doe | $1,500,000 | PAID",
                "CC: 901234562 | Robert Smith | $1,600,000 | PAID",
            ],
            [
                "PLANILLA INTEGRADA DE LIQUIDACION DE APORTES (CONTINUACION)",
                "Empresa Aportante: ACME Contractor SAS",
                "NIT: 900123451",
                "Fecha de Pago: 2026-07-02 | Periodo: 2026-07",
                "CC: 901234563 | Elena Gomez | $1,450,000 | PAID",
            ],
        ],
    )

    # 3-page individual safety-at-heights certificates PDF (SHOULD be split per page)
    certs_pdf = _write_pdf(
        dst / "REQ-1001_safety_certificates.pdf",
        pages=[
            [
                "CERTIFICADO DE TRABAJO SEGURO EN ALTURAS",
                "Centro Capacitador: SafeWork Academy Demo",
                "Se certifica que Jane Doe identificado con CC: 901234561",
                "Realizo el curso avanzado de trabajo en alturas.",
                "Empleador: ACME Contractor SAS",
                "NIT Empleador: 900123451",
                "Fecha Curso: 2026-02-15",
            ],
            [
                "CERTIFICADO DE REENTRENAMIENTO TRABAJO EN ALTURAS",
                "Centro Capacitador: Vertical Safety Demo",
                "Se certifica que Robert Smith identificado con CC: 901234562",
                "Historial de formacion:",
                "Fecha Curso: 2023-05-10",
                "Fecha Curso: 2025-11-20",
                "Empleador: Other Subcontractor Ltd",
                "NIT Empleador: 800999888",
            ],
            [
                "CERTIFICADO DE TRABAJO EN ALTURAS",
                "Centro Capacitador: SafeWork Academy Demo",
                "Se certifica que Elena Gomez identificado con CC: 901234563",
                "Empleador: ACME Contractor SAS",
                "NIT Empleador: 900123451",
                "Fecha Curso: 2024-01-05",
            ],
        ],
    )

    return [excel_path, payroll_pdf, certs_pdf]


if __name__ == "__main__":
    generated = generate_all_fixtures()
    for file_path in generated:
        print(f"Generated synthetic fixture: {file_path} ({file_path.stat().st_size} bytes)")
