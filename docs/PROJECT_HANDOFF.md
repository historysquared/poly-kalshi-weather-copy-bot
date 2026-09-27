# Weather Alpha Project Handoff

_Last updated: 2026-09-26_

## Purpose

This is the durable handoff for the Weather Alpha project. GitHub `main` is the source of truth for code; `/data/weather` on `weather-research` is the source of truth for live archives and generated research outputs. No secrets belong in the repository.

## 2026-09-26 audit update

The current detailed audit is `docs/PROJECT_AUDIT_2026-09-26.md`. The unified branch has 132 passing tests after the collector-first hardening pass.

Important corrections/current state:

- strict Kalshi `<` / `>` tail semantics are now enforced; the corrected historical settlement strategy is about 19.1% ROI at 300-second latency/depth-5 over 28 independent dates, not the earlier ~22% estimate;
- all 30 corrected settlement robustness cells remain `ROBUST_PASS`, but 28 dates are below the 50-date promotion gate;
- empirical remaining-heating economics are strongly positive in the current sample but only 21-22 independent dates, so the strategy is `PROVISIONAL`;
- the canonical research store now contains migrated main/A/B/C/D forward history and drives the live economic scorecard;
- main Weather Company terminal-high forward paper remains negative (-60.98% ROI over 29 settled fills / 13 dates);
- live surface retrieval now has bounded 429 retry plus transparent fresh-cache fallback;
- Kalshi L2 is live under systemd across all 20 configured daily-high series; Telegram remains blocked by absent credentials;
- the unified branch must still be published through GitHub and merged before `main` becomes the code source of truth for these audit changes.

## Repository state

- PR #10 (`Consolidate Weather Alpha project into main`) merged successfully on 2026-09-23.
- Main merge commit: `c7325b0a29937cfdfe125a21490c5dca48ebcf61`.
- Current PR #10 head CI passed before merge: Weather Alpha CI, 106 unit tests plus compile checks.
- The old stacked PRs #4-#9 were closed as superseded after consolidation; PR #3 was already recognized as merged through ancestry.
- Contrails are a first-class research signal family but remain research-gated.
- No live-order path was enabled by the NBM/contrail work.

## Parallel deployment

Unified main is deployed in parallel at:

`/opt/weather-alpha-unified-deploy`

The deployment worktree is pinned to merged `main`, not to the older live checkout. A dedicated venv was created there and populated with the project's runtime/test dependencies. Local validation on the unified worktree:

- `python -m compileall weather_alpha scripts` — PASS
- `pytest -q` — current unified pipeline branch: `110 passed`
- NBM one-day smoke — 21/21 configured stations, 0 errors
- exact Kalshi bucket one-day smoke — 20/20 city events, 0 invalid ladders
- Google Contrails one-day smoke — 21/21 configured locations, 0 errors
- rolling-q90 study smoke — completed and produced an auditable JSON result
- current research panel smoke — completed across all 21 configured locations

Healthy long-running services from the older checkout were deliberately not restarted or replaced:

- `weather-paper`
- `weather-score`
- `weather-dash`
- `weather-tourney`
- `weather-tourney-score`

They continue writing current data under `/data/weather/live`.

## Live services added during consolidation deploy

`weather-diagnostic` is now running from `/opt/weather-alpha-unified-deploy` in tmux. It is explicitly diagnostic/paper-only, bypasses the production lock only for measurement, and sets `live_order_submission=false` / `production_eligible=false`.

First verified cycle produced a real diagnostic signal for `KXHIGHDEN-26SEP23` and began delayed-fill tracking; the control failure was recorded rather than hidden.

Two requested services remain blocked by missing local credentials on `weather-research`:

- `weather-telegram` requires `TELEGRAM_BOT_TOKEN` plus `TELEGRAM_CHAT_ID`.

Kalshi credentials are now installed locally with restrictive permissions and are not committed. `weather-collector-kalshi-l2.service` is enabled and active. The dedicated archive collector tracks 20 recurring daily-high series / 240 currently open contracts and writes append-only raw gzip records under `/data/weather/raw/kalshi_l2`.

