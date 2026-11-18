"""Unit tests for PILA calendar, payroll/safety validators, and ComplianceEvaluator."""

from compliance_engine.llm.extractor import RuleBasedMockExtractor
from compliance_engine.validators.compliance_evaluator import ComplianceEvaluator
from compliance_engine.validators.pila_calendar import compute_payroll_cutoff_date
from compliance_engine.validators.safety_training import SafetyTrainingValidator
from compliance_engine.validators.social_security import PayrollValidator


def test_pila_cutoff_calendar_and_december_rollover() -> None:
    # Suffix '51' July payment -> August index (14th)
    assert compute_payroll_cutoff_date("900123451", "2026-07-02") == "2026-08-14"
    # Suffix '00' December payment -> January next year (deadline index 0 = day 3)
    assert compute_payroll_cutoff_date("900000000", "2026-12-10") == "2027-01-03"
    assert compute_payroll_cutoff_date(None, "2026-07-02") is None
    assert compute_payroll_cutoff_date("900123451", "invalid-date") is None


def test_safety_training_chronological_override_and_18_months() -> None:
    # Must override wrong older date selected by LLM
    chosen = SafetyTrainingValidator.select_most_recent_course_date(
        ["2023-05-10", "2025-11-20", "not-a-date"],
        llm_selected_date="2023-05-10",
    )
    assert chosen == "2025-11-20"
    assert SafetyTrainingValidator.calculate_valid_until("2025-11-20", 18) == "2027-05-20"
    assert SafetyTrainingValidator.calculate_valid_until("2024-08-31", 18) == "2026-02-28"
    assert SafetyTrainingValidator.calculate_status("2026-07-10", "2026-07-10") == "VALID"
    assert SafetyTrainingValidator.calculate_status("2026-07-09", "2026-07-10") == "EXPIRED"


def test_validators_from_text_independent_and_mismatched_docs() -> None:
    extractor = RuleBasedMockExtractor()
    ind_text = (
        "PLANILLA PILA SEGURIDAD SOCIAL\n"
        "Empresa Aportante: Independent\nNIT: 901234569\n"
        "Fecha de Pago: 2026-07-03\nCC: 901234569"
    )
    res = PayrollValidator.validate_from_text(ind_text, "ind.pdf", "901234569", extractor)
    assert res["is_valid_payroll_doc"] is True
    assert res["payroll_tax_id"] == "901234569"

    # Course document must fail payroll validation
    cert_text = "CERTIFICADO TRABAJO EN ALTURAS\nEmpleador: ACME\nNIT Empleador: 900123451\nCC: 901234569\nFecha Curso: 2026-02-01"
    bad_pf = PayrollValidator.validate_from_text(cert_text, "cert.pdf", "901234569", extractor)
    assert bad_pf["is_valid_payroll_doc"] is False

    # Off-by-1 worker ID (901234564 Marco Silva) must NOT match listed worker 901234561 Jane Doe
    payroll_text = (
        "PLANILLA PILA SEGURIDAD SOCIAL\n"
        "Empresa Aportante: ACME\nNIT: 900123451\n"
        "Fecha de Pago: 2026-07-02\nCC: 901234561 | Jane Doe"
    )
    unlisted = PayrollValidator.validate_from_text(
        payroll_text, "pila.pdf", "901234564", extractor, worker_name="Marco Silva"
    )
    assert unlisted["is_valid_payroll_doc"] is False


def test_compliance_evaluator_all_decision_branches() -> None:
    evaluator = ComplianceEvaluator(facility_operator_name="Facility Operator")

    # 1. Missing payroll
    out_missing = evaluator.evaluate({"payroll_status": "MISSING_DOCUMENTS"})
    assert out_missing.overall_status.startswith("REJECTED")

    # 2. Payroll validity expires before permit end
    out_short = evaluator.evaluate(
        {
            "payroll_status": "EXPIRED",
            "payroll_valid_until": "2026-07-05",
            "permit_end_date": "2026-07-10",
        }
    )
    assert "does not cover" in out_short.rejection_reason

    # 3. Payment month mismatch
    out_month = evaluator.evaluate(
        {
            "payroll_status": "EXPIRED",
            "payroll_payment_date": "2026-06-05",
            "permit_start_date": "2026-07-01",
        }
    )
    assert "does not match the requested permit month" in out_month.rejection_reason

    # 4. Fully approved matching tax IDs
    out_ok = evaluator.evaluate(
        {
            "payroll_status": "VALID",
            "safety_status": "VALID",
            "payroll_tax_id": "900123451",
            "safety_tax_id": "900123451",
        }
    )
    assert out_ok.overall_status.startswith("APPROVED")
    assert out_ok.rejection_reason == "NO_ISSUES"

    # 5. Approved general, rejected heights (expired cert)
    out_exp_cert = evaluator.evaluate({"payroll_status": "VALID", "safety_status": "EXPIRED"})
    assert out_exp_cert.overall_status.startswith("APPROVED")
    assert "expired safety certificate" in out_exp_cert.rejection_reason

    # 6. Approved general, rejected heights (tax ID mismatch)
    out_mismatch = evaluator.evaluate(
        {
            "payroll_status": "VALID",
            "safety_status": "VALID",
            "payroll_tax_id": "900123451",
            "safety_tax_id": "800999888",
        }
    )
    assert out_mismatch.overall_status.startswith("APPROVED")
    assert "Resolution 4272/2021" in out_mismatch.rejection_reason
