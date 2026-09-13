# Weather Alpha Project Roadmap

_Last updated: 2026-09-12_

## 0. Source of truth and branch discipline

GitHub is the source of truth for the project, not any individual chat.

Current branch / PR stack:

1. `main`
   - baseline Weather Alpha Lab v0.1 only.
2. PR #3 — `codex/weather-alpha-v2`
   - Polymarket US + cross-venue normalization.
3. PR #4 — `codex/weather-backtest-tournament`
   - exhaustive weather strategy tournament.
4. PR #5 — `codex/kalshi-pmxt-history`
   - PMXT historical L2 adapter / replay.
5. PR #6 — `fix/pmxt-archive-endpoint`
   - PMXT archive fixes and safer payload validation.
6. PR #7 — `feature/live-weather-observability`
   - live Kalshi L2 capture, paper markouts, dashboard, Telegram, recent Kalshi cache.
7. PR #8 — `feature/google-contrails-signal`
   - research-only Google Contrails signal, stacked on PR #7.

PR #8 is intentionally stacked on PR #7. It therefore contains PR #7 in its ancestry, but that does **not** mean either PR has been merged to `main`.

Do not merge the whole stack simply because a feature works. Each layer must be validated first.

## 1. Workstream boundaries

### Core weather trading workstream

Primary path:
- `/opt/poly-kalshi-weather-copy-bot`
- core Kalshi / Polymarket-US weather logic;
- paper trading;
- historical validation;
- L2 collection;
- state-transition / terminal-high signals;
- dashboard;
- Telegram;
- eventual execution research.

### Contrail research workstream

Keep lightweight and isolated:
- branch: `feature/google-contrails-signal`
- worktree / deployment path may remain `/opt/weather-contrails`
- contrail-specific outputs should stay under names containing `contrail` where practical.

The contrail module may reuse:
- station/city metadata;
- shared Telegram environment-variable conventions;
- common weather timestamps / location metadata.

It must **not** directly change production trade decisions yet.

The eventual integration contract should be small:

```python
get_contrail_features(station, timestamp) -> {
    "detection_percentile": ...,
    "persistence_percentile": ...,
    "peak_hour_percentile": ...,
    "nearest_detection_km": ...,
    "cfi": ...,
    "formation_probability": ...,
    "expected_energy_forcing": ...,
}
```

Only after untouched out-of-sample validation should those values enter fair-value logic.

## 2. Current empirical baseline

The existing historical control strategy has produced approximately:

- 21–23% ROI in the existing historical test grid;
- 127 physical events;
- 28 independent settlement dates;
- robustness across 0–300 second latency assumptions in the existing test.

Treat this as a **candidate**, not proven alpha.

Status:
- 50 independent dates: not yet reached;
- 100+ preferred dates: not yet reached;
- newest data remains highest priority.

The historical control strategy is frozen until rerun on expanded / more recent data.

## 3. Immediate priority — live paper observability

The highest-value data is live data that cannot be reconstructed perfectly later.

Keep these processes logically separate:

1. `weather-paper`
   - existing production-shaped paper trader;
   - no live order submission;
   - frozen gates unless explicitly versioned.

2. `weather-tourney`
   - synchronized A/B/C forward comparison;
   - preserve current thresholds and state.

3. `weather-diagnostic`
   - D_DIAGNOSTIC_NO_LOCK;
   - deliberately bypasses the control lock;
   - diagnostic only;
   - never production-eligible;
   - reports distance from each control gate.

4. `weather-dash`
   - live weather / market dashboard;
   - contract snapshots;
   - latest L2 state when available.

5. `weather-l2`
   - authenticated, read-only Kalshi WebSocket capture;
   - records `orderbook_snapshot`, `orderbook_delta`, `trade`, and `ticker`;
   - reconstructs YES/NO books;
   - records top-5 depth and executable VWAP;
   - creates 0/5/15/30/60/120/300 second post-signal markouts.

6. `weather-telegram`
   - signal/fill/collector-error alerts only;
   - no order-entry dependency;
   - deduplicated episode alerts.

### Phase-1 acceptance gates

