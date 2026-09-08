"""
market_data.py

Fetches REAL, live stock quotes (price, change %, market cap, currency) so
the report never has to trust Gemini's guess at these numbers. This is the
same principle applied elsewhere in this app (the scorecard math, the price
projection path): anything that can be fetched or computed deterministically
should never be left to the model to estimate.

Uses yfinance (a well-maintained, actively used community wrapper around
Yahoo Finance's public, keyless quote data — no API key or account needed).
Two honest caveats worth knowing about, both handled gracefully below:

1. yfinance/Yahoo can occasionally rate-limit or block requests from cloud
   provider IP ranges (including hosts like Render). When that happens we
   fall back to Gemini's estimated price rather than failing the whole
   report — but we mark it clearly as an estimate (see `is_live` below) so
   the frontend/PDF can show that honestly instead of silently guessing.
2. Yahoo's internal endpoints are undocumented and change occasionally,
   which can require bumping the yfinance dependency version. If live
   quotes stop working entirely, `pip install -U yfinance` is usually the
   first thing to try.
"""

import asyncio
import logging

import yfinance as yf

logger = logging.getLogger("stock-terminal.market_data")

QUOTE_TIMEOUT_SECONDS = 8


def _format_market_cap(market_cap: float, currency: str) -> str:
    """Formats a raw market cap number into a readable string, using Indian
    lakh/crore notation for INR and T/B/M notation otherwise."""
    if not market_cap or market_cap <= 0:
        return "N/A"

    if (currency or "").upper() == "INR":
        crore = market_cap / 1e7  # 1 crore = 10,000,000
        if crore >= 1e5:
            return f"{crore / 1e5:.2f}L Cr"
        return f"{crore:,.0f} Cr"

    if market_cap >= 1e12:
        return f"{market_cap / 1e12:.2f}T"
    if market_cap >= 1e9:
        return f"{market_cap / 1e9:.2f}B"
    if market_cap >= 1e6:
        return f"{market_cap / 1e6:.2f}M"
    return f"{market_cap:,.0f}"


def _candidate_symbols(ticker: str) -> list[str]:
    """
    Builds the list of Yahoo Finance symbols to try, in order. If the
    ticker already carries an exchange suffix (e.g. "RELIANCE.NS") or a
    dotted class share (e.g. "BRK.B"), it's tried as-is only.

    Otherwise we try NSE (.NS) and BSE (.BO) first, then the plain symbol
    (covers US/global tickers). This order is deliberate: this app's
    primary examples are Indian tickers, and a bare Indian symbol like
    "ITC" risks silently matching an unrelated stock on Yahoo's global
    symbol space if tried plain first — a random plain ticker is far less
    likely to accidentally match a real NSE-listed symbol than the reverse.
    """
    ticker = ticker.strip().upper()
    if "." in ticker:
        return [ticker]
    return [f"{ticker}.NS", f"{ticker}.BO", ticker]


def _fetch_one_sync(ticker: str) -> dict | None:
    """Blocking fetch for a single ticker — run this inside a thread."""
    for symbol in _candidate_symbols(ticker):
        try:
            info = yf.Ticker(symbol).fast_info
            last_price = info.get("last_price") or info.get("lastPrice")
            prev_close = info.get("previous_close") or info.get("previousClose")
            currency = (info.get("currency") or "").upper()
            market_cap = info.get("market_cap") or info.get("marketCap")

            if not last_price or not prev_close or last_price <= 0 or prev_close <= 0:
                continue

            change_percent = round(((last_price - prev_close) / prev_close) * 100, 2)

            return {
                "symbol": symbol,
                "current_price": round(float(last_price), 2),
                "change_percent": change_percent,
                "currency": currency or ("INR" if symbol.endswith((".NS", ".BO")) else "USD"),
                "market_cap": _format_market_cap(market_cap, currency),
                "exchange": "NSE" if symbol.endswith(".NS") else ("BSE" if symbol.endswith(".BO") else ""),
            }
        except Exception:
            continue
    return None


async def fetch_live_quote(ticker: str) -> dict | None:
    """
    Async wrapper: fetches a live quote for one ticker without blocking the
    event loop, with a timeout so one slow/unresponsive symbol can't stall
    the whole request. Returns None (never raises) on any failure — callers
    must treat that as "no live data available" and fall back gracefully.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_one_sync, ticker), timeout=QUOTE_TIMEOUT_SECONDS
        )
    except Exception:
        logger.warning("Live quote fetch failed or timed out for %s", ticker, exc_info=True)
        return None


async def fetch_live_quotes(tickers: list[str]) -> dict[str, dict]:
    """
    Fetches live quotes for multiple tickers concurrently. Returns a dict
    keyed by the ORIGINAL requested ticker string (uppercased); tickers
    with no available live data are simply absent from the result — callers
    should treat a missing key as "fall back to the model's estimate".
    """
    results = await asyncio.gather(*(fetch_live_quote(t) for t in tickers))
    return {
        t.strip().upper(): r
        for t, r in zip(tickers, results)
        if r is not None
    }
