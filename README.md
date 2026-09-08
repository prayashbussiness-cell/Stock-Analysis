# DEEP//TERMINAL — AI Stock Research Terminal

A single-service FastAPI app that pairs a Bloomberg-style terminal frontend
with a Gemini-powered research backend. Enter an analyst ID and one or more
tickers, hit **DEEP SEARCH**, and get a comprehensive 11-section investment
research report — company overview, competitive landscape, financials,
technicals, candlestick patterns, ownership activity, real web-searched
mutual fund holdings, real web-searched news & catalysts, valuation,
investment strategy, and risks, each with a PASS/FAIL verdict — plus a
final checklist + recommendation, a 24-month best-case price projection
chart, and a weighted scorecard donut. PDF export only (no analyst ID field
— tickers are all the app asks for).

Architecture mirrors the earlier PalmAI project: one FastAPI service serves
both the API (`/analyze`, `/health`, `/reports`) and the static frontend
(`backend/static/index.html`), so it deploys as a single Render web service.

## Project structure

```
stock-terminal/
├── backend/
│   ├── main.py            FastAPI app: routes, news-search + report Gemini
│   │                       calls, price-path generator, scorecard math
│   ├── prompt.py           Prompts + fixed section order/weights (strict JSON schema)
│   ├── pdf_generator.py    Builds the downloadable PDF (reportlab, incl. charts)
│   ├── static/
│   │   └── index.html      The whole frontend (HTML/CSS/JS, terminal UI)
│   ├── reports/             Generated PDFs land here at runtime
│   ├── requirements.txt
│   ├── Procfile
│   ├── .python-version
│   └── .env.example
├── requirements.txt         (mirror, used if Render root dir is unset)
├── render.yaml
└── README.md
```

## How it works

1. The frontend posts `{ ticker }` to `POST /analyze`
   (`ticker` can be comma-separated, e.g. `NVDA, AAPL, ITC`, up to 5 symbols).
