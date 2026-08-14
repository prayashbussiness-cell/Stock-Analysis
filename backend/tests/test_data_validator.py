from datetime import date

import pytest

from app.services.data_validator import calculated_delivery_percentage, validate_row
from app.services.nse_client import RawMarketRow


def make_row(**overrides):
    base = dict(
        symbol="RELIANCE",
        trade_date=date(2026, 8, 13),
        open=1400.0,
        high=1430.0,
        low=1395.0,
        close=1420.0,
        volume=5_200_000,
        traded_quantity=5_200_000,
        deliverable_quantity=2_860_000,
        source_delivery_percentage=55.0,
    )
    base.update(overrides)
    return RawMarketRow(**base)


def test_valid_row_passes():
    row = make_row()
    result = validate_row(row)
    assert result.is_valid is True
    assert result.flagged is False


def test_negative_close_is_invalid():
    row = make_row(close=-5.0)
    result = validate_row(row)
    assert result.is_valid is False
    assert "close <= 0" in result.reasons


def test_high_below_low_is_invalid():
    row = make_row(high=100.0, low=200.0)
    result = validate_row(row)
    assert result.is_valid is False


def test_deliverable_exceeding_traded_is_flagged_not_dropped():
    row = make_row(deliverable_quantity=6_000_000, traded_quantity=5_200_000)
    result = validate_row(row)
    assert result.is_valid is True  # still usable, not silently overwritten
    assert result.flagged is True


def test_source_vs_calculated_delivery_mismatch_flags():
    # calculated = 2.86M / 5.2M * 100 = 55.0%, source says 40% -> big drift
    row = make_row(source_delivery_percentage=40.0)
    result = validate_row(row)
    assert result.flagged is True


def test_calculated_delivery_percentage_helper():
    assert calculated_delivery_percentage(2_860_000, 5_200_000) == pytest.approx(55.0, abs=0.01)
    assert calculated_delivery_percentage(None, 5_200_000) is None
    assert calculated_delivery_percentage(1000, 0) is None
