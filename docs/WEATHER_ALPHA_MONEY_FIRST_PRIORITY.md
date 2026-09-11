# Weather Alpha Money-First Priority Roadmap

## Objective

Get to executable, live-capable weather alpha as fast as possible. Prioritize structural edges that depend on settlement mechanics, venue rules, and persistent market behavior before speculative forecasting races. Forecast accuracy is useful only when it translates into executable net edge after fees, latency, queue effects, and settlement truth.

This document updates and narrows `WEATHER_ALPHA_PRODUCTION_ROADMAP.md` and `WEATHER_ROADMAP_RESEARCH_HARDENING.md` into a money-first build order.

## Tier 1: build and test first

These are the highest-priority strategy families because they are structural and can be killed or validated quickly.

### A. Settlement-measurement edge

1. Invert the public whole-degree-Celsius 5-minute ASOS representation into a posterior over the compatible true whole-degree-Fahrenheit rolling 5-minute value.
2. Model unobserved minutes between public 5-minute timestamps; estimate the probability that the true daily extreme occurred between stamps.
3. Build a per-station empirical distribution of `official CLI high/low - public-feed max/min`.
4. Trade only boundary cases where the market appears to price the public feed rather than the official settlement process.
5. Model Local Standard Time versus civil/DST windows exactly; include post-midnight observations that may belong to the prior settlement day.
6. Treat owning settlement reconstruction as an informational edge: keep probabilities live after the public feed appears decisive if settlement mechanics still permit a different official outcome.

Required deliverables:
- CLI historical outcomes
- high-resolution ASOS history
- exact LST windows
- posterior-compatible public-feed inversion
- boundary-crossing statistics
- per-station mismatch distribution
- live settlement-state engine

Kill gate: if reconstructed/public-feed basis almost never changes the winning bracket after realistic timing, deprioritize this family.

### B. NBM percentile baseline with fat-tailed residuals

7. Use NBM percentile/probability guidance as the first forecast baseline rather than raw HRRR.
8. Fit empirical residual distributions by station/season; do not assume Gaussian errors.
9. Rank model skill by station and lead horizon.
10. Use ASOS only as a causal residual/state update on top of NBM.

Required deliverables:
- NBM cycle/vintage ingestion
- station-level percentiles/probabilities
- empirical residual CDF/tails
- calibration by station/season/lead
- contract probability mapper

Kill gate: if NBM + causal ASOS does not improve executable decisions beyond market-only after costs, do not add HRRR complexity yet.

### C. Maker-side favorite / tail selling

11. Test favorite-longshot behavior in current weather markets as a maker, not as a taker assumption.
12. Make maker execution a first-class mode: JOIN / IMPROVE / WIDE / HYBRID.
13. Default research filter: test entry-price floors at 5/10/15/20/25 cents, with 15 cents as a provisional starting point rather than a permanent invariant.
14. Use the weather/settlement model primarily as a veto against unsafe favorites and tails.
15. Cancel or widen resting quotes around known scheduled-information windows; re-quote after the release rather than trying to win the race.

Required deliverables:
- queue-ahead estimate
- partial-fill model
- maker vs taker fees
- post-only validation
- +1m/+5m/+15m markouts
- adverse-selection diagnostics
- price-band return decomposition
- station/date/liquidity stratification

Kill gate: if realistic queue/fill assumptions plus adverse-selection markouts erase the edge, do not market-make that family.

### D. Cross-venue settlement basis

16. Treat Kalshi vs Polymarket US as a basis trade unless station/source/window/bucket semantics are exactly equivalent.
17. For each pair, classify:
   - EXACT_EQUIVALENT
   - SAME_REGION_DIFFERENT_STATION
   - SAME_STATION_DIFFERENT_SOURCE_OR_WINDOW
   - RELATED_WEATHER_EVENT
   - UNRELATED
18. Estimate full conditional basis distributions for non-exact pairs.
19. Test known high-value examples such as central-city vs airport-station differences only after verifying the actual Polymarket US settlement source; never import global Polymarket rules into Polymarket US.

Required deliverables:
- venue-specific exact settlement catalog
- station/source/window basis history
- bracket-geometry mapper
- cross-venue executable quote snapshots
- fee-aware route selection

Kill gate: if the basis is unstable or liquidity/capacity is too low after hedging error, do not label it arbitrage or scalable RV.

## Tier 2: test after Tier 1 data exists

These can reuse the same decision snapshots but should not delay the Tier-1 pipeline:

- peak attenuation from rolling 5-minute averaging conditioned on dT/dt volatility
- sensor drift/bias history and probability of QC adjustment
- daily low markets, which may be more window-sensitive
- Kalshi delay/inconsistency rule around CLI versus METAR extrema
- preliminary-vs-final CLI uncertainty
- NBM release absorption and forecast revision
- SPECI / off-hour rapid-change triggers
- per-station microclimate effects: Central Park canopy, sea breeze, cold-air drainage, downslope/chinook, marine layer, lake effects, urban heat-island asymmetry, snowpack albedo
- clear-sky/cloudy conditional residual bias
- remaining-heating/cooling physics caps
- adjacent-bracket coherence and ladder-shape violations
- overnight spread capture and secondary-city liquidity
- new-listing lazy pricing
- final-hour forced unwinds
- venue lead/lag by hour

