"""
jobs/backfill.py

One-off / manual historical loader. Use this for Phase 2/3 of the
build (getting RELIANCE's first 15 sessions in) and later for Phase 4
(expanding to the full ~250 stock universe).

Usage:
    python -m jobs.backfill --days 60
    python -m jobs.backfill --days 15 --symbols RELIANCE
    python -m jobs.backfill --days 15 --symbols RELIANCE,TCS,INFY,SBIN,HDFCBANK

If --symbols is omitted, backfills every symbol currently marked
active in the stocks table (seed that table first for a fresh DB —
see database/schema.sql / README for the starter list).
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from app.database import get_service_client
from app.services import ingestion
from app.services.nse_client import candidate_trading_dates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill")


def run(days: int, symbols: list[str] | None) -> None:
    db = get_service_client()

    if not symbols:
        active = db.table("stocks").select("symbol").eq("active", True).execute().data
        symbols = [s["symbol"] for s in active]
        if not symbols:
            logger.error(
                "No symbols given and no active stocks in the DB yet. "
                "Pass --symbols RELIANCE,TCS,... for the first run."
            )
            return

    end = date.today()
    start = end - timedelta(days=int(days * 1.6))  # pad for weekends/holidays
    candidate_dates = candidate_trading_dates(start, end)

    logger.info("Backfilling %d symbols across up to %d candidate dates (%s -> %s)",
                len(symbols), len(candidate_dates), start, end)

    success_dates = 0
    for d in candidate_dates:
        summary = ingestion.ingest_date(db, d, symbols)
        if summary["status"] == "SUCCESS":
            success_dates += 1
        logger.info("%s -> %s", d, summary)

    logger.info("Backfill fetch phase complete: %d/%d candidate dates yielded data.",
                success_dates, len(candidate_dates))

    logger.info("Computing indicators + scan results for backfilled symbols...")
    for symbol in symbols:
        stock = db.table("stocks").select("id").eq("symbol", symbol.upper()).limit(1).execute().data
        if not stock:
            continue
        ingestion.recompute_and_store(db, stock[0]["id"], end)
    logger.info("Backfill complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical NSE market data")
    parser.add_argument("--days", type=int, default=15, help="Number of trading sessions to target")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols, e.g. RELIANCE,TCS")
    args = parser.parse_args()

    symbol_list = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    run(days=args.days, symbols=symbol_list)