2. **Step 1 — real data, not memory.** `main.py` makes a first Gemini call
   with Google Search grounding enabled, covering BOTH mutual fund/AMC
   holding data and recent, dated news/catalysts for the requested
   ticker(s) in one combined pass. This is a plain-text call (grounding
   tools aren't combined with strict-JSON mode).
3. **Step 2 — structured report.** That grounded text is injected into
   the main prompt (`prompt.py`), which forces a **strict JSON** response —
   11 sections in a fixed order, each with a PASS/FAIL verdict — so the
   frontend can render verdict pills and a checklist reliably instead of
   parsing freeform AI text. Each section's *body* is still markdown
   (tables, bullets), rendered by a small markdown-to-HTML converter in the
   frontend.
4. **Step 3 — server-side enrichment (not the model's job):**
   - **Real live price/change%/market cap** are fetched from Yahoo Finance
     (`market_data.py`, via the `yfinance` package — free, no API key) for
     every ticker, concurrently with the grounded search step. These real
     figures are injected into the prompt AND forcibly override whatever
     the model guessed in its JSON response afterward — the model's price
     is never trusted when a real quote was retrieved. Each ticker is
     tagged `price_is_live: true/false` so the frontend/PDF can show a
     "LIVE" vs "ESTIMATED" badge honestly instead of silently guessing.
   - The 25-point, 24-month price-projection path is generated in Python
     (a seeded random walk with drift, pinned to the *real* current price
     when available, and the model's best-case target) — LLMs are
     unreliable at producing a series that's simultaneously "realistically
     wiggly" and "ends exactly at X".
   - The scorecard's weighted total, and the Final Summary checklist, are
     both computed in Python directly from each section's own `verdict`
     field (matched by `id`, not by re-parsing free text) — never trusted
     as model arithmetic, and never vulnerable to the model rewording a
     section title slightly differently in two places (a real bug this
     fixed: Gemini would sometimes write "Candlestick Analysis" in one
     spot and "Candlestick Pattern Analysis" in another, which broke
     naive title-string matching).
5. `pdf_generator.py` turns the enriched JSON into a full PDF report,
   including the price-projection line chart and the scorecard donut chart
   (drawn with reportlab's graphics primitives, no extra dependencies).
6. The frontend shows a simple "deep research in progress" status line +
   progress bar while all of this runs, then renders the full report,
   charts included, plus PDF export.
7. Optional: if `SUPABASE_URL`/`SUPABASE_KEY` are set, each search
   (tickers, latency) is logged to a Supabase table for history — this is
   best-effort and the app works fine without it.

**Important:** only the live-quote step and the news-search step touch
real data. Everything else — the qualitative analysis, technical levels,
quarterly financial estimates, ratios — is still Gemini's own trained
knowledge, producing a plausible, internally consistent report around the
real price, not verified real-time fundamentals. Treat those parts
accordingly.

**A known limitation worth knowing about:** Yahoo Finance's data is
unofficial/undocumented and can occasionally rate-limit or block requests
from cloud provider IP ranges (including hosting platforms like Render).
When that happens, `market_data.py` fails gracefully and the report falls
back to the model's estimate — clearly marked "ESTIMATED" rather than
pretending it's live. If live quotes stop working entirely, try
`pip install -U yfinance` first (Yahoo's internal endpoints change
occasionally and new yfinance releases track those changes).

## Local setup

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and set GEMINI_API_KEY (get one at https://aistudio.google.com/apikey)

uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 — the frontend and API are served from the same
place, so there's nothing else to configure.

## Deploying to Render

1. Push this folder to a GitHub repo.
2. In Render, "New +" → "Blueprint", point it at the repo — `render.yaml` at
   the root configures everything (root dir `backend`, build/start commands).
3. When prompted, set the `GEMINI_API_KEY` secret. Everything else has a
   working default (Supabase logging stays off until you add those two
   secrets too).
4. Deploy. Your terminal is live at the Render URL Render gives you.

If you'd rather set it up manually instead of using the blueprint: create a
Python web service, set **Root Directory** to `backend`, build command
`pip install -r requirements.txt`, start command
`uvicorn main:app --host 0.0.0.0 --port $PORT`, and add the env vars from
`.env.example`.

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | — | From https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | No | `gemini-3.5-flash-lite` | Any Gemini model that supports `response_mime_type: application/json` |
| `GEMINI_SEARCH_MODEL` | No | same as `GEMINI_MODEL` | Model used for the Google-Search-grounded step (mutual fund holdings + news); override if your account has grounding enabled on a different model |
| `SUPABASE_URL` / `SUPABASE_KEY` | No | — | Leave blank to disable search-history logging |
| `SUPABASE_TABLE` | No | `terminal_searches` | Table name for logging, if enabled |
| `ALLOWED_ORIGINS` | No | `*` | Comma-separated list to restrict CORS in production |

### Optional: Supabase logging table

```sql
create table terminal_searches (
  id uuid primary key default gen_random_uuid(),
  tickers text not null,
  latency_ms integer,
  created_at timestamptz not null default now()
);
```

## Notes / things you may want to tweak

- **News search reliability**: if Google Search grounding fails (account/
  model doesn't support it, quota, transient error), `fetch_news_context`
  falls back to a clear "search unavailable" string rather than failing the
  whole request — the report prompt instructs Gemini to disclose that
  limitation in the News & Catalysts section instead of inventing news.
- **Rate limiting / abuse protection**: there's none out of the box. If this
  goes anywhere public, put basic rate limiting in front of `/analyze` so a
  single visitor can't burn through your Gemini quota — note each request
  now makes **two** Gemini calls (news search + main report).
- **Ticker validation**: the backend doesn't check tickers against a real
  exchange list — Gemini will do its best with whatever symbol you give it.
- **Model choice**: `gemini-3.5-flash-lite` is fast and cheap for this use
  case; swap `GEMINI_MODEL` for a stronger model if you want deeper
  analysis at the cost of latency (the 11-section report + grounded search step is
  significantly longer than a simple query, so expect noticeably higher
  latency than a typical chat request).

