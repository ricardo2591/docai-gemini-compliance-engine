"""Statutory monthly payroll contribution cutoff calendar keyed by 2-digit tax ID suffix."""

from __future__ import annotations

from datetime import datetime

# Standard 12-month payment deadline schedule by trailing two digits of employer tax ID.
PAYROLL_DEADLINE_BY_SUFFIX: dict[str, list[int]] = {
    **{f"{d:02d}": [3, 4, 4, 2, 5, 4, 4, 4, 2, 2, 5, 2] for d in range(0, 8)},
    **{f"{d:02d}": [4, 5, 5, 3, 6, 5, 5, 5, 3, 3, 6, 3] for d in range(8, 15)},
    **{f"{d:02d}": [5, 6, 6, 4, 7, 6, 7, 6, 4, 6, 7, 4] for d in range(15, 22)},
    **{f"{d:02d}": [6, 7, 7, 7, 8, 9, 8, 8, 5, 7, 10, 5] for d in range(22, 29)},
    **{f"{d:02d}": [7, 10, 10, 8, 9, 10, 9, 11, 8, 8, 11, 9] for d in range(29, 36)},
    **{f"{d:02d}": [10, 11, 11, 9, 12, 11, 10, 12, 9, 9, 12, 10] for d in range(36, 43)},
    **{f"{d:02d}": [11, 12, 12, 10, 13, 12, 11, 13, 10, 10, 13, 11] for d in range(43, 50)},
    **{f"{d:02d}": [12, 13, 13, 11, 14, 13, 14, 14, 11, 14, 14, 12] for d in range(50, 57)},
    **{f"{d:02d}": [13, 14, 14, 14, 15, 16, 15, 15, 12, 15, 18, 15] for d in range(57, 64)},
    **{f"{d:02d}": [14, 17, 17, 15, 16, 17, 16, 19, 15, 16, 19, 16] for d in range(64, 70)},
    **{f"{d:02d}": [17, 18, 18, 16, 19, 18, 17, 20, 16, 17, 20, 17] for d in range(70, 76)},
    **{f"{d:02d}": [18, 19, 19, 21, 20, 19, 18, 21, 17, 20, 21, 18] for d in range(76, 82)},
    **{f"{d:02d}": [19, 20, 20, 22, 21, 20, 21, 22, 18, 21, 24, 19] for d in range(82, 88)},
    **{f"{d:02d}": [20, 21, 21, 23, 22, 24, 22, 25, 19, 22, 25, 22] for d in range(88, 94)},
    **{f"{d:02d}": [21, 24, 25, 24, 23, 25, 23, 26, 22, 23, 26, 23] for d in range(94, 100)},
}


def compute_payroll_cutoff_date(tax_id: str | None, payment_date: str | None) -> str | None:
    """Compute social-security validity cutoff (`YYYY-MM-DD`) for next month after payment."""
    if not tax_id or not payment_date:
        return None

    digits = "".join(ch for ch in str(tax_id) if ch.isdigit())
    if not digits:
        return None
    suffix = digits[-2:].zfill(2)
    deadlines = PAYROLL_DEADLINE_BY_SUFFIX.get(suffix)
    if not deadlines:
        return None

    try:
        paid_dt = datetime.strptime(str(payment_date).strip(), "%Y-%m-%d")
    except ValueError:
        return None

    pay_month_idx = paid_dt.month - 1
    next_month_idx = (pay_month_idx + 1) % 12
    target_year = paid_dt.year + (1 if paid_dt.month == 12 else 0)
    day_limit = deadlines[next_month_idx]
    return f"{target_year}-{next_month_idx + 1:02d}-{day_limit:02d}"
