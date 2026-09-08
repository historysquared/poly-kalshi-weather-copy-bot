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
- NEXRAD Level II, MRMS and GOES GLM convective-cooling feature layer
- RTMA-RU 15-minute rapid-update analysis track
- NWS Area Forecast Discussion forecaster-intent text features
- transparent model-vs-observation `AlphaDelta` physical-reality score
- append-only research archive and paper portfolio primitives

No live order placement is enabled in the current research branch.

## Project boundary

This repo is weather-only. BTC/crypto repos and databases may be inspected read-only for engineering ideas, but weather runtime code, data, tables, results, backtests and execution remain independent.

## Key source corrections

- GOES-19 is operational GOES-East; GOES-18 is operational GOES-West. GOES-16 is historical/backup here.
- the current public NEXRAD Level II bucket is `unidata-nexrad-level2`; the old `noaa-nexrad-level2` bucket is deprecated.
- IEM provides a true historical one-minute ASOS archive, while live cadence depends on the actual feed available.
- NWSChat 2.0 requires authorized partner/core-partner access and is not treated as a public production data source.
- cloud, convective and forecaster-intent scores are research features, not probabilities, until calibrated walk-forward.

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

## Physical-reality alpha

The live/replay layer compares what the atmosphere is doing now against the model baseline:

- GOES ABI cloud/solar features
- HRRR forecast baseline
- one-minute ASOS history and fast surface observations
- NEXRAD/MRMS precipitation and reflectivity proximity
- GLM lightning initiation
- RTMA-RU rapidly assimilated surface analysis
- AFD forecaster-intent revisions

`cloud_optical_depth_proxy`, `AlphaDelta`, convective-cooling score and text-intent score are deliberately transparent research features. They must prove incremental value after true data latency and execution costs before changing trade probabilities.

See `research/CLOUD_ALPHA_ARCHITECTURE.md` and `research/WEATHER_BACKTEST_PLAN.md`.

## Principle

Forecast accuracy by itself is not the objective. **Net expected value at executable market prices, after costs and realistic latency, is the objective.**
