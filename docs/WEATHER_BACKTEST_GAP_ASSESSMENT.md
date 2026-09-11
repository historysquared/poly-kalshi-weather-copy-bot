# Weather Backtest Gap Assessment

## Current evidence

The strongest historical result currently comes from the causal settlement strategy evaluated with real PMXT books, latency, depth, fee, price-floor and official CLI settlement logic. The broad parameter grid produced many cells around ~19–22% ROI; the representative 300-second-latency / 5-contract / no-price-floor cell had 114 executed trades, 71 wins, 43 losses, ~62.3% win rate, net P&L ~64.31 on ~290.69 capital at risk, ~22.1% ROI and 28 settlement dates. The 15-cent floor variants had fewer trades and higher win rates in several cells. Date-block bootstrap diagnostics were reported as ROBUST_PASS for the tested cells.

This is **promising evidence, not a production claim**. The strategy needs more chronological coverage, more stations/seasons, source-family separation, feature ablation and forward confirmation.

## Why the current 21% result is not enough

1. Roughly 28 settlement dates is still a small independent-date sample.
2. PMXT coverage is concentrated in the archive span we found, not a full year across regimes.
3. The strategy is tied to historical NWS/CLI settlement semantics; current Weather Company markets must remain a separate model/regime.
4. The current probability engine is settlement-basis/ASOS-centric and does not yet exploit most of the 100-edge feature set.
5. Historical ASOS quality/control and official CLI reconstruction need station-by-station validation.
6. Execution is taker-centric; maker results require explicit queue-priority and adverse-selection simulation.
7. The backtest needs a standardized feature table so additional sources can be added without changing the observation universe.
8. Model selection must happen on train/validation date blocks, with one untouched final OOS block.

## Required historical data expansion

### A. Market / execution truth — highest priority

- Kalshi historical market catalog, exact settlement source/rules, event date, station, bucket, result.
- PMXT order-book archives, downloaded **newest-first and then backward** in monthly/date blocks.
- Trades where available, to estimate queue consumption, phantom depth and maker fill plausibility.
- Exact fee schedules and tick sizes by series/date.

Target: >= 100 independent settlement dates before promoting a strategy; >= 200 preferred for station/season segmentation.

### B. Surface weather

- 1-minute/high-frequency ASOS temperature where available.
- METAR/SPECI stream with observation timestamps and publication/receipt timestamps when available.
- Dewpoint, wind speed/direction/gust, pressure, precipitation/weather codes, cloud layers.
- Official NWS CLI daily products and correction history.

Derived core features:
- T, high-so-far, distance to bucket edges
- dT/dt over 1/5/10/15/30m
- d²T/dt² / heating acceleration
- minutes since high
- drop from high
- positive/negative slope regime
- dewpoint depression and change
- wind-direction regime shift / sea-breeze/front flags
- pressure tendency
- SPECI/event flags
- observation age and source latency

### C. Forecast/model baseline

Build the probability baseline before exotic feeds:
- NBM station/grid TMAX distribution or best available probabilistic guidance
- HRRR deterministic/revision history
- time-lagged HRRR ensemble features
- model-vintage age
- cross-model disagreement
- station/season residual distributions

NBM should be the baseline probability model; each richer feed must prove incremental OOS value against the same snapshots.

### D. Remote sensing / convective feeds

Add only after A-C are stable:
- GOES visible albedo/cloud shielding
- GOES Band 13 IR cloud-top temperature/cooling
- GLM flash/pulse rate and lightning jumps
- MRMS precipitation and radar-derived outflow/cold-pool timing
- RTMA/RTMA-RU surface analysis
- AFD/TAF/SPECI text/revision metadata

Each source gets an explicit `available_at` timestamp and latency model. No feature may use data that was not available by the market snapshot time.

## Backtest architecture to build

### 1. Immutable observation universe

Create one decision-snapshot table keyed by:

`event_id, contract_id, station, settlement_date, snapshot_time`

with market book, exact source family/rules, weather observations, model vintages and source availability timestamps.

### 2. Feature registry

Every feature records:
- feature ID
- source
- event-time
- available-at time
- lookback
- station applicability
- missing-data policy
- unit
- causal status

### 3. Model tiers

- **M0 market-only baseline**: market-implied probability / favorite-longshot controls.
- **M1 surface nowcast**: ASOS level/slope/acceleration/high-so-far/time-to-close.
- **M2 forecast residual**: NBM/HRRR + station-season residuals.
- **M3 satellite/radar**: GOES/GLM/MRMS incremental features.
- **M4 text/aviation**: SPECI/TAF/AFD incremental features.

Promote a tier only if it improves untouched OOS economics/calibration over the previous tier.

### 4. Chronological validation

- Train on earlier date blocks.
- Validate thresholds on later blocks.
- Freeze.
- Evaluate untouched final block.
- Bootstrap whole settlement dates, not individual trades.
- Report station-wise and month/season-wise performance.

Minimum promotion gates:
- >= 30 trades and >= 20 dates for exploratory read
- >= 75 trades and >= 50 dates for serious candidate
- >= 100 independent dates preferred for production consideration
- no single station/date cluster dominates P&L
- positive after 300s latency, real fees and depth
- positive under at least one conservative price-floor scenario

### 5. Execution stress grid

Always report:
- latency: 0/5/15/30/60/120/300s
- depth: 1/5/25 contracts plus observed-depth cap
- price floor: 0/0.05/0.10/0.15/0.20
- taker fee exact schedule
- stale-book rejection
- spread widening/slippage scenario
- maker tests only with back-of-queue fill model and 1/5/15m markouts

## Highest-value next experiments

1. **Reproduce/freeze the ~21% causal-settlement result** with a named config and immutable output manifest.
2. **Expand PMXT newest-first**, then backward by month/date, while building compact replay partitions.
3. **ASOS slope/acceleration sweep**: test whether the first flattening or negative dT/dt is more predictive than the existing hard lock gate.
4. **Remaining-heating model**: probability of a new high from current T, high-so-far, time of day, solar/time-to-close, dewpoint, wind and slope.
5. **Latency-value curve**: measure signal P&L at 0/5/15/30/60/120/300s to quantify the economic value of lower-latency data/execution.
6. **Observation-source latency study**: METAR/SPECI vs 1-minute ASOS vs any faster feed; record when each observation becomes available.
7. **NBM baseline + residual calibration**.
8. Add GOES/MRMS/GLM one at a time and keep only incremental OOS improvements.

## Interpretation of latency as an edge

The user's hypothesis is plausible and testable: the exact moment heating stalls, slope turns flat/negative, a wind shift arrives, or cloud/precipitation changes the surface energy balance can move terminal-temperature probability before slower market participants reprice. But the backtest must distinguish:

- **meteorological lead**: our source observes the state change sooner;
- **transport lead**: we receive the same source sooner;
- **compute lead**: faster parsing/feature evaluation;
- **execution lead**: faster order placement/fill;
- **false lead**: a faster noisy reading that official settlement later ignores.

The latency-value curve above is the required proof.

## Immediate implementation order

1. Freeze the 21% configuration and manifest.
2. Patch historical downloader to recent-first ordering.
3. Add a backtest-readiness audit that reports date/station/source/order-book/settlement coverage.
4. Build normalized ASOS feature table with slope + acceleration.
5. Build counterfactual gate analyzer over historical and current forward snapshots.
6. Add NBM/HRRR history.
7. Only then add GOES/MRMS/GLM and text feeds.
