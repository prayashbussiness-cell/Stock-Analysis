import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import market, scanner, stocks
from app.config import get_settings

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(
    title="Stock Research & Swing-Trade Screening API",
    description=(
        "Quantitative technical/delivery screening tool for NSE-listed stocks. "
        "Educational/research use only — not investment advice."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(scanner.router, prefix="/api/scanner", tags=["scanner"])
app.include_router(market.router, prefix="/api", tags=["market"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
