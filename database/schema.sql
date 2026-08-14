-- ============================================================
-- Stock Research & Swing-Trade Screening Dashboard
-- Supabase PostgreSQL schema
-- ============================================================
-- Run this in the Supabase SQL editor (or via `supabase db push`).
-- Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
-- ============================================================

create extension if not exists "uuid-ossp";

-- ------------------------------------------------------------
-- stocks : master list of tradable symbols
-- ------------------------------------------------------------
create table if not exists stocks (
    id              uuid primary key default uuid_generate_v4(),
    symbol          text not null unique,        -- e.g. RELIANCE
    company_name    text not null,                -- e.g. RELIANCE INDUSTRIES LTD
    isin            text,
    exchange        text not null default 'NSE',
    sector          text,
    active          boolean not null default true,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists idx_stocks_symbol on stocks (symbol);
create index if not exists idx_stocks_active on stocks (active);

-- ------------------------------------------------------------
-- daily_market_data : one row per stock per trading day
-- ------------------------------------------------------------
create table if not exists daily_market_data (
    id                                uuid primary key default uuid_generate_v4(),
    stock_id                          uuid not null references stocks(id) on delete cascade,
    trade_date                        date not null,

    open                              numeric(14,4) not null,
    high                              numeric(14,4) not null,
    low                               numeric(14,4) not null,
    close                             numeric(14,4) not null,
    volume                            bigint not null default 0,

    traded_quantity                   bigint,
    deliverable_quantity              bigint,
    source_delivery_percentage        numeric(6,2),   -- as provided by NSE, if available
    calculated_delivery_percentage    numeric(6,2),   -- deliverable_quantity / traded_quantity * 100

    flagged_for_review                boolean not null default false,
    flag_reason                       text,

    created_at                        timestamptz not null default now(),

    constraint uq_stock_date unique (stock_id, trade_date)
);

create index if not exists idx_dmd_stock_date on daily_market_data (stock_id, trade_date desc);
create index if not exists idx_dmd_trade_date on daily_market_data (trade_date);

-- ------------------------------------------------------------
-- technical_indicators : one row per stock per calculation date
-- (calculation_date is normally the latest trade_date the
--  indicator set is based on)
-- ------------------------------------------------------------
create table if not exists technical_indicators (
    id                          uuid primary key default uuid_generate_v4(),
    stock_id                    uuid not null references stocks(id) on delete cascade,
    calculation_date            date not null,

    ema20_daily                 numeric(14,4),
    ema50_daily                 numeric(14,4),
    ema200_daily                numeric(14,4),

    ema200_weekly               numeric(14,4),

    macd_weekly                 numeric(14,4),
    macd_signal_weekly          numeric(14,4),
    macd_histogram_weekly       numeric(14,4),

    supertrend_weekly           numeric(14,4),
    supertrend_direction_weekly text,          -- 'bullish' | 'bearish' | null

    volume_ratio                numeric(8,3),  -- current volume / 20D avg volume

    delivery_5d_avg             numeric(6,2),
    delivery_10d_avg            numeric(6,2),
    delivery_15d_avg            numeric(6,2),
    delivery_20d_avg            numeric(6,2),
    delivery_50d_avg            numeric(6,2),

    daily_return                numeric(8,3),
    return_5d                   numeric(8,3),
    return_10d                  numeric(8,3),
    return_15d                  numeric(8,3),
    weekly_return                numeric(8,3),

    dist_from_ema20              numeric(8,3),
    dist_from_ema50              numeric(8,3),
    dist_from_ema200             numeric(8,3),

    created_at                  timestamptz not null default now(),

    constraint uq_stock_calc_date unique (stock_id, calculation_date)
);

create index if not exists idx_ti_stock_date on technical_indicators (stock_id, calculation_date desc);

-- ------------------------------------------------------------
-- scan_results : pass/fail per checkpoint + composite score
-- ------------------------------------------------------------
create table if not exists scan_results (
    id                    uuid primary key default uuid_generate_v4(),
    stock_id              uuid not null references stocks(id) on delete cascade,
    calculation_date      date not null,

    macd_pass             boolean not null default false,
    ema200_pass           boolean not null default false,
    supertrend_pass       boolean not null default false,
    price_trend_pass      boolean not null default false,
    delivery_pass         boolean not null default false,
    volume_pass           boolean not null default false,
    ema20_pass            boolean not null default false,

    score                 integer not null default 0,
    max_score             integer not null default 7,

    setup_classification  text not null default 'NO SETUP',
    -- 'STRONG SETUP' | 'MODERATE SETUP' | 'WEAK SETUP' | 'NO SETUP'

    created_at            timestamptz not null default now(),

    constraint uq_stock_scan_date unique (stock_id, calculation_date)
);

create index if not exists idx_sr_stock_date on scan_results (stock_id, calculation_date desc);
create index if not exists idx_sr_classification on scan_results (setup_classification);

-- ------------------------------------------------------------
-- ingestion_logs : auditable record of every ingestion attempt
-- ------------------------------------------------------------
create table if not exists ingestion_logs (
    id              uuid primary key default uuid_generate_v4(),
    source          text not null,              -- e.g. 'NSE_BHAVCOPY', 'NSE_DELIVERY'
    report_type     text,
    trade_date      date,
    started_at      timestamptz not null default now(),
    completed_at    timestamptz,
    status          text not null default 'RUNNING', -- RUNNING | SUCCESS | PARTIAL | FAILED
    rows_processed  integer default 0,
    rows_inserted   integer default 0,
    rows_updated    integer default 0,
    error_message   text
);

create index if not exists idx_il_trade_date on ingestion_logs (trade_date desc);
create index if not exists idx_il_status on ingestion_logs (status);

-- ------------------------------------------------------------
-- scan_config : configurable thresholds (avoid hard-coding)
-- single-row-per-key config table, editable without a deploy
-- ------------------------------------------------------------
create table if not exists scan_config (
    key             text primary key,
    value           numeric not null,
    description     text,
    updated_at      timestamptz not null default now()
);

insert into scan_config (key, value, description) values
    ('delivery_strong_multiplier', 1.30, 'Current delivery >= this x 15D avg => Strong'),
    ('delivery_moderate_multiplier', 1.15, 'Current delivery >= this x 15D avg => Moderate'),
    ('volume_strong_ratio', 1.5, 'Volume ratio >= this => Strong'),
    ('volume_moderate_ratio', 1.2, 'Volume ratio >= this => Moderate'),
    ('macd_fast_period', 12, 'MACD fast EMA period'),
    ('macd_slow_period', 26, 'MACD slow EMA period'),
    ('macd_signal_period', 9, 'MACD signal EMA period'),
    ('supertrend_atr_period', 10, 'Supertrend ATR period'),
    ('supertrend_multiplier', 3, 'Supertrend ATR multiplier'),
    ('score_strong_min', 6, 'Min score for STRONG SETUP (out of 7)'),
    ('score_moderate_min', 4, 'Min score for MODERATE SETUP'),
    ('score_weak_min', 2, 'Min score for WEAK SETUP')
on conflict (key) do nothing;

-- ------------------------------------------------------------
-- trading_calendar : derived/maintained list of actual NSE
-- trading days, so ingestion never assumes calendar day = trading day
-- ------------------------------------------------------------
create table if not exists trading_calendar (
    trade_date      date primary key,
    is_trading_day  boolean not null default true,
    source          text default 'derived'  -- 'derived' from ingested data, or 'manual'
);

-- updated_at trigger for stocks
create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_stocks_updated_at on stocks;
create trigger trg_stocks_updated_at
    before update on stocks
    for each row execute function set_updated_at();
