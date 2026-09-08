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

1. Existing weather-relevant databases available to the project: use them for market metadata, outcomes, historical weather observations/forecasts, and Kalshi/Polymarket weather records where present.
2. pmxt Polymarket v2 archive: hourly Parquet CLOB event stream, coverage beginning 2026-04-13T19 UTC. Direct object pattern: `https://r2v2.pmxt.dev/polymarket_orderbook_YYYY-MM-DDTHH.parquet`.
3. Polymarket Gamma public metadata API: used to map pmxt condition/token IDs to weather market questions, outcomes, dates, and resolution metadata.
4. pmxt Kalshi archive where useful as a second historical orderbook source.
5. NOAA GOES AWS Open Data: GOES-19 East, GOES-18 West, historical GOES-16 where appropriate; ABI Band 2 and Band 13 CMIP features.
6. NOAA AWS HRRR archive: 2 m temperature, total cloud cover, downward shortwave radiation, with explicit model-cycle availability.
7. IEM/NCEI one-minute ASOS archive for historical high-frequency surface telemetry. This is a fast feature source; settlement authority remains whatever each contract specifies.

The pmxt raw hourly files are large. The ingestion pipeline should download one hour, predicate-filter to weather condition IDs, write a compact weather-only Parquet partition under `data/weather/`, and optionally delete the raw hour. Raw external archives are never committed to Git.

Never replace executable prices with midpoint prices. Use historical executable asks reconstructed from the book available at the simulated decision timestamp, with depth, fee, quote-age, and slippage controls.

## Canonical normalized backtest row

Every candidate contract snapshot should contain:

- timestamp / source timestamp / timestamp_received
- venue
- event_id / condition_id / market_id / asset_id
- city / station / settlement date / timezone
- exact settlement source/rule version
- contract shape and exact lower/upper bounds
- YES bid/ask and NO bid/ask
- visible depth and spread when available
- fee schedule / fee reserve / slippage reserve
- time to close and time to expected settlement
- forecast model probabilities known at that timestamp
- official weather observations known at that timestamp
- daily high/low observed so far
- satellite scan/object timestamp and source key
- Band 2 reflectance/transmission features
- Band 13 brightness-temperature/cloud-structure features
- HRRR model cycle, forecast hour, temperature/cloud/shortwave baseline
- high-frequency ASOS dT/dt, pressure tendency and front features
- eventual official settlement value and winning bucket

All joins must be AS-OF joins using information that had actually arrived by the simulated timestamp. `timestamp_received` is the default market-data availability clock unless there is a documented reason to use another field.

## Strategy families

### A. Blind narrow-bucket NO baseline

Purpose: test the claim that exact/narrow ranges are structurally overpriced without using weather information.

Grid:
- NO entry price: 0.80–0.99 in 1-cent increments
- YES implied price: reciprocal range
- bucket width: 1°F, 2°F, 3°F+
- hours to close: <1, 1–3, 3–6, 6–12, 12–24, >24
- city/station
- day of year / season
- liquidity / spread quantile

Report both win rate and ROI. A high NO win rate is meaningless unless it exceeds the break-even probability implied by the actual NO purchase price plus costs.

### B. Model-filtered NO tails

Buy NO when calibrated fair NO probability exceeds all-in executable NO ask.

Grid:
- minimum net edge: 1c, 2c, 3c, 4c, 5c, 7.5c, 10c
- model: market-only base rate, GFS ensemble, NBM, HRRR, ECMWF/AIFS, NWS, blended, station-nowcast
- probability shrinkage level
- time to settlement
- quote age / spread / depth

### C. Best single YES bucket

Buy the single bucket with highest positive fair-probability minus executable YES ask.

Grid the same edge thresholds and model variants as B.

### D. Adjacent YES basket

For mutually exclusive temperature buckets, enumerate contiguous baskets of 2–8 adjacent ranges. Compare summed calibrated probability mass to summed executable YES asks plus costs. Test:
- 2, 3, 4, 5, 6, 7, 8 legs
- minimum basket edge 2c–20c
- centered-on-mode baskets
- optimizer-selected contiguous baskets
- fixed-dollar and equal-share sizing
- hold to resolution vs profit-taking exits

### E. Late-day impossible-bucket NO

For daily-high markets, once official observed high exceeds a bucket upper bound, that bucket is mathematically unable to win. Test buying NO subject to:
- minimum locked edge after fees/slippage
- quote age
- minimum displayed size
- settlement-source confirmation
- delay after official observation publication: 0, 15s, 30s, 60s, 2m, 5m

This is the highest-priority nonforecast strategy.

### F. Near-impossible bucket NO

