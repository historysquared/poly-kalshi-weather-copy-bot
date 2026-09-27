# Weather Alpha Signal Research Pipeline

## Purpose

All active weather strategies should pass through one research contract instead of creating bespoke signal/fill accounting. The canonical engine is `weather_alpha.engine`; strategy-specific scripts may retain their existing JSONL outputs for operator continuity, but canonical evaluations, signals, fills, settlements and scorecards live in the shared research store.

## Canonical flow

```text
causal source data + executable market snapshot
        -> registered feature payload
        -> registered signal/strategy evaluation
        -> ModelEvaluation (including abstentions and reason)
        -> Signal when emitted
        -> delayed/executable paper fill
        -> forward markouts when L2 is available
        -> official settlement
        -> reconciled paper P&L
        -> economic scorecard
```

No signal may silently substitute placeholder data for a missing dependency. Missing registered inputs fail closed and are recorded as an evaluation with a reason.

## Executable registry

`weather_alpha/research/registry.py` contains the code-level feature and signal registry. It is deliberately smaller than the 153-item idea inventory in `WEATHER_ALPHA_MASTER_STRATEGY_REGISTRY.md`: only signals with actual code/data plumbing should enter the executable registry.

Current registered strategy IDs include:

- `settlement_basis_causal_v1`
- `weather_company_terminal_high_v1`
- `weather_company_terminal_high_A_control_v1`
- `weather_company_terminal_high_B_moderate_v1`
- `weather_company_terminal_high_C_exploratory_v1`
- `weather_company_terminal_high_D_diagnostic_v1`
- `nbm_bucket_baseline_v1`
- `nbm_station_bias_v1`
- `contrail_q90_prior_evening_v1`

Every definition records its feature dependencies, family, stage, venue scope and whether live order submission is permitted. All current registry entries default to no live-order enablement.


## Full master-strategy tracking

`scripts/build_strategy_status_report.py` parses the complete `WEATHER_ALPHA_MASTER_STRATEGY_REGISTRY.md` inventory and joins each master hypothesis to executable signal IDs through `SignalDefinition.master_ids`. Unimplemented items remain `IDEA`; implemented historical/paper items inherit their real research stage. The report deliberately distinguishes the broad 153-item idea inventory from code that can actually run.

Current outputs:

- `/data/weather/live/weather_strategy_inventory.json`
- `/data/weather/live/weather_strategy_inventory.md`

The report must include every master ID. A count other than the current registry count is a parser/inventory failure, not permission to silently drop rows.

## NBM station residual correction

`weather_alpha/research/forecast_residuals.py` implements prior-only rolling residual calibration for F04. For each city/station/date it uses only earlier resolved dates inside the configured lookback window and records history count, mean/median residual, historical MAE, adjusted MaxT, and current raw/adjusted error. No current/future settlement value is allowed into the adjustment.

`weather_alpha/research/nbm.py` exposes the exact Kalshi integer-bucket probability mapping plus `NbmBucketStrategy`. `nbm_station_bias_v1` fails closed until its minimum prior-history requirement is satisfied. It remains historical/research only and does not enable live orders.

Build the residual history with:

```bash
PYTHONPATH=. python scripts/build_nbm_residual_calibration.py \
  --nbm /data/weather/normalized/forecasts/nbm_station_high_history_120d.json \
  --buckets /data/weather/normalized/markets/kalshi_weather_bucket_history_120d.json
```

## Research runner

`weather_alpha/research/runner.py` validates a strategy against the executable registry before evaluation. It:

1. verifies the strategy ID exists and is enabled;
2. verifies venue compatibility;
3. verifies all registered feature dependencies are present;
4. records fail-closed missing-feature evaluations;
5. records strategy exceptions in `strategy_errors`;
6. persists canonical `ModelEvaluation` and `Signal` objects.

This reuses the existing `StateStore`, `MarketSnapshot`, `ModelEvaluation`, `Signal`, `SimulatedFill`, `Settlement` and `ForwardMark` engine primitives rather than creating another data model.

## Weather Company compatibility bridge

The existing Weather Company forward scripts remain useful and are intentionally not rewritten in one risky cutover. `weather_alpha/research/weather_company.py` is the compatibility bridge.

The bridge now records the main paper runner plus A/B/C tournament and D diagnostic tracks into the same SQLite research store while preserving the established JSONL files and state files. Signal IDs are deterministic so the same legacy signal maps to the same canonical signal identifier.

Canonical live research DB:

`/data/weather/live/weather_research.sqlite3`

Legacy paper history can be imported with:

```bash
PYTHONPATH=. python scripts/migrate_weather_company_jsonl_to_research_store.py
```

The migration covers main paper, A/B/C tournament and D diagnostic history.

## Unified economic scorecard

`weather_alpha/research/scorecard.py` and `scripts/build_unified_signal_scorecard.py` produce one consistent operational/economic scorecard per strategy.

Active metrics are intentionally limited to:

- model evaluations
- emitted signals
- simulated fills
- settled fills
- wins / losses / win rate
- net P&L
- capital at risk
- ROI
- independent settlement dates
- station count
- maximum drawdown
- forward executable markouts by horizon when available
- validation verdict and reason from the existing date-block validation layer

The scorecard does not promote a strategy merely because a point estimate is positive. Sample-size requirements and whole-date uncertainty remain part of the validation layer.

Current live outputs:

- `/data/weather/live/weather_signal_scorecard.json`
- `/data/weather/live/weather_signal_scorecard.md`

The unified launcher refreshes the scorecard every minute.

## Historical strategies

Historical economic studies use the same scorecard object through `score_event_returns`. The existing causal settlement backtest can be summarized with:

```bash
PYTHONPATH=. python scripts/build_historical_settlement_scorecard.py
```

The default frozen comparison is the existing 300-second latency, depth-5, no-price-floor causal settlement configuration. The historical backtest still uses its established PMXT/CLI causal replay; this script standardizes the result presentation rather than rewriting the validated replay engine.

## Live paper services

The launcher now manages these research services idempotently:

- `weather-paper`
- `weather-score`
- `weather-dash`
- `weather-tourney`
- `weather-tourney-score`
- `weather-diagnostic`
- `weather-diagnostic-score`
- `weather-scorecard`
- `weather-l2` when Kalshi read credentials exist
- `weather-telegram` when Telegram credentials exist

The A/B/C/D tracks now persist into the canonical research DB in addition to their existing JSONL audit streams.

## Strategy promotion

Use the existing promotion ladder:

`IDEA -> HISTORICAL -> SHADOW -> PAPER -> CANDIDATE`

`PAUSED` and `REJECTED` remain explicit terminal/research states. Promotion continues to require causal timestamps, exact settlement mapping, executable prices, costs, latency stress, independent-date counts and forward confirmation.

## What remains separate by design

The idea inventory remains broader than executable code. A provider being implemented does not mean its signal is validated. GOES, HRRR, radar, RTMA and text providers should enter the executable registry only when a concrete signal definition and causal historical/live path exists.

Polymarket US execution also remains venue-specific and must not be treated as implemented until authenticated schemas and settlement semantics are validated.
