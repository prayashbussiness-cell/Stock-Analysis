"""
data_ingestion/nse_client.py
=============================

Responsible for talking to NSE's publicly available end-of-day
report system and turning whatever it returns into clean rows.

Design notes (see project spec section 4):
- We do NOT hard-code a single brittle endpoint. `KNOWN_REPORT_SOURCES`
  lists more than one permitted report format NSE has published
  historically. `fetch_report_for_date` tries them in order and
  records which one worked (or that none did) in ingestion_logs.
- We do NOT attempt to bypass any access control (no header spoofing
  beyond a normal desktop User-Agent, no captcha solving, no proxy
  rotation). If NSE blocks automated access, we log the failure and
  surface `data_source_status` via the admin endpoint instead of
  trying to work around it.
- The "full bhavcopy" (`sec_bhavdata_full_DDMMYYYY.csv`) report is
  preferred because it carries OHLCV *and* delivery quantity /
  % deliverable in one file, which keeps ingestion simple and avoids
  reconciling two separate reports pulled at different times.

This module only fetches + parses + normalizes + validates.
Persisting to Supabase is a separate step (see services/ingestion.py)
so this module stays easy to unit test without a live database.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger("nse_client")


# ---------------------------------------------------------------------------
# Data shape returned by this module. Storage layer maps this 1:1 onto
# the daily_market_data table.
# ---------------------------------------------------------------------------
@dataclass
class RawMarketRow:
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    traded_quantity: Optional[int]
    deliverable_quantity: Optional[int]
    source_delivery_percentage: Optional[float]


class NseFetchError(Exception):
    """Raised when no permitted report could be retrieved for a date."""


class NseParseError(Exception):
    """Raised when a report was retrieved but could not be parsed."""


# ---------------------------------------------------------------------------
# Known permitted report shapes, most preferred first.
# Each entry describes how to build the URL and how to parse the body.
# Keeping this as data (not nested if/else) means adding/removing a
# source later is a one-line change, per the "don't hard-code
# assumptions" requirement.
# ---------------------------------------------------------------------------
def _full_bhavcopy_url(d: date) -> str:
    # e.g. https://archives.nseindia.com/products/content/sec_bhavdata_full_13082026.csv
    return (
        "https://archives.nseindia.com/products/content/"
        f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
    )


def _legacy_bhavcopy_zip_url(d: date) -> str:
    # Older-style per-month-folder EQ bhavcopy zip, kept as a fallback
    # shape in case the primary path is retired/changed by NSE.
    month = d.strftime("%b").upper()
    return (
        "https://archives.nseindia.com/content/historical/EQUITIES/"
        f"{d.year}/{month}/cm{d.strftime('%d%b%Y').upper()}bhav.csv.zip"
    )


KNOWN_REPORT_SOURCES = [
    {"name": "NSE_FULL_BHAVDATA_CSV", "url_builder": _full_bhavcopy_url, "format": "csv"},
    {"name": "NSE_LEGACY_BHAVCOPY_ZIP", "url_builder": _legacy_bhavcopy_zip_url, "format": "zip"},
]


def _detect_format(content: bytes, declared_format: str) -> str:
    """Sniff the actual bytes rather than trusting the URL extension,
    since a permitted-but-changed endpoint could start returning a
    different container (e.g. csv -> zip) without warning."""
    if content[:2] == b"PK":
        return "zip"
    try:
        content[:2000].decode("utf-8", errors="strict")
        return "csv"
    except UnicodeDecodeError:
        return "dat"


def _normalize_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map whatever header names NSE ships this month onto our
    canonical field names. NSE has changed casing/spacing of these
    headers before, so match loosely."""
    mapping = {}
    for raw in fieldnames:
        key = raw.strip().upper().replace(" ", "").replace("_", "")
        if key in {"SYMBOL"}:
            mapping[raw] = "symbol"
        elif key in {"SERIES"}:
            mapping[raw] = "series"
        elif key in {"DATE1", "DATE", "TIMESTAMP"}:
            mapping[raw] = "trade_date"
        elif key in {"OPENPRICE", "OPEN"}:
            mapping[raw] = "open"
        elif key in {"HIGHPRICE", "HIGH"}:
            mapping[raw] = "high"
        elif key in {"LOWPRICE", "LOW"}:
            mapping[raw] = "low"
        elif key in {"CLOSEPRICE", "CLOSE"}:
            mapping[raw] = "close"
        elif key in {"TTLTRDQNTY", "TOTTRDQTY", "VOLUME"}:
            mapping[raw] = "volume"
        elif key in {"DELIVQTY", "DELIVERABLEQTY", "DELIVQTY."}:
            mapping[raw] = "deliverable_quantity"
        elif key in {"DELIVPER", "DELIVERABLEPERCENTAGE", "%DLYQTTOTRADEDQTY"}:
            mapping[raw] = "source_delivery_percentage"
    return mapping


