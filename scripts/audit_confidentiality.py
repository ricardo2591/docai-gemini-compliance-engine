"""Redundant multi-layer privacy, secret, PII, and binary-metadata audit scanner.

Scans source code, notebooks (excluding raw base64 image payloads), configs,
markdown, YAML, JSON, binary PDF extracted text/metadata, Excel cell/metadata
contents, and local Git commit history for any proprietary or personal leaks.
"""

from __future__ import annotations

import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path

import openpyxl
from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Companies, clients, internal platforms, templates
    ("company_hybrico", re.compile(r"\bhybrico\b|hybricoenergy", re.IGNORECASE)),
    ("internal_platform_hyforce", re.compile(r"hyforce", re.IGNORECASE)),
    ("client_tower_one", re.compile(r"tower\s*one", re.IGNORECASE)),
    ("company_iotlatam", re.compile(r"iotlatam", re.IGNORECASE)),
    ("operator_tigo", re.compile(r"\btigo\b|\btiguar\b", re.IGNORECASE)),
    ("operator_claro", re.compile(r"\bclaro\b", re.IGNORECASE)),
    ("operator_pti", re.compile(r"\bpti\b", re.IGNORECASE)),
    ("project_fii_fi", re.compile(r"honduras\s*f(?:i|ii)", re.IGNORECASE)),
    ("internal_torre_control", re.compile(r"torre[_\s]+de[_\s]+control", re.IGNORECASE)),
    ("internal_template_t3", re.compile(r"formato\s+(?:de\s+)?(?:acceso\s+)?t3|\bt3\b", re.IGNORECASE)),
    ("internal_filename_autoliq", re.compile(r"autoliquidaciones", re.IGNORECASE)),
    ("internal_domain_newsunroad", re.compile(r"newsunroad|vrpmnhbygiujdtgivuas", re.IGNORECASE)),
    ("internal_drive_id", re.compile(r"1BQKKunlOq12ZlH_Iy1vWaUeqyJNoFK83")),
    # Real employee / colleague / user names from source projects
    (
        "real_person_names",
        re.compile(
            r"\b(?:oscar|andres|pablo[_\s]*prado|pablo|prado|xavier|ariza|cesar|osorio|haydy|alvarado|hconoc|carlos)\b",
            re.IGNORECASE,
        ),
    ),
    ("real_worker_ids", re.compile(r"\b(?:85940039|86454208)\b")),
    ("real_phone_col", re.compile(r"319\s*2881715")),
    # Credentials & secrets
    ("secret_google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{20,}")),
    ("secret_jwt_token", re.compile(r"eyJ[A-Za-z0-9\-_]{10,}\.eyJ[A-Za-z0-9\-_]{10,}")),
    ("secret_private_key", re.compile(r"BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY")),
    ("secret_service_account_json", re.compile(r'"type"\s*:\s*"service_account"')),
    ("secret_supabase_domain", re.compile(r"https://[a-z0-9-]+\.supabase\.co", re.IGNORECASE)),
    # Absolute local filesystem paths
    ("abs_windows_path", re.compile(r"[A-Za-z]:\\\\?Users\\\\?[^\s\"']+", re.IGNORECASE)),
    ("abs_linux_home_path", re.compile(r"/home/[a-z0-9_-]+/", re.IGNORECASE)),
]

TEXT_EXTENSIONS = {
    ".py",
    ".ipynb",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".txt",
    ".cfg",
    ".env",
    ".example",
}
IGNORED_DIRS = {".git", ".pytest_cache", ".ruff_cache", "__pycache__", ".venv", "build", "dist", "staged_pdfs"}

IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SAFE_IP_ALLOWLIST = {"127.0.0.1", "0.0.0.0"}


def _check_text_against_rules(text: str, location: str) -> list[str]:
    findings: list[str] = []
    for label, pattern in FORBIDDEN_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"{location} [{label}]: '{match.group(0)}'")

    for ip_match in IP_PATTERN.finditer(text):
        candidate = ip_match.group(0)
        if candidate in SAFE_IP_ALLOWLIST:
            continue
        try:
            ip_obj = ipaddress.ip_address(candidate)
            if not ip_obj.is_private and not ip_obj.is_reserved:
                findings.append(f"{location} [public_ipv4]: '{candidate}'")
        except ValueError:
            continue
    return findings


