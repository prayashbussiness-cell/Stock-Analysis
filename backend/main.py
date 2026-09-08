"""
main.py

FastAPI backend for the AI Stock Research Terminal.

Responsibilities:
- Accept a JSON request with one or more comma-separated stock tickers.
- Run a web-search-grounded Gemini call covering mutual fund holdings +
  recent news/catalysts (real data, not memory).
- Call Gemini again (via generate_research_report) to produce the full
  structured JSON research report, informed by that grounded context.
- Enrich the result server-side with a deterministic price-projection path
  and a weighted scorecard (never trusting the model's arithmetic).
- Generate a professional PDF version of the report.
- Optionally log the search to Supabase (best-effort, never blocks the
  response) if SUPABASE_URL / SUPABASE_KEY are configured.
- Return the structured report JSON + a link to the PDF to the frontend.
"""

import os
import re
import json
import math
import time
import uuid
import random
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google import genai
from google.genai import types as genai_types

try:
    # Works when the app is run from inside backend/ (uvicorn main:app),
    # i.e. when Render's Root Directory is set to "backend".
    from prompt import (
        SYSTEM_PROMPT,
        SECTION_ORDER,
        SECTION_WEIGHTS,
        TOTAL_WEIGHT,
        PRICE_PROJECTION_DISCLAIMER,
        GROUNDED_SEARCH_SYSTEM_PROMPT,
        build_grounded_search_prompt,
        build_user_prompt,
    )
    from pdf_generator import generate_pdf
    from market_data import fetch_live_quotes
except ImportError:
    # Works when the app is run from the repo root (uvicorn backend.main:app).
    from backend.prompt import (
        SYSTEM_PROMPT,
        SECTION_ORDER,
        SECTION_WEIGHTS,
        TOTAL_WEIGHT,
        PRICE_PROJECTION_DISCLAIMER,
        GROUNDED_SEARCH_SYSTEM_PROMPT,
        build_grounded_search_prompt,
        build_user_prompt,
    )
    from backend.pdf_generator import generate_pdf
    from backend.market_data import fetch_live_quotes

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stock-terminal")

BASE_DIR = os.path.dirname(__file__)
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# --- Gemini config ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Model used for the web-search-grounded step. Defaults to the same model,
# but can be overridden if your account has grounding enabled on a
# different model.
GEMINI_SEARCH_MODEL = os.environ.get("GEMINI_SEARCH_MODEL", GEMINI_MODEL)

# --- Optional Supabase logging (off unless both vars are set) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_TABLE = os.environ.get("SUPABASE_TABLE", "terminal_searches")

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")
origins = (
    ["*"] if ALLOWED_ORIGINS.strip() == "*" else [o.strip() for o in ALLOWED_ORIGINS.split(",")]
)

MAX_TICKERS = 5

