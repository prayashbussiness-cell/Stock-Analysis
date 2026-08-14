"""
All indicator math lives here. Nothing in this module talks to
Supabase or NSE — it takes a pandas DataFrame of clean daily OHLCV +
delivery rows and returns computed values. This keeps it trivially
unit-testable.

Expected input DataFrame columns (one row per trading day, sorted
ascending by date):
    trade_date, open, high, low, close, volume,
    traded_quantity, deliverable_quantity, calculated_delivery_percentage

Every function returns `None` (or "INSUFFICIENT_DATA" sentinel where
noted) rather than fabricating a value when there isn't enough
history — per the "do not fake data" rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# EMA / SMA
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> Optional[pd.Series]:
    """Standard EMA. Returns None if there isn't at least `period` bars."""
    if len(series.dropna()) < period:
        return None
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> Optional[pd.Series]:
    if len(series.dropna()) < period:
        return None
    return series.rolling(window=period, min_periods=period).mean()


def latest_ema(series: pd.Series, period: int) -> Optional[float]:
    result = ema(series, period)
    if result is None or result.dropna().empty:
        return None
    return float(result.iloc[-1])


def distance_from_ema_pct(price: float, ema_value: Optional[float]) -> Optional[float]:
    if ema_value in (None, 0):
        return None
    return round(((price - ema_value) / ema_value) * 100, 3)