Once those credentials are restored locally, run the unified stack launcher with:

`ROOT=/opt/weather-alpha-unified-deploy WEATHER_LIVE_ENV=/root/.config/weather-alpha/live.env bash scripts/start_weather_live_stack.sh`

The launcher is idempotent: it leaves already-running tmux services alone and starts only missing components whose prerequisites exist.

## Current architecture

The project now contains:

- Kalshi market discovery, exact settlement mapping, bucket reconstruction, paper trading and research execution models
- Polymarket US normalization / cross-venue research adapters
- Weather Company terminal-high paper and A/B/C tournament stack
- D diagnostic lock-bypass measurement track
- historical CLI / ASOS settlement reconstruction
- read-only authenticated Kalshi L2 recorder and 0/5/15/30/60/120/300-second markout engine
- NBM primary forecast baseline and HRRR incremental research
- Google Contrails observation / persistence research
- dashboard and Telegram observability
- historical replay, parameter tournaments and OOS validation tooling

## Unified signal research pipeline

A canonical executable research layer is now built on top of the existing generic engine:

- `weather_alpha/research/registry.py` — code-level `FeatureDefinition` / `SignalDefinition` registry
- `weather_alpha/research/runner.py` — fail-closed registered strategy runner
- `weather_alpha/research/scorecard.py` — unified economic/operational scorecard
- `weather_alpha/research/weather_company.py` — compatibility bridge from the established Weather Company JSONL runners into canonical engine state
- `scripts/migrate_weather_company_jsonl_to_research_store.py` — imports existing main paper, A/B/C tournament and D diagnostic history
- `scripts/build_unified_signal_scorecard.py` — current forward scorecard
- `scripts/build_historical_settlement_scorecard.py` — canonical scorecard for the frozen historical causal-settlement configuration

The duplicate root-level `Signal` model was removed; simple scan code now emits `weather_alpha.engine.models.Signal`. The unused legacy root-level `weather_alpha/paper.py` portfolio was removed. Existing live JSONL files remain for continuity, but new main/A/B/C/D evaluations, signals, fills and settlements are also written to `/data/weather/live/weather_research.sqlite3`.

The active scorecard is deliberately economic/operational for this phase: evaluations, signals, fills, settlement outcomes, P&L, ROI, independent dates, stations, drawdown, execution markouts and validation status.

`docs/SIGNAL_RESEARCH_PIPELINE.md` is the durable design reference.

## NBM + exact bucket + contrail research tooling

Durable code added during the Sep 22-23 research pass:

- `weather_alpha/providers/nbm.py`
  - NOAA NBM NBS station-text archive parser
  - 00Z station MaxT and XND uncertainty extraction
- `scripts/build_nbm_station_high_history.py`
  - reproducible archived NBM station-high history
- `scripts/build_kalshi_weather_bucket_history.py`
  - exact resolved Kalshi daily-high ladders
  - final `expiration_value`
  - exact winning contract
  - requires one settlement value and one YES winner per event
- `scripts/build_contrail_hourly_history.py`
  - hourly Google Contrails detections by local city/station clock
  - 150 km default research radius
  - segmented API windows to remain inside duration constraints
- `scripts/study_contrail_q90_bucket_edge.py`
  - rolling city-specific q90 test
  - each city's q90 uses only that city's own earlier observations
  - walk-forward adjustment uses only prior eligible data
  - exact Kalshi winner comparison
- `scripts/build_weather_today_research_panel.py`
  - current contrails
  - matched-window percentile
  - prior-evening q90 flag
  - NBM MaxT / uncertainty
  - current Kalshi bucket probabilities

## Contrail sanity constraint

The initial resolved sample is small:

- 29 calendar days: 2026-08-23 through 2026-09-20
- 20 market cities
- the earlier fixed split used 20 training days and 9 OOS days
- 179 usable OOS city-days
- the earlier fixed q90 experiment produced 19 extreme city-days on only 7 independent calendar dates

