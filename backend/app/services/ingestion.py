"""
Orchestration layer: pulls a report via nse_client, validates each
row, upserts into Supabase, and logs the attempt into
ingestion_logs. Also holds the small repository helpers used by the
API layer to read stocks/history back out of Supabase, and the
indicator/scan recompute step.

Kept separate from nse_client.py (pure fetch/parse) and
indicators.py (pure math) so each piece is independently testable.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from supabase import Client

from app.services import indicators as ind
from app.services import scanner_engine as scan
from app.services.data_validator import calculated_delivery_percentage, validate_row
from app.services.nse_client import (
    NseFetchError,
    fetch_report_for_date,
    rows_for_symbol,
)

logger = logging.getLogger("ingestion")


# ---------------------------------------------------------------------------
# Stocks repository helpers
# ---------------------------------------------------------------------------
def get_or_create_stock(db: Client, symbol: str, company_name: str = "") -> dict:
    symbol = symbol.upper().strip()
    existing = db.table("stocks").select("*").eq("symbol", symbol).limit(1).execute()
    if existing.data:
        return existing.data[0]
    inserted = (
        db.table("stocks")
        .insert({"symbol": symbol, "company_name": company_name or symbol, "exchange": "NSE"})
        .execute()
    )
    return inserted.data[0]


def get_scan_config(db: Client) -> dict:
    rows = db.table("scan_config").select("key,value").execute().data
    return {r["key"]: r["value"] for r in rows} if rows else {}


# ---------------------------------------------------------------------------
# Ingestion log helpers
# ---------------------------------------------------------------------------
def _start_log(db: Client, source: str, trade_date: Optional[date]) -> str:
    row = (
        db.table("ingestion_logs")
        .insert(
            {
                "source": source,
                "trade_date": trade_date.isoformat() if trade_date else None,
                "status": "RUNNING",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .execute()
    )
    return row.data[0]["id"]


def _finish_log(db: Client, log_id: str, status: str, processed: int, inserted: int,
                 updated: int, error: Optional[str] = None) -> None:
    db.table("ingestion_logs").update(
        {
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "rows_processed": processed,
            "rows_inserted": inserted,
            "rows_updated": updated,
            "error_message": error,
        }
    ).eq("id", log_id).execute()


# ---------------------------------------------------------------------------
# Core ingestion for one trading date, across the given symbol universe
# ---------------------------------------------------------------------------
def ingest_date(db: Client, target_date: date, symbols: list[str]) -> dict:
    """
    Fetch the report for `target_date` once, then upsert only the rows
    matching `symbols`. Returns a summary dict. Never raises for
    per-row problems — those are logged and skipped so one bad stock
    doesn't take down the whole batch (spec section 29).
    """
    log_id = _start_log(db, "NSE_FULL_BHAVDATA", target_date)
    processed = inserted = updated = 0

    try:
        all_rows, source_used = fetch_report_for_date(target_date)
    except NseFetchError as e:
        logger.error("Ingestion failed for %s: %s", target_date, e)
        _finish_log(db, log_id, "FAILED", 0, 0, 0, error=str(e))
        return {"status": "FAILED", "date": target_date.isoformat(), "error": str(e)}

    for symbol in symbols:
        raw = rows_for_symbol(all_rows, symbol)
        if raw is None:
            continue
        processed += 1

        result = validate_row(raw)
        if not result.is_valid:
            logger.warning("Dropping invalid row %s %s: %s", symbol, target_date, result.reasons)
            continue

        calc_pct = calculated_delivery_percentage(raw.deliverable_quantity, raw.traded_quantity)
        stock = get_or_create_stock(db, symbol)

        payload = {
            "stock_id": stock["id"],
            "trade_date": target_date.isoformat(),
            "open": raw.open,
            "high": raw.high,
            "low": raw.low,
            "close": raw.close,
            "volume": raw.volume,
            "traded_quantity": raw.traded_quantity,
            "deliverable_quantity": raw.deliverable_quantity,
            "source_delivery_percentage": raw.source_delivery_percentage,
            "calculated_delivery_percentage": calc_pct,
            "flagged_for_review": result.flagged,
            "flag_reason": "; ".join(result.reasons) if result.reasons else None,
        }

        existing = (
            db.table("daily_market_data")
            .select("id")
            .eq("stock_id", stock["id"])
            .eq("trade_date", target_date.isoformat())
            .limit(1)
            .execute()
        )
        if existing.data:
            db.table("daily_market_data").update(payload).eq("id", existing.data[0]["id"]).execute()
            updated += 1
        else:
            db.table("daily_market_data").insert(payload).execute()
            inserted += 1

    status = "SUCCESS" if processed > 0 else "PARTIAL"
    _finish_log(db, log_id, status, processed, inserted, updated)
    return {
        "status": status,
        "date": target_date.isoformat(),
        "source": source_used,
        "processed": processed,
        "inserted": inserted,
        "updated": updated,
    }


# ---------------------------------------------------------------------------
# Load history for a stock into a pandas DataFrame ready for indicators.py
# ---------------------------------------------------------------------------
def load_history_df(db: Client, stock_id: str, limit_days: int = 300) -> pd.DataFrame:
    resp = (
        db.table("daily_market_data")
        .select("trade_date,open,high,low,close,volume,calculated_delivery_percentage,source_delivery_percentage")
        .eq("stock_id", stock_id)
        .order("trade_date", desc=True)
        .limit(limit_days)
        .execute()
    )
    if not resp.data:
        return pd.DataFrame()

    df = pd.DataFrame(resp.data)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").set_index("trade_date")

    # Prefer NSE's own delivery % where our calculated one is missing.
    if "calculated_delivery_percentage" in df.columns:
        df["calculated_delivery_percentage"] = df["calculated_delivery_percentage"].fillna(
            df.get("source_delivery_percentage")
        )
    return df


# ---------------------------------------------------------------------------
# Recompute indicators + scan result for one stock and persist both
# ---------------------------------------------------------------------------
def recompute_and_store(db: Client, stock_id: str, calc_date: date) -> Optional[scan.ScanResult]:
    df = load_history_df(db, stock_id)
    if df.empty:
        return None

    config = get_scan_config(db)
    bundle = ind.compute_all(df, config)

    current_delivery = None
    if "calculated_delivery_percentage" in df.columns and not df["calculated_delivery_percentage"].empty:
        last_val = df["calculated_delivery_percentage"].iloc[-1]
        current_delivery = float(last_val) if pd.notna(last_val) else None

    result = scan.evaluate(bundle, config, current_delivery_pct=current_delivery)

    indicator_payload = {
        "stock_id": stock_id,
        "calculation_date": calc_date.isoformat(),
        "ema20_daily": bundle.ema20_daily,
        "ema50_daily": bundle.ema50_daily,
        "ema200_daily": bundle.ema200_daily,
        "ema200_weekly": bundle.ema200_weekly,
        "macd_weekly": bundle.macd.macd_line,
        "macd_signal_weekly": bundle.macd.signal_line,
        "macd_histogram_weekly": bundle.macd.histogram,
        "supertrend_weekly": bundle.supertrend.value,
        "supertrend_direction_weekly": bundle.supertrend.direction,
        "volume_ratio": bundle.volume_ratio,
        "delivery_5d_avg": bundle.delivery_5d_avg,
        "delivery_10d_avg": bundle.delivery_10d_avg,
        "delivery_15d_avg": bundle.delivery_15d_avg,
        "delivery_20d_avg": bundle.delivery_20d_avg,
        "delivery_50d_avg": bundle.delivery_50d_avg,
        "daily_return": bundle.daily_return,
        "return_5d": bundle.return_5d,
        "return_10d": bundle.return_10d,
        "return_15d": bundle.return_15d,
        "weekly_return": bundle.weekly_return,
        "dist_from_ema20": bundle.dist_from_ema20,
        "dist_from_ema50": bundle.dist_from_ema50,
        "dist_from_ema200": bundle.dist_from_ema200,
    }
    _upsert(db, "technical_indicators", indicator_payload, ["stock_id", "calculation_date"])

    checkpoint_map = {c.key: c.passed for c in result.checkpoints}
    scan_payload = {
        "stock_id": stock_id,
        "calculation_date": calc_date.isoformat(),
        **{k: bool(v) for k, v in checkpoint_map.items()},
        "score": result.score,
        "max_score": result.max_score,
        "setup_classification": result.classification,
    }
    _upsert(db, "scan_results", scan_payload, ["stock_id", "calculation_date"])

    return result


def _upsert(db: Client, table: str, payload: dict, conflict_cols: list[str]) -> None:
    existing = db.table(table).select("id")
    for col in conflict_cols:
        existing = existing.eq(col, payload[col])
    existing = existing.limit(1).execute()
    if existing.data:
        db.table(table).update(payload).eq("id", existing.data[0]["id"]).execute()
    else:
        db.table(table).insert(payload).execute()
