# Weather Alpha Production Roadmap

## Mission

Build an independent weather prediction-market system that can rapidly discover, backtest, shadow trade, paper trade, and cautiously live trade alpha-generating weather strategies on Kalshi and Polymarket US. The system must optimize for executable net edge and realized P&L, not forecast accuracy in isolation.

## Governing objective hierarchy

Every strategy must answer, in order:

1. Is the physical weather event and venue settlement definition understood exactly?
2. What is the best causal probability estimate available at the decision timestamp?
3. Is the venue market mispriced relative to that probability?
4. Can the edge be executed after latency, displayed depth, queue/partial-fill assumptions, fees, and slippage?
5. Does the edge survive chronological out-of-sample evaluation and settlement-date block uncertainty?
6. Is there enough frequency/capacity to matter?
7. Does it survive live shadow and paper trading?
8. Does tiny live trading reconcile exactly with exchange fills, fees, and settlement?

Only then is it a production strategy.

---

# A. Non-negotiable architecture boundaries

## Weather-only project

This repository remains fully separate from BTC/crypto projects. Reuse generic engineering patterns only. Do not import BTC data, strategies, labels, settlement logic, or runtime databases.

## Venue separation

Kalshi and Polymarket US remain separate execution venues with separate:

- market IDs and APIs
- settlement rules
- fees
- tick sizes
- order books
- fill/reconciliation logic
- live credentials

They can share a canonical weather event only when the physical event and settlement semantics are equivalent.

## Canonical event model

The internal weather key is independent of venue, for example:

`KNYC_2026-07-15_DAILY_HIGH`

A venue contract maps to that event through explicit fields:

- venue
- contract_id / slug
- event_id
- station
- settlement date
- measure: DAILY_HIGH, DAILY_LOW, RAIN, SNOW, etc.
- lower/upper bound
- inclusivity
- unit
- settlement source/report
- timezone and reporting day
- close/open times
- fee/tick metadata
- mapping status EXACT / PROBABLE / REVIEW / REJECT

Only EXACT mappings may enter serious automated P&L backtests and live trading.

---

# B. Production data architecture

## Storage layout

`/data/weather/`

- `raw/kalshi/pmxt_orderbooks/`
- `raw/kalshi/market_metadata/`
- `raw/kalshi/trades/`
- `raw/kalshi/settlements/`
- `raw/polymarket_us/market_metadata/`
- `raw/polymarket_us/orderbooks/`
- `raw/polymarket_us/trades/`
- `raw/polymarket_us/settlements/`
- `raw/asos/`
- `raw/hrrr/`
- `raw/goes/`
- `raw/rtma/`
- `raw/mrms/`
- `raw/nws/`
- `normalized/markets/`
- `normalized/orderbooks/`
- `normalized/observations/`
- `normalized/forecasts/`
- `normalized/settlements/`
- `features/`
- `decision_snapshots/`
- `results/`
- `manifests/`
- `live/`

## Storage rules

- Raw inputs are immutable.
- Normalized outputs are reproducible.
- Feature datasets are disposable/rebuildable.
- Results are versioned.
- Historical large datasets use Parquet + DuckDB.
- SQLite is for compact operational state, not giant historical order-book archives.
- Preserve native decimal precision until final modeling/execution boundaries.
- Every derived dataset includes provenance: schema version, git commit, source files, generated_at, feature version, latency model, fee model, settlement-rules version.

## Disk controls

- >25 GB free: normal
- 10–25 GB: warn
- <10 GB: stop new bulk downloads
- <5 GB: emergency stop

The current 80 GB disk is enough for initial research, but bulk PMXT + HRRR/GOES eventually requires a mounted DigitalOcean Volume at `/data/weather`.

---

# C. Market discovery and settlement truth

## C1. Kalshi

Use official Kalshi metadata and series/event rules, not generic ticker keywords.

Current validated archive path:

PMXT raw Parquet -> parser -> reconstructed executable YES/NO books.

Current real-file validation already proved:

- actual PMXT raw Parquet schema handling
- Decimal prices/sizes
- receipt timestamp fallback
- complementary YES/NO ask reconstruction
- no-lookahead replay

Next settlement/catalog tasks:

1. Fetch official series metadata for known weather families.
2. Resolve authoritative station / report / settlement source.
3. Decode event date from official event metadata, with ticker date only as consistency check.
4. Verify bucket bounds/inclusivity.
5. Store exact settlement timezone/day definition.
6. Promote contracts from PROBABLE to EXACT only when all required evidence is present.

Do not infer station solely from city name.