These should be implemented as conditioning variables or strategy variants inside the shared framework, not as isolated bespoke bots.

## Tier 3 backlog

Do not spend engineering time here until Tier-1 results justify it:

- media/retail narrative effects
- weekend participation
- round-number preferences
- yesterday-outcome anchoring
- ENSO/MJO standalone signals
- record-temperature narrative flow
- hurricane/advisory special cases
- monthly/seasonal aggregate products
- dewpoint/wet-bulb/wind/precip/snow product families
- advanced AI forecast models

Any behavioral/calendar hypothesis must survive multiple-hypothesis correction and recent OOS testing before capital is allocated.

## Critical infrastructure changes required now

### 1. Settlement state engine

For every canonical event store:
- station
- venue
- settlement source/report
- exact reporting window start/end UTC
- station local-standard offset
- station civil/DST offset
- official value if causally published
- public-feed value/state
- posterior over official settlement value
- bracket win probabilities
- mapping confidence

### 2. ASOS QC fail-closed layer

Each observation receives PASS/WARN/FAIL plus reason codes. QC failures block new orders and cancel resting event-specific quotes where safe.

### 3. Decision snapshot additions

Add:
- public_5min_max/min_so_far
- reconstructed_settlement_p05/p50/p95
- measurement_basis_expected
- settlement_window_start/end_utc
- local-standard/civil offsets
- ASOS QC state
- NBM cycle/age/percentiles/probabilities
- execution mode
- resting price
- queue ahead estimate
- fill probability estimate
- maker/taker fee estimate
- markout_1m/5m/15m
- model_markout_1m/5m/15m

### 4. Maker simulation requirements

A passive fill is never considered profitable merely because posted price was better than fair value. Every maker backtest must include queue uncertainty, partial fills, cancellation latency, scheduled-release blackout windows, and forward markouts.

### 5. Hard live safety

Unknown settlement state, QC FAIL, stale book, stale weather input, unknown fee class, bad clock, or unresolved order state => no new live order.

## Money-first build sequence

### Phase 0 — finish exact truth
- Kalshi exact daily-high/low catalog
- Polymarket US exact weather catalog
- series/event rules
- station/source/window/bounds/inclusivity
- real fee/tick metadata

### Phase 1 — settlement reconstruction
- historical CLI downloader/parser
- LST/DST window engine
- ASOS reconstruction
- public-Celsius inversion posterior
- unobserved-minute extreme model
- station mismatch distributions
- boundary relevance report

### Phase 2 — executable market data
- weather-only PMXT Kalshi Parquet
- decision snapshots
- maker/taker fields
- markouts
- price-band stats
- queue/fill assumptions versioned

### Phase 3 — forward shadow starts immediately
Once exact current mappings exist, continuously log:
- both venue books where available
- ASOS state + QC
- settlement posterior
- NBM
- model probability
- maker/taker candidates
- quote/fill markouts
- final settlement

Do not wait for perfect historical data to begin collecting forward truth.

### Phase 4 — first strategy tournaments
Run in this order:
1. settlement-boundary mispricing
2. passive favorite/tail selling with model veto
3. NBM + ASOS fat-tail baseline
4. cross-venue basis
5. per-station skill/microclimate variants

### Phase 5 — paper
Require:
- clean multi-day operation
- no duplicate orders
- correct cancellations/requotes around scheduled windows
- exact fee/fill/settlement reconciliation
- positive recent candidate P&L or useful negative evidence

### Phase 6 — tiny live
- one-contract only initially
- verify maker fee classification
- verify taker fee classification
- cancel/replace
- private websocket + REST reconciliation
- final settlement reconciliation
- scale only after live assumptions match observed reality

## Strategy scorecard

Every experiment must report:
- strategy family
- station
- venue
- side
- maker/taker mode
- entry price band
- net P&L
- ROI
- mean and median event ROI
- adverse-selection markouts
- fill ratio
- queue assumptions
- capacity at 1/5/25 contracts
- date-block bootstrap CI
- max drawdown
- concentration
- latency sensitivity
- fee sensitivity
- recent OOS result

## Fast-kill rules

Kill or pause a family when:
- edge disappears after executable pricing/fees
- queue realism erases maker edge
- settlement basis is not bracket-relevant
- OOS lower confidence bound is persistently negative with adequate sample size
- signal depends on a latency race we are demonstrably losing
- capacity is too small to matter
- live shadow markouts materially contradict backtest assumptions

## Near-term priority allocation

Engineering/research time until first live candidate:
- 35% settlement reconstruction and exact venue rules
- 25% executable books + maker/taker microstructure
- 20% forward shadow recorder
- 10% NBM + ASOS baseline
- 10% Polymarket US cross-venue basis catalog

Do not allocate meaningful time to HRRR, satellite, radar, AI models, behavioral narratives, or exotic weather products until the Tier-1 structural tests have results.
