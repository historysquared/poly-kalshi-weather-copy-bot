from fastapi import FastAPI, Query
from .service import WeatherAlphaService

app = FastAPI(title="Weather Alpha Lab", version="0.1.0")
svc = WeatherAlphaService()

@app.get("/health")
async def health():
    return {"ok": True, "mode": "paper-research", "live_trading": False}

@app.get("/scan")
async def scan(
    station: str = Query("KDCA"),
    lat: float = Query(38.8512),
    lon: float = Query(-77.0402),
    series: str = Query(..., description="Kalshi weather series ticker, e.g. KXHIGHNY"),
    target_date: str | None = None,
):
    return await svc.scan_series(station, lat, lon, series, target_date)
