# Contrail Signal Audit — 2026-09-28

## Scope

This audit covers the Google Contrails current-data path and `contrail_q90_prior_evening_v1`. It is intentionally separate from any unsupported claim that the feature has an ~80% executable trading win rate.

## Bugs found

1. **Missing historical contrail day was converted to zero.** `study_contrail_q90_bucket_edge.py` previously used an empty dictionary when a `(city, prior_date)` record was absent. That made unavailable satellite history look like a real zero-count evening. Fixed: missing history rows are excluded.
2. **Top-decile ties were mishandled.** The old rule used `count >= q90 threshold`. When q90 was zero, a zero count could be labeled top-decile. Fixed: top-decile status now uses a rolling tie-aware midrank percentile; a zero tied with historical zeros is not automatically extreme.
3. **Current matched-time panel could also substitute missing city history with zero.** Fixed: only actual city/date historical rows enter the percentile distributions.
4. **Contrail live access was not operationally wired.** The Google API credential existed in `/root/.weather-contrails.env`, but the active Telegram service did not load it and the old Stayton timer referenced an obsolete deploy path. The hardened release loads the correct environment and provides a current all-city collector plus Telegram command path.

## Corrected historical q90 result

Using the canonical hourly history available through 2026-09-27, NBM station-high history, exact Kalshi bucket outcomes, minimum 20 prior history days and corrected missing/tie handling:

- eligible city-days: **220**
- q90 extreme city-days: **25**
- independent extreme dates: **8**
- mean `(actual high - NBM high)` on extremes: **-0.48°F**
- baseline NBM top-bucket hits on extremes: **6**
- contrail-adjusted top-bucket hits: **6**
- wrong→correct bucket flips: **0**
- correct→wrong bucket flips: **0**

This corrected result still suggests a modest negative temperature-residual tendency on extreme prior-evening contrail days, but it does **not** validate an executable bucket trade or an 80% trading win rate.

The previously discussed ~80% rule is not preserved in durable project artifacts with a complete entry condition, side/bucket rule, prices, dates and outcomes. It must not be recreated from memory and represented as the same strategy.

## Forward treatment

`contrail_q90_prior_evening_v1` therefore runs as a **forward research trigger**, not a trade recommendation:

- city-specific prior-evening count;
- corrected rolling percentile;
- current matched-time contrail percentile;
- NBM high and uncertainty;
- current Kalshi model-leading bucket where available;
- `trade_decision=ABSTAIN_RESEARCH_ONLY`;
- `live_order_submission=false`.

Telegram exposes these triggers so their forward behavior can be reviewed while the exact executable hypothesis is rebuilt and validated.

## Current-data operation

- `/contrails` performs a fresh all-city Google Contrails API pull.
- `/contrails <city>` returns raw detections/frames/line length plus last-full-hour, rolling-3-hour, matched-time and prior-evening percentiles from the maintained history.
- a 15-minute collector keeps `/data/weather/live/contrails_current.json` current and appends history snapshots;
- a daily collector merges the most recent complete day into `/data/weather/normalized/contrails/contrail_hourly_history.json`;
- data/API/history errors publish explicit health states and do not create strategy signals.
