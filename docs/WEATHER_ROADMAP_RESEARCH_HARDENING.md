# Weather Roadmap Research Hardening Addendum

This addendum records research-driven changes to `WEATHER_ALPHA_PRODUCTION_ROADMAP.md`. It is deliberately weather-specific and does not import BTC/crypto strategy logic.

## Executive changes

1. Insert a settlement-reconstruction workstream before forecast modeling.
2. Make NBM the primary forecast baseline; retain HRRR as a secondary/incremental source and ablation target.
3. Add ASOS quality-control gates and event-specific fail-closed behavior.
4. Model NWS Local Standard Time reporting windows explicitly per station/event.
5. Add maker/taker as a first-class strategy dimension, including queue/fill probability and adverse-selection markouts.
6. Treat favorite-longshot behavior as a hypothesis to test in weather, not as an assumed permanent edge.
7. Add a default research exclusion for very cheap contracts, but make the threshold empirical and configurable rather than a universal hard-coded truth.
8. Keep Kalshi and Polymarket US execution state machines separate. Do not impose global-Polymarket blockchain/CTF assumptions on Polymarket US unless the US API proves they apply.
9. Treat cross-venue station/source differences as relative-value basis, never arbitrage, unless settlement semantics are exactly identical.

---

# 1. Settlement reconstruction becomes Stage 0B

Before NBM, HRRR, machine learning, satellite, radar, or live alpha claims, validate the physical-to-settlement pipeline.

For each target station/event family:

- obtain historical CLI/NWS official daily climate values
- obtain the highest-resolution causally available ASOS observations possible
- preserve native precision and timestamps
- reconstruct the rolling-max/min logic implied by the official station/reporting methodology
- compare reconstructed value with official CLI settlement value
- measure residual `official_settlement - reconstructed_public_observation_value`
- measure boundary-crossing frequency where a one-degree or rounding difference changes the winning contract
- stratify by station, season, DST/non-DST, temperature level, and observation-product type

Critical test outputs:

- exact-match rate
- ±1°F mismatch rate
- distribution of mismatch magnitude
- probability a mismatch changes the winning bucket
- conditional mismatch around contract boundaries
- frequency of an official extreme not visible in ordinary public METAR/5-minute reports

If settlement reconstruction cannot reproduce the official outcome reliably, all strategies using that station remain blocked.

## Why this is high priority

NWS documentation confirms that daily extrema may occur between ordinary METAR reports, and that rolling 5-minute temperature values can differ from the less precise public 5-minute representation because of Fahrenheit/Celsius conversion and rounding. This creates a potential measurement-basis edge that must be validated empirically rather than assumed.

---

# 2. NWS Local Standard Time is explicit event state

The canonical event must store:

- station
- station timezone
- local-standard-time offset
- civil/DST offset at the event date
- observation-window start UTC
- observation-window end UTC
- settlement report date
- settlement publication timestamp when available

Do not represent a weather settlement merely as a civil calendar date.

During daylight-saving periods, a midnight-to-midnight Local Standard Time observation day may correspond to 01:00–01:00 local daylight time. The event mapper must compute the exact UTC interval and prove it against official NWS/venue rules.

A post-midnight civil-time observation may therefore belong to the prior settlement day. This must be covered by unit tests around DST transitions and summer dates.

---

# 3. ASOS quality-control layer

Add a QC state between raw observations and normalized decision features.

Each observation receives:

- `qc_status`: PASS / WARN / FAIL
- `qc_reason_codes`
- source timestamp
- receipt/assumed-availability timestamp
- station freshness
- recent flatline duration
- local jump / dT-dt score
- cross-sensor consistency score when a trusted proxy exists

Initial QC tests:

- stale observation
- duplicate timestamps
- impossible/implausible jump rate
- long flatline
- missing sequence/gap
- unit/encoding anomaly
- out-of-range value
- disagreement with nearby trusted observations large enough to threaten a bucket decision

Thresholds must be station/season aware where appropriate; avoid a single universal temperature-jump constant if historical data show legitimate extremes.

## Live action on QC failure

If the settling station is FAIL:

- block new orders for that canonical event
- cancel resting weather-alpha orders for that event where safe/allowed
- preserve existing positions and surface an operator alert
- continue recording raw data for diagnosis

WARN may permit shadow logging but should be configurable for live trading.

---

# 4. NBM becomes forecast baseline; HRRR moves to incremental layer

The first forecast benchmark should be the National Blend of Models (NBM), because the project needs a calibrated station-level forecast distribution rather than another raw deterministic model.

Build:

- NBM station/text ingestion
- NBM gridded/probabilistic ingestion where needed
- explicit cycle/vintage timestamps
- percentile/probability fields
- deterministic max/min guidance where available
- no-lookahead availability rules

Primary baseline ladder:

1. market-only
2. climatology
3. ASOS settlement-state only
4. NBM only
5. NBM + ASOS residual update
6. NBM + ASOS + HRRR incremental features

HRRR remains useful for:

- very short-horizon temperature path
- cloud/solar evolution
- convection/frontal structure
- forecast-revision information
- residual information not already embedded in NBM

But HRRR must prove incremental OOS value beyond NBM before becoming production-critical.

Do not assume every useful NBM probabilistic field is available at every short horizon/cycle; schema-validate the exact operational product used.

---

# 5. Maker/taker becomes a first-class strategy axis

Every strategy experiment must specify execution mode:

- TAKER
- MAKER_JOIN
- MAKER_IMPROVE
- MAKER_WIDE
- HYBRID

For taker simulations:

- executable touch
- depth VWAP
- fee
- latency shift
- adverse tick/slippage stress

For maker simulations:

- posted price
- queue ahead estimate / uncertainty
- time resting
- cancel latency
- fill probability
- partial-fill model
- fee class
- post-only validity
- book movement while resting
- markouts after fill

## Adverse-selection diagnostics

For every simulated or real maker fill record contract-price markouts at minimum:

- +1 minute
- +5 minutes
- +15 minutes
- later configurable horizons appropriate to market life

Also record fair-value/model markout at the same horizons.

A maker strategy is not considered profitable merely because the posted price was below model fair value. Promotion requires post-fill adverse selection to remain acceptable after queue/fill assumptions and fees.

---

# 6. Favorite-longshot harvest is a research family, not an assumption

Academic Kalshi evidence supports a broad favorite-longshot bias and substantially better historical outcomes for makers than takers. The same paper reports a statistically significant positive price-slope in its Climate & Weather subsample and positive average returns for makers buying contracts priced at 50c and above in the pooled sample.

This motivates, but does not prove, a current weather edge.

Create a dedicated tournament:

- venue: Kalshi weather only
- maker vs taker
- YES and NO symmetrically by contract price
- price bands: 1–10, 11–20, ... 90–99c
- days/hours to settlement
- station
- season
- liquidity/depth
- contract family
- pre/post current fee schedule
- pre/post publication date of the anomaly research

Candidate implementation idea:

`passive favorite + weather-model veto`

The weather model is used primarily to reject favorites whose physical fair value is not sufficiently safe, rather than to hunt every low-priced tail discrepancy.

Promotion still requires current-date OOS and forward paper evidence because published anomalies can decay.

---

# 7. Cheap-contract floor is configurable and empirical

Add research parameter:

`MIN_ENTRY_PRICE_CENTS`

Initial tournament values:

- 5
- 10
- 15
- 20
- 25

A 15-cent exclusion is a sensible provisional default for experiments motivated by cheap-tail losses and fee drag, but it is not a universal production invariant until our own weather data confirm it.

Report edge and realized return by price band and execution mode.

---

# 8. Cross-venue basis replaces naive arbitrage assumptions

Canonical matching must distinguish:

- EXACT_EQUIVALENT
- SAME_PHYSICAL_REGION_DIFFERENT_STATION
- SAME_STATION_DIFFERENT_SOURCE_OR_WINDOW
- RELATED_WEATHER_EVENT
- UNRELATED

Only EXACT_EQUIVALENT can be treated as direct cross-venue parity/arbitrage.

For non-exact pairs, model the basis explicitly:

`venue_A_settlement_value - venue_B_settlement_value`

Potential features:

- long-run station spread
- intraday station spread
- wind-direction regime
- marine/urban heat-island regime
- cloud cover difference
- frontal timing difference
- Local Standard Time window mismatch
- measurement/rounding source mismatch

Estimate the full conditional basis distribution before calling the position hedged.

Important scope rule: settlement examples from global Polymarket (`polymarket.com`) must not be copied onto Polymarket US. Polymarket US rules must be independently discovered and verified from the official US market/event/rules surface.

---

# 9. Polymarket US execution model correction

Do not assume Polymarket US uses the same blockchain/CTF execution confirmation model as global Polymarket.

The official Polymarket US SDK exposes:

- market listing/retrieval
- order book and BBO
- market WebSocket
- private WebSocket for orders, positions and account balance
- order IDs/state updates

Therefore the correct production architecture is still venue-specific, but the US adapter should be driven by what the official US API actually returns.

Implement separate `KalshiOrderManager` and `PolymarketUSOrderManager` behind one interface, with venue-specific state transitions. Add transaction-hash/block-receipt handling only if Polymarket US live responses/documentation prove those fields are part of the US execution path.

Never reuse global Polymarket Polygon/CTF assumptions in the US adapter by default.

---

# 10. Revised rapid build sequence

## Stage 0A — market truth

