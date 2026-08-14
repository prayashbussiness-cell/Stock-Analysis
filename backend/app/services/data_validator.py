"""
Validation rules applied before any row is written to
daily_market_data. Invalid rows are dropped (and logged); rows that
are plausible but internally inconsistent are flagged for review
rather than silently corrected.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.nse_client import RawMarketRow


@dataclass
class ValidationResult:
    is_valid: bool
    flagged: bool
    reasons: list[str]


def validate_row(row: RawMarketRow) -> ValidationResult:
    reasons: list[str] = []
    is_valid = True

    if row.close <= 0:
        reasons.append("close <= 0")
        is_valid = False
    if row.high < row.low:
        reasons.append("high < low")
        is_valid = False
    if row.high < row.open:
        reasons.append("high < open")
        is_valid = False
    if row.high < row.close:
        reasons.append("high < close")
        is_valid = False
    if row.low > row.open:
        reasons.append("low > open")
        is_valid = False
    if row.low > row.close:
        reasons.append("low > close")
        is_valid = False
    if row.volume < 0:
        reasons.append("volume < 0")
        is_valid = False

    flagged = False
    if row.deliverable_quantity is not None and row.traded_quantity is not None:
        if row.deliverable_quantity < 0:
            reasons.append("deliverable_quantity < 0")
            is_valid = False
        elif row.deliverable_quantity > row.traded_quantity:
            reasons.append("deliverable_quantity > traded_quantity")
            flagged = True  # plausible data-source glitch, not necessarily fatal

    # Cross-check source vs calculated delivery % if both are present.
    if (
        row.source_delivery_percentage is not None
        and row.traded_quantity
        and row.deliverable_quantity is not None
        and row.traded_quantity > 0
    ):
        calculated = (row.deliverable_quantity / row.traded_quantity) * 100
        if abs(calculated - row.source_delivery_percentage) > 1.0:  # > 1 percentage point drift
            reasons.append(
                f"source_delivery_percentage ({row.source_delivery_percentage:.2f}) "
                f"vs calculated ({calculated:.2f}) differ materially"
            )
            flagged = True

    return ValidationResult(is_valid=is_valid, flagged=flagged, reasons=reasons)


def calculated_delivery_percentage(deliverable_quantity: int | None, traded_quantity: int | None) -> float | None:
    if not deliverable_quantity or not traded_quantity or traded_quantity <= 0:
        return None
    return round((deliverable_quantity / traded_quantity) * 100, 2)