Before calling live observability complete:

- at least one full trading day without silent collector gaps;
- raw L2 files visibly grow hour by hour;
- every signal has a contemporaneous weather state;
- every signal has L2 state or an explicit `NO_L2_BOOK` reason;
- every signal receives all seven latency marks;
- reconnect counts / sequence gaps / stale feed state are visible;
- Telegram survives restart without duplicate signal episodes;
- CPU, memory and disk-growth rates are measured on the DigitalOcean host.

## 4. Live data products

Primary append-only products:

- `/data/weather/live/weather_company_contract_history.jsonl`
- `/data/weather/live/weather_company_paper_signals.jsonl`
- `/data/weather/live/weather_company_paper_fills.jsonl`
- `/data/weather/live/weather_company_tournament_decisions.jsonl`
- `/data/weather/live/weather_company_tournament_signals.jsonl`
- `/data/weather/live/weather_company_tournament_fills.jsonl`
- `/data/weather/live/weather_company_diagnostic_decisions.jsonl`
- `/data/weather/live/weather_company_diagnostic_signals.jsonl`
- `/data/weather/live/weather_company_diagnostic_fills.jsonl`
- `/data/weather/live/kalshi_l2/kalshi_weather_ws_YYYY-MM-DDTHH.jsonl.gz`
- `/data/weather/live/weather_company_latency_markouts.jsonl`

Current-state products:

- `/data/weather/live/weather_company_dashboard.json`
- `/data/weather/live/weather_company_dashboard.csv`
- `/data/weather/live/weather_company_contract_dashboard.csv`
- `/data/weather/live/weather_company_dashboard.txt`
- `/data/weather/live/kalshi_l2_latest.json`
- `/data/weather/live/kalshi_l2_health.json`

Contrail research products should remain separately identifiable, e.g.:

- `/data/weather/live/google_contrails_latest.json`
- `/data/weather/live/stayton_contrail_30d_afternoon.json`
- `/data/weather/live/stayton_contrail_30d_afternoon.csv`
- `/data/weather/live/stayton_contrail_30d_afternoon.log`

## 5. Recent-data program — highest historical priority

Use the newest data first so signals can be manually checked against live/recent charts.

### Kalshi-native history

Use Kalshi itself for:

- public trades;
- recent and archived trades through the appropriate API tiers;
- 1-minute candles;
- market metadata / official settlements.

Priority order:

1. most recent completed dates;
2. August 2026 block already catalogued;
3. extend backward only after recent coverage is secured.

Required outputs:

- exact signal time;
- market price / trade state at signal;
- realistic post-signal executable proxies;
- station weather state;
- settlement result;
- fees;
- P&L;
- by-date / by-city summaries;
- manual-review sheet.

### PMXT

PMXT is an augmentation source, not a project dependency.

Use:

- valid historical bulk Parquet where it exists;
- free-tier hosted historical L2 selectively once credentials are installed;
- targeted windows around actual signals rather than indiscriminate high-cost pulls;
- cached results to avoid repeated credit usage.

Do not treat HTML/index responses as Parquet.

## 6. Terminal-high state-transition signal

Keep this separate from the frozen historical control.

Feature family:

- temperature now;
- high so far;
- minutes since high;
- drop from high;
- slope 1m / 3m / 5m / 10m / 15m / 30m;
- acceleration 3m / 5m / 10m;
- dewpoint level and slope;
- pressure tendency;
- wind speed / direction;
- 5m / 15m wind shifts;
- observation age;
- minutes to settlement end;
- minutes to sunset / solar state.

Core hypothesis:

> Heating velocity collapses, acceleration turns negative, and the market reprices terminal-high probability with measurable delay.

Every state-transition signal should be evaluated at:

- 0s
- 5s
- 15s
- 30s
- 60s
- 120s
- 300s

That latency curve determines whether the edge is primarily:
- better meteorology;
- faster data;
- faster execution;
- or some combination.

## 7. Contrail signal research

PR #8 currently includes:

- Google Contrails API v2 detections;
- CFI;
- persistent-formation probability;
- expected effective energy forcing;
- nominal CoCiP energy forcing;
- temporal detection statistics;
- peak-hour measurements;
- attribution metrics when available;
- city/station registry;
- Telegram research alerts;
- 30-day baseline ranking;
- retry/backoff/checkpoint support for long runs.

Current interpretation rules:

- CFI is **not** surface-temperature degrees;
- energy forcing is **not** a direct temperature adjustment;
- raw detection count / line length is not itself a trading signal;
- alert thresholds are exploratory.

### Contrail near-term tasks

1. Finish / verify the Stayton 30-day afternoon baseline.
2. Confirm resume/checkpoint behavior over flaky Google responses.
3. Archive contrail features by city and timestamp.
4. Join contrail observations to:
   - pre-event model forecast;
   - observed station temperature path;
   - official daily high;
   - cloud / solar variables;
   - contemporaneous market prices.
5. Estimate:
   `official_high_F - pre_event_model_high_F = f(contrail_features, cloud_features, regime)`
6. Test incremental forecast-error reduction.
7. Test incremental executable trading economics on untouched dates.
8. Only then expose the small feature interface to the core weather model.

## 8. Incremental weather-source edge program

Add one family at a time against the same frozen date blocks:

1. ASOS state-transition features.
2. Nearby-station spatial progression.
3. Dewpoint / pressure / wind / SPECI.
4. NBM / HRRR forecast error and revisions.
5. GOES visible / IR cloud shielding.
6. Contrail features.
7. MRMS / NEXRAD precipitation and outflow.
8. GLM lightning jump / convective initiation.
9. RTMA / RU and other high-frequency analysis products.

A source survives only if it improves untouched out-of-sample economics **after its real publication latency**.

Do not build one giant model before individual sources have demonstrated incremental value.

## 9. Execution research — later

Only after signal quality is demonstrated:

- taker versus maker;
- queue-priority / back-of-queue simulation;
- adverse-selection markouts;
- top-5 depth / VWAP sizing;
- fractional Kelly caps;
- cross-venue Kalshi / Polymarket-US comparison where settlement definitions truly match.

Do not prioritize Rust / DPDK / microsecond engineering until measured latency curves prove the edge disappears fast enough to justify it.

## 10. Validation framework

Maintain three distinct datasets:

1. long-history development;
2. recent manual-validation block;
3. newest untouched final holdout.

Minimum standards:

- 50 independent settlement dates for a serious candidate;
- 100+ preferred;
- date-block / walk-forward validation;
- no random train/test split;
- exact causal timestamps;
- actual fees;
- realistic latency;
- no lookahead weather data;
- missing/stale-feed kill switches;
- one-trade-per-event rules where applicable.

## 11. Recommended sequencing from today

### Now

1. Verify what live processes are actually running on the server today.
2. Start / validate full Kalshi L2 recording.
3. Start D diagnostic track.
4. Start Telegram paper alerts.
5. Confirm dashboard includes L2 state.
6. Let A/B/C continue untouched.

### Next

7. Pull the most recent Kalshi-native trades and 1-minute candles.
8. Build the recent trade-by-trade manual-review report.
9. Rerun the frozen historical control on recent OOS data.
10. Complete the temperature state-transition feature family.

### In parallel

11. Let contrail baselines / scans run as a lightweight sidecar.
12. Archive contrail features, but do not let them alter trading decisions.
13. Add PMXT free-tier targeted L2 once credentials are ready.

### After evidence accumulates

14. Compare control vs state-transition vs incremental-source models.
15. Keep only source families with genuine OOS improvement.
16. Evaluate maker / queue / execution strategies.
17. Promote only validated components toward production.

## 12. Merge policy

Do not merge PR #8 merely because the contrail scanner works.

Preferred path:

- validate PR #7 live observability first;
- validate PR #8 as a research sidecar;
- resolve any branch stacking cleanly;
- merge in logical layers only after tests and live acceptance checks;
- keep contrail-to-trading integration behind an explicit research gate.

The objective is a weather-trading system that can explain **why** a signal existed, what information created it, how fast the market reacted, and whether the edge survived realistic execution — not just a collection of interesting weather indicators.
