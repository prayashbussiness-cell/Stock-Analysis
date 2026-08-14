/* ===========================================================
   Stock Research Scanner — frontend logic
   Plain vanilla JS. No build step, no framework.
   Set API_BASE below to your deployed Render backend URL.
   =========================================================== */

const API_BASE = window.__API_BASE__ || "http://localhost:8000/api";

// ---------------------------------------------------------------
// Small fetch helper
// ---------------------------------------------------------------
async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function fmtNum(n, decimals = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-IN", { maximumFractionDigits: decimals, minimumFractionDigits: decimals });
}

function fmtInt(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString("en-IN");
}

function fmtCompact(n) {
  if (n === null || n === undefined) return "—";
  const num = Number(n);
  if (Math.abs(num) >= 1e7) return (num / 1e7).toFixed(2) + "Cr";
  if (Math.abs(num) >= 1e5) return (num / 1e5).toFixed(2) + "L";
  if (Math.abs(num) >= 1e3) return (num / 1e3).toFixed(1) + "K";
  return String(num);
}

function fmtPct(n, withSign = true) {
  if (n === null || n === undefined) return "—";
  const sign = withSign && n > 0 ? "+" : "";
  return `${sign}${Number(n).toFixed(2)}%`;
}

function classificationClass(c) {
  if (!c) return "insufficient";
  const v = c.toUpperCase();
  if (v.includes("STRONG")) return "strong";
  if (v.includes("MODERATE")) return "moderate";
  if (v.includes("WEAK")) return "weak";
  if (v.includes("INSUFFICIENT")) return "insufficient";
  return "no";
}

// ---------------------------------------------------------------
// Search box + autocomplete (shared across all pages)
// ---------------------------------------------------------------
function initSearch() {
  const input = document.getElementById("stock-search-input");
  const dropdown = document.getElementById("autocomplete-list");
  const form = document.getElementById("stock-search-form");
  if (!input || !form) return;

  let debounceTimer = null;
  let highlightedIndex = -1;
  let currentResults = [];

  function closeDropdown() {
    dropdown.classList.remove("open");
    dropdown.innerHTML = "";
    highlightedIndex = -1;
  }

  function goToStock(symbol) {
    window.location.href = `stock.html?symbol=${encodeURIComponent(symbol)}`;
  }

  input.addEventListener("input", () => {
    const q = input.value.trim();
    clearTimeout(debounceTimer);
    if (q.length < 1) {
      closeDropdown();
      return;
    }
    debounceTimer = setTimeout(async () => {
      try {
        const results = await apiGet(`/stocks/search?q=${encodeURIComponent(q)}`);
        currentResults = results;
        if (!results.length) {
          closeDropdown();
          return;
        }
        dropdown.innerHTML = results
          .map(
            (r, i) =>
              `<div class="autocomplete-item" data-symbol="${r.symbol}" data-index="${i}">
                 <span class="sym">${r.symbol}</span>
                 <span class="name">${r.name || ""}</span>
               </div>`
          )
          .join("");
        dropdown.classList.add("open");
      } catch (e) {
        closeDropdown();
      }
    }, 200);
  });

  dropdown.addEventListener("click", (e) => {
    const item = e.target.closest(".autocomplete-item");
    if (item) goToStock(item.dataset.symbol);
  });

  input.addEventListener("keydown", (e) => {
    const items = dropdown.querySelectorAll(".autocomplete-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      highlightedIndex = Math.min(highlightedIndex + 1, items.length - 1);
      items.forEach((it, i) => it.classList.toggle("highlighted", i === highlightedIndex));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      highlightedIndex = Math.max(highlightedIndex - 1, 0);
      items.forEach((it, i) => it.classList.toggle("highlighted", i === highlightedIndex));
    } else if (e.key === "Escape") {
      closeDropdown();
    }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (highlightedIndex >= 0 && currentResults[highlightedIndex]) {
      goToStock(currentResults[highlightedIndex].symbol);
    } else if (input.value.trim()) {
      goToStock(input.value.trim().toUpperCase());
    }
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-box")) closeDropdown();
  });
}