## C2. Polymarket US

Use the official Polymarket US API/SDK, not global Polymarket Gamma/CLOB assumptions.

Required functions:

- list events/markets
- filter weather category/tags
- retrieve market by slug/id
- fetch BBO and order book
- fetch settlement result
- authenticate only in live execution layer

The official US SDK exposes market listing, retrieval, order book, BBO and settlement methods. Build a weather-specific adapter around these stable boundaries.

Discovery requirements:

1. Discover current and archived weather events.
2. Parse title/description/outcome labels.
3. Resolve city -> official settlement station from rules, not city-center guess.
4. Parse threshold/bucket shape and target date.
5. Store settlement language and source.
6. Classify EXACT / PROBABLE / REVIEW / REJECT.
7. Map exact-equivalent contracts to the canonical weather event.

## C3. Cross-venue matching

Cross-venue equivalence requires all of:

- same physical station/location
- same date/reporting window
- same weather measure
- same unit/rounding semantics
- same lower/upper bound and inclusivity
- compatible settlement source/report

If any differ, label relative-value only, not risk-free arbitrage.

---

# D. Historical execution dataset

## D1. Weather-only PMXT extraction

Once Kalshi contracts are EXACT:

- filter the general PMXT archive down to weather tickers
- retain raw timestamps, bids, sizes, and source provenance
- reconstruct executable YES and NO sides
- save compact partitioned weather-only Parquet

## D2. Decision snapshots

The central backtest table should represent exactly what the bot knew at a decision time.

Fields include:

- decision_time
- venue
- contract_id
- canonical weather_event_id
- best YES bid/ask
- best NO bid/ask
- depth VWAP at 1 / 5 / 25 contracts
- spread
- book age
- source receipt/exchange timestamp
- ASOS current temp
- high-so-far / low-so-far
- ASOS observation age
- dT/dt at 5/10/15/30 min
- dewpoint
- pressure tendency
- wind shift/speed
- HRRR predicted high/low
- HRRR cycle/vintage/lead time
- HRRR age
- model residuals
- calibrated contract probability
- executable raw edge
- fees/slippage/latency reserve
- executable net EV
- eventual settlement/result

Expensive raw replay happens once; strategy tournaments mostly read compact decision snapshots.

---

# E. Weather data and causal timing

## E1. ASOS baseline

Build first because it is cheap, interpretable, and directly measures the settling station.

Core features:

- current temperature
- high/low so far
- dT/dt over 5/10/15/30 min
- dewpoint/dewpoint spread
- wind speed/direction shift
- pressure tendency
- observation age
- distance to contract boundary
- time of day / solar geometry
- time since local sunrise / until sunset

Historical IEM/ASOS lacks exact receipt timestamps. Apply an explicit conservative publication-delay assumption, e.g. 5–10 minutes, and stress it rather than pretending observations were instantaneous.

## E2. HRRR baseline

Use archived model vintages with explicit cycle and lead time.

Primary fields:

- 2m temperature
- cloud cover
- downward shortwave radiation
- wind
- humidity/dewpoint if available
- predicted daily max/min path
- forecast revision between cycles
- model-vs-observed residual
- remaining expected heating/cooling

Never join a future HRRR cycle to an earlier decision time.

## E3. Core combined model

Model the final official daily value as:

`final official value = current forecast path + learned real-time forecast error`

Use ASOS to learn/update the residual conditional on:

- model-observation error
- high/low so far
- temperature velocity
- cloud/solar mismatch
- wind shift
- dewpoint
- pressure
- remaining daylight
- expected time of daily extreme

Output a calibrated continuous distribution, not just a point estimate. Convert that distribution into each venue contract probability.

---

# F. Alpha strategy tournament

Run many simple strategies early rather than overbuilding one model.

## F1. Baselines / controls

- market implied probability only
- climatology only
- HRRR raw deterministic-to-probability baseline
- ASOS persistence
- blind-NO negative control

Blind-NO remains frozen as a negative control and must never be optimized.

## F2. Candidate strategy families

1. ASOS nowcast mispricing
   - current temp/high-so-far relative to market bucket
   - rapid heating/cooling continuation
   - stalled heating reversal

2. HRRR forecast-value strategy
   - trade when HRRR distribution differs materially from market after costs

3. ASOS + HRRR residual strategy
   - primary first serious model

4. Forecast-revision strategy
   - exploit large HRRR cycle revisions not yet reflected in prediction markets

5. Late-day extreme lock strategy
   - when physics makes additional warming/cooling increasingly improbable

