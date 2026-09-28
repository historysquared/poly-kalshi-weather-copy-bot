# Weather Market Universe, Signal, and Storage Plan — 2026-09-26

## Objective

Cover the complete weather-market universe on Kalshi and Polymarket US without conflating market discovery with strategy eligibility. Collect first; only emit strategy signals after settlement semantics, station/time window, causal data, and model support are verified.

## Kalshi daily-temperature universe

Current high-temperature registry contains 24 city families: NYC, Chicago, Miami, Los Angeles, San Francisco, Denver, Boston, Austin, Seattle, Atlanta, Las Vegas, Minneapolis, New Orleans, Washington DC, Philadelphia, San Antonio, Dallas, Oklahoma City, Phoenix, Houston, Trenton, Louisville, Newark, and San Diego.

Trenton (`KXHIGHTTTN` / KTTN), Louisville (`KXHIGHTSDF` / KSDF), and Newark (`KXHIGHTEWR` / KEWR) were verified against current Trade API rules and The Weather Company settlement language. San Diego (`KXHIGHTSAN` / KSAN) was also verified against current Trade API rules: the settlement rule references San Diego `CLISAN` and The Weather Company.

The complete matching 24-city daily-low family is also now in the collection registry. The all-weather audit found 48 active tagged daily-temperature series (24 high + 24 low), each with 12 open contracts at audit time. Daily-low strategies remain separate from daily-high strategies even though collection is shared.

Do not assume this static registry is the full Kalshi weather universe. Build a persistent catalog for all weather measurements (high, low, hourly temperature, rain, snow, and future weather families) and classify contracts EXACT / PROBABLE / REVIEW / REJECT.

## Polymarket US

Public discovery, books, and BBO must use `https://gateway.polymarket.us`; authenticated resources use the API host. Discovery now uses the official `tag_slug=weather` filter so it does not crawl thousands of unrelated events.

At audit time the US weather tag returned 12 active events / 70 markets: ten daily-high events covering NYC, Chicago, Miami, Los Angeles, and San Francisco across two dates, plus two annual global-temperature-rank climate events. The daily-temperature normalizer produced 60 outcome contracts. A read-only one-minute snapshot collector now archives the entire weather-tagged Polymarket US set; non-daily-temperature weather events stay in the catalog but are not fed into the daily-high model.

## Signal delivery

Telegram is the operator alert channel for paper/shadow signals. The watcher now includes the empirical remaining-heating v2 shadow stream in addition to main paper, A/B/C tournament, and D diagnostic streams. Its collector-health input is the durable systemd archive health file.

The remaining-heating v2 shadow service is intended to run continuously. A zero-signal cycle is valid: it means no contract passed the frozen model/edge requirements, not that alerting is broken. Never loosen thresholds merely to force signal frequency.

A separate read-only structural-alpha shadow stream monitors complete-ladder underrounds and conservative irreversible daily-high boundary locks. These alerts are explicitly labeled with strategy IDs and reasoning in Telegram, write to append-only signal history, and never submit orders. Diagnostic Weather Company signals remain a control/falsification stream and must not be presented as validated alpha.

The legacy Weather Company heuristic is a forward falsification/control track. Do not present it as the preferred alpha model. Continue collecting its evaluation history while prioritizing frozen settlement-basis and empirical remaining-heating research.

## Faster/better data build order

1. Settlement truth: exact Weather Company/Kalshi rule, preliminary/final value, corrections, source timestamps.
2. Surface truth: AviationWeather METAR/SPECI plus high-frequency ASOS with explicit receipt latency and cache fallback.
3. NBM probabilistic guidance: exact model cycle/release time and station-specific empirical residual distribution.
4. HRRR point/revision stream: retain station/city extracts and cycle-to-cycle deltas instead of full CONUS grids.
5. RTMA-RU/URMA rapid surface analyses for state correction between model cycles.
6. GOES visible/IR cropped patches for cloud shielding, burnoff, and convective development.
7. MRMS/GLM event features for precipitation, outflow, and rapid cooling.
8. Cross-venue Kalshi + Polymarket US books/BBO for lead-lag and fee-aware routing.
9. Contrail matched-time percentiles and pace features only as incremental information beyond NBM + causal surface state.

For every source preserve source time, receipt/available time, and local receive time. Measure meteorological lead, transport lead, compute lead, and market reaction separately.

## Storage policy

Current server has about 38 GiB free. The historical PMXT Kalshi archive already occupies about 26 GiB. Current compressed 20-series live Kalshi L2 was writing at roughly 0.25–0.30 GiB/day during the first measured hours; current duplicated live JSONL research outputs add roughly 0.11–0.12 GiB/day.

Do not scale minute-by-minute duplicate JSONL files linearly to every city. Compact decision snapshots daily into Parquet, deduplicate common event/surface fields, and rotate operator logs.

The permanent Kalshi archive is now subscribed to the complete active all-weather catalog (1,382 contracts at cutover). A 60-second full-universe compressed shadow sample projected about 0.46 GiB/day for exchange messages at that moment, including initial book snapshots. Traffic varies materially, so capacity planning remains 1.0–1.5 GiB/day total after adding weather features, Polymarket US snapshots, research outputs, and safety margin until a seven-day measurement replaces the estimate.

Retention tiers:
- Exchange L2/trades/ticker raw: 14 days local hot; archive indefinitely because missed history cannot be reconstructed.
- Market/rule metadata and settlements: local + archive indefinitely.
- Normalized Parquet decision snapshots/features: at least 90 days local if space permits; archive indefinitely.
- ASOS/METAR/SPECI point observations: retain indefinitely; small and high-value.
- NBM/HRRR point extracts and model revisions: retain indefinitely; avoid full-grid retention unless needed for a defined study.
- GOES/MRMS/GLM: retain cropped station/city patches and derived features; raw large files only for short event windows unless a frozen experiment requires them.
- Contrail derived histories: retain indefinitely.
- Rebuildable dashboards/logs: rotate after 7–14 days once research records are safely compacted.

At a 1.5 GiB/day planning rate, 14 hot days require about 21 GiB, 90 days about 135 GiB, and one year about 548 GiB. Keep at least 10–15 GiB free on the boot disk; archive before the disk approaches that reserve.

## Next execution sequence

1. Merge and deploy the market-universe/Polymarket gateway changes without restarting the durable collector during strategy deployment.
2. Keep the durable Kalshi archive collector on the complete active all-weather catalog and verify sequence health plus catalog refresh changes; do not couple its restart to strategy deployment.
3. Keep `weather-v2-shadow`, `weather-telegram`, and the Polymarket US weather snapshot collector running continuously and report signal/abstention counts by strategy and city.
4. Build the persistent venue catalog collector so static ticker lists become seeds rather than the source of truth.
5. Add exact rule/settlement verification for every newly discovered city/family before signal eligibility.
6. Replace high-volume duplicate JSONL research streams with daily Parquet compaction and explicit retention/rotation.
7. Add Spaces archival with checksum/manifest verification before deleting local raw files.
8. Measure real seven-day bytes/day by source and revise the current 1.0–1.5 GiB/day planning envelope from observation rather than assumptions.
9. Expand remaining-heating historical features to the verified city set, freeze configuration, and shadow-test new cities without retuning thresholds.
10. Add NBM residual + high-frequency surface state as the primary probability layer, then test HRRR/RTMA/GOES/MRMS/contrail features incrementally.