// ---------------------------------------------------------------
// Market status strip (shared)
// ---------------------------------------------------------------
async function renderMarketStatus(targetId) {
  const el = document.getElementById(targetId);
  if (!el) return;
  try {
    const status = await apiGet("/market-status");
    let html = "";
    if (status.status === "NO_DATA") {
      html = `<span>Market data: not yet ingested</span>`;
    } else {
      html = `<span>Last successful update: ${status.last_successful_update ? new Date(status.last_successful_update).toLocaleString("en-IN") : "—"}</span>`;
      if (status.warning) {
        const cls = status.last_attempt_status === "FAILED" ? "fail" : "warn";
        html += `<span class="${cls}">⚠ ${status.warning}</span>`;
      }
    }
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<span class="fail">⚠ Unable to reach backend API</span>`;
  }
}

// ---------------------------------------------------------------
// Home page: top matching setups
// ---------------------------------------------------------------
async function renderHomeMatches() {
  const el = document.getElementById("top-matches");
  if (!el) return;
  el.innerHTML = `<div class="state-block">Loading scanner results…</div>`;
  try {
    const results = await apiGet("/scanner");
    if (!results.length) {
      el.innerHTML = `<div class="state-block">No scan results yet. Run the backfill + daily update jobs to populate data.</div>`;
      return;
    }
    el.innerHTML = `<div class="match-list">${results
      .slice(0, 12)
      .map(
        (r) => `
        <div class="match-row" tabindex="0" onclick="window.location.href='stock.html?symbol=${r.symbol}'">
          <div>
            <div class="match-symbol">${r.symbol}</div>
            <div class="match-name">${r.company_name || ""}</div>
          </div>
          <div style="display:flex; align-items:center; gap:12px;">
            <span class="pill ${classificationClass(r.classification)}">${r.classification}</span>
            <span class="match-score">${r.score}/${r.max_score}</span>
          </div>
        </div>`
      )
      .join("")}</div>`;
  } catch (e) {
    el.innerHTML = `<div class="state-block error">Could not load scanner results: ${e.message}</div>`;
  }
}

// ---------------------------------------------------------------
// Stock detail page
// ---------------------------------------------------------------
const CHECKPOINT_RENDER = {
  macd_pass: (d) => ({
    title: "Weekly MACD",
    lines: [
      ["MACD", fmtNum(d.macd_line, 2)],
      ["Signal", fmtNum(d.signal, 2)],
      ["Histogram", fmtNum(d.histogram, 2)],
    ],
  }),
  ema200_pass: (d) => ({
    title: "Weekly EMA 200",
    lines: [
      ["Close", "₹" + fmtNum(d.weekly_close)],
      ["EMA 200", "₹" + fmtNum(d.ema200_weekly)],
    ],
  }),
  supertrend_pass: (d) => ({
    title: "Weekly Supertrend",
    lines: [
      ["Trend", d.direction ? d.direction[0].toUpperCase() + d.direction.slice(1) : "—"],
      ["Value", "₹" + fmtNum(d.value)],
    ],
  }),
  price_trend_pass: (d) => ({
    title: "Price Trend",
    lines: [
      ["This week", "₹" + fmtNum(d.current_weekly_close)],
      ["Prev week", "₹" + fmtNum(d.previous_weekly_close)],
      ["Change", fmtPct(d.weekly_return_pct)],
    ],
  }),
  delivery_pass: (d) => ({
    title: "Delivery",
    lines: [
      ["Current", fmtPct(d.current_delivery_pct, false)],
      ["15D Avg", fmtPct(d.avg_15d_pct, false)],
      ["Strength", d.strength || "—"],
    ],
  }),
  volume_pass: (d) => ({
    title: "Volume",
    lines: [
      ["Ratio", d.volume_ratio ? d.volume_ratio.toFixed(2) + "x" : "—"],
      ["Strength", d.strength || "—"],
    ],
  }),
  ema20_pass: (d) => ({
    title: "20 EMA (Daily)",
    lines: [
      ["Price", "₹" + fmtNum(d.current_price)],
      ["EMA 20", "₹" + fmtNum(d.ema20_daily)],
    ],
  }),
};

function renderCheckpointCard(cp) {
  const renderer = CHECKPOINT_RENDER[cp.key];
  const info = renderer ? renderer(cp.detail || {}) : { title: cp.label, lines: [] };
  const state = cp.passed === null || cp.passed === undefined ? "na" : cp.passed ? "pass" : "fail";
  const statusText = state === "na" ? "INSUFFICIENT DATA" : state === "pass" ? "PASS" : "FAIL";
  const dot = state === "na" ? "⚪" : state === "pass" ? "🟢" : "🔴";

  return `
    <div class="checkpoint-card ${state}">
      <div class="checkpoint-label">${info.title}</div>
      <div class="checkpoint-status ${state}">${dot} ${statusText}</div>
      <div class="checkpoint-detail">
        ${info.lines.map(([k, v]) => `<div class="row"><span>${k}</span><span>${v}</span></div>`).join("")}
      </div>
    </div>`;
}

async function renderStockPage() {
  const params = new URLSearchParams(window.location.search);
  const symbol = (params.get("symbol") || "").toUpperCase();
  const container = document.getElementById("stock-container");
  if (!symbol) {
    container.innerHTML = `<div class="state-block">Search for a stock above to see its screening result.</div>`;
    return;
  }

  container.innerHTML = `<div class="state-block">Loading ${symbol}…</div>`;

  try {
    const [overview, scoreData, historyData] = await Promise.all([
      apiGet(`/stocks/${symbol}`),
      apiGet(`/stocks/${symbol}/score`).catch(() => null),
      apiGet(`/stocks/${symbol}/history?days=50`).catch(() => null),
    ]);

    const changeCls = overview.change_pct > 0 ? "positive" : overview.change_pct < 0 ? "negative" : "neutral";
    const cls = classificationClass(scoreData ? scoreData.classification : overview.classification);

    let html = `
      <div class="stock-header">
        <div>
          <div class="symbol">${overview.symbol} · NSE</div>
          <h1>${overview.company_name}</h1>
        </div>
        <div class="price-block">
          <div class="price">${overview.price ? "₹" + fmtNum(overview.price) : "—"}</div>
          <div class="change ${changeCls}">${overview.change_pct !== null ? fmtPct(overview.change_pct) : "No prior close"}</div>
        </div>
      </div>`;

    if (scoreData) {
      html += `
        <div class="setup-banner ${cls}">
          <div>
            <div class="setup-label">${scoreData.classification}</div>
            <div class="disclaimer-inline">This is a quantitative screening result, not investment advice.</div>
          </div>
          <div class="setup-score">${scoreData.score} / ${scoreData.max_score} CONDITIONS MATCHED</div>
        </div>
        <div class="section-title">Checkpoints</div>
        <div class="checkpoint-grid">
          ${scoreData.checkpoints.map(renderCheckpointCard).join("")}
        </div>`;
    } else {
      html += `<div class="state-block">No score calculated yet for ${symbol}. Run the backend indicator job first.</div>`;
    }

    if (historyData && historyData.data && historyData.data.length) {
      html += `<div class="section-title">Delivery History</div>`;
      if (historyData.note) {
        html += `<div class="disclaimer-inline" style="margin-bottom:10px;">${historyData.note}</div>`;
      }
      html += `
        <table class="data-table">
          <thead><tr><th>Date</th><th>Close</th><th>Traded Qty</th><th>Delivery Qty</th><th>Delivery %</th></tr></thead>
          <tbody>
            ${historyData.data
              .slice(0, 15)
              .map(
                (r) => `
              <tr>
                <td>${r.trade_date}</td>
                <td>₹${fmtNum(r.close)}</td>
                <td>${fmtCompact(r.traded_quantity)}</td>
                <td>${fmtCompact(r.deliverable_quantity)}</td>
                <td>${r.calculated_delivery_percentage !== null ? fmtNum(r.calculated_delivery_percentage) + "%" : "—"}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>`;
    }

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="state-block error">Could not load ${symbol}: ${e.message}</div>`;
  }
}

// ---------------------------------------------------------------
// Scanner page
// ---------------------------------------------------------------
async function renderScannerPage(filter = null) {
  const el = document.getElementById("scanner-table-wrap");
  if (!el) return;
  el.innerHTML = `<div class="state-block">Loading scanner…</div>`;
  try {
    const path = filter ? `/scanner?setup=${filter}` : "/scanner";
    const results = await apiGet(path);
    if (!results.length) {
      el.innerHTML = `<div class="state-block">No matching stocks yet.</div>`;
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th style="text-align:left">Symbol</th>
            <th style="text-align:left">Company</th>
            <th>Score</th>
            <th style="text-align:left">Setup</th>
            <th>Calculated</th>
          </tr>
        </thead>
        <tbody>
          ${results
            .map(
              (r) => `
            <tr class="row-link" onclick="window.location.href='stock.html?symbol=${r.symbol}'">
              <td style="text-align:left; font-weight:600;">${r.symbol}</td>
              <td style="text-align:left; color:var(--muted);">${r.company_name || ""}</td>
              <td>${r.score}/${r.max_score}</td>
              <td style="text-align:left;"><span class="pill ${classificationClass(r.classification)}">${r.classification}</span></td>
              <td>${r.calculation_date}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  } catch (e) {
    el.innerHTML = `<div class="state-block error">Could not load scanner: ${e.message}</div>`;
  }
}

function initScannerFilters() {
  const chips = document.querySelectorAll(".filter-chip");
  if (!chips.length) return;
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      chips.forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      const filter = chip.dataset.filter === "all" ? null : chip.dataset.filter;
      renderScannerPage(filter);
    });
  });
}

// ---------------------------------------------------------------
// Page init
// ---------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initSearch();
  renderMarketStatus("market-status-strip");

  const page = document.body.dataset.page;
  if (page === "home") renderHomeMatches();
  if (page === "stock") renderStockPage();
  if (page === "scanner") {
    initScannerFilters();
    renderScannerPage();
  }
});
