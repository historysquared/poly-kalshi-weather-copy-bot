# Weather Production Engine

## Objective

This repository is the independent weather trading system for researching, backtesting, shadow/paper trading and eventually live trading alpha-generating weather strategies on Kalshi and Polymarket.

The engine architecture is adapted from the proven generic infrastructure in `historysquared/kalshi-15m-lab`. There is no runtime dependency on the BTC repository and no BTC data, strategies, results or settlement logic are imported.

## Ported generic engine components

- venue-neutral market/signal/fill/settlement/forward-mark models
- explicit execution-quality labels
- transactional SQLite state for signals, fills, marks, settlements, paper P&L and errors
- partitioned atomic Parquet recording for high-frequency/history streams
- lazy DuckDB scans for large historical Parquet stores
- latency-aware executable-touch backtest fills
- explicit slippage/stress assumptions
- settled P&L / ROI primitives
- shadow strategy isolation and multi-horizon markouts
- paper fill/reconciliation primitives
- fail-closed fee schedule routing and net-EV calculation
- dual-gated live mutation safety
- per-order contract/risk validation
- direct authenticated Kalshi REST client and post-only-capable order payloads

## Weather-only layers that remain authoritative

- canonical weather event IDs and venue contract mapping
- station/settlement-source mapping
- bucket boundaries and inclusivity
- official weather settlement transforms
- ASOS observations and publication-latency assumptions
- HRRR/model vintages
- GOES/RTMA/MRMS/GLM/AFD features
- calibrated weather probabilities
- weather strategy logic
- Polymarket-vs-Kalshi semantic equivalence classification

## Production data flow

```text
weather observations + model vintages
        -> feature snapshot
        -> calibrated event probability
        -> venue-specific contract probability
        -> executable venue book
        -> raw edge
        -> fees + slippage + latency reserve
        -> executable net EV
        -> strategy signal / abstain
        -> shadow -> paper -> guarded live
        -> settlement reconciliation
        -> date-block OOS scorecard
```

## Promotion gates

A strategy must not be promoted based on forecast accuracy alone. Promotion requires:

1. exact settlement mapping and no-lookahead data timestamps
2. executable quotes/depth rather than midpoint prices
3. venue-specific verified fee treatment
4. realistic latency/slippage stress
5. chronological OOS evaluation
6. positive net P&L and mean event ROI
7. positive settlement-date block-bootstrap lower bound or explicit provisional status
8. acceptable concentration/capacity
9. successful forward paper trading
10. tiny controlled live deployment before scaling

## Live safety

Live mutations require two independent gates:

- venue-specific configured live-trading enablement
- explicit application `--live` intent

Default max size is one contract per order until deliberately raised. Unknown fee schedules and unknown settlement semantics fail closed.

## Venue status

### Kalshi

Authenticated REST plumbing has been ported from the BTC engine. Weather contract discovery and settlement mapping stay weather-specific. Live use remains disabled by default.

### Polymarket

Public weather discovery/normalization already exists. The generic execution engine supports a Polymarket client through the same guarded interface, but the authenticated Polymarket US trading adapter must be implemented and schema-validated before live mutations are enabled.

## Immediate build order

1. finish real PMXT Kalshi raw-Parquet parser against the validated archive schema
2. normalize historical executable books into weather-only Parquet
3. canonical weather-event/contract catalog with EXACT/PROBABLE/REVIEW/REJECT semantic mapping
4. ASOS + HRRR baseline decision snapshots
5. chronological net-EV backtests
6. continuous shadow/paper runner using this engine
7. settlement reconciliation and scorecards
8. guarded Kalshi live smoke path
9. authenticated Polymarket trading adapter and controlled live smoke path
10. add GOES/radar/RTMA/AFD only when they prove incremental OOS value