# ---------------------------------------------------------------------------
# Weekly resampling (NOT last-5-days — a proper Mon-Fri OHLC resample)
# ---------------------------------------------------------------------------
def resample_to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    daily_df must be indexed by a DatetimeIndex (trade_date) and have
    columns open/high/low/close/volume. Weeks are anchored to Friday
    (W-FRI) so a Mon-Fri trading week rolls up into one bar even if
    Friday was a holiday (label = last available day in that week).
    """
    df = daily_df.copy()
    df.index = pd.to_datetime(df.index)
    weekly = df.resample("W-FRI").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    weekly = weekly.dropna(subset=["open", "high", "low", "close"])
    return weekly


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------
@dataclass
class MacdResult:
    macd_line: Optional[float]
    signal_line: Optional[float]
    histogram: Optional[float]
    bullish: Optional[bool]  # None => insufficient data


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> MacdResult:
    if len(series.dropna()) < slow + signal:
        return MacdResult(None, None, None, None)

    ema_fast = series.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = series.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line

    if signal_line.dropna().empty:
        return MacdResult(None, None, None, None)

    macd_val = float(macd_line.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    hist_val = float(histogram.iloc[-1])
    bullish = macd_val > signal_val

    return MacdResult(round(macd_val, 4), round(signal_val, 4), round(hist_val, 4), bullish)


# ---------------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------------
@dataclass
class SupertrendResult:
    value: Optional[float]
    direction: Optional[str]  # 'bullish' | 'bearish' | None


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def supertrend(df: pd.DataFrame, atr_period: int = 10, multiplier: float = 3.0) -> SupertrendResult:
    """
    df must have columns high/low/close, sorted ascending by date.
    Standard Supertrend algorithm.
    """
    if len(df) < atr_period + 1:
        return SupertrendResult(None, None)

    atr = _atr(df, atr_period)
    hl2 = (df["high"] + df["low"]) / 2
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    upper_band = upper_basic.copy()
    lower_band = lower_basic.copy()
    close = df["close"]

    for i in range(1, len(df)):
        if pd.isna(upper_band.iloc[i - 1]) or pd.isna(atr.iloc[i]):
            continue
        upper_band.iloc[i] = (
            upper_basic.iloc[i]
            if (upper_basic.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1])
            else upper_band.iloc[i - 1]
        )
        lower_band.iloc[i] = (
            lower_basic.iloc[i]
            if (lower_basic.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1])
            else lower_band.iloc[i - 1]
        )

    trend = pd.Series(index=df.index, dtype="object")
    st_value = pd.Series(index=df.index, dtype="float64")

    first_valid = atr.first_valid_index()
    if first_valid is None:
        return SupertrendResult(None, None)
    start_pos = df.index.get_loc(first_valid)

    trend.iloc[start_pos] = "bullish"
    st_value.iloc[start_pos] = lower_band.iloc[start_pos]

    for i in range(start_pos + 1, len(df)):
        prev_trend = trend.iloc[i - 1]
        if prev_trend == "bullish":
            if close.iloc[i] < lower_band.iloc[i]:
                trend.iloc[i] = "bearish"
                st_value.iloc[i] = upper_band.iloc[i]
            else:
                trend.iloc[i] = "bullish"
                st_value.iloc[i] = lower_band.iloc[i]
        else:
            if close.iloc[i] > upper_band.iloc[i]:
                trend.iloc[i] = "bullish"
                st_value.iloc[i] = lower_band.iloc[i]
            else:
                trend.iloc[i] = "bearish"
                st_value.iloc[i] = upper_band.iloc[i]

    last_valid = st_value.last_valid_index()
    if last_valid is None:
        return SupertrendResult(None, None)

    return SupertrendResult(
        value=round(float(st_value.loc[last_valid]), 4),
        direction=trend.loc[last_valid],
    )


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------
def pct_return(series: pd.Series, periods: int) -> Optional[float]:
    if len(series.dropna()) < periods + 1:
        return None
    start = series.iloc[-(periods + 1)]
    end = series.iloc[-1]
    if start == 0:
        return None
    return round(((end - start) / start) * 100, 3)


# ---------------------------------------------------------------------------
# Delivery averages
# ---------------------------------------------------------------------------
def rolling_delivery_avg(delivery_pct_series: pd.Series, window: int) -> Optional[float]:
    clean = delivery_pct_series.dropna()
    if len(clean) < window:
        return None
    return round(float(clean.iloc[-window:].mean()), 2)


# ---------------------------------------------------------------------------
# Volume ratio
# ---------------------------------------------------------------------------
def volume_ratio(volume_series: pd.Series, window: int = 20) -> Optional[float]:
    clean = volume_series.dropna()
    if len(clean) < window + 1:
        return None
    avg = clean.iloc[-(window + 1):-1].mean()  # average excluding today
    if avg == 0:
        return None
    current = clean.iloc[-1]
    return round(float(current / avg), 3)


@dataclass
class IndicatorBundle:
    """Everything the scanner + API need, computed once per stock."""
    ema20_daily: Optional[float] = None
    ema50_daily: Optional[float] = None
    ema200_daily: Optional[float] = None
    ema200_weekly: Optional[float] = None

    macd: MacdResult = field(default_factory=lambda: MacdResult(None, None, None, None))
    supertrend: SupertrendResult = field(default_factory=lambda: SupertrendResult(None, None))

    volume_ratio: Optional[float] = None

    delivery_5d_avg: Optional[float] = None
    delivery_10d_avg: Optional[float] = None
    delivery_15d_avg: Optional[float] = None
    delivery_20d_avg: Optional[float] = None
    delivery_50d_avg: Optional[float] = None

    daily_return: Optional[float] = None
    return_5d: Optional[float] = None
    return_10d: Optional[float] = None
    return_15d: Optional[float] = None
    weekly_return: Optional[float] = None

    dist_from_ema20: Optional[float] = None
    dist_from_ema50: Optional[float] = None
    dist_from_ema200: Optional[float] = None

    current_price: Optional[float] = None
    weekly_close: Optional[float] = None
    prev_weekly_close: Optional[float] = None


def compute_all(daily_df: pd.DataFrame, config: dict) -> IndicatorBundle:
    """
    daily_df: DataFrame indexed by trade_date (ascending), columns:
      open, high, low, close, volume, calculated_delivery_percentage
      (falls back to source_delivery_percentage where calculated is null)
    config: dict of thresholds pulled from scan_config table, e.g.
      {'macd_fast_period': 12, 'macd_slow_period': 26, 'macd_signal_period': 9,
       'supertrend_atr_period': 10, 'supertrend_multiplier': 3}
    """
    bundle = IndicatorBundle()
    if daily_df.empty:
        return bundle

    close = daily_df["close"]
    bundle.current_price = float(close.iloc[-1])

    bundle.ema20_daily = latest_ema(close, 20)
    bundle.ema50_daily = latest_ema(close, 50)
    bundle.ema200_daily = latest_ema(close, 200)

    bundle.dist_from_ema20 = distance_from_ema_pct(bundle.current_price, bundle.ema20_daily)
    bundle.dist_from_ema50 = distance_from_ema_pct(bundle.current_price, bundle.ema50_daily)
    bundle.dist_from_ema200 = distance_from_ema_pct(bundle.current_price, bundle.ema200_daily)

    bundle.daily_return = pct_return(close, 1)
    bundle.return_5d = pct_return(close, 5)
    bundle.return_10d = pct_return(close, 10)
    bundle.return_15d = pct_return(close, 15)

    delivery_col = "calculated_delivery_percentage"
    if delivery_col in daily_df.columns:
        dpct = daily_df[delivery_col]
        bundle.delivery_5d_avg = rolling_delivery_avg(dpct, 5)
        bundle.delivery_10d_avg = rolling_delivery_avg(dpct, 10)
        bundle.delivery_15d_avg = rolling_delivery_avg(dpct, 15)
        bundle.delivery_20d_avg = rolling_delivery_avg(dpct, 20)
        bundle.delivery_50d_avg = rolling_delivery_avg(dpct, 50)

    bundle.volume_ratio = volume_ratio(daily_df["volume"], 20)

    weekly = resample_to_weekly(daily_df)
    if not weekly.empty:
        bundle.ema200_weekly = latest_ema(weekly["close"], 200)
        bundle.macd = macd(
            weekly["close"],
            fast=int(config.get("macd_fast_period", 12)),
            slow=int(config.get("macd_slow_period", 26)),
            signal=int(config.get("macd_signal_period", 9)),
        )
        bundle.supertrend = supertrend(
            weekly,
            atr_period=int(config.get("supertrend_atr_period", 10)),
            multiplier=float(config.get("supertrend_multiplier", 3)),
        )
        bundle.weekly_close = float(weekly["close"].iloc[-1])
        if len(weekly) >= 2:
            bundle.prev_weekly_close = float(weekly["close"].iloc[-2])
            if bundle.prev_weekly_close:
                bundle.weekly_return = round(
                    ((bundle.weekly_close - bundle.prev_weekly_close) / bundle.prev_weekly_close) * 100, 3
                )

    return bundle
