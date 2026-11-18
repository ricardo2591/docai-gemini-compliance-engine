"""Redundant privacy, secret, PII, and detector self-tests."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_confidentiality import (  # noqa: E402
    _check_text_against_rules,
    scan_binary_fixtures,
    scan_git_history,
    scan_text_and_configs,
)


def test_zero_confidential_tokens_across_text_and_configs() -> None:
    leaks = scan_text_and_configs(REPO_ROOT)
    assert leaks == [], f"Confidential leaks detected in text/configs: {leaks}"


def test_zero_confidential_tokens_inside_pdf_and_excel_binaries() -> None:
    leaks = scan_binary_fixtures(REPO_ROOT)
    assert leaks == [], f"Confidential leaks detected in PDF/Excel metadata/content: {leaks}"


def test_zero_confidential_tokens_in_git_history() -> None:
    leaks = scan_git_history(REPO_ROOT)
    assert leaks == [], f"Confidential leaks detected in Git commit history: {leaks}"


def test_redundant_security_scanner_catches_simulated_leaks() -> None:
    """Self-test guaranteeing the detector itself never silently passes known leak patterns."""
    synthetic_bad_payloads = [
        "Company: Hybrico Energy",
        "Client: Tower One / Tigo Honduras FII",
        "Contact: Oscar Hernandez & Andres B.",
        r"Path: C:\Users\PC\Desktop\secret.xlsx",
        "Linux: /home/pablo_prado/Desktop/run.py",
        "Secret: AIzaSyDummyLeakedKey1234567890ABCDEF",
        "Server IP: 172.217.14.206",
    ]
    for sample in synthetic_bad_payloads:
        hits = _check_text_against_rules(sample, "negative_test")
        assert len(hits) >= 1, f"Scanner failed to catch simulated leak: {sample}"
