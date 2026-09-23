# Weather Alpha Project Roadmap

_Last updated: 2026-09-12_

## Project definition

This repository is one project: a weather prediction-market research and trading system spanning Kalshi, Polymarket US, live paper trading, historical replay, market microstructure, meteorological state-transition signals, contrail/cloud features, dashboards, Telegram alerts, and eventual execution research.

GitHub is the source of truth across chats.

Contrails are now a **first-class project signal family**, not a separate project. They remain research-gated until they prove incremental out-of-sample value.

## Current branch / PR stack

1. `main`
   - Weather Alpha Lab v0.1 baseline.
2. PR #3 — `codex/weather-alpha-v2`
   - Polymarket US + cross-venue normalization.
3. PR #4 — `codex/weather-backtest-tournament`
   - weather strategy tournament / validation framework.
4. PR #5 — `codex/kalshi-pmxt-history`
   - PMXT historical L2 replay.
5. PR #6 — `fix/pmxt-archive-endpoint`
   - PMXT archive hardening and payload validation.
6. PR #7 — `feature/live-weather-observability`
   - live Kalshi L2 capture, paper markouts, dashboard, Telegram, recent Kalshi cache.
7. PR #8 — `feature/google-contrails-signal`
   - Google Contrails signal family, currently stacked on PR #7.

PR #8 being stacked on PR #7 does not mean either PR is merged to main.

## Current empirical baseline

The frozen historical control has produced roughly:

- 21–23% ROI in the current historical grid;
- 127 physical events;
- 28 independent settlement dates;
- apparent robustness across 0–300 second latency assumptions.

Treat this as a candidate, not proven alpha.

Targets:
- 50 independent settlement dates minimum for a serious candidate;
- 100+ preferred;
- newest data has priority over adding more old data.

## Phase 1 — live observability and paper trading

Highest priority: collect live data that cannot be reconstructed perfectly later.

Required live components:

- `weather-paper`
  - production-shaped paper trader;
  - no live orders.

- `weather-tourney`
  - frozen A/B/C forward comparison.

- `weather-diagnostic`
  - D diagnostic track;
  - bypasses control lock only for diagnosis;
  - reports distance from each gate.

- `weather-dash`
  - live weather + market dashboard.

- `weather-l2`
  - authenticated, read-only Kalshi WebSocket collector;
  - `orderbook_snapshot`, `orderbook_delta`, `trade`, `ticker`;
  - reconstruct YES/NO books;
  - top-5 depth;
  - executable VWAP;
  - 0/5/15/30/60/120/300 second markouts.

- `weather-telegram`
  - signal, fill and collector-health alerts;
  - no execution dependency.

Acceptance gates:

- one full trading day without silent collector gaps;
- L2 archive files grow continuously;
- every signal has contemporaneous weather state;
- every signal has L2 state or explicit `NO_L2_BOOK`;
- all seven latency marks are produced;
- reconnects, sequence gaps and stale-feed state are observable;
- Telegram survives restart without duplicate signal episodes;
- CPU, memory and disk growth are measured.

## Phase 2 — recent Kalshi-native history

Most recent completed dates first.

Use Kalshi directly for:

- public trades;
- archived trades;
- 1-minute candles;
- official market metadata and settlements.

Required outputs:

- signal time;
- weather state;
- actual market prints / price proxy;
- post-signal price path;
- settlement result;
- fees;
- P&L;
- by-date / by-city performance;
- trade-by-trade manual review.

Then rerun the frozen 21–23% control **unchanged** on recent OOS data.

## Phase 3 — PMXT augmentation

PMXT is supplemental, not a hard dependency.

Use:

- valid bulk historical Parquet where available;
- hosted historical L2 selectively on free tier;
- targeted windows around real signals;
- cached responses to avoid repeated credits.

Never accept HTML/index payloads as Parquet.

## Phase 4 — terminal-high state-transition signal

Keep separate from the frozen historical control.

Feature family:

- current temperature;
- high so far;
- minutes since high;
- drop from high;
- slopes: 1m / 3m / 5m / 10m / 15m / 30m;
- acceleration: 3m / 5m / 10m;
- dewpoint level / slope;
- pressure tendency;
- wind speed / direction;
- 5m / 15m wind shifts;
- observation age;
- minutes to settlement end;
- minutes to sunset / solar state.

Core hypothesis:

> Heating velocity stalls or reverses before the market fully reprices terminal-high probability.

