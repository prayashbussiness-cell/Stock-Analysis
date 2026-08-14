"""
Unit tests for the pure-math indicator functions. These use
synthetic OHLCV data (never real market data) so they run
deterministically with no network/database access — see spec
section 37 Phase 5 ("write unit tests").

Run with:
    cd backend && python -m pytest tests/ -v
"""
import numpy as np
import pandas as pd
import pytest

from app.services import indicators as ind


def make_daily_df(n_days=260, start_price=100.0, daily_drift=0.15, seed=42):
    """Deterministic synthetic uptrending series with a bit of noise."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n_days)  # business days only
    closes = [start_price]
    for _ in range(1, n_days):
        change = daily_drift + rng.normal(0, 1.0)
        closes.append(max(1.0, closes[-1] + change))
    closes = np.array(closes)
    highs = closes + rng.uniform(0.5, 2.0, n_days)
    lows = closes - rng.uniform(0.5, 2.0, n_days)
    opens = closes - rng.uniform(-1.0, 1.0, n_days)
    volumes = rng.integers(1_000_000, 5_000_000, n_days)
    delivered = (volumes * rng.uniform(0.3, 0.6, n_days)).astype(int)
    delivery_pct = (delivered / volumes) * 100

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "calculated_delivery_percentage": delivery_pct,
        },
        index=dates,
    )
    df.index.name = "trade_date"
    return df


def test_ema_insufficient_data_returns_none():
    short_series = pd.Series([100, 101, 102, 103, 104])
    assert ind.latest_ema(short_series, 20) is None


def test_ema_with_enough_data_returns_value():
    df = make_daily_df(n_days=60)
    val = ind.latest_ema(df["close"], 20)
    assert val is not None
    assert val > 0


def test_weekly_resample_shape():
    df = make_daily_df(n_days=30)
    weekly = ind.resample_to_weekly(df)
    assert not weekly.empty
    # weekly high should never be below weekly low
    assert (weekly["high"] >= weekly["low"]).all()
    # roughly 30 business days / 5 ~= 6 weekly bars
    assert 4 <= len(weekly) <= 8


def test_macd_insufficient_data():
    df = make_daily_df(n_days=20)
    weekly = ind.resample_to_weekly(df)
    result = ind.macd(weekly["close"])
    assert result.bullish is None
    assert result.macd_line is None


def make_accelerating_df(n_days=400, seed=42):
    """A trend that recently steepened — unlike a constant-slope line,
    this produces genuine MACD/signal separation (a purely linear
    trend converges to a flat, near-zero MACD histogram, which is
    correct MACD behavior, not something a bullish-detection test
    should rely on)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n_days)
    closes = [100.0]
    for i in range(1, n_days):
        # drift ramps up over the back half of the series
        drift = 0.1 if i < n_days * 0.6 else 0.9
        closes.append(max(1.0, closes[-1] + drift + rng.normal(0, 0.8)))
    closes = np.array(closes)
    df = pd.DataFrame(
        {
            "open": closes,
            "high": closes + 1.5,
            "low": closes - 1.5,
            "close": closes,
            "volume": rng.integers(1_000_000, 5_000_000, n_days),
        },
        index=dates,
    )
    df.index.name = "trade_date"
    return df


def test_macd_with_uptrend_is_bullish():
    df = make_accelerating_df(n_days=400)
    weekly = ind.resample_to_weekly(df)
    result = ind.macd(weekly["close"])
    assert result.bullish is True
    assert result.macd_line > result.signal_line


def test_supertrend_insufficient_data():
    df = make_daily_df(n_days=5)
    result = ind.supertrend(df, atr_period=10)
    assert result.direction is None


def test_supertrend_uptrend_is_bullish():
    df = make_daily_df(n_days=200, daily_drift=0.6)
    weekly = ind.resample_to_weekly(df)
    result = ind.supertrend(weekly, atr_period=10, multiplier=3.0)
    assert result.direction == "bullish"
    assert result.value is not None
    assert result.value < weekly["close"].iloc[-1]  # trend line should sit below price in an uptrend


def test_rolling_delivery_avg_insufficient():
    series = pd.Series([40.0, 42.0, 38.0])
    assert ind.rolling_delivery_avg(series, 15) is None


def test_rolling_delivery_avg_computes_mean():
    series = pd.Series([10.0] * 20)
    avg = ind.rolling_delivery_avg(series, 15)
    assert avg == 10.0


def test_volume_ratio():
    # 20 days of volume=100, then today's volume=250 -> ratio should be 2.5
    series = pd.Series([100.0] * 20 + [250.0])
    ratio = ind.volume_ratio(series, window=20)
    assert ratio == pytest.approx(2.5, abs=0.01)


def test_pct_return():
    series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 110.0])
    r = ind.pct_return(series, 5)
    assert r == pytest.approx(10.0, abs=0.01)


def test_compute_all_insufficient_history_no_crash():
    df = make_daily_df(n_days=10)
    bundle = ind.compute_all(df, config={})
    # should not raise, and should leave long-window fields as None
    assert bundle.ema200_daily is None
    assert bundle.current_price is not None


def test_compute_all_full_history_populates_fields():
    df = make_daily_df(n_days=300, daily_drift=0.3)
    bundle = ind.compute_all(df, config={})
    assert bundle.ema20_daily is not None
    assert bundle.ema200_daily is not None
    assert bundle.delivery_15d_avg is not None
    assert bundle.volume_ratio is not None
