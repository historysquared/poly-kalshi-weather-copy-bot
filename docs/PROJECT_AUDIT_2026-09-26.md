# Weather Alpha Complete Audit — 2026-09-26

## Executive status

The project is operational as a paper/research system and the unified signal branch is clean and testable. No live-order submission is enabled.

Current validated branch: `codex/unify-weather-signal-pipeline`.

Key audit conclusions:

- canonical research pipeline is implemented and now populated from legacy forward JSONL history;
- live dashboard, main paper, A/B/C tournament, D diagnostic, settlement scorers, and scorecard are running from the unified worktree;
- exact Kalshi tail semantics were corrected from inclusive to strict `<` / `>` and historical economics were rerun;
- historical settlement alpha remains positive after the correction but has only 28 independent settlement dates;
- empirical remaining-heating alpha is highly positive in the current historical sample but has only 21–22 independent dates and is therefore `PROVISIONAL`, not promoted;
- the current Weather Company forward heuristic is performing poorly and remains a falsification/data-collection track;
- Kalshi L2 is now active as a read-only 20-series systemd archive collector; Telegram remains blocked by missing local credentials;
- the unified branch is not yet published to GitHub; server-side git push authentication is absent.

## Code and test health

- `pytest -q`: **132 passed**
- `python -m compileall`: PASS
- runtime placeholder scan: no active mock/placeholder/dummy/stub substitutions found in production scripts/modules
- legacy duplicate root paper portfolio removed on the unified branch
- canonical signal model is `weather_alpha.engine.models.Signal`
- strategy registry is machine-readable and mapped to the master hypothesis inventory

## Corrected historical settlement alpha

The audit found a real contract-boundary bug: Kalshi tail contracts resolve on strict `greater than` / `less than`, while old code treated exact thresholds as inclusive.

Cross-check against 5,694 resolved contracts found 100 exact-boundary tail misclassifications under the old semantics and zero under strict semantics.

Corrected historical causal-settlement replay:

- physical events: **127**
- independent settlement dates: **28**
- 300-second latency / depth 5 / no price floor: **114 trades, 69 wins / 45 losses, 19.14% ROI**
- 0-second / depth 5 / no price floor: **127 trades, 82 / 45, 20.42% ROI**
- all **30/30** tested latency/depth/price-floor cells pass the existing whole-date robustness test
- representative 300-second/depth-5 date-block 95% ROI interval: approximately **8.1% to 29.8%**

This remains a research candidate because 28 independent dates are below the project promotion requirement.

## Empirical remaining-heating alpha

Causal historical state is built only from observations available at each snapshot. Neighbor outcomes are restricted to earlier settlement dates at the same station.

Current economic replay:

- selected signals: **78**
- 5 stations
- 21–22 independent dates depending on latency
- 300-second / depth 5: **64 executed, 53 wins / 11 losses, 83.13% ROI**
- date-block ROI interval for that configuration is strongly positive in this sample

The result is intentionally classified **PROVISIONAL**. The validator now requires at least **50 independent settlement dates** before a positive strategy can receive `KEEP`; 100+ dates remain preferred for production consideration.

## Forward paper scorecard

Legacy Weather Company JSONL history was migrated into `/data/weather/live/weather_research.sqlite3` after creating a pre-migration backup.

Current settled forward-paper economics:

- main terminal-high v1: **29 settled, 4–25, -60.98% ROI, 13 dates / 5 stations**
- A control: **2 settled, 0–2**
- B moderate: **2 settled, 0–2**
- C exploratory: **2 settled, 0–2**
- D diagnostic no-lock: **10 settled, 4–6, +43.88% ROI**, but only **2 independent dates**

All forward tracks remain `INSUFFICIENT_DATA`. D is diagnostic-only and is not production eligible.

## Forecast and contrail data

- archived NBM station-high history: 21 stations with one explicit archive gap on **2026-06-17**; no placeholder fill was inserted
- simple station median-bias correction was approximately neutral overall and is not promoted
- exact Kalshi bucket reconstruction is available for the historical study windows
- contrail signal is now represented in the master registry as **M23**
- master hypothesis inventory: **154 items**, **14 executable-mapped**, **0 unmapped executable signals**
- contrail q90 work remains underpowered and research-gated

## Live operations

Active tmux services:

- `weather-dash`
- `weather-paper`
- `weather-tourney`
- `weather-diagnostic`
- main/tournament/diagnostic settlement scorers
- `weather-scorecard`

The launcher now uses exact tmux session-name matching and an explicit worktree Python executable, preventing prefix collisions and cross-worktree import failures.

## Live feed hardening

`weather_alpha.providers.live_surface` now:

- prefers AviationWeather METAR;
- caches successful station observations atomically;
- avoids unnecessary IEM requests when a very fresh cache exists;
- retries IEM HTTP 429 responses with bounded backoff;
- can use a still-fresh cache when both providers fail;
- preserves the original provider in the cache source label and still fails closed on stale observations.

A direct five-station smoke successfully returned KNYC, KMDW, KMIA, KLAX and KDEN and populated the cache. At the beginning of a new Local Standard Time settlement day, KNYC/KMIA can legitimately have no in-window observation until the first station report arrives; this is not filled with pre-window data.

## Cleanup completed

- stale partial research outputs moved to `/data/weather/archive/cleanup_20260926/live_partials`
- accidental legacy script backups moved to `/data/weather/archive/cleanup_20260926/legacy_backups`
- legacy checkout restored to a clean working tree
- dev worktree now reuses the tested unified Python environment
- launcher made worktree-portable
- live health script now audits process state, freshness, research-store counts, scorecard and credential blockers
- 26 GB PMXT raw orderbook archive retained; disk still has substantial free capacity and this data is not disposable

## Remaining blockers

1. Telegram bot token/chat ID are absent, so operational alerting is not yet live.
2. Unified code still needs publication through the GitHub connector/PR flow.
3. Historical high-performing strategies remain below the pre-registered promotion threshold.
4. ASOS/market/contrail collection still needs separation from strategy processes into collector services.

## Next build order

1. Keep the Kalshi L2 archive continuously running; collector deploys are manual/tagged and separate from strategy deploys.
2. Restore Telegram and add stale/dead/recovered collector alerts plus an external dead-man heartbeat.
3. Publish this unified branch to GitHub, run CI, review and merge.
4. Separate ASOS/market/contrail collectors from strategy services.
5. Extend the corrected settlement and remaining-heating studies under frozen configs and pre-registered clustered promotion rules.
6. Continue P0/P1 alpha implementation from the master registry using the canonical runner/scorecard.