Nineteen city-days are not nineteen independent days. A q90 estimated from only 20 historical observations per city is fragile. The durable study script therefore uses rolling city-specific thresholds and reports independent dates explicitly.

The next serious validation requires at least 90-180 preceding days per city where available, date-block/walk-forward OOS evaluation, and no future observations in threshold or effect estimation.

## Current research hypothesis

Primary question:

> Conditional on the pre-day NBM forecast distribution, does unusually high contrail exposure for the same city shift the final settlement distribution enough to improve exact Kalshi bucket probabilities?

Rules:

- daytime and nighttime contrails are modeled separately when evidence supports it
- late-day contrails are retained because they may affect the current evening and following day
- collection runs through the whole day
- a trading snapshot may only use observations available by that decision time
- raw detection count is not a temperature adjustment
- contrails must prove incremental OOS value beyond NBM and causal ASOS state before promotion

## Representative generated research outputs

Current files under `/data/weather/live` include:

- `contrail_all_cities_hourly_20260823_20260920.json`
- `nbm00z_station_max_20260823_20260920.json`
- `kalshi_bucket_structure_20260823_20260920.json`
- `top90_contrail_bucket_test.json`
- `contrail_nbm_rolling_snapshot_results.json`
- `today_contrails_nbm_panel.json`
- `today_kalshi_nbm_bucket_projection.json`

These are generated data, not source code. Rebuild them with the committed scripts instead of relying on temporary `/tmp` programs.

## Validation requirements

Every alpha family must preserve:

1. long-history development data
2. recent manual-validation block
3. newest untouched final holdout

Required controls:

- date-block / walk-forward validation
- causal source and receipt timestamps
- no future weather leakage
- exact venue settlement semantics
- actual fees
- executable bid/ask or VWAP rather than midpoint fills
- latency stress
- independent-date counts alongside city-day counts
- stale / missing feed fail-closed behavior

## Planned alpha families

Priority families remain:

- settlement mechanics: CLI/public-feed basis, hidden extrema, rounding, LST/DST windows
- NBM calibrated bracket probabilities and station/season residuals
- NBM + causal ASOS residual update
- terminal-high state transition: high-so-far, dT/dt, d2T/dt2, minutes since high, remaining heating
- dewpoint / pressure / wind / SPECI / sea-breeze / frontal transitions
- NBM release absorption, HRRR revisions and cross-model disagreement
- GOES visible/IR cloud shielding
- contrail exposure and persistence
- GLM lightning and MRMS precipitation/outflow timing
- RTMA/RU rapid surface analysis
- AFD/TAF/SPECI text/revision signals
- maker favorite-longshot, queue/adverse-selection and scheduled-release pickoff protection
- exact-equivalent or explicit-basis Kalshi vs Polymarket US relative value

NBM remains the primary forecast baseline. HRRR, contrails, satellite, radar and text features must prove incremental OOS value before promotion.

## Immediate next actions

1. Restore Kalshi read-only API credentials locally and start `weather-l2`; verify raw gzip growth, sequence health, reconnects and markouts.
2. Restore Telegram credentials locally, send one test alert, then start `weather-telegram`.
3. Leave healthy paper/tournament/dashboard processes untouched until a service-by-service cutover is justified.
4. Extend NBM, exact bucket and hourly contrail history to at least 90-180 days.
5. Rerun rolling city-specific q90 walk-forward analysis with independent-date accounting.
6. Add causal ASOS residual features and test whether contrails add value beyond NBM+ASOS.
7. Rerun the frozen historical control unchanged on newest Kalshi-native OOS data.
8. Only after signal quality is established, promote execution/queue/market-making research.

## Deployment rule

Never replace a healthy long-running process in place merely because new code exists. Validate in the parallel unified worktree first, then migrate one service at a time with output continuity and health checks confirmed before stopping the old process.
