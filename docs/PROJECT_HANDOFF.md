# Weather Alpha Project Handoff

_Last updated: 2026-09-23_

## Purpose

This file is the durable handoff for the Weather Alpha project. It records the current architecture, empirical state, live deployment, incomplete work, validation constraints, and immediate next actions. GitHub is the source of truth for code; `/data/weather` is the source of truth for server-side research outputs and live archives.

## Repository / consolidation state

- Consolidation branch: `integration/weather-alpha-unified`.
- Consolidation PR: #10, `Consolidate Weather Alpha project into main`.
- PR #10 includes the earlier stacked Weather Alpha work plus the contrail research family.
- Contrails are a first-class research signal family but remain research-gated.
- No live-order path is enabled by the contrail/NBM research work.

## Core architecture

The project contains:

- Kalshi market discovery, settlement mapping, paper trading and research execution models.
- Polymarket US normalization and cross-venue research adapters.
- Weather Company terminal-high paper/tournament stack.
- Historical settlement reconstruction and ASOS/CLI studies.
- Kalshi L2 recorder and markout engine.
- NBM/HRRR forecast research.
- Google Contrails observation research.
- Dashboard / Telegram observability.
- Historical replay, parameter tournaments and OOS validation tooling.

## Live deployment state before unified cutover

Healthy long-running processes on `weather-research` were intentionally left uninterrupted during consolidation:

- `weather-paper` — `scripts/run_weather_company_paper_live.py`
- `weather-score` — settlement scorer
- `weather-dash` — live dashboard
- `weather-tourney` — frozen A/B/C forward tournament
- `weather-tourney-score` — tournament settlement scorer

These processes were launched from the older `/opt/poly-kalshi-weather-copy-bot` checkout and should not be killed merely to deploy the unified tree. Use a parallel worktree for validation and migrate processes one at a time only after health checks pass.

At the time of this handoff the missing live components were:

- `weather-l2` — authenticated read-only Kalshi WebSocket L2/trade capture
- `weather-diagnostic` — D lock-bypass diagnostic paper track
- `weather-telegram` — signal/fill/collector-health alerts

The L2 and Telegram services require local credentials. Never commit credentials or private keys.

## Current empirical control

Historical frozen control remains a candidate, not proven alpha:

- roughly 21–23% ROI in the existing historical grid
- 127 physical events
- 28 independent settlement dates
- apparent robustness across 0–300 second latency assumptions

Promotion target remains at least 50 independent dates, preferably 100+.

## NBM + exact bucket reconstruction added 2026-09-23

New reproducible components:

- `weather_alpha/providers/nbm.py`
  - NOAA NBM NBS station-text archive parser
  - 00Z station MaxT and XND uncertainty extraction
- `scripts/build_nbm_station_high_history.py`
  - archived NBM station-high history
- `scripts/build_kalshi_weather_bucket_history.py`
  - exact resolved Kalshi daily-high bucket ladders
  - final `expiration_value`
  - exact winning contract
  - validation requires one settlement value and one YES winner
- `scripts/build_contrail_hourly_history.py`
  - city/station hourly Google Contrails detections
  - per-city local-day clock
  - 150 km default research radius
  - segmented requests to stay inside API-duration constraints
- `scripts/study_contrail_q90_bucket_edge.py`
  - rolling, city-specific q90 test
  - q90 is calculated from that city's own preceding series only
  - walk-forward adjustment uses only earlier eligible rows
  - exact Kalshi winning-bucket comparison
- `scripts/build_weather_today_research_panel.py`
  - current contrails
  - matched-window activity percentile
  - prior-evening q90 flag
  - NBM MaxT / uncertainty
  - current Kalshi top bucket under NBM probabilities

## Contrail experiment sanity check

The initial Sep 2026 research sample is deliberately small:

- full resolved sample used in the first bucket study: 29 calendar days, Aug 23–Sep 20
- 20 cities
- initial fixed split used 20 training days and 9 OOS days
- 179 usable OOS city-days
- prior-evening 18:00–21:00 q90 test produced 19 extreme city-days
- those 19 observations occurred on only 7 independent calendar dates