- exact Kalshi weather family discovery
- exact Polymarket US weather discovery
- official station/source/window/bucket mapping
- fee/tick metadata

## Stage 0B — settlement reconstruction

- historical CLI official outcomes
- high-resolution/public ASOS reconstruction
- LST/DST event windows
- measurement/rounding mismatch study
- boundary-crossing analysis

This stage can kill or validate the highest-conviction settlement-mechanics thesis quickly.

## Stage 1 — executable historical books

- PMXT weather-only Kalshi Parquet
- causal book replay
- maker/taker execution fields
- decision snapshots
- queue/fill assumptions versioned

## Stage 1P — parallel live shadow recorder

Start immediately once exact mappings exist. Continuously record:

- public ASOS/settlement-state observations
- NBM guidance
- optional HRRR
- both venue books where available
- model/fair-value estimates
- maker quote candidates
- taker candidates
- markouts
- final official settlement

Do not wait for the full historical archive before collecting forward truth.

## Stage 2 — settlement-mechanics strategies

Test first:

- public-observation vs reconstructed settlement posterior
- boundary probability around hidden/rounded extrema
- LST-window edge cases
- official-report timing effects

## Stage 3 — structural maker strategies

Test:

- passive high-price favorites
- maker favorite + settlement-model veto
- maker favorite + NBM/ASOS veto
- queue/adverse-selection filters

## Stage 4 — NBM/ASOS forecasting

- NBM baseline
- ASOS residual update
- continuous final-value distribution
- contract probability map

## Stage 5 — HRRR incremental test

Only add if it improves untouched OOS net P&L or calibration beyond NBM + ASOS.

## Stage 6 — cross-venue relative value

Only after each venue's settlement mapping is exact. Model station/source/window basis explicitly.

## Stage 7 — paper and tiny live

- venue-specific paper managers
- maker/taker paper modes
- private websocket + REST reconciliation
- one-contract live smoke
- actual fee/fill/settlement reconciliation

---

# 11. Decision-snapshot additions

Add to the existing snapshot schema:

- `settlement_window_start_utc`
- `settlement_window_end_utc`
- `station_local_standard_offset`
- `station_civil_offset`
- `asos_qc_status`
- `asos_qc_reason_codes`
- `official_cli_value_so_far` when causally available
- `public_5min_max_or_min_so_far`
- `reconstructed_settlement_posterior_mean`
- `reconstructed_settlement_posterior_p05/p50/p95`
- `measurement_basis_expected`
- `nbm_cycle`
- `nbm_age`
- `nbm_percentiles/probabilities`
- `execution_mode`
- `resting_price`
- `queue_ahead_estimate`
- `fill_probability_estimate`
- `maker_fee_estimate`
- `taker_fee_estimate`
- `markout_1m`
- `markout_5m`
- `markout_15m`
- `model_markout_1m/5m/15m`

---

# 12. New promotion gates

In addition to the existing roadmap gates:

- settlement reconstruction error characterized and acceptable
- DST/LST boundary tests pass
- ASOS QC fail-closed tests pass
- maker strategy adverse-selection markouts acceptable
- queue/fill model sensitivity does not erase the edge
- current fee class verified by venue/series
- cheap-contract performance reported separately
- structural favorite-longshot strategy survives recent OOS, not just historical pooled evidence
- NBM baseline beaten before HRRR complexity is promoted
- cross-venue positions classified as exact-equivalent or explicit basis trades
- venue-specific live order state reconciles exactly

---

# 13. Two-week validation sprint

Run in parallel:

### Track A — settlement truth
- choose 2–3 highest-volume temperature station families
- fetch historical CLI + best available ASOS history
- reconstruct daily high/low
- quantify public-observation/CLI mismatch
- validate LST windows

### Track B — market microstructure
- generate weather-only PMXT books
- build taker executable snapshots
- prototype maker queue/fill assumptions
- calculate 1/5/15-minute fill markouts
- stratify by price band

### Track C — forward shadow
- exact current weather market catalog
- record ASOS + NBM + books continuously
- record candidate maker/taker signals without orders
- reconcile official settlement daily

### Track D — Polymarket US
- discover actual US weather markets
- capture exact US settlement rules/station/source
- validate official US BBO/book/private-order-state schemas
- do not reuse global-Polymarket settlement or blockchain assumptions

At the end of two weeks, answer four kill-or-continue questions:

1. Does public-ASOS vs official-settlement reconstruction produce a repeatable, bucket-relevant basis?
2. Does passive favorite trading survive realistic queue/fill and adverse-selection assumptions in recent weather data?
3. Does NBM + causal ASOS improve executable pricing decisions beyond the market alone?
4. Are there enough exact Kalshi/Polymarket US contracts and liquidity to justify production capacity?

If the answer to a strategy family is no, kill it quickly and move capital/research time to the next family.
