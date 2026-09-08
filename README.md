# Weather Alpha Lab

Weather-market research and paper-trading engine for Kalshi and Polymarket US. The objective is to determine whether executable prediction-market prices are materially wrong relative to calibrated probabilities of official weather settlement outcomes.

## Current research stack

- public Kalshi market discovery/read-only quotes
- Polymarket US normalization / cross-venue comparison scaffold
- official NWS station observations
- Open-Meteo ensemble ingestion
- Gaussian bucket / above / below probabilities
- settlement-nowcast primitive
- pmxt Polymarket v2 historical CLOB ingestion
- depth-aware historical orderbook reconstruction
- exhaustive weather strategy tournament
- NOAA GOES-19 / GOES-18 ABI cloud feature ingestion
- NOAA AWS HRRR point-baseline retrieval
- IEM/NCEI historical one-minute ASOS surface telemetry
- transparent model-vs-observation `AlphaDelta` physical-reality score
- append-only research archive and paper portfolio primitives

No live order placement is enabled in the current research branch.

## Project boundary

This repo is weather-only. BTC/crypto repos and databases may be inspected read-only for engineering ideas, but weather runtime code, data, tables, results, backtests and execution remain independent.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
uvicorn weather_alpha.main:app --host 0.0.0.0 --port 8000
```

## Historical market data

The backtest branch can stream the pmxt Polymarket v2 hourly Parquet archive, reconstruct executable books and extract only weather markets into a compact weather-only cache under `data/weather/`.

## Satellite / physical-reality alpha

The live/replay physical layer compares what the atmosphere is doing now against the model baseline:

- GOES-19 = operational GOES-East
- GOES-18 = operational GOES-West
- ABI Band 2 = daytime reflectance / solar attenuation feature
- ABI Band 13 = day/night cloud brightness-temperature structure
- HRRR = 2 m temperature, total cloud cover, downward shortwave baseline
- one-minute ASOS history = temperature/pressure velocity and front features

`cloud_optical_depth_proxy` is deliberately a proxy, not an official physical cloud-optical-depth retrieval. `AlphaDelta` is a research feature, not a probability. Both must be calibrated historically before they can alter trading probabilities.

See `research/CLOUD_ALPHA_ARCHITECTURE.md` and `research/WEATHER_BACKTEST_PLAN.md`.

## Principle

Forecast accuracy by itself is not the objective. **Net expected value at executable market prices, after costs and realistic latency, is the objective.**
