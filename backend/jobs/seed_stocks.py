"""
jobs/seed_stocks.py

One-time helper to seed the `stocks` table with a starter universe
so backfill.py / daily_update.py have something to loop over. Per
spec Phase 4, start small (a handful of liquid large-caps) before
expanding to ~250.

Usage:
    python -m jobs.seed_stocks
    python -m jobs.seed_stocks --expand   # adds a broader Nifty50-ish list
"""
from __future__ import annotations

import argparse
import logging

from app.database import get_service_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_stocks")

STARTER_FIVE = [
    ("RELIANCE", "RELIANCE INDUSTRIES LTD"),
    ("TCS", "TATA CONSULTANCY SERVICES LTD"),
    ("INFY", "INFOSYS LTD"),
    ("SBIN", "STATE BANK OF INDIA"),
    ("HDFCBANK", "HDFC BANK LTD"),
]

EXPANDED_LIST = STARTER_FIVE + [
    ("ICICIBANK", "ICICI BANK LTD"),
    ("HINDUNILVR", "HINDUSTAN UNILEVER LTD"),
    ("ITC", "ITC LTD"),
    ("KOTAKBANK", "KOTAK MAHINDRA BANK LTD"),
    ("LT", "LARSEN & TOUBRO LTD"),
    ("AXISBANK", "AXIS BANK LTD"),
    ("BHARTIARTL", "BHARTI AIRTEL LTD"),
    ("BAJFINANCE", "BAJAJ FINANCE LTD"),
    ("ASIANPAINT", "ASIAN PAINTS LTD"),
    ("MARUTI", "MARUTI SUZUKI INDIA LTD"),
    ("SUNPHARMA", "SUN PHARMACEUTICAL INDUSTRIES LTD"),
    ("TITAN", "TITAN COMPANY LTD"),
    ("ULTRACEMCO", "ULTRATECH CEMENT LTD"),
    ("WIPRO", "WIPRO LTD"),
    ("HCLTECH", "HCL TECHNOLOGIES LTD"),
    ("NTPC", "NTPC LTD"),
    ("POWERGRID", "POWER GRID CORPORATION OF INDIA LTD"),
    ("TATAMOTORS", "TATA MOTORS LTD"),
    ("TATASTEEL", "TATA STEEL LTD"),
    ("ADANIENT", "ADANI ENTERPRISES LTD"),
]


def run(expand: bool) -> None:
    db = get_service_client()
    universe = EXPANDED_LIST if expand else STARTER_FIVE

    for symbol, name in universe:
        existing = db.table("stocks").select("id").eq("symbol", symbol).limit(1).execute().data
        if existing:
            logger.info("Already present: %s", symbol)
            continue
        db.table("stocks").insert(
            {"symbol": symbol, "company_name": name, "exchange": "NSE", "active": True}
        ).execute()
        logger.info("Inserted: %s", symbol)

    logger.info("Seed complete: %d symbols targeted.", len(universe))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expand", action="store_true", help="Seed the broader ~20-stock list instead of just 5")
    args = parser.parse_args()
    run(expand=args.expand)
