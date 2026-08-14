from fastapi import APIRouter, Query

from app.database import get_service_client

router = APIRouter()

SETUP_MAP = {
    "strong": "STRONG SETUP",
    "moderate": "MODERATE SETUP",
    "weak": "WEAK SETUP",
    "none": "NO SETUP",
}


@router.get("")
def scanner_list(setup: str | None = Query(None, description="strong | moderate | weak | none")):
    db = get_service_client()

    # Latest scan_results row per stock. Supabase/PostgREST doesn't do
    # DISTINCT ON over the wire easily, so pull recent rows and reduce
    # in Python — fine at this dataset size (a few hundred stocks).
    rows = (
        db.table("scan_results")
        .select("*, stocks(symbol, company_name)")
        .order("calculation_date", desc=True)
        .limit(2000)
        .execute()
        .data
    )

    latest_by_stock: dict[str, dict] = {}
    for r in rows:
        sid = r["stock_id"]
        if sid not in latest_by_stock:
            latest_by_stock[sid] = r

    results = list(latest_by_stock.values())

    if setup:
        target = SETUP_MAP.get(setup.lower())
        if target:
            results = [r for r in results if r["setup_classification"] == target]

    results.sort(key=lambda r: r["score"], reverse=True)

    return [
        {
            "symbol": r["stocks"]["symbol"] if r.get("stocks") else None,
            "company_name": r["stocks"]["company_name"] if r.get("stocks") else None,
            "score": r["score"],
            "max_score": r["max_score"],
            "classification": r["setup_classification"],
            "calculation_date": r["calculation_date"],
        }
        for r in results
    ]