def _extract_scannable_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() != ".ipynb":
        return raw

    try:
        nb = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    parts: list[str] = [json.dumps(nb.get("metadata", {}))]
    for cell in nb.get("cells", []):
        src = cell.get("source", "")
        parts.append("".join(src) if isinstance(src, list) else str(src))
        for out in cell.get("outputs", []):
            for key in ("text", "ename", "evalue"):
                val = out.get(key)
                if val:
                    parts.append("".join(val) if isinstance(val, list) else str(val))
            data = out.get("data", {})
            if isinstance(data, dict):
                for mime, payload in data.items():
                    if str(mime).startswith("image/"):
                        continue
                    parts.append("".join(payload) if isinstance(payload, list) else str(payload))
    return "\n".join(parts)


def scan_text_and_configs(repo_root: Path = REPO_ROOT) -> list[str]:
    """Layer 1: Scan all text, scripts, notebooks, configs, markdown, and JSON/YAML files."""
    findings: list[str] = []
    self_path = Path(__file__).resolve()

    for path in repo_root.rglob("*"):
        if path.is_dir() or any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.resolve() == self_path or path.name == "test_security_audit.py":
            continue

        rel = str(path.relative_to(repo_root))
        findings.extend(_check_text_against_rules(rel, f"filename:{rel}"))

        if path.suffix.lower() in TEXT_EXTENSIONS or path.name in {"Makefile", ".gitignore", ".env.example"}:
            content = _extract_scannable_text(path)
            findings.extend(_check_text_against_rules(content, rel))
    return findings


def scan_binary_fixtures(repo_root: Path = REPO_ROOT) -> list[str]:
    """Layer 2: Scan extracted text and document properties inside `.pdf` and `.xlsx` files."""
    findings: list[str] = []

    for pdf_path in repo_root.rglob("*.pdf"):
        if any(part in IGNORED_DIRS for part in pdf_path.parts):
            continue
        rel = str(pdf_path.relative_to(repo_root))
        reader = PdfReader(str(pdf_path))
        meta_text = " ".join(f"{k}={v}" for k, v in (reader.metadata or {}).items())
        page_text = "\n".join((p.extract_text() or "") for p in reader.pages)
        findings.extend(_check_text_against_rules(f"{meta_text}\n{page_text}", f"pdf:{rel}"))

    for xlsx_path in repo_root.rglob("*.xlsx"):
        if any(part in IGNORED_DIRS for part in xlsx_path.parts) or xlsx_path.name.startswith("~$"):
            continue
        rel = str(xlsx_path.relative_to(repo_root))
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        props = wb.properties
        meta_parts = [
            f"{attr}={getattr(props, attr, '')}"
            for attr in ("creator", "lastModifiedBy", "title", "subject", "description", "keywords", "category")
            if getattr(props, attr, None)
        ]
        meta_text = " ".join(meta_parts)
        cell_texts: list[str] = [meta_text]
        for ws in wb.worksheets:
            cell_texts.append(ws.title)
            for row in ws.iter_rows(values_only=True):
                for val in row:
                    if val is not None:
                        cell_texts.append(str(val))
        findings.extend(_check_text_against_rules("\n".join(cell_texts), f"xlsx:{rel}"))

    return findings


def scan_git_history(repo_root: Path = REPO_ROOT) -> list[str]:
    """Layer 3: Scan Git commit author/committer emails, names, and commit messages."""
    if not (repo_root / ".git").exists():
        return []
    result = subprocess.run(
        ["git", "-C", str(repo_root), "log", "--all", "--format=%an|%ae|%cn|%ce|%s"],
        capture_output=True,
        text=True,
        check=False,
    )
    findings: list[str] = []
    for line in result.stdout.splitlines():
        findings.extend(_check_text_against_rules(line, "git_history"))
    return findings


def main() -> int:
    findings = scan_text_and_configs() + scan_binary_fixtures() + scan_git_history()
    if findings:
        print("REDUNDANT SECURITY & CONFIDENTIALITY AUDIT FAILED:")
        for item in findings:
            print(f"  - {item}")
        return 1
    print(
        "Redundant security audit passed: 0 leaks across code, notebooks, configs, "
        "binary PDF/Excel fixtures, filenames, and Git history."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
