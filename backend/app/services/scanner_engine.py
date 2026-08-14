"""
Turns an IndicatorBundle into the 7 checkpoint pass/fail flags, a
score, and a setup classification. All thresholds are read from the
`config` dict (sourced from the scan_config table) — nothing here is
a hard-coded magic number, per the spec's configurability requirement.

Output is descriptive, never prescriptive: this module produces
"6 / 7 CONDITIONS MATCHED" style results, never a buy/sell verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.services.indicators import IndicatorBundle

CHECKPOINT_KEYS = [
    "macd_pass",
    "ema200_pass",
    "supertrend_pass",
    "price_trend_pass",
    "delivery_pass",
    "volume_pass",
    "ema20_pass",
]


@dataclass
class Checkpoint:
    key: str
    label: str
    passed: Optional[bool]  # None => insufficient data to evaluate
    detail: dict


@dataclass
class ScanResult:
    checkpoints: list[Checkpoint]
    score: int
    max_score: int
    evaluable_count: int  # checkpoints that weren't skipped for lack of data
    classification: str


def evaluate(
    bundle: IndicatorBundle,
    config: dict,
    current_delivery_pct: Optional[float] = None,
) -> ScanResult:
    checkpoints: list[Checkpoint] = []

    # 1. Weekly MACD bullish
    macd_bullish = bundle.macd.bullish
    checkpoints.append(
        Checkpoint(
            "macd_pass",
            "Weekly MACD",
            macd_bullish,
            {
                "macd_line": bundle.macd.macd_line,
                "signal": bundle.macd.signal_line,
                "histogram": bundle.macd.histogram,
            },
        )
    )

    # 2. Weekly close above EMA 200
    ema200_pass = None
    if bundle.weekly_close is not None and bundle.ema200_weekly is not None:
        ema200_pass = bundle.weekly_close > bundle.ema200_weekly
    checkpoints.append(
        Checkpoint(
            "ema200_pass",
            "Weekly EMA 200",
            ema200_pass,
            {"weekly_close": bundle.weekly_close, "ema200_weekly": bundle.ema200_weekly},
        )
    )

    # 3. Weekly Supertrend bullish
    st_pass = None
    if bundle.supertrend.direction is not None:
        st_pass = bundle.supertrend.direction == "bullish"
    checkpoints.append(
        Checkpoint(
            "supertrend_pass",
            "Weekly Supertrend",
            st_pass,
            {"direction": bundle.supertrend.direction, "value": bundle.supertrend.value},
        )
    )

    # 4. Price trend positive: current weekly close > previous weekly close
    trend_pass = None
    if bundle.weekly_close is not None and bundle.prev_weekly_close is not None:
        trend_pass = bundle.weekly_close > bundle.prev_weekly_close
    checkpoints.append(
        Checkpoint(
            "price_trend_pass",
            "Price Trend",
            trend_pass,
            {
                "current_weekly_close": bundle.weekly_close,
                "previous_weekly_close": bundle.prev_weekly_close,
                "weekly_return_pct": bundle.weekly_return,
            },
        )
    )

    # 5. Delivery above 15D average
    delivery_pass = None
    diff_points = None
    if current_delivery_pct is not None and bundle.delivery_15d_avg is not None:
        strong_mult = float(config.get("delivery_strong_multiplier", 1.30))
        moderate_mult = float(config.get("delivery_moderate_multiplier", 1.15))
        diff_points = round(current_delivery_pct - bundle.delivery_15d_avg, 2)
        delivery_pass = current_delivery_pct > bundle.delivery_15d_avg
        delivery_strength = (
            "Strong"
            if bundle.delivery_15d_avg > 0 and current_delivery_pct >= strong_mult * bundle.delivery_15d_avg
            else "Moderate"
            if bundle.delivery_15d_avg > 0 and current_delivery_pct >= moderate_mult * bundle.delivery_15d_avg
            else "Normal"
        )
    else:
        delivery_strength = None
    checkpoints.append(
        Checkpoint(
            "delivery_pass",
            "Delivery",
            delivery_pass,
            {
                "current_delivery_pct": current_delivery_pct,
                "avg_15d_pct": bundle.delivery_15d_avg,
                "difference_points": diff_points,
                "strength": delivery_strength,
            },
        )
    )

    # 6. Volume above average
    volume_pass = None
    volume_strength = None
    if bundle.volume_ratio is not None:
        strong_ratio = float(config.get("volume_strong_ratio", 1.5))
        moderate_ratio = float(config.get("volume_moderate_ratio", 1.2))
        volume_pass = bundle.volume_ratio >= moderate_ratio
        volume_strength = (
            "Strong" if bundle.volume_ratio >= strong_ratio
            else "Moderate" if bundle.volume_ratio >= moderate_ratio
            else "Normal"
        )
    checkpoints.append(
        Checkpoint(
            "volume_pass",
            "Volume",
            volume_pass,
            {"volume_ratio": bundle.volume_ratio, "strength": volume_strength},
        )
    )

    # 7. Price above 20 EMA (daily)
    ema20_pass = None
    if bundle.current_price is not None and bundle.ema20_daily is not None:
        ema20_pass = bundle.current_price > bundle.ema20_daily
    checkpoints.append(
        Checkpoint(
            "ema20_pass",
            "20 EMA (daily)",
            ema20_pass,
            {"current_price": bundle.current_price, "ema20_daily": bundle.ema20_daily},
        )
    )

    evaluable = [c for c in checkpoints if c.passed is not None]
    score = sum(1 for c in evaluable if c.passed)
    max_score = 7

    classification = classify(score, len(evaluable), config)

    return ScanResult(
        checkpoints=checkpoints,
        score=score,
        max_score=max_score,
        evaluable_count=len(evaluable),
        classification=classification,
    )


def classify(score: int, evaluable_count: int, config: dict) -> str:
    """
    Classification is based on the 0-7 score. If fewer than 4
    checkpoints could even be evaluated (insufficient history), we
    say so explicitly rather than force a classification on a
    partial picture.
    """
    if evaluable_count < 4:
        return "INSUFFICIENT DATA"

    strong_min = int(config.get("score_strong_min", 6))
    moderate_min = int(config.get("score_moderate_min", 4))
    weak_min = int(config.get("score_weak_min", 2))

    if score >= strong_min:
        return "STRONG SETUP"
    if score >= moderate_min:
        return "MODERATE SETUP"
    if score >= weak_min:
        return "WEAK SETUP"
    return "NO SETUP"
