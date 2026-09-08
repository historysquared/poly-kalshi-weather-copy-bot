# Weather Strategy Tournament — Historical Backtest Plan

The objective is not to prove one social-media strategy. It is to run a broad, timestamp-safe tournament across every weather trade family that has a plausible economic mechanism, then require out-of-sample persistence.

## Project boundary

This weather project is intentionally separate from all BTC/crypto research projects and databases.

- Canonical codebase: `historysquared/poly-kalshi-weather-copy-bot`
- Weather data, normalized stores, backtests, results, and execution logic live here.
- BTC repos/datasets may be inspected read-only for engineering ideas such as Parquet loading, no-lookahead joins, orderbook replay, fee/slippage modeling, reporting, or deployment patterns.
- Do not import BTC strategies, BTC features, BTC tables, BTC results, or BTC runtime dependencies into this project.
- External source data may be transformed into weather-specific normalized stores, but the weather runtime must not depend on a BTC database or BTC project checkout.

## Data sources

1. Existing weather-relevant databases available to the project.
2. pmxt Polymarket v2 hourly Parquet CLOB archive.
3. Polymarket Gamma metadata for condition/token mapping.
4. pmxt Kalshi archive where useful.
5. NOAA GOES AWS Open Data: GOES-19 East, GOES-18 West, historical GOES-16.
6. NOAA AWS HRRR archive with explicit model-cycle availability.
7. IEM/NCEI one-minute ASOS history.
8. NEXRAD Level II via the current `unidata-nexrad-level2` bucket; the old `noaa-nexrad-level2` bucket is deprecated.
9. NOAA MRMS via `noaa-mrms-pds`.
10. GOES GLM Level-2 lightning (`GLM-L2-LCFA`) from GOES-19/18 buckets.
11. NCEP RTMA-RU 2.5 km CONUS analysis every 15 minutes, with actual product availability timestamp retained.
12. NWS Area Forecast Discussions (AFD) via public NWS product feeds/API.
13. Optional DOT/RWIS and CWOP/APRS observations only after station-level QC, clock-quality checks, and historical availability are established.
14. Aviation METAR/SPECI feeds as fast official-station events, preserving publication/receipt timestamps.

NWSChat is not assumed to be a public trading-data feed. NWSChat 2.0 is a partner/core-partner communication service requiring account access, so the production system must not depend on it unless lawful authorized access is separately established.

The pmxt raw hourly files are large. The ingestion pipeline should download one hour, predicate-filter to weather condition IDs, write a compact weather-only Parquet partition under `data/weather/`, and optionally delete the raw hour. Raw external archives are never committed to Git.

Never replace executable prices with midpoint prices. Use historical executable asks reconstructed from the book available at the simulated decision timestamp, with depth, fee, quote-age, and slippage controls.

## Canonical normalized backtest row

Every candidate contract snapshot should contain timestamp/source timestamp/received timestamp; venue/market identifiers; city/station/date/timezone; settlement rule/version; exact contract bounds; executable YES/NO bid/ask and depth; fees/slippage; time-to-close/settlement; all forecast/model features known then; official observations known then; daily high/low so far; satellite/radar/lightning/RTMA provenance and feature timestamps; text-product issuance time; and eventual official settlement.

All joins must be AS-OF joins using information actually available by the simulated timestamp. `timestamp_received` is the default market-data availability clock unless another field is explicitly justified.

## Core strategy families

### A. Blind narrow-bucket NO baseline
Test NO entry prices 0.80–0.99, bucket widths, horizons, cities, seasons, liquidity and spread bands. Report ROI, not just win rate.

### B. Model-filtered NO tails
Buy NO only when calibrated fair NO exceeds all-in executable ask; sweep edge thresholds, models, shrinkage, horizon and liquidity filters.

### C. Best single YES bucket
Buy the single bucket with the highest positive calibrated probability minus all-in executable YES ask.

### D. Adjacent YES basket
Enumerate contiguous baskets of 2–8 mutually exclusive ranges and compare summed fair probability with summed executable asks plus costs.

### E. Late-day impossible-bucket NO
Once an official observed daily high exceeds a bucket upper bound, test buying NO under quote-age, liquidity and latency constraints.

### F. Near-impossible bucket NO
Use remaining daylight, current high, forecast path and residual distribution to identify brackets that are not mathematically dead but have collapsed probability.