app = FastAPI(title="AI Stock Research Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(BASE_DIR, "static")


class NoCacheStaticFiles(StaticFiles):
    """Adds Cache-Control: no-cache so browsers always revalidate instead of
    silently reusing a stale index.html/script/style after a deploy."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")

gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        logger.exception("Failed to initialize Gemini client — check GEMINI_API_KEY.")
else:
    logger.warning("GEMINI_API_KEY not set — report generation will fail until it's configured.")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client

        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        logger.exception("Failed to initialize Supabase client — search logging disabled.")
else:
    logger.info("SUPABASE_URL / SUPABASE_KEY not set — search history logging disabled.")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    ticker: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json(raw_text: str) -> dict:
    """Gemini is asked for strict JSON, but strip code fences defensively
    in case the model wraps the response in ```json ... ``` anyway."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def _clean_tickers(raw: str) -> list[str]:
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    # de-duplicate while preserving order
    seen = set()
    cleaned = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            cleaned.append(p)
    return cleaned[:MAX_TICKERS]


async def fetch_grounded_context(tickers: list[str]) -> str:
    """
    Step 1 of report generation: a SEPARATE Gemini call with Google Search
    grounding enabled, covering BOTH mutual fund holdings (Section 7) and
    news/catalysts (Section 8) in one combined pass — so those sections are
    built from real search results instead of the model's memory. Strict
    JSON output (response_mime_type) is not combined with tool use here —
    that combination isn't reliably supported by the API — so this call
    returns plain grounded text, handed to the main structured-report call
    as context.

    Best-effort: if grounding fails for any reason (model/account doesn't
    support it, quota, transient error), returns a clear fallback string so
    report generation can still proceed — the main prompt instructs the
    model to disclose the limitation rather than invent data.
    """
    if gemini_client is None:
        return "Live search unavailable: Gemini is not configured."

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_SEARCH_MODEL,
            contents=[build_grounded_search_prompt(", ".join(tickers))],
            config=genai_types.GenerateContentConfig(
                system_instruction=GROUNDED_SEARCH_SYSTEM_PROMPT,
                max_output_tokens=3000,
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return "Live search returned no results for these tickers."
        return text
    except Exception:
        logger.exception("Grounded search (Google Search tool) failed")
        return (
            "Live search was unavailable for this request (search tool error). "
            "No confirmed mutual fund holding data or recent news items could be retrieved."
        )


def generate_price_path(current_price: float, target_price: float, months: int = 24, seed=None) -> list:
    """
    Deterministic-ish (seeded) monthly price path from current_price at
    month 0 to target_price at month `months`, with realistic up/down
    wiggle in between (a damped sine wave + random jitter) rather than a
    smooth straight line. Endpoints are pinned exactly to the given prices.
    Returns a list of `months + 1` rounded prices.
    """
    try:
        current_price = float(current_price)
        target_price = float(target_price)
    except (TypeError, ValueError):
        current_price, target_price = 100.0, 110.0

    rng = random.Random(seed)
    n = months + 1
    amplitude = abs(target_price - current_price) * 0.18 + current_price * 0.035
    phase = rng.uniform(0, 2 * math.pi)
    freq = rng.uniform(1.6, 3.2)

    path = []
    for i in range(n):
        t = i / months
        trend = current_price + (target_price - current_price) * t
        damp = math.sin(math.pi * t)  # 0 at both endpoints, 1 mid-path
        wave = math.sin(freq * math.pi * t + phase) * amplitude * damp
        jitter = rng.uniform(-1, 1) * amplitude * 0.35 * damp
        price = max(trend + wave + jitter, 0.01)
        path.append(round(price, 2))

    path[0] = round(current_price, 2)
    path[-1] = round(target_price, 2)
    return path


def _verdict_map_from_sections(entry: dict) -> dict:
    """
    Builds an {id: verdict} map straight from entry["sections"] — the
    single source of truth for verdicts. We deliberately do NOT read
    verdicts from final_summary.checklist: that array requires the model to
    re-type each section's title as free text a second time, and it doesn't
    always match the canonical title exactly (e.g. "Candlestick Analysis"
    vs "Candlestick Pattern Analysis"), which silently breaks any
    title-string matching downstream. Matching by "id" (a small fixed enum
    we specify in the prompt) is far more reliable than matching by title.

    Falls back to positional matching (section N -> SECTION_ORDER[N]) for
    any entry whose "id" doesn't match a known one, since the prompt also
    requires sections to be returned in a fixed order — this covers the
    rarer case where the model gets the id slug wrong too.
    """
    sections = entry.get("sections") or []
    known_ids = {sid for sid, _ in SECTION_ORDER}

    by_id = {}
    unmatched_positional = []
    for i, s in enumerate(sections):
        sid = (s.get("id") or "").strip().lower()
        verdict = (s.get("verdict") or "").strip().lower()
        if verdict not in ("positive", "negative"):
            verdict = "negative"
        if sid in known_ids:
            by_id[sid] = verdict
        else:
            unmatched_positional.append((i, verdict))

    # Positional fallback for anything not matched by id.
    for i, verdict in unmatched_positional:
        if i < len(SECTION_ORDER):
            fallback_id = SECTION_ORDER[i][0]
            by_id.setdefault(fallback_id, verdict)

    return by_id


def compute_scorecard(entry: dict) -> dict:
    """
    Computes the weighted scorecard total in Python from the model's stated
    per-section verdicts (matched by id, see _verdict_map_from_sections),
    using the fixed SECTION_WEIGHTS business rule — never trusts the model
    to do this arithmetic itself. Returns section rows in the canonical
    SECTION_ORDER (not whatever order the model returned).
    """
    verdict_by_id = _verdict_map_from_sections(entry)

    rows = []
    total_score = 0
    for sid, title in SECTION_ORDER:
        weight = SECTION_WEIGHTS.get(title, 0)
        verdict = verdict_by_id.get(sid, "negative")
        if verdict == "positive":
            total_score += weight
        rows.append({"title": title, "weight": weight, "verdict": verdict})

    return {"sections": rows, "total_score": total_score, "max_score": TOTAL_WEIGHT}


def rebuild_final_checklist(entry: dict) -> list:
    """
    Rebuilds final_summary.checklist server-side from the same canonical
    per-section verdicts used for the scorecard (see
    _verdict_map_from_sections), instead of trusting whatever free-text
    checklist the model wrote separately. This guarantees the Final Summary
    card, the PDF checklist table, and the Scorecard always agree with each
    other and with the numbered section verdicts — they're now all reading
    from one source of truth instead of three independently-authored copies.
    """
    verdict_by_id = _verdict_map_from_sections(entry)
    return [
        {"section": title, "verdict": verdict_by_id.get(sid, "negative")}
        for sid, title in SECTION_ORDER
    ]


async def generate_research_report(tickers: list[str]) -> dict:
    """Calls Gemini and returns the parsed structured report dict, enriched
    with real live quotes, a server-computed price_projection, and a
    scorecard per ticker."""
    if gemini_client is None:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured on the server. Set it in your "
            ".env file (or Render environment variables) before starting the backend."
        )

    # Step 1: real mutual-fund + news data via Google Search grounding, AND
    # real live price/change/market-cap via Yahoo Finance — run concurrently
    # since they're independent of each other.
    grounded_context, live_quotes = await asyncio.gather(
        fetch_grounded_context(tickers),
        fetch_live_quotes(tickers),
    )

    if live_quotes:
        live_quotes_lines = "\n".join(
            f"- {t}: {q['current_price']} {q['currency']} "
            f"({'+' if q['change_percent'] >= 0 else ''}{q['change_percent']}%), "
            f"market cap {q['market_cap']}"
            for t, q in live_quotes.items()
        )
    else:
        live_quotes_lines = "No live quotes could be retrieved for these tickers."

    # Step 2: main structured report call, informed by the grounded context
    # AND told to use the real live prices verbatim (still overridden
    # server-side afterwards regardless — see below — so this is belt AND
    # suspenders, not the only safeguard).
    user_prompt = build_user_prompt(
        tickers=", ".join(tickers),
        grounded_context=grounded_context,
        live_quotes_context=live_quotes_lines,
    )

    # The 11-section report (with quarterly/valuation/pivot/news/mutual-fund
    # tables per section) is far longer than a flat-schema report, so scale
    # the token budget with the number of tickers requested rather than
    # using one fixed small cap.
    token_budget = min(32000, 8500 * max(1, len(tickers)))

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[user_prompt],
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            # Note: temperature/top_p/top_k are deprecated on Gemini 3.x
            # models (silently ignored by the API, and Google has signaled
            # they may hard-error on future releases) — intentionally
            # omitted here rather than passed and ignored.
            max_output_tokens=token_budget,
            response_mime_type="application/json",
        ),
    )

    raw_text = (response.text or "").strip()
    if not raw_text:
        raise RuntimeError("Gemini returned an empty response.")

    try:
        data = _extract_json(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Gemini JSON output: %s", raw_text[:500])
        raise RuntimeError("The model returned malformed data. Please try again.") from exc

    if "tickers" not in data or not isinstance(data["tickers"], list) or not data["tickers"]:
        raise RuntimeError("The model response was missing report data. Please try again.")

    # Step 3: server-side enrichment — REAL price override, price projection
    # path, and scorecard. None of these are trusted from the model's own
    # arithmetic/estimates.
    for entry in data["tickers"]:
        ticker_key = (entry.get("ticker") or "").strip().upper()
        live = live_quotes.get(ticker_key)
        if live:
            # Never trust the model's guessed price when we have a real one.
            entry["current_price"] = live["current_price"]
            entry["change_percent"] = live["change_percent"]
            entry["currency"] = live["currency"]
            if live.get("market_cap") and live["market_cap"] != "N/A":
                entry["market_cap"] = live["market_cap"]
            entry["price_is_live"] = True
            entry["price_as_of"] = datetime.now(timezone.utc).isoformat()
        else:
            entry["price_is_live"] = False
            entry["price_as_of"] = None

        current_price = entry.get("current_price")
        target_price = entry.get("best_case_target_price")
        seed = f"{entry.get('ticker', '')}-{datetime.now().strftime('%Y%m%d%H')}"
        prices = generate_price_path(current_price, target_price, months=24, seed=seed)
        entry["price_projection"] = {
            "months": list(range(0, 25)),
            "prices": prices,
            "reasoning": entry.get("price_projection_reasoning", []),
            "disclaimer": PRICE_PROJECTION_DISCLAIMER,
        }
        entry["scorecard"] = compute_scorecard(entry)
        # Overwrite the model's own checklist with one derived from the same
        # canonical per-section verdicts as the scorecard, so the two can
        # never disagree (see rebuild_final_checklist docstring).
        entry.setdefault("final_summary", {})["checklist"] = rebuild_final_checklist(entry)

    return data


def log_search_to_supabase(tickers: list[str], latency_ms: int) -> None:
    """Best-effort search history log. Never blocks or fails the request."""
    if supabase is None:
        return
    try:
        supabase.table(SUPABASE_TABLE).insert(
            {
                "tickers": ", ".join(tickers),
                "latency_ms": latency_ms,
            }
        ).execute()
    except Exception:
        logger.exception("Failed to log search to Supabase")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/analyze")
async def analyze(payload: AnalyzeRequest):
    """
    Main endpoint: accepts a ticker string (comma-separated for multiple
    symbols), generates the AI research report via Gemini, builds a PDF,
    and returns both to the frontend.
    """
    tickers = _clean_tickers(payload.ticker or "")

    if not tickers:
        raise HTTPException(status_code=422, detail="At least one valid ticker is required.")

    start = time.monotonic()

    try:
        report_data = await generate_research_report(tickers)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        logger.exception("Gemini generation failed")
        raise HTTPException(
            status_code=502,
            detail="Something went wrong while generating the report. Please try again.",
        )

    latency_ms = int((time.monotonic() - start) * 1000)

    try:
        pdf_filename = generate_pdf(tickers=tickers, report_data=report_data)
        pdf_url = f"/reports/{pdf_filename}"
    except Exception:
        logger.exception("PDF generation failed")
        pdf_url = None

    log_search_to_supabase(tickers, latency_ms)

    return JSONResponse(
        {
            "success": True,
            "tickers": tickers,
            "report": report_data,
            "pdf": pdf_url,
            "latency_ms": latency_ms,
            "session_id": uuid.uuid4().hex[:8].upper(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# Frontend (static site)
# ---------------------------------------------------------------------------
# Serves backend/static/index.html at "/" so frontend + backend + Gemini all
# run from this single FastAPI app/Render service. Mounted LAST so it never
# shadows the /health, /analyze, or /reports routes defined above it.
app.mount("/", NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
