"""Configuration loader and canonical output schema for compliance validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "compliance.yaml"

OUTPUT_COLUMNS: list[str] = [
    "request_id",
    "company_tax_id",
    "worker_id",
    "full_name",
    "email",
    "insurance_provider",
    "health_provider",
    "contractor_name",
    "validated_at",
    "permit_start_date",
    "permit_end_date",
    "payroll_payment_date",
    "payroll_valid_until",
    "payroll_status",
    "safety_training_date",
    "safety_valid_until",
    "safety_status",
    "overall_status",
    "rejection_reason",
    "observations",
    "payroll_company_name",
    "payroll_tax_id",
    "safety_employer_name",
    "safety_tax_id",
    "payroll_document",
    "safety_document",
]


@dataclass(frozen=True)
class AppConfig:
    """Resolved runtime configuration for OCR, LLM extraction, validators, and IO."""

    repo_root: Path
    raw: dict[str, Any]

    def resolve_path(self, key: str) -> Path:
        """Resolve a path configured under `paths.<key>` relative to repo root."""
        rel_path = Path(self.raw["paths"][key])
        return rel_path if rel_path.is_absolute() else (self.repo_root / rel_path)

    @property
    def fixtures_dir(self) -> Path:
        return self.resolve_path("fixtures_dir")

    @property
    def output_dir(self) -> Path:
        return self.resolve_path("output_dir")

    @property
    def figures_dir(self) -> Path:
        return self.resolve_path("figures_dir")


def load_config(
    config_path: str | Path | None = None,
    repo_root: Path | None = None,
) -> AppConfig:
    """Load YAML pipeline configuration with relative paths bound to `repo_root`."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    cfg_file = Path(config_path) if config_path is not None else (root / "configs" / "compliance.yaml")
    with cfg_file.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AppConfig(repo_root=root, raw=raw)