def _parse_csv_bytes(content: bytes, expected_date: date) -> list[RawMarketRow]:
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise NseParseError("CSV has no header row")

    colmap = _normalize_columns(reader.fieldnames)
    required = {"symbol", "open", "high", "low", "close", "volume"}
    if not required.issubset(set(colmap.values())):
        missing = required - set(colmap.values())
        raise NseParseError(f"CSV missing required columns: {missing}")

    rows: list[RawMarketRow] = []
    for raw_row in reader:
        norm = {colmap[k]: v.strip() if isinstance(v, str) else v
                for k, v in raw_row.items() if k in colmap}
        # EQ series only for v1 (ignore BE/SM/others to keep the
        # dataset to standard equity delivery-eligible symbols)
        series = raw_row.get("SERIES", raw_row.get(" SERIES", "")).strip()
        if series and series not in {"EQ", "BE"}:
            continue
        try:
            traded_qty = int(float(norm["volume"])) if norm.get("volume") else None
            deliv_qty = (
                int(float(norm["deliverable_quantity"]))
                if norm.get("deliverable_quantity") not in (None, "", "-")
                else None
            )
            src_pct = (
                float(norm["source_delivery_percentage"])
                if norm.get("source_delivery_percentage") not in (None, "", "-")
                else None
            )
            rows.append(
                RawMarketRow(
                    symbol=norm["symbol"].upper(),
                    trade_date=expected_date,
                    open=float(norm["open"]),
                    high=float(norm["high"]),
                    low=float(norm["low"]),
                    close=float(norm["close"]),
                    volume=traded_qty or 0,
                    traded_quantity=traded_qty,
                    deliverable_quantity=deliv_qty,
                    source_delivery_percentage=src_pct,
                )
            )
        except (ValueError, KeyError) as e:
            logger.warning("Skipping malformed row for %s: %s", raw_row.get("SYMBOL"), e)
            continue
    return rows


def _parse_zip_bytes(content: bytes, expected_date: date) -> list[RawMarketRow]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise NseParseError("Zip archive contained no CSV file")
        inner = zf.read(csv_names[0])
    # legacy bhavcopy zip doesn't include delivery data - that comes
    # from a separate "security-wise delivery position" report, which
    # a future source entry can add without touching this function.
    return _parse_csv_bytes(inner, expected_date)


PARSERS = {"csv": _parse_csv_bytes, "zip": _parse_zip_bytes}


def fetch_report_for_date(target_date: date) -> tuple[list[RawMarketRow], str]:
    """
    Try each known permitted report source in order for `target_date`.
    Returns (rows, source_name_used).
    Raises NseFetchError if nothing worked.
    """
    settings = get_settings()
    headers = {"User-Agent": settings.nse_user_agent}
    last_error: Optional[Exception] = None

    for source in KNOWN_REPORT_SOURCES:
        url = source["url_builder"](target_date)
        try:
            resp = httpx.get(url, headers=headers, timeout=settings.nse_request_timeout_seconds)
            if resp.status_code == 404:
                # No trading session / no report published for this date -
                # not an error worth retrying other sources for.
                last_error = NseFetchError(f"No report at {url} (404 - likely non-trading day)")
                continue
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Fetch failed for source %s: %s", source["name"], e)
            last_error = e
            continue

        actual_format = _detect_format(resp.content, source["format"])
        parser = PARSERS.get(actual_format)
        if parser is None:
            last_error = NseParseError(f"Unsupported format '{actual_format}' from {source['name']}")
            continue

        try:
            rows = parser(resp.content, target_date)
        except NseParseError as e:
            logger.warning("Parse failed for source %s: %s", source["name"], e)
            last_error = e
            continue

        if not rows:
            last_error = NseParseError(f"{source['name']} returned zero usable rows")
            continue

        logger.info("Fetched %d rows for %s from %s", len(rows), target_date, source["name"])
        return rows, source["name"]

    raise NseFetchError(
        f"All permitted sources failed for {target_date}: {last_error}"
    )


def rows_for_symbol(rows: list[RawMarketRow], symbol: str) -> Optional[RawMarketRow]:
    symbol = symbol.upper()
    for r in rows:
        if r.symbol == symbol:
            return r
    return None


def candidate_trading_dates(start: date, end: date) -> list[date]:
    """
    Calendar days between start and end, excluding weekends. This is a
    *candidate* list only — actual trading-day confirmation comes from
    whether a report was successfully retrieved for that date (or from
    the trading_calendar table once populated). We deliberately do not
    hard-code NSE holidays here since that list changes every year;
    a 404 for a weekday is treated as "not a trading day" upstream.
    """
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        d += timedelta(days=1)
    return days
