"""
jobs/daily_update.py

Meant to be run on a schedule (Render Cron Job, or any external
scheduler hitting `python -m jobs.daily_update`) once per trading
day after NSE publishes end-of-day reports (typically after ~18:30
IST, allow a safety margin — e.g. schedule for 20:00 IST).

Steps (per spec section 27):
  1. Determine the latest candidate trading date (today, or the most
     recent weekday if run on a weekend/holiday by mistake).
  2. For each active stock, check whether daily_market_data already
     has a row for that date.
  3. If missing for any symbol, fetch+ingest that date once (the
     report covers all symbols in one file) and upsert.
  4. Recompute indicators + scan_results for every stock that has
     data through that date.
  5. Everything is logged to ingestion_logs regardless of outcome.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from app.database import get_service_client
from app.services import ingestion

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("daily_update")


def latest_candidate_trading_date(today: date | None = None) -> date:
    d = today or date.today()
    # roll back over weekends; holidays are handled by nse_client
    # treating a 404 as "not a trading day" rather than a hard failure
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def run() -> None:
    db = get_service_client()
    target_date = latest_candidate_trading_date()

    active_stocks = db.table("stocks").select("id,symbol").eq("active", True).execute().data
    symbols = [s["symbol"] for s in active_stocks]

    if not symbols:
        logger.warning("No active stocks in the stocks table yet — nothing to update. "
                        "Seed the stocks table first (see database/schema.sql).")
        return

    # Only bother fetching if at least one symbol is missing this date.
    existing_ids = {
        r["stock_id"]
        for r in db.table("daily_market_data")
        .select("stock_id")
        .eq("trade_date", target_date.isoformat())
        .execute()
        .data
    }
    stock_id_by_symbol = {s["symbol"]: s["id"] for s in active_stocks}
    missing_symbols = [sym for sym in symbols if stock_id_by_symbol[sym] not in existing_ids]

    if not missing_symbols:
        logger.info("All %d active stocks already have data for %s. Nothing to do.", len(symbols), target_date)
    else:
        logger.info("Ingesting %d missing symbols for %s", len(missing_symbols), target_date)
        summary = ingestion.ingest_date(db, target_date, missing_symbols)
        logger.info("Ingestion summary: %s", summary)

    logger.info("Recomputing indicators and scan results...")
    recomputed = 0
    for s in active_stocks:
        result = ingestion.recompute_and_store(db, s["id"], target_date)
        if result is not None:
            recomputed += 1
    logger.info("Recomputed indicators/scan for %d/%d stocks.", recomputed, len(active_stocks))


if __name__ == "__main__":
    run()
