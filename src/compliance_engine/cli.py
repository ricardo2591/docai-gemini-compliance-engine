"""CLI entrypoints for fixture generation and compliance validation execution."""

from __future__ import annotations

import argparse
from pathlib import Path

from compliance_engine.config import load_config
from compliance_engine.pipelines.runner import CompliancePipelineRunner


def fixtures_cmd(argv: list[str] | None = None) -> None:
    from scripts.generate_synthetic_fixtures import generate_all_fixtures

    parser = argparse.ArgumentParser(description="Generate synthetic Excel & PDF test fixtures.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    files = generate_all_fixtures(cfg.fixtures_dir)
    print(f"Generated {len(files)} synthetic fixtures in {cfg.fixtures_dir}")


def run_cmd(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run multimodal compliance pipeline on folder.")
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--cloud", action="store_true", help="Enable live Document AI / Vision / Gemini.")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    runner = CompliancePipelineRunner(cfg, use_cloud=args.cloud)
    summary = runner.run_directory(input_dir=args.input_dir, output_dir=args.output_dir)
    print(
        f"Compliance pipeline complete: {summary.total_records} workers | "
        f"Approved={summary.approved_records}, Rejected={summary.rejected_records} | "
        f"Report={summary.output_excel_path}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="compliance_engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("fixtures")
    args, remainder = parser.parse_known_args(argv)
    if args.command == "run":
        run_cmd(remainder)
    elif args.command == "fixtures":
        fixtures_cmd(remainder)


if __name__ == "__main__":
    main()