### G. Forecast-revision momentum
Measure price response and settlement P&L after new model runs shift fair bucket probabilities.

### H. Observation shock / stale quote catch-up
Trade after new METAR/SPECI/ASOS observations change settlement probability while executable quotes remain stale.

### I. Whole-distribution relative value
Treat each event as one probability distribution and test local distortions, tails vs center and executable basket sums.

### J. Cross-venue relative value
Compare Kalshi and Polymarket US only after exact station/source/date/rounding/observation-window/bound equivalence is established.

### K. Market-only microstructure controls
Test quote staleness, spread changes, order-book imbalance, large-trade response and cross-bucket inconsistencies without weather features.

### L. Satellite / high-frequency physical-reality alpha
Test GOES Band 2 solar-transmission mismatch, Band 13 cloud structure, high-frequency surface dT/dt/pressure, observed-vs-HRRR error, marine-layer clearing and rapid-front rollover. AlphaDelta is a feature score, not a probability, and must be calibrated walk-forward.

### M. Convective cooling / precipitation-arrival alpha
Use NEXRAD, MRMS and GLM to detect a storm-driven temperature rollover before conventional hourly forecasts reflect it.

Features and tests:
- nearest >=30/35/40/45 dBZ radar core distance to settlement station
- bearing of core relative to surface wind-from direction
- radial approach speed from successive radar/MRMS frames
- MRMS precipitation-rate field at station and along upwind corridor
- GLM flash counts within 5/10/25/50 km over rolling 1/5/10/20 minute windows
- lightning initiation before first >35 dBZ core as a separate lead-time feature
- official temperature dT/dt before/after first precipitation
- pressure rise and wind-shift confirmation
- whether daily high had already been set at signal time
- market response after 30s/1m/2m/5m/10m/15m

Do not assume rain always creates a tradable cooling event. Calibrate by station, season, storm mode, humidity/dewpoint depression, time of day and observed temperature trajectory.

### N. RTMA-RU assimilation delta
Compare latest 15-minute RTMA-RU analyzed surface state with the older forecast baseline available to the market. Test whether RTMA-RU temperature/wind/cloud deltas improve final-high residual forecasts and market P&L after true product latency.

### O. Forecaster-intent text alpha
Archive every AFD issue and compare text revisions with the next numeric forecast/market move. Start with transparent phrase features (lowered/raised highs, persistent stratus, delayed/earlier clearing, model-confidence language) before any complex NLP. Strictly use issuance time and avoid backfilled text. Test whether text adds incremental signal beyond the latest numerical guidance.

### P. Auxiliary mesonet / RWIS / CWOP front propagation
Use non-airport stations only as proxy sensors. Require per-station quality scoring, timestamp sanity, persistent bias estimation, elevation/distance metadata and robust outlier rejection. Test whether a front/cloud/cooling feature observed at neighboring stations predicts the settlement-station observation with usable lead time.

## Exit variants

Every entry strategy should be tested under hold-to-resolution, exit-when-edge<=0, profit targets, stops where executable, and time-based exits. Do not assume public wallets hold every position to settlement.

## Execution assumptions

Run optimistic, realistic and conservative fill scenarios. Depth-aware fills may not exceed displayed size without an explicit queue/fill model. Include source-to-signal, signal-to-order and exchange-ack latency sweeps.

## Statistical protocol

Train/calibrate on earlier dates only; walk forward; hold out cities/stations; report by station/season/horizon/price/spread/model; bootstrap ROI/P&L confidence intervals; track Brier/log loss separately from trading P&L; correct for multiple testing; and promote only untouched rules that persist out of sample.

## Primary ranking metrics

1. Net P&L after costs
2. ROI on capital committed
3. P&L per event/day
4. Maximum drawdown
5. Profit factor
6. Daily Sharpe-like statistic
7. Win rate as descriptive only
8. Calibration/Brier/log loss
9. Historical executable capacity
10. Incremental value versus simpler baseline strategy

## First research queue

Run A, B, D, E, L, M, N and O first. The key questions are whether blind NO is truly profitable, whether model filtering or adjacent baskets improve it, whether eliminated-bucket NO is the smooth-P&L mechanism, and whether satellite/radar/lightning/RTMA/text information adds incremental edge after real source and execution latency.