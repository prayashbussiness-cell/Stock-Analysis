from app.services import scanner_engine as scan
from app.services.indicators import IndicatorBundle, MacdResult, SupertrendResult


def all_bullish_bundle():
    b = IndicatorBundle()
    b.current_price = 1420.0
    b.ema20_daily = 1380.0
    b.macd = MacdResult(18.42, 14.31, 4.11, True)
    b.supertrend = SupertrendResult(1365.0, "bullish")
    b.weekly_close = 1420.0
    b.prev_weekly_close = 1382.0
    b.ema200_weekly = 1310.0
    b.volume_ratio = 2.0
    b.delivery_15d_avg = 38.4
    return b


def test_all_bullish_scores_seven_of_seven():
    b = all_bullish_bundle()
    result = scan.evaluate(b, config={}, current_delivery_pct=58.2)
    assert result.score == 7
    assert result.classification == "STRONG SETUP"
    assert all(c.passed is True for c in result.checkpoints)


def test_classification_thresholds_are_configurable():
    b = all_bullish_bundle()
    # force a custom, stricter threshold via config
    result = scan.evaluate(b, config={"score_strong_min": 8}, current_delivery_pct=58.2)
    # 7/7 no longer clears a strong_min of 8
    assert result.classification == "MODERATE SETUP"


def test_insufficient_data_classification():
    b = IndicatorBundle()  # everything None
    result = scan.evaluate(b, config={}, current_delivery_pct=None)
    assert result.classification == "INSUFFICIENT DATA"
    assert result.evaluable_count < 4


def test_mixed_pass_fail_scores_correctly():
    b = all_bullish_bundle()
    b.macd = MacdResult(10.0, 12.0, -2.0, False)  # flip MACD to bearish
    b.supertrend = SupertrendResult(1450.0, "bearish")  # flip supertrend
    result = scan.evaluate(b, config={}, current_delivery_pct=58.2)
    assert result.score == 5
    macd_cp = next(c for c in result.checkpoints if c.key == "macd_pass")
    st_cp = next(c for c in result.checkpoints if c.key == "supertrend_pass")
    assert macd_cp.passed is False
    assert st_cp.passed is False


def test_delivery_strength_labels():
    b = all_bullish_bundle()
    result = scan.evaluate(b, config={}, current_delivery_pct=58.2)  # 58.2 / 38.4 = 1.51x -> Strong
    delivery_cp = next(c for c in result.checkpoints if c.key == "delivery_pass")
    assert delivery_cp.detail["strength"] == "Strong"

    result2 = scan.evaluate(b, config={}, current_delivery_pct=40.0)  # 40/38.4 ~ 1.04x -> Normal
    delivery_cp2 = next(c for c in result2.checkpoints if c.key == "delivery_pass")
    assert delivery_cp2.detail["strength"] == "Normal"


def test_never_produces_buy_sell_language():
    b = all_bullish_bundle()
    result = scan.evaluate(b, config={}, current_delivery_pct=58.2)
    forbidden = ["buy", "sell", "guaranteed"]
    assert result.classification.lower() not in forbidden
    assert not any(word in result.classification.lower() for word in forbidden)
