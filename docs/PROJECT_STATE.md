# PredictionBots Weather — Project State

This file contains human judgment only. Runtime facts, process health, commit hashes, timestamps, and live metrics belong in machine-generated status data, not here.

## Current priorities

1. Preserve irrecoverable Kalshi L2 history continuously.
2. Separate collectors from strategy deployment and give collectors a manual/tagged release cadence.
3. Add minimal stale/dead/recovered alerts plus an external dead-man heartbeat.
4. Publish the audited unified branch to GitHub and make GitHub the durable code/task source of truth.
5. Extend frozen settlement and remaining-heating tests without retuning them on the added sample.
6. Continue contrail research only as an incremental feature beyond NBM + causal surface state.

## Current blockers

- Telegram bot token/chat ID are not installed yet.
- ASOS, market-snapshot, and contrail collection are not yet fully separated from strategy/research processes.
- The historical strategy samples remain too small for promotion despite positive point estimates.
- Recent historical L2 cannot be reconstructed for periods that were not recorded live.

## Important decisions

- GitHub is the source of truth for code, issues, PRs, CI, and durable project judgment.
- Cursor is the primary implementation environment; ChatGPT is the architecture, research, audit, and validation layer.
- Persistent production collectors use systemd, append-only raw storage, schema/version tags, and restart automatically.
- Collector releases are independent from strategy/paper releases; a strategy merge must not restart data collectors.
- Strategy deployments may become automated after CI/health/rollback is reliable; collectors remain manual/tagged by default.
- Runtime state is generated mechanically and must be treated as UNKNOWN when its own timestamp is stale.
- Point-in-time, settlement-source, and no-future-data invariants belong in CI tests, not only AI/code review.
- Experiment lifecycle and sample-quality flags are separate concepts.
- Minimum sample counts are gates, not promotion criteria; promotion rules must be pre-registered in frozen configs.
- Inference should account for within-date and cross-city dependence, preferably through date-clustered uncertainty and weather-regime robustness.
- Multiple-hypothesis screening must be distinguished from frozen confirmatory tests.
- The legacy Weather Company strategy stack may be retired only after its useful history/consumers are preserved or replaced; collectors must not depend on it.

## Research risks

- Remaining-heating economics are unusually strong in a small sample and therefore vulnerable to selection effects or hidden regime dependence.
- Corrected settlement alpha remains based on fewer independent dates than desired.
- Same-date city observations share synoptic regimes and cannot be counted as independent trials.
- Contrail detection counts are observed LineString features, not unique aircraft or necessarily unique physical contrails.
- Market-source transitions (for example NWS/CLI versus Weather Company settlement) must remain explicit in model eligibility.

## Near-term sequence

L2 archive → collector alerting/dead-man → collector separation → CI research invariants → strategy-only release automation → frozen OOS expansion → additional alpha families.
