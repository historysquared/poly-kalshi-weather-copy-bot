# Weather Alpha Lab

Kalshi-first weather-market research and paper-trading engine. The project is designed to answer one question: **is the executable Kalshi price materially wrong relative to a calibrated probability of the official weather settlement?**

## v0.1 scope

Working foundation:
- public Kalshi market discovery/read-only quotes
- official NWS station observations
- Open-Meteo ensemble ingestion
- Gaussian bucket / above / below probabilities
- spread, liquidity and minimum-edge filters
- settlement-nowcast primitive using official anchor + nearby trend + forecast blend
- append-only research archive
- paper portfolio primitives
- FastAPI research endpoint
- unit tests

No live order placement is implemented.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
uvicorn weather_alpha.main:app --host 0.0.0.0 --port 8000
```

Then call `/scan` with a real Kalshi weather `series` ticker.

## Research roadmap

1. Add exact Kalshi weather contract normalization and settlement metadata.
2. Add high-frequency ASOS/MADIS/Synoptic observations and nearby-station correlation learning.
3. Add NBM, HRRR, ECMWF/AIFS and Aurora adapters as separate model competitors.
4. Archive every forecast vintage and score Brier, MAE, RMSE, ROI and calibration by station/horizon.
5. Learn station/horizon-specific bias and sigma rather than using fixed uncertainty floors.
6. Add historical market snapshots and true executable backtests.
7. Add Polymarket as a second venue adapter only after the Kalshi research pipeline is stable.
8. Only after successful forward testing, add authenticated execution as a separate disabled-by-default module.

## Principle

Forecast accuracy is not the objective. **Expected value after executable market prices is the objective.**
