from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from app.database import get_service_client

router = APIRouter()


@router.get("/market-status")
def market_status():
    db = get_service_client()
    last_success = (
        db.table("ingestion_logs")
        .select("*")
        .eq("status", "SUCCESS")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    last_attempt = (
        db.table("ingestion_logs")
        .select("*")
        .order("started_at", desc=True)
        .limit(1)
        .execute()
        .data
    )

    if not last_attempt:
        return {"status": "NO_DATA", "message": "No ingestion has run yet."}

    latest = last_attempt[0]
    stale_warning = None
    if last_success:
        completed = last_success[0].get("completed_at")
        if completed:
            completed_dt = datetime.fromisoformat(completed.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - completed_dt > timedelta(days=2):
                stale_warning = "Last successful update is more than 2 days old."

    return {
        "last_attempt_status": latest["status"],
        "last_attempt_at": latest.get("completed_at") or latest.get("started_at"),
        "last_successful_update": last_success[0]["completed_at"] if last_success else None,
        "warning": "DATA UPDATE FAILED" if latest["status"] == "FAILED" else stale_warning,
    }


@router.get("/admin/data-status")
def admin_data_status():
    db = get_service_client()

    stock_count = db.table("stocks").select("id", count="exact").execute().count
    record_count = db.table("daily_market_data").select("id", count="exact").execute().count

    last_ingestions = (
        db.table("ingestion_logs")
        .select("*")
        .order("started_at", desc=True)
        .limit(10)
        .execute()
        .data
    )
    last_success = (
        db.table("ingestion_logs")
        .select("*")
        .eq("status", "SUCCESS")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    last_indicator_calc = (
        db.table("technical_indicators")
        .select("calculation_date")
        .order("calculation_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    failed_recent = [r for r in last_ingestions if r["status"] == "FAILED"]

    return {
        "number_of_stocks": stock_count,
        "number_of_records": record_count,
        "last_successful_ingestion": last_success[0] if last_success else None,
        "last_indicator_calculation_date": last_indicator_calc[0]["calculation_date"] if last_indicator_calc else None,
        "recent_ingestion_attempts": last_ingestions,
        "recent_failures": failed_recent,
    }