6. Boundary oscillation / bucket transition strategy
   - when probabilities shift sharply around adjacent brackets as observed/model path crosses thresholds

7. Cross-venue relative-value strategy
   - Kalshi vs Polymarket US only when settlement semantics are exactly aligned

8. Liquidity/market-making strategy
   - only after fair value is reliable
   - wide passive quotes around calibrated fair value
   - require queue/adverse-selection testing

9. Forecast-source disagreement strategy
   - HRRR vs alternative ensemble/model when one venue appears slow to incorporate revision

10. Event-specific weather regimes
   - cloud arrival
   - frontal passage
   - convective cooling
   - overnight radiational cooling

## F3. Later incremental datasets

Add only if they improve untouched OOS results:

- GOES cloud/solar observations
- RTMA-RU thermal field
- MRMS/radar
- GLM lightning
- NWS AFD forecaster text
- additional ensembles/models

Each addition requires ablation proof.

---

# G. Backtest methodology

## No-lookahead

Every source requires:

- source timestamp
- receipt/availability timestamp or explicit conservative availability model
- decision timestamp

A feature is available only if its assumed receipt time <= decision time.

## Executable pricing

Never score midpoint alpha as P&L.

For each signal calculate:

- best executable ask/bid
- VWAP for requested size
- visible depth
- stale-book rejection
- latency-shifted book
- one-tick adverse scenario
- fees
- slippage

Test latency at minimum:

0 / 30 / 60 / 120 / 300 / 600 / 900 seconds

Weather is slower than BTC, but stale inputs and late market reactions still matter.

## Chronological evaluation

Use walk-forward/date-block OOS splits, never random row splits.

Minimum promotion evidence:

- >=30 independent resolved events
- >=20 independent settlement dates
- >=3 stations

Report:

- net P&L
- ROI
- mean event ROI
- median event ROI
- hit rate
- calibration/Brier/log loss as diagnostics
- max drawdown
- concentration by station/date/strategy
- capacity at 1/5/25 contracts
- settlement-date block-bootstrap 95% CI

Promotion requires positive net P&L after costs and a positive lower confidence bound, or explicit PROVISIONAL status if sample size is insufficient.

---

# H. Generic trading infrastructure to keep/port

The BTC/Polymarket repo review shows several generic infrastructure improvements that are useful for weather and should be incorporated without importing BTC logic.

## Already aligned / ported

- venue-neutral signal/fill/settlement models
- SQLite operational state
- atomic Parquet recording
- DuckDB research queries
- paper execution primitives
- fee routing
- live double-gating
- per-order risk validation
- forward markouts
- authenticated Kalshi client

## Additional generic patterns to incorporate

1. Credential-free paper mode
   - paper mode must override all order-submission gates even if config was imported earlier
   - healthcheck must contain no submission method

2. ResolutionConfig boundary
   - each market snapshot carries rule-derived settlement config
   - missing settlement evidence blocks settlement-sensitive trading

3. Stable provider boundaries
   - market discovery provider
   - order-book provider
   - settlement provider
   - paper/live order manager
   - weather-data providers

4. Market WebSocket + user/order WebSocket pattern
   - public market feed drives books
   - private user feed drives order/fill state in live mode
   - periodic REST reconciliation catches missed websocket events

5. Fill-based accounting
   - risk, fees, notional and P&L recorded on fills, not merely on order submission

6. Exchange event handling
   - market resolved
   - tick-size changes
   - reconnect/resubscribe
   - stale feed detection

7. Execution health / market quality telemetry
   - book age
   - websocket connected state
   - last fill time
   - open orders
   - recent fills
   - strategy eligibility/block reason

8. Explicit live acceptance test
   - public APIs reachable
   - current market discovered
   - book received
   - websocket connected
   - SQLite read/write
   - paper fill persisted
   - real submissions = zero

9. Strategy status taxonomy
   - WORKING only after observed attributable paper/live execution
   - NO SIGNAL OBSERVED if strategy runs cleanly but emits none
   - PARTIAL if a dependency is unavailable
   - BROKEN if execution path fails

10. Preserve successful pagination/data on late API failure
   - do not discard already-valid pages because a later page errors

## Do not port

- BTC/crypto settlement feeds
- Binance logic
- Chainlink TWAP logic
- BTC model artifacts
- crypto strategy code
- wallet-copy assumptions unrelated to weather
- crypto-specific fee/tick assumptions

---

# I. Live trading safety and production operations

## I1. Live gates

Require both:

- venue-specific `ENABLE_LIVE=true`
- explicit CLI/runtime `--live`

