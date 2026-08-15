# Stock Research Scanner

A lightweight NSE technical + delivery-based swing-trade screening dashboard.
**Educational/research tool — not investment advice.** See the disclaimer at
the bottom of every page.

## Stack

- **Frontend**: static HTML/CSS/vanilla JS → deploy on Netlify
- **Backend**: FastAPI (Python) → deploy on Render
- **Database**: Supabase PostgreSQL
- **Data source**: NSE's public end-of-day bhavcopy/delivery reports (no
  Chartink scraping — all indicators are calculated independently in Python)

## Project structure

```
stock-dashboard/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app + CORS
│   │   ├── config.py          env-var settings
│   │   ├── database.py        Supabase client
│   │   ├── api/                stocks.py, scanner.py, market.py
│   │   ├── services/            nse_client.py, indicators.py,
│   │   │                        scanner_engine.py, data_validator.py,
│   │   │                        ingestion.py
│   ├── jobs/
│   │   ├── seed_stocks.py     one-time: seed the stocks table
│   │   ├── backfill.py        manual historical loader
│   │   └── daily_update.py    scheduled daily job
│   ├── tests/                 unit tests (indicators, scanner, validator)
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html   stock.html   scanner.html
│   ├── css/style.css
│   └── js/app.js
├── database/schema.sql
└── .gitignore
```

## 1. Set up Supabase

1. Create a project at supabase.com.
2. Open the SQL editor and run `database/schema.sql`.
3. Grab your Project URL, anon key, and service-role key from
   Project Settings → API.

## 2. Backend — local setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your Supabase keys
```

Seed a starter stock universe (5 large-caps to start, per the phased build
plan — expand later):

```bash
python -m jobs.seed_stocks
```

Backfill history for those stocks (start with just RELIANCE and verify
manually against NSE's own published data before expanding, per Phase 2/3):

```bash
python -m jobs.backfill --days 15 --symbols RELIANCE
```

Once verified, backfill the rest:

```bash
python -m jobs.backfill --days 60
```

Run the API:

```bash
uvicorn app.main:app --reload --port 8000
```

Visit `http://localhost:8000/docs` for interactive Swagger docs.

## 3. Backend tests

```bash
cd backend
pip install pytest
python -m pytest tests/ -v
```

These are pure unit tests against synthetic price data — no network or
database required — covering the indicator math, the scoring engine, and
row validation.

## 4. Daily scheduled update

Run manually:

```bash
python -m jobs.daily_update
```

In production (Render), set this up as a **Cron Job** (separate from the
web service) scheduled for after NSE publishes EOD reports, e.g. 20:00 IST.

## 5. Frontend — local preview

The frontend is fully static. Any static server works, e.g.:

```bash
cd frontend
python -m http.server 8080
```

Before deploying, edit the `window.__API_BASE__` line at the bottom of each
HTML file to point at your deployed Render backend URL, e.g.:

```html
<script>window.__API_BASE__ = "https://your-backend.onrender.com/api";</script>
```

## 6. Deploy

**Backend → Render**
- New Web Service, root directory `backend/`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Add the env vars from `.env.example` in the Render dashboard
- Add a separate Cron Job service for `python -m jobs.daily_update`
- `runtime.txt` pins Python to 3.11.9, but **Render's Python buildpack often
  ignores `runtime.txt` and builds with its current default (3.14 at time
  of writing) unless you also set it explicitly.** Do this in the Render
  dashboard for the backend service:
  - Go to your Web Service → **Environment** tab
  - Add an environment variable: `PYTHON_VERSION` = `3.11.9`
  - Save, then trigger **Manual Deploy → Clear build cache & deploy**
    (a stale cached build is a common reason the old Python version keeps
    reappearing even after this change)
  - This matters because Python 3.14 has no pre-built pandas/numpy wheels
    yet, forcing pip to compile them from source, which fails with a
    Cython/GCC `[[maybe_unused]]` error unrelated to your code. 3.11
    has ready-made wheels for everything in `requirements.txt`, so the
    build just downloads binaries instead of compiling anything.

**Frontend → Netlify**
- New site from Git, base directory `frontend/`
- No build command needed (static files)
- Publish directory: `frontend`
- Update `window.__API_BASE__` in each HTML file to your Render URL before
  deploying (or template it via Netlify environment/build step later)

## Notes on scope / what's intentionally left for you to run live

This project was built without live network access, so the NSE fetch and
Supabase writes are implemented against NSE's known public report formats
and the standard Supabase client, but haven't been exercised end-to-end
against the real services. Before trusting the numbers:

1. Run `jobs.backfill --days 15 --symbols RELIANCE` and manually compare a
   few rows against NSE's own published bhavcopy for the same dates.
2. Check `/api/admin/data-status` after your first ingestion run.
3. If NSE has changed its report URL/format since this was written,
   `app/services/nse_client.py` is the single place to update — it's
   deliberately built as a list of `KNOWN_REPORT_SOURCES` rather than one
   hard-coded endpoint, so adding a new source is a small, isolated change.

## Disclaimer

This dashboard is a quantitative stock-screening and research tool. The
displayed indicators and scores are generated from predefined technical and
market-data rules. They are not personalized investment advice, a guarantee
of returns, or a recommendation to buy or sell any security. Users should
independently evaluate investments and consider consulting a qualified
financial professional where appropriate.