Not mathematically eliminated, but weather path makes the remaining move very unlikely. Use current official high, remaining daylight, short-range model path, cloud/solar/wind, nearby stations, and calibrated residual distribution.

### G. Forecast-revision momentum

Trade when latest model run materially changes bucket probability but market price has not responded. Measure 5m/15m/30m/60m subsequent price response and settlement P&L.

### H. Observation shock / stale quote catch-up

Trade after new METAR/SPECI/ASOS observation changes settlement probability while market quote remains stale. Test latency windows and quote-age thresholds.

### I. Whole-distribution relative value

Within one weather event, compute market-implied distribution across all mutually exclusive buckets. Test:
- sum of executable YES asks
- sum of executable NO equivalents
- local distortions versus smoothed calibrated distribution
- butterfly-style relative value across neighboring ranges
- tails versus center mispricing

Do not call a basket risk-free unless payout mechanics and settlement semantics make it locked.

### J. Cross-venue relative value

Only compare Kalshi and Polymarket US contracts after exact semantic equivalence is established: same station/source, date, variable, observation window, rounding, bounds, inclusivity, and settlement procedure.

Test:
- buy cheaper equivalent YES
- buy cheaper equivalent NO
- complementary locked pairs when verified
- synthetic unions of narrower buckets versus a wider bucket on the other venue

### K. Market-only microstructure signals

Without weather model:
- stale quote age
- sudden spread widening/compression
- orderbook imbalance
- price reaction after large trade
- mean reversion after short-lived bucket dislocation
- cross-bucket probability inconsistency

These are controls to determine whether profit comes from weather information or exchange microstructure.

### L. Satellite / high-frequency physical-reality alpha

Test whether fast physical observations move the final-high distribution before prediction-market prices adjust.

Features:
- GOES Band 2 reflectance / solar-transmission proxy versus HRRR cloud baseline
- GOES Band 13 brightness temperature / cold-cloud fraction
- one-minute ASOS temperature velocity
- ASOS pressure tendency
- current observed-vs-HRRR temperature error
- time to expected daily peak
- later: wind-shift/front features and coastal marine-layer burnoff metrics

Variants:
- cloud suppression downside
- clearer-than-HRRR upside
- rapid-front/temperature-rollover
- marine-layer burnoff lag by coastal station
- AlphaDelta-filtered NO tails
- AlphaDelta-adjusted adjacent YES baskets
- AlphaDelta as a calibrated residual-model feature instead of a standalone threshold

Latency grid:
- 0s, 30s, 1m, 2m, 5m, 10m, 15m after source data becomes available

The current AlphaDelta weights are research priors only. The actual production mapping must be learned and validated walk-forward by station/horizon/season.

## Exit variants

Every entry strategy should be tested under:
- hold to resolution
- exit when edge <= 0
- exit at +10%, +25%, +50%, +100% return
- stop at -25%, -50% where executable
- time-based exit 15m/30m/60m before close

Do not assume the social-media wallets hold every leg to settlement.

## Execution assumptions

Run at least three execution scenarios:

1. Optimistic: touch best ask immediately, stated fees only.
2. Realistic: walk displayed asks for requested size, add documented fees, minimum visible size, quote freshness check.
3. Conservative: delayed entry, worse level/tick, larger slippage reserve, and capacity haircut.

If depth is known, cap simulated size at observed executable depth. Never fill more size than was displayed without an explicit fill model.

## Statistical protocol

- Train/calibrate on earlier dates only.
- Walk forward through later dates.
- Hold out entire cities/stations as a robustness test.
- Report results by strategy, city, station, season, horizon, price band, spread band, and model.
- Minimum sample counts before promoting a variant.
- Show bootstrap confidence intervals for ROI and P&L/trade.
- Track Brier/log loss for probability models separately from trading P&L.
- Correct for multiple testing: broad parameter sweeps are research, not evidence until the winning rules survive untouched out of sample.

## Primary ranking metrics

1. Net P&L after costs
2. ROI on capital committed
3. P&L per event/day
4. Maximum drawdown
5. Profit factor
6. Sharpe-like daily return statistic
7. Win rate only as a descriptive statistic
8. Calibration/Brier/log loss for probability-producing models
9. Capacity: executable depth at historical signals

## First research question

Run A, B, D, E, and L first. They directly test the claims in the weather-wallet posts and the physical-reality thesis:

- Is blind NO actually profitable at 90–99c?
- Does a model filter turn NO tails profitable?
- Are adjacent YES baskets superior to single-bucket entries?
- Is late official-observation bucket elimination the real source of the smooth high-win-rate P&L?
- Does GOES + high-frequency surface telemetry add incremental edge beyond HRRR and existing forecast models after realistic data/execution latency?
