# Fast Weather Data / MADIS LDM Plan

## Objective

Make data latency itself a measurable alpha input. The strategy layer must receive high-frequency surface observations through a normalized event stream and must fail closed whenever the source, clock, sensor status, settlement mapping, or market data are not healthy.

No live order submission is enabled by this plan. All strategies remain dry paper / shadow until separately promoted.

## Why MADIS 1-minute ASOS (OMO)

NOAA MADIS documents One Minute Observations (OMO, historically HFMETAR) as ASOS binary observations distinct from METAR. They contain the raw observed value on the minute. The public dataset includes temperature, dewpoint, humidity, wind, pressure-related fields, visibility, weather, and sensor-status fields such as TSS/TDSS/OPSTAT.

MADIS supports HTTPS/FTP/LDM and recommends LDM for users requiring the fastest access to real-time data. We must still measure actual publication latency; transport speed cannot make the upstream product cadence faster.

## Phase 1 — obtain and measure the feed

1. Request NOAA MADIS real-time LDM access for the weather collector host, using its stable hostname/IP.
2. Request only the OMO/HFMETAR feed needed for CONUS surface work rather than broad data families.
3. Record three clocks for every product/observation:
   - observation valid time;
   - first local receive time;
   - parsed/normalized time.
4. Build a latency distribution by station and minute before using the feed for alpha.
5. Compare OMO arrival with AviationWeather METAR/SPECI, current market repricing, and any Synoptic high-frequency feed we later obtain.

## Phase 2 — collector isolation

Run LDM as a collector service independent of strategies. Strategy deployments must never restart it.

Raw products are immutable under `/data/weather/raw/madis_omo/YYYY/MM/DD/`. Store product identifier, raw bytes/file reference, upstream metadata, local receive timestamp, schema version and collector version.

A separate decoder writes normalized append-only observations. Collector failure, decoder failure and strategy failure are three different health states.

## Phase 3 — normalized surface event

Each normalized record should include at minimum:

- station ICAO/NWS identifier;
- observation valid timestamp UTC;
- local receive timestamp UTC;
- temperature at native precision;
- dewpoint / RH;
- wind direction and speed;
- pressure fields when semantics are verified;
- visibility / present weather where useful;
- temperature/dewpoint sensor status;
- overall operating status;
- source=`MADIS_OMO_LDM`;
- raw product provenance and checksum.

Never round the observation before feature or settlement-basis calculations.

## Phase 4 — causal feature engine

Recompute only from observations available as of the signal timestamp:

- current temperature;
- high-so-far and low-so-far;
- minutes since high / low;
- 1m, 3m, 5m, 10m and 15m temperature slopes;
- 5m/10m acceleration (`d²T/dt²`);
- drop from high / rise from low;
- dewpoint depression and its slope;
- wind shift / gust transition;
- pressure tendency when validated;
- observation cadence/gap health;
- sensor-status health.

The existing `surface_state` contract remains the common interface. MADIS/LDM becomes a better provider, not a separate model architecture.

## Phase 5 — strategy uses

1. **Daily-high heating stall** — identify transition from positive heating to flat/negative slope plus negative acceleration. Compare the newly inferred terminal-high distribution with Kalshi brackets.
2. **Daily-low cooling continuation/stall** — mirror the causal state for nighttime lows rather than reusing the high model.
3. **Sea-breeze lock** — combine one-minute wind-direction jump, temperature break and coastal station history.
4. **Front/outflow arrival** — combine OMO temperature/wind/dewpoint transitions with MRMS/GLM/RWIS/CWOP upstream evidence.
5. **Hourly temperature markets** — measure whether the official/near-official observation becomes knowable before the Kalshi ladder fully reprices.
6. **Settlement-basis model** — learn the distribution from OMO state to each venue's actual settlement source; never assume OMO itself is settlement truth.
7. **METAR-vs-OMO incremental test** — every strategy must show whether OMO adds after-cost value beyond the slower public METAR/SPECI path.

## Phase 6 — market synchronization

Join each OMO event to the locally received Kalshi L2 state using receive timestamps, not future exchange snapshots.

For every candidate store:

`obs_time → local_receive_time → feature_ready_time → market_book_time → signal_time → simulated_fill_time`.

Run the same 0/5/15/30/60/120/300-second latency stress and executable depth/fee assumptions used elsewhere in Weather Alpha.

## Fail-closed gates

No strategy signal if any required condition fails. At minimum:

- OMO source stale beyond its strategy-specific age limit;
- missing required slope/acceleration;
- sensor status invalid/out-of-service/conflicting;
- observation gap too large;
- clock/timestamp regression;
- exact settlement source/window not verified;
- market L2 stale or sequence health bad;
- executable ask/depth unavailable;
- calculation exception or schema mismatch.

Every failure updates a strategy health record and emits a deduplicated Telegram DEGRADED/ERROR alert. Silence must never mean both “no edge” and “broken data.”

## Telegram output

A paper signal should expose the evidence needed to audit it manually:

- strategy and city/station;
- OMO valid time and local receive latency;
- current temp, high/low, 5m/15m slope and acceleration;
- dewpoint/wind regime if used;
- settlement source and basis status;
- contract, side, executable price/depth and fee;
- model probability / edge;
- DATA_OK flag and any gate reasons;
- PAPER ONLY label.

`/status` must expose source and strategy health independently.

## Promotion test

Do not promote because the backtest win rate is attractive. Require chronological forward paper observations and whole-date confidence accounting. Measure:

- independent settlement dates;
- calibration / Brier / log loss where probabilities are used;
- executable after-fee P&L;
- date-clustered confidence interval;
- source-latency distribution;
- incremental value versus METAR-only baseline;
- robustness by station, season and weather regime.

A useful initial gate is 50 independent dates; 100+ is preferred. Promotion still requires a positive conservative after-cost result rather than merely crossing a date count.

## While LDM access is pending

Keep AviationWeather METAR/SPECI as the live fallback and IEM/NCEI one-minute data for historical reconstruction. Do not fabricate a one-minute live slope from sparse METAR reports. If a real 15-minute slope cannot be computed, the live strategies abstain and health says `MISSING_REQUIRED_SLOPE`.