Default max order size = 1 contract until tiny live smoke tests pass.

Unknown settlement mapping, fee schedule, tick size, account state, or stale book -> fail closed.

## I2. Preflight healthcheck

Must test, without any order submission capability:

- clock skew
- disk space
- DB write/read
- market discovery
- settlement config retrieval
- public book/BBO
- websocket connectivity
- weather-data freshness
- fee/tick retrieval
- credential presence only if live mode is requested

## I3. Live reconciliation

For every order/fill:

- deterministic client/order ID
- persist intent before submission
- persist exchange response
- update on private websocket
- periodic REST reconciliation
- deduplicate fills
- reconcile fees
- reconcile final settlement
- compute realized P&L from exchange truth

## I4. Kill switches

Global stop on:

- stale or disconnected market data
- unknown settlement semantics
- excessive clock skew
- repeated order errors
- unexpected position
- missing fee schedule
- disk below threshold
- loss/drawdown limit
- duplicate-order detection

---

# J. Rapid path to first profitable candidate

## Stage 0 — finish truth layer

Deliverables:

- Kalshi EXACT daily-high contract catalog
- Polymarket US weather discovery/catalog
- station/date/settlement mapping
- fees/ticks verified

Exit gate: at least one station/date family can be mapped and settled exactly on each available venue.

## Stage 1 — historical executable dataset

Deliverables:

- weather-only PMXT Kalshi L2 Parquet
- historical metadata/results
- decision snapshot builder
- first 20+ settlement dates if archive coverage permits

Exit gate: replayed executable quotes at arbitrary historical decision timestamps with no-lookahead proof.

## Stage 2 — ASOS baseline tournament

Run immediately once decision snapshots exist.

Models:

- persistence
- high-so-far / low-so-far
- velocity
- distance-to-bucket
- time-of-day

Exit gate: identify whether simple station observations already beat market pricing after costs.

## Stage 3 — HRRR + ASOS residual model

Build calibrated continuous final-value distribution.

Exit gate: chronological net-EV backtest with realistic execution assumptions.

## Stage 4 — live shadow immediately

Do not wait for perfect historical research. As soon as market mapping and probability generation are reliable, continuously log:

- model probability
- executable market probability
- theoretical edge
- net edge
- hypothetical order
- markouts
- settlement result

This creates current-condition forward data while historical testing continues.

## Stage 5 — paper execution

Simulate real order choices, sizes, cancellations and fills against live books.

Exit gate:

- clean 7+ day run
- no duplicate orders
- exact settlement reconciliation
- positive candidate net P&L or clearly useful negative evidence

## Stage 6 — one-contract live smoke

Kalshi first if authenticated path is ready; Polymarket US once official authenticated adapter is complete.

Test:

- one post-only/maker order where appropriate
- one tiny taker order if needed to verify real fee treatment
- cancel/replace
- fill classification
- fee logging
- settlement/P&L reconciliation

Only scale after execution truth matches assumptions.

---

# K. Parallel workstreams to maximize speed

Run these in parallel rather than serially:

1. Market truth: Kalshi + Polymarket US catalog/settlement mapping
2. Historical replay: PMXT weather subset + decision snapshots
3. Weather data: ASOS archive + HRRR vintages
4. Research: ASOS baseline strategies
5. Production: shadow/paper runner + healthcheck + reconciliation
6. Polymarket US authenticated execution adapter

The shortest route to money is not waiting for GOES/radar/AFD. The first candidate should be tested with ASOS and HRRR as soon as executable historical snapshots exist.

---

# L. Strategy promotion states

Every strategy has one status:

- IDEA
- DATA_READY
- BACKTESTING
- KILL
- INSUFFICIENT_DATA
- PROVISIONAL
- SHADOW
- PAPER
- LIVE_TINY
- LIVE

No strategy may jump directly from backtest to scaled live.

---

# M. Definition of done for production v1

Production v1 is complete when:

1. Kalshi and Polymarket US weather discovery is automatic.
2. Exact settlement mappings are enforced.
3. Historical executable books can be replayed causally.
4. ASOS and HRRR can be joined without lookahead.
5. Models output calibrated contract probabilities.
6. Backtests use executable depth, latency, fees and slippage.
7. Walk-forward/date-block scorecards are automatic.
8. Shadow and paper modes run continuously.
9. Private/live order state is reconciled against exchange truth.
10. Both venues have guarded live adapters or are explicitly marked unavailable.
11. One-contract live tests reconcile fills, fees and settlement correctly.
12. At least one strategy has survived all promotion gates or has been explicitly killed with evidence.
