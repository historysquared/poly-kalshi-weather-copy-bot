# April-August Weather Backtest Execution Plan

## Objective

Run the weather research stack over historical Polymarket order books from pmxt v2 across many independent settlement dates, then add weather observations one layer at a time and retain only signals that improve untouched walk-forward performance after realistic latency and execution costs.

This project remains weather-only. BTC/crypto repositories may be inspected for generic engineering patterns only and are not runtime dependencies or data sources for weather research.

## Phase 0 — Research gates

A strategy may not be promoted from research unless it clears all of the following on the designated out-of-sample segment:

- at least 30 independent resolved events;
- at least 20 independent settlement dates;
- at least 3 settlement stations;
- positive net P&L after executable ask prices, fee reserve, and slippage assumptions;
- positive mean event ROI;
- positive 95% settlement-date block-bootstrap lower confidence bound for ROI;
- no catastrophic concentration in one station/date/meteorological regime;
- positive results at realistic latency, not only zero-latency;
- no use of data published after the simulated decision timestamp.

Verdicts:

- `KEEP`: clears all research gates on untouched OOS data.
- `PROVISIONAL`: positive point estimate but uncertainty/coverage gate not cleared.
- `KILL`: negative economics or loses edge under realistic latency/execution.
- `INSUFFICIENT_DATA`: sample-size/coverage gate not cleared.

## Phase 1 — Blind NO baseline

### Historical period

Use pmxt v2 coverage from 2026-04-13 onward. Build a broad panel through 2026-08-31 where archive hours exist.

### Batch design

Process the archive in week-sized partitions. Keep raw pmxt files ephemeral; store only normalized weather rows under the weather repository/cache hierarchy.

Target batches:

- Apr 13-19
- Apr 20-26
- Apr 27-May 3
- May 4-10
- May 11-17
- May 18-24
- May 25-31
- Jun 1-7
- Jun 8-14
- Jun 15-21
- Jun 22-28
- Jun 29-Jul 5
- Jul 6-12
- Jul 13-19
- Jul 20-26
- Jul 27-Aug 2
- Aug 3-9
- Aug 10-16
- Aug 17-23
- Aug 24-31

### Parameter grid

Blind NO minimum entry thresholds:

`80, 85, 90, 92, 94, 95, 96, 97, 98` cents.

Latency grid:

`0, 30, 60, 120, 300, 600, 900` seconds.

Execution cases:

1. best executable ask, one share;
2. best ask plus one tick/adverse slippage reserve;
3. depth-aware 5-share VWAP where depth exists;
4. depth-aware 25-share VWAP where depth exists.

### Freeze rule

After the full April-August baseline run, freeze only thresholds that clear the research gates. Do not optimize thresholds again on the later weather-feature OOS period.

## Phase 2 — ASOS increment

Join only observations whose publication/receipt time is known to be available at the simulated timestamp.

Features:

- current temperature;
- daily high so far;
- 5/10/15/30-minute dT/dt;
- pressure tendency;
- wind shift;
- dew point;
- precipitation/SPECI state where available;
- minutes since latest observation;
- distance from current temperature to contract bucket.

Compare ASOS-filtered strategies against exactly the same market/event observations used by the frozen blind-NO baseline.

Promotion criterion: incremental OOS P&L/ROI and/or drawdown improvement after latency, not merely a better in-sample hit rate.

## Phase 3 — HRRR increment

Build an as-of HRRR vintage store at the settlement station coordinate. Required fields initially:

- 2 m temperature;
- total cloud cover;
- downward shortwave radiation;
- forecast run/vintage time;
- lead time;
- forecast daily high implied by available hourly path.

Derived features:

- observed temperature minus HRRR temperature;
- HRRR expected remaining heating;
- forecast revision magnitude;
- forecast error conditioned on station/time-of-day;
- ASOS + HRRR residual estimate.

Again compare to the exact frozen baseline observation set.

## Phase 4 — ASOS + HRRR combined model

Train/calibrate on the earlier chronological segment and evaluate on untouched later dates.

Outputs:

- calibrated probability for each temperature bucket;
- probability daily high has already occurred;
- expected final-high residual vs HRRR;
- fair YES/NO probabilities;
- net executable edge after costs.

Candidate strategies:

- model-filtered NO;
- late impossible/near-impossible bucket NO;
- best single YES;
- adjacent YES basket;
- forecast-revision strategy;
- observation-shock strategy.

## Phase 5 — Expensive incremental data sources

Only proceed source-by-source. Each source must beat the frozen ASOS+HRRR comparator on untouched dates after its real publication latency.

Priority order:

1. GOES cloud/solar features;
2. MRMS/NEXRAD convective cooling;
3. GLM lightning initiation;
4. RTMA-RU rapid analysis;
5. AFD forecaster-intent text.

A source is killed if it fails to add net OOS value after realistic latency, even if it improves meteorological forecast accuracy.

## Walk-forward protocol

Use chronological folds. No random train/test split.

Initial default:

- research/train: Apr 13-Jun 30;
- validation: Jul 1-Jul 31;
- untouched OOS: Aug 1-Aug 31.

Also report expanding-window folds when coverage allows:

- train through May -> test Jun;
- train through Jun -> test Jul;
- train through Jul -> test Aug.

Station holdouts should be run after the basic chronological result is stable.

## Required reports

For every strategy/parameter/latency/execution case:

- events;
- settlement dates;
- stations;
- net P&L;
- mean event ROI;
- win rate;
- profit factor;
- max drawdown;
- daily/event Sharpe-like statistic;
- 95% settlement-date block-bootstrap CI;
- performance by station;
- performance by month;
- performance by local time-to-settlement;
- performance by entry-price band;
- performance by liquidity/depth;
- capacity at 1/5/25 shares;
- verdict and reason.

## Execution order

1. Build April-August weather-only pmxt cache in weekly partitions.
2. Run Blind NO grid across all partitions.
3. Aggregate and apply 30-event / 20-date gates.
4. Freeze surviving thresholds.
5. Build ASOS historical feature cache.
6. Run ASOS incremental tournament on same observations.
7. Build HRRR vintage feature cache.
8. Run HRRR and ASOS+HRRR incremental tournament.
9. Freeze the ASOS+HRRR comparator.
10. Add GOES, radar/MRMS, GLM, RTMA-RU, AFD one at a time.
11. Kill any source/strategy that does not improve untouched walk-forward economics after realistic latency.
