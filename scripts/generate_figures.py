"""Generate visual summary chart from synthetic compliance validation output."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns

from compliance_engine.config import load_config

sns.set_theme(style="whitegrid")


def build_compliance_summary_chart(summary_json: Path, out_png: Path) -> Path:
    data = json.loads(summary_json.read_text(encoding="utf-8"))
    records = data.get("records", [])

    status_counts = {
        "Fully Approved": 0,
        "Approved (Heights Restricted)": 0,
        "Rejected (Payroll/Docs)": 0,
    }
    for rec in records:
        overall = rec.get("overall_status", "")
        reason = rec.get("rejection_reason", "")
        if overall.startswith("REJECTED"):
            status_counts["Rejected (Payroll/Docs)"] += 1
        elif reason == "NO_ISSUES":
            status_counts["Fully Approved"] += 1
        else:
            status_counts["Approved (Heights Restricted)"] += 1

    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(11.5, 4.4), dpi=130)

    labels = list(status_counts.keys())
    values = list(status_counts.values())
    colors = ["#2D6A4F", "#F4A261", "#D62828"]
    bars = ax_left.bar(labels, values, color=colors, edgecolor="#1B1B1B", linewidth=0.8)
    ax_left.set_title("Compliance Decision Distribution (Synthetic Batch)", fontweight="bold")
    ax_left.set_ylabel("Worker Records")
    ax_left.tick_params(axis="x", rotation=12)
    ax_left.set_ylim(0, max(values + [1]) + 0.8)
    for bar in bars:
        h = bar.get_height()
        ax_left.text(bar.get_x() + bar.get_width() / 2.0, h + 0.08, str(int(h)), ha="center", fontweight="bold")

    # Right panel: component checks breakdown (Payroll vs Safety Cert)
    categories = ["Social Security\n(Payroll PILA)", "Work at Heights\n(Safety Training)"]
    valid_counts = [
        sum(1 for r in records if r.get("payroll_status") == "VALID"),
        sum(1 for r in records if r.get("safety_status") == "VALID"),
    ]
    non_valid_counts = [len(records) - v for v in valid_counts]

    x = [0, 1]
    ax_right.bar(x, valid_counts, width=0.45, label="VALID", color="#2D6A4F", edgecolor="#1B1B1B")
    ax_right.bar(
        x,
        non_valid_counts,
        bottom=valid_counts,
        width=0.45,
        label="EXPIRED / MISSING",
        color="#E76F51",
        edgecolor="#1B1B1B",
    )
    ax_right.set_xticks(x)
    ax_right.set_xticklabels(categories)
    ax_right.set_title("Sub-Validator Pass vs. Flagged Checks", fontweight="bold")
    ax_right.set_ylabel("Worker Records")
    ax_right.set_ylim(0, len(records) + 1.2)
    ax_right.legend(loc="upper right", frameon=True, facecolor="white")

    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out_png


if __name__ == "__main__":
    cfg = load_config()
    json_path = cfg.output_dir / "compliance_summary.json"
    out_path = cfg.figures_dir / "compliance_summary.png"
    build_compliance_summary_chart(json_path, out_path)
    print(f"Generated compliance summary chart: {out_path}")
