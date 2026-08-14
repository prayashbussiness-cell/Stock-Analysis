from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.database import get_service_client
from app.services import ingestion, scanner_engine
from app.services import indicators as ind

router = APIRouter()


@router.get("/search")
def search_stocks(q: str = Query(..., min_length=1)):
    db = get_service_client()
    q_upper = q.upper().strip()
    resp = (
        db.table("stocks")
        .select("symbol,company_name")
        .or_(f"symbol.ilike.%{q_upper}%,company_name.ilike.%{q}%")
        .eq("active", True)
        .limit(15)
        .execute()
    )
    return [{"symbol": r["symbol"], "name": r["company_name"]} for r in resp.data]


def _get_stock_or_404(db, symbol: str) -> dict:
    resp = db.table("stocks").select("*").eq("symbol", symbol.upper()).limit(1).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail=f"Stock '{symbol}' not found")
    return resp.data[0]


@router.get("/{symbol}")
def get_stock(symbol: str):
    db = get_service_client()
    stock = _get_stock_or_404(db, symbol)

    latest = (
        db.table("daily_market_data")
        .select("*")
        .eq("stock_id", stock["id"])
        .order("trade_date", desc=True)
        .limit(2)
        .execute()
        .data
    )
    if not latest:
        return {
            "symbol": stock["symbol"],
            "company_name": stock["company_name"],
            "price": None,
            "change_pct": None,
            "message": "No market data ingested yet for this stock.",
        }

    today = latest[0]
    prev_close = latest[1]["close"] if len(latest) > 1 else None
    change_pct = round(((today["close"] - prev_close) / prev_close) * 100, 2) if prev_close else None

    scan_row = (
        db.table("scan_results")
        .select("*")
        .eq("stock_id", stock["id"])
        .order("calculation_date", desc=True)
        .limit(1)
        .execute()
        .data
    )

    return {
        "symbol": stock["symbol"],
        "company_name": stock["company_name"],
        "price": today["close"],
        "change_pct": change_pct,
        "last_trade_date": today["trade_date"],
        "score": scan_row[0]["score"] if scan_row else None,
        "max_score": scan_row[0]["max_score"] if scan_row else None,
        "classification": scan_row[0]["setup_classification"] if scan_row else "INSUFFICIENT DATA",
    }


@router.get("/{symbol}/history")
def get_history(symbol: str, days: int = Query(15, ge=1, le=250)):
    db = get_service_client()
    stock = _get_stock_or_404(db, symbol)

    resp = (
        db.table("daily_market_data")
        .select("trade_date,open,high,low,close,volume,traded_quantity,deliverable_quantity,"
                "source_delivery_percentage,calculated_delivery_percentage")
        .eq("stock_id", stock["id"])
        .order("trade_date", desc=True)
        .limit(days)
        .execute()
        .data
    )
    resp.sort(key=lambda r: r["trade_date"], reverse=True)

    if len(resp) < 15:
        note = (
            f"Only {len(resp)} of the requested {days} trading sessions are available. "
            "Fewer than 15 sessions means some checkpoints will show INSUFFICIENT DATA."
        )
    else:
        note = None

    return {"symbol": stock["symbol"], "sessions_returned": len(resp), "note": note, "data": resp}


@router.get("/{symbol}/indicators")
def get_indicators(symbol: str):
    db = get_service_client()
    stock = _get_stock_or_404(db, symbol)

    row = (
        db.table("technical_indicators")
        .select("*")
        .eq("stock_id", stock["id"])
        .order("calculation_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not row:
        raise HTTPException(status_code=404, detail="No indicators calculated yet for this stock")
    return row[0]


@router.get("/{symbol}/score")
def get_score(symbol: str, recompute: bool = False):
    db = get_service_client()
    stock = _get_stock_or_404(db, symbol)

    if recompute:
        result = ingestion.recompute_and_store(db, stock["id"], date.today())
        if result is None:
            raise HTTPException(status_code=422, detail="No market data available to score this stock")
        return _format_scan_result(stock["symbol"], result)

    row = (
        db.table("scan_results")
        .select("*")
        .eq("stock_id", stock["id"])
        .order("calculation_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not row:
        raise HTTPException(status_code=404, detail="No score calculated yet. Call with ?recompute=true.")
    r = row[0]
    return {
        "symbol": stock["symbol"],
        "score": r["score"],
        "max_score": r["max_score"],
        "classification": r["setup_classification"],
        "calculation_date": r["calculation_date"],
        "checkpoints": {k: r[k] for k in scanner_engine.CHECKPOINT_KEYS},
        "disclaimer": "This is a quantitative screening result, not investment advice.",
    }


def _format_scan_result(symbol: str, result) -> dict:
    return {
        "symbol": symbol,
        "score": result.score,
        "max_score": result.max_score,
        "classification": result.classification,
        "checkpoints": [
            {"key": c.key, "label": c.label, "passed": c.passed, "detail": c.detail}
            for c in result.checkpoints
        ],
        "disclaimer": "This is a quantitative screening result, not investment advice.",
    }
