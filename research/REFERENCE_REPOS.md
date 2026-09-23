# External Weather/Trading Repositories — Engineering Reference Only

These repositories are reference material. Do not merge their projects wholesale into Weather Alpha Lab. Borrow isolated engineering patterns after review, reimplement them cleanly here, and preserve this project's separate weather-only data/runtime boundary.

## suislanchez/polymarket-kalshi-weather-bot

Useful patterns to inspect/reimplement:
- Kalshi + Polymarket venue abstraction
- periodic weather scanning/scheduling
- simulation mode and bankroll/equity tracking
- Brier-score calibration reporting
- fractional Kelly sizing with hard caps
- daily-loss circuit breaker and pending-position caps
- FastAPI backend/dashboard separation

Do not import:
- BTC strategy/data/runtime
- fixed 8% weather edge threshold as a truth
- direct 31-member GFS vote as final calibrated probability
- city/station assumptions without checking contract settlement rules

## yangyuan-zhen/PolyWeather

Useful patterns to inspect/reimplement:
- settlement-oriented city/station mapping
- multi-model blending / Dynamic Error Balancing concept
- full bucket-distribution view rather than single point forecast
- Gaussian / EMOS-style calibrated bucket probabilities
- intraday peak-window analysis
- live observation + forecast + market evidence chains
- replay tooling, health endpoints, metrics and incident visibility

Do not import:
- proprietary/private production thresholds or risk logic
- hosted-service/payment code
- data or assumptions whose licensing/settlement provenance is unclear

## nicolastinkl/hermes_weatherbot

Useful patterns to inspect/reimplement:
- execution loop structure: fetch -> probability -> EV -> size -> execute -> record
- min-volume / max-spread filters
- persistent trade history
- adaptive research thresholds as an experiment, not a default
- Telegram/alert ergonomics

Do not import:
- fixed sigma=2F probability assumption
- unvalidated self-tuning Kelly/EV thresholds
- private-key/on-chain Polymarket.com execution into Polymarket US execution code

## MoonsatProtocol/Polymarket-Weather-Bot

Useful patterns to inspect/reimplement:
- CLI run modes
- live/simulation separation
- position/PnL status tooling
- interval-driven scanning

Do not assume its NWS+Kelly signal creates alpha; Kelly is a sizing method, not a forecast edge.

## General reuse rule

A pattern is worth borrowing only if it improves one of:
1. exact settlement mapping
2. no-lookahead data replay
3. probability calibration
4. executable-price estimation
5. risk controls
6. observability/auditability
7. deployment reliability

All copied ideas must be implemented in `historysquared/poly-kalshi-weather-copy-bot` with weather-only tests and no runtime dependency on BTC projects.