Therefore the 19 observations must not be described as 19 independent days. The first q90 threshold was estimated from only 20 prior observations per city, which is fragile.

The correct production research standard is rolling city-specific q90 using at least 90–180 prior days where available, with date-block OOS evaluation and no future observations in the threshold or effect estimate.

## Current contrail/NBM hypothesis

Research question:

> Conditional on the pre-day NBM forecast distribution, does unusually high contrail exposure for the same city shift the final settlement distribution enough to improve exact Kalshi bucket probabilities?

Important distinctions:

- daytime and nighttime contrails are not assumed to have identical effects
- late-day contrails are retained because they may affect both the current evening and the following day
- observation collection runs through the entire day
- a trading snapshot may use only observations available by that decision time
- raw detection count is not itself a temperature adjustment
- any contrail adjustment must prove incremental OOS value beyond NBM and causal ASOS state

## Server-side research outputs created during the Sep 22–23 analysis

Representative files under `/data/weather/live` include:

- `contrail_all_cities_hourly_20260823_20260920.json`
- `nbm00z_station_max_20260823_20260920.json`
- `kalshi_bucket_structure_20260823_20260920.json`
- `top90_contrail_bucket_test.json`
- `contrail_nbm_rolling_snapshot_results.json`
- `today_contrails_nbm_panel.json`
- `today_kalshi_nbm_bucket_projection.json`

These are research outputs, not committed fixtures. Rebuild them with the version-controlled scripts above instead of treating ad-hoc `/tmp` scripts as durable source code.

## Validation requirements

Every alpha family must use:

1. long-history development data
2. recent manual-validation block
3. newest untouched final holdout

Required controls:

- date-block / walk-forward validation
- causal source timestamps and conservative availability assumptions
- no future weather leakage
- exact venue settlement semantics
- actual fees
- executable bid/ask or VWAP, not midpoint fantasy fills
- latency stress
- independent-date counts alongside city-day counts
- missing/stale feed fail-closed behavior
- ablation proof for every added data family

## Planned alpha families

Priority families in the master registry include:

- settlement mechanics: CLI/public-feed basis, hidden extrema, rounding, LST/DST windows
- NBM calibrated bracket probabilities and station/season bias
- NBM + causal ASOS residual update
- terminal-high state transition: high-so-far, dT/dt, d2T/dt2, minutes since high, remaining heating
- dewpoint / pressure / wind / SPECI / sea-breeze and frontal transitions
- NBM release absorption, HRRR revision and cross-model disagreement
- GOES visible/IR cloud shielding
- contrail exposure and persistence
- GLM lightning and MRMS precipitation/outflow timing
- RTMA/RU rapid surface analysis
- AFD/TAF/SPECI text/revision information
- maker favorite-longshot, queue/adverse-selection and scheduled-release pickoff protection
- exact-equivalent or explicit-basis Kalshi vs Polymarket US relative value

NBM is the primary forecast baseline. HRRR, contrails, satellite, radar and text features must prove incremental OOS value beyond the simpler baseline before promotion.

## Immediate build sequence

1. Merge PR #10 only after current-head CI is green and mergeability is rechecked.
2. Create a parallel unified deployment worktree on `weather-research`; do not stop healthy paper/tournament/dashboard processes.
3. Run compile/tests and smoke the new NBM/bucket/contrail scripts in the parallel worktree.
4. Start `weather-diagnostic` from the unified worktree.
5. Start `weather-l2` only when the local Kalshi API key ID and private-key file are present and readable; the recorder is read-only and must remain order-disabled.
6. Start `weather-telegram` only when local Telegram token/chat credentials are present; send one explicit test message before long-running launch.
7. Verify output-file growth, collector health timestamps, reconnect behavior and disk growth.
8. Extend NBM + contrail history to at least 90–180 days and rerun rolling city-specific q90 walk-forward analysis.
9. Add causal ASOS residual features before deciding whether contrails have incremental trading value.
10. Rerun the frozen historical control unchanged on newest Kalshi-native OOS data.

## Deployment rule

Do not replace a healthy long-running process in place unless the replacement has passed smoke tests in the parallel unified worktree. Migration should be service-by-service, with old and new output paths checked for continuity before the old process is stopped.
