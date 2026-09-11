# Live Weather Research Roadmap

## Repository boundary

This repository is the only repository modified by this work.

historysquared/kalshi-15m-lab was inspected read-only for implementation ideas:
- read-only Telegram signal watcher pattern;
- persistent WebSocket/reconnect/feed-health pattern;
- separation between research alerts and execution code.

No files, branches, commits, or configuration were changed in kalshi-15m-lab.

## Immediate live objective

The highest-priority dataset is the live Weather Company/Kalshi regime because it is impossible to recreate perfectly after the fact.

Keep these forward processes separate:

1. weather-paper
   - existing production-shaped paper trader;
   - no live order submission;
   - frozen gates unless explicitly versioned.

2. weather-tourney
   - synchronized A/B/C forward comparison;
   - preserve existing state and thresholds.

3. weather-diagnostic
   - D_DIAGNOSTIC_NO_LOCK;
   - intentionally bypasses the lock gate;
   - diagnostic only, never production-eligible;
   - provides candidate trades and explains which control lock components would have failed.

4. weather-dash
   - live weather/market dashboard;
   - records contract snapshots;
   - consumes latest reconstructed L2 state when available.

5. weather-l2
   - authenticated, read-only Kalshi WebSocket collector;
   - records raw orderbook_snapshot, orderbook_delta, trade, and ticker messages;
   - maintains reconstructed books;
   - writes 0/5/15/30/60/120/300-second markouts for new paper/tournament/diagnostic signals.

6. weather-telegram
   - read-only alert companion;
   - watches signal/fill JSONL files;
   - never imports execution/order code;
   - alerts on paper/tournament/diagnostic signals, fills, and L2 collector health errors.

## Live data products

Primary append-only/raw products:

- /data/weather/live/weather_company_contract_history.jsonl
- /data/weather/live/weather_company_paper_signals.jsonl
- /data/weather/live/weather_company_paper_fills.jsonl
- /data/weather/live/weather_company_tournament_decisions.jsonl
- /data/weather/live/weather_company_tournament_signals.jsonl
- /data/weather/live/weather_company_tournament_fills.jsonl
- /data/weather/live/weather_company_diagnostic_decisions.jsonl
- /data/weather/live/weather_company_diagnostic_signals.jsonl
- /data/weather/live/weather_company_diagnostic_fills.jsonl
- /data/weather/live/kalshi_l2/kalshi_weather_ws_YYYY-MM-DDTHH.jsonl.gz
- /data/weather/live/weather_company_latency_markouts.jsonl

Current-state products:

- /data/weather/live/weather_company_dashboard.json
- /data/weather/live/weather_company_dashboard.csv
- /data/weather/live/weather_company_contract_dashboard.csv
- /data/weather/live/weather_company_dashboard.txt
- /data/weather/live/kalshi_l2_latest.json
- /data/weather/live/kalshi_l2_health.json

## Credential policy

Secrets are never committed.

Recommended root-only env file:

/root/.config/weather-alpha/live.env

with permissions 0600.

Expected environment variables:

- KALSHI_API_KEY_ID
- KALSHI_PRIVATE_KEY_PATH
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- later: PMXT_API_KEY

The Kalshi WebSocket collector is read-only and subscribes only to market-data channels. No order-entry code is imported.

## Phase 1 — live observability now

Goal: collect enough synchronized evidence to diagnose every signal by looking at the live Kalshi chart and weather state.

Required:
- keep paper/dashboard/tournament alive;
- start D diagnostic track;
- start full Kalshi L2 + public trade capture;
- start 0/5/15/30/60/120/300-second markouts;
- start Telegram signal/fill/error alerts;
- measure reconnects, sequence gaps, message rates, file growth, CPU and RSS on the DigitalOcean host.

Acceptance:
- at least one full trading day with no silent collector gaps;
- every emitted signal has a contemporaneous weather state and L2 book;
- every signal gets all seven latency marks or an explicit NO_L2_BOOK reason;
- Telegram produces no duplicate episode alerts after restart.

## Phase 2 — recent Kalshi-native history

Goal: validate recent trades without waiting for PMXT.

Use:
- Kalshi /markets/trades for recent public prints;
- Kalshi /historical/trades for archived prints;
- 1-minute live or historical candlesticks according to Kalshi's cutoff tier.

Cache recent exact weather contracts locally with scripts/download_kalshi_recent_weather_marketdata.py.

Priority:
1. most recent completed dates;
2. August 2026 block already resolved;
3. extend forward/backward as API coverage allows.

Outputs should support:
- trade-by-trade manual review;
- recent fill/price proxy tests;
- by-date and by-station P&L;
- frozen-strategy recent OOS comparison.

## Phase 3 — PMXT free-tier augmentation

After PMXT_API_KEY is installed:
- use hosted historical L2 only for targeted windows where it adds information beyond Kalshi trades/candles;
- cache responses once;
- stay within free-tier request/credit limits;
- prioritize recent unresolved gaps;
- never make the research pipeline dependent on PMXT availability.

PMXT bulk files remain useful for older periods where valid Parquet archives exist.

## Phase 4 — terminal-high state-transition features

Do not tune this into the frozen control strategy.

Build a separate feature family:
- temperature now;
- high so far;
- minutes since high;
- drop from high;
- slope 1m/3m/5m/10m/15m/30m;
- acceleration 3m/5m/10m;
- dewpoint level/slope;
- pressure tendency;
- wind speed/direction and 5m/15m shifts;
- observation age;
- minutes to settlement end;
- minutes to sunset/solar state.

Primary hypothesis:
heating velocity collapses, acceleration turns negative, and the market reprices terminal-high probability with measurable delay.

Evaluate the same event at 0/5/15/30/60/120/300 seconds using recorded L2.

## Phase 5 — incremental weather-source edge tests

Against frozen date blocks, add one family at a time:

1. ASOS state-transition features;
2. nearby-station spatial progression;
3. dewpoint/pressure/wind/SPECI;
4. NBM/HRRR forecast errors and revisions;
5. GOES visible/IR cloud shielding;
6. MRMS/NEXRAD precipitation/outflow;
7. GLM lightning jump/convective initiation;
8. RTMA/RU and other higher-frequency analysis products.

A source survives only if it improves untouched OOS economics after its real publication latency.

## Phase 6 — execution research

Only after signal quality is demonstrated:
- maker versus taker;
- queue-priority/back-of-queue simulation;
- adverse-selection markouts;
- top-5 depth/VWAP sizing;
- fractional Kelly caps;
- cross-venue Kalshi/Polymarket comparison where settlement definitions are truly equivalent.

Do not optimize Rust/DPDK/microseconds until the measured latency curve shows the edge disappears at sub-second/seconds horizons.

## Phase 7 — validation gates

Maintain three datasets:

- long-history development;
- recent manual-validation block;
- newest untouched final holdout.

Minimum evidence targets:
- 50 independent settlement dates for a serious candidate;
- 100+ preferred;
- date-block/walk-forward validation;
- no random train/test split;
- exact causal timestamps;
- actual fees;
- no lookahead weather data;
- explicit missing/stale-feed kill switches.

The existing approximately 21–23% historical control result stays frozen until rerun on expanded data.