Evaluate every signal at:

- 0s
- 5s
- 15s
- 30s
- 60s
- 120s
- 300s

This determines whether the edge is primarily:
- meteorological;
- data-latency;
- execution-latency;
- or mixed.

## Phase 5 — contrail signal family

Contrails are part of the project now.

Current capability in PR #8:

- Google Contrails API v2 detections;
- CFI;
- persistent-formation probability;
- expected effective energy forcing;
- nominal CoCiP forcing;
- temporal detection statistics;
- peak-hour measurements;
- attribution metrics where available;
- city / settlement-station registry;
- Telegram research alerts;
- 30-day baseline ranking;
- retry / backoff / checkpoint support.

Interpretation rules:

- CFI is not surface-temperature degrees;
- forcing is not a direct temperature adjustment;
- detection count or line length alone is not alpha;
- alert thresholds are exploratory.

Near-term tasks:

1. finish / verify the Stayton 30-day baseline;
2. archive contrail features by station and timestamp;
3. join them to pre-event forecast, observed temperature path, cloud / solar state, market prices and final high;
4. model:
   `official_high_F - pre_event_model_high_F = f(contrail_features, cloud_features, regime)`
5. test forecast-error improvement;
6. test executable trading improvement on untouched dates;
7. if validated, expose contrail features directly to the shared project feature pipeline.

Contrails should eventually be a normal project feature provider, not a separate deployment concept.

## Phase 6 — incremental weather-source program

Add one signal family at a time against the same frozen date blocks:

1. ASOS state transition;
2. nearby-station spatial progression;
3. dewpoint / pressure / wind / SPECI;
4. NBM / HRRR revisions and forecast error;
5. GOES visible / IR cloud shielding;
6. contrail features;
7. MRMS / NEXRAD precipitation / outflow;
8. GLM lightning jump / convective initiation;
9. RTMA / RU and other high-frequency analyses.

A source survives only if it improves untouched OOS economics after its actual publication latency.

Do not combine everything into one large model before each family earns its place.

## Phase 7 — cross-venue research

Where settlement semantics genuinely match:

- Kalshi vs Polymarket US fair value;
- cross-venue dislocations;
- station mapping verification;
- latency-adjusted executable spread;
- same-outcome settlement consistency.

Never assume two similarly named weather contracts settle identically.

## Phase 8 — execution research

Only after signal quality is established:

- taker vs maker;
- queue-priority / back-of-queue simulation;
- adverse-selection markouts;
- top-5 depth / VWAP sizing;
- fractional Kelly caps;
- position and event limits;
- execution-speed optimization.

Do not prioritize Rust / DPDK / microsecond work until the measured latency curve proves it matters.

## Validation framework

Maintain three distinct datasets:

1. long-history development;
2. recent manual-validation block;
3. newest untouched final holdout.

Standards:

- date-block / walk-forward validation;
- no random train/test split;
- exact causal timestamps;
- actual fees;
- realistic latency;
- no future weather leakage;
- stale / missing-feed kill switches;
- one-trade-per-event controls where applicable.

## Immediate sequencing

### Now

1. verify what live processes are actually running;
2. start / validate full Kalshi L2 collection;
3. start D diagnostic;
4. start Telegram;
5. confirm dashboard L2 integration;
6. leave A/B/C untouched.

### Next

7. pull newest Kalshi trades and 1-minute candles;
8. generate recent manual-review trades;
9. rerun frozen control on recent OOS;
10. complete state-transition signal family.

### In parallel

11. finish contrail baseline / feature collection;
12. archive contrail features with the same station/timestamp conventions;
13. add PMXT targeted recent L2 where useful.

### After evidence accumulates

14. compare control vs state-transition vs contrail vs other source families;
15. retain only genuine incremental OOS edges;
16. evaluate execution strategies;
17. promote validated components toward live trading.

## Merge policy

Project features can live on separate branches during development, but the project roadmap itself belongs at project level.

Preferred sequence:

- merge documentation / roadmap independently;
- validate PR #7 live observability;
- validate PR #8 contrail signal family;
- merge functional layers in dependency order;
- keep any trade-impacting feature behind an explicit research gate until OOS validation is complete.

The target system should explain:

- why a signal existed;
- which weather information created it;
- how fast the market reacted;
- whether the edge survived real execution;
- and whether each added feature family genuinely improved the result.
